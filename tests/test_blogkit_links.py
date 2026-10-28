"""S3: the external link check module (adapters/astro/blogkit_links.py), in process.

A fake resolver replaces socket.getaddrinfo (host -> address) and a fake
opener answers each URL with a scripted status and Location, so no test
touches the network. The module's return values are the interface; they
point at URLs by digest only, never by the URL text.

Stdlib + pytest only. No network.
"""

from __future__ import annotations

import hashlib
import importlib.util
import contextlib
import io
import os
import signal
import socket
import sys
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "adapters" / "astro" / "blogkit_links.py"
PUBLIC = "93.184.216.34"
PRIVATE = "10.0.0.7"
# Fake credentials, joined so the secret scanner does not read them as a real basic-auth URL.
CREDENTIALED = "https://user:" + "pass@site.example"


@pytest.fixture(scope="module")
def links():
    spec = importlib.util.spec_from_file_location("blogkit_links_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["blogkit_links_under_test"] = module
    spec.loader.exec_module(module)
    return module


class Net:
    """Fake DNS and HTTP. Each URL maps to a list of answers used in turn (the last repeats)."""

    def __init__(self) -> None:
        self.hosts: dict[str, str] = {}
        self.answers: dict[str, list] = {}
        self.calls: list[tuple[str, str]] = []

    def host(self, name: str, address: str = PUBLIC) -> None:
        self.hosts[name] = address

    def answer(self, url: str, *answers) -> None:
        self.answers[url] = list(answers)

    def getaddrinfo(self, host, port, *args, **kwargs):
        if host not in self.hosts:
            raise socket.gaierror(socket.EAI_NONAME, "unknown host")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (self.hosts[host], port))]

    def open(self, request, timeout=None):
        url = request.full_url
        self.calls.append((request.get_method(), url))
        queue = self.answers.get(url) or [200]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        status, location = answer if isinstance(answer, tuple) else (answer, None)
        headers = Message()
        if location:
            headers["Location"] = location
        if status >= 300:
            raise urllib.error.HTTPError(url, status, "scripted", headers, io.BytesIO(b""))
        response = io.BytesIO(b"x")
        response.status = status
        response.headers = headers
        return response


@pytest.fixture
def net(monkeypatch) -> Net:
    fake = Net()
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    return fake


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _check(links, net: Net, url: str, **kwargs) -> dict:
    result = links.check_url(url, opener=net, pause=0, **kwargs)
    assert url not in repr(result)
    assert result["url"] == _digest(url)
    return result


def test_a_reachable_url_is_ok(links, net: Net) -> None:
    net.host("site.example")
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["status"]) == ("ok", 200)


@pytest.mark.parametrize("status", [404, 410])
def test_a_missing_page_blocks_as_permanent(links, net: Net, status: int) -> None:
    net.host("site.example")
    net.answer("https://site.example/gone", status)
    result = _check(links, net, "https://site.example/gone")
    assert (result["result"], result["kind"], result["status"]) == ("blocked", "permanent", status)


@pytest.mark.parametrize("url,setup", [
    ("https://inside.example/a", lambda n: n.host("inside.example", PRIVATE)),
    ("https://meta.example/a", lambda n: n.host("meta.example", "169.254.169.254")),
    ("https://loop.example/a", lambda n: n.host("loop.example", "127.0.0.1")),
    (CREDENTIALED + "/a", lambda n: n.host("site.example")),
    ("https://user@site.example/a", lambda n: n.host("site.example")),
    ("ftp://site.example/a", lambda n: n.host("site.example")),
], ids=["private", "metadata", "loopback", "user and password", "user only", "ftp"])
def test_a_policy_refusal_blocks_without_a_request(links, net: Net, url: str, setup) -> None:
    setup(net)
    result = _check(links, net, url)
    assert (result["result"], result["kind"]) == ("blocked", "policy")
    assert net.calls == []


def test_mailto_is_skipped_without_a_request(links, net: Net) -> None:
    result = _check(links, net, "mailto:someone@example.com")
    assert (result["result"], result["kind"]) == ("skipped", None)
    assert net.calls == []


def test_redirects_are_followed_hop_by_hop(links, net: Net) -> None:
    net.host("site.example")
    net.host("other.example")
    net.answer("https://site.example/a", (301, "/b"))
    net.answer("https://site.example/b", (302, "https://other.example/c"))
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["status"], result["hops"]) == ("ok", 200, 2)
    assert [url for _, url in net.calls] == [
        "https://site.example/a", "https://site.example/b", "https://other.example/c"]


@pytest.mark.parametrize("status", [404, 410])
def test_a_redirect_that_ends_missing_blocks(links, net: Net, status: int) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", (308, "/gone"))
    net.answer("https://site.example/gone", status)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["status"], result["hops"]) == ("blocked", "permanent", status, 1)


@pytest.mark.parametrize("target,setup,reason", [
    ("https://inside.example/x", lambda n: n.host("inside.example", PRIVATE), "url_not_allowed"),
    (CREDENTIALED + "/x", lambda n: None, "credentials_in_url"),
    ("file:///etc/passwd", lambda n: None, "url_not_allowed"),
], ids=["private host", "credentials", "file scheme"])
def test_a_redirect_to_an_unsafe_url_blocks_as_policy(links, net: Net, target: str, setup, reason: str) -> None:
    net.host("site.example")
    setup(net)
    net.answer("https://site.example/a", (302, target))
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["reason"], result["hops"]) == ("blocked", "policy", reason, 1)
    assert [url for _, url in net.calls] == ["https://site.example/a"]


def _chain(net: Net, length: int) -> None:
    net.host("site.example")
    for hop in range(length):
        net.answer(f"https://site.example/{hop}", (301, f"/{hop + 1}"))


def test_five_redirects_are_followed(links, net: Net) -> None:
    _chain(net, 5)
    result = _check(links, net, "https://site.example/0")
    assert (result["result"], result["hops"]) == ("ok", 5)


def test_a_sixth_redirect_blocks(links, net: Net) -> None:
    _chain(net, 6)
    result = _check(links, net, "https://site.example/0")
    assert (result["result"], result["kind"], result["reason"]) == ("blocked", "permanent", "too_many_redirects")
    assert len(net.calls) == 6


def test_a_redirect_without_a_location_blocks(links, net: Net) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", 302)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["reason"]) == ("blocked", "permanent", "redirect_without_location")


# ------------------------------------------------------------------ outcomes and retries


def _timeout() -> urllib.error.URLError:
    return urllib.error.URLError(socket.timeout("timed out"))


@pytest.mark.parametrize("answer,reason", [
    (503, "server_error"),
    (429, "rate_limited"),
    (_timeout(), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError()), "connection_failed"),
    (TimeoutError(), "timeout"),
], ids=["503", "429", "timeout", "refused", "bare timeout"])
def test_a_transient_failure_is_retried_twice_then_warns(links, net: Net, answer, reason: str) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", answer)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["reason"], result["attempts"]) == (
        "warning", "transient", reason, 3)
    assert len(net.calls) == 3


def test_an_unresolvable_host_is_transient(links, net: Net) -> None:
    result = _check(links, net, "https://nowhere.example/a")
    assert (result["result"], result["kind"], result["reason"], result["attempts"]) == (
        "warning", "transient", "dns_failed", 3)
    assert net.calls == []


def test_a_transient_failure_that_recovers_is_ok(links, net: Net) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", 502, _timeout(), 200)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["attempts"]) == ("ok", 3)


@pytest.mark.parametrize("url,setup", [
    ("https://inside.example/a", lambda n: n.host("inside.example", PRIVATE)),
    ("https://site.example/gone", lambda n: n.host("site.example") or n.answer("https://site.example/gone", 404)),
    ("https://site.example/bad", lambda n: n.host("site.example") or n.answer("https://site.example/bad", 400)),
], ids=["policy", "404", "400"])
def test_policy_and_permanent_outcomes_are_not_retried(links, net: Net, url: str, setup) -> None:
    setup(net)
    result = _check(links, net, url)
    assert result["result"] == "blocked" and result["attempts"] == 1
    assert len(net.calls) <= 1


@pytest.mark.parametrize("status", [403, 405])
def test_403_and_405_block_unless_listed(links, net: Net, status: int) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", status)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["reason"], result["status"]) == (
        "blocked", "permanent", "forbidden", status)
    # HEAD first, then one ranged GET, as upstream does.
    assert [method for method, _ in net.calls] == ["HEAD", "GET"]


@pytest.mark.parametrize("status", [403, 405])
def test_403_and_405_are_allowed_for_a_listed_url(links, net: Net, status: int) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", status)
    result = _check(links, net, "https://site.example/a", allowed={"https://site.example/a"})
    assert (result["result"], result["status"]) == ("allowed", status)


def test_a_listed_url_does_not_excuse_other_failures(links, net: Net) -> None:
    net.host("inside.example", PRIVATE)
    result = _check(links, net, "https://inside.example/a", allowed={"https://inside.example/a"})
    assert (result["result"], result["kind"]) == ("blocked", "policy")


def test_a_head_refusal_falls_back_to_get(links, net: Net) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", 405, 200)
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["status"]) == ("ok", 200)


# ------------------------------------------------------------------ malformed URLs never raise


@pytest.mark.parametrize("url", ["http://[::1", "https://site.example:99999/a", "https:///no-host", "http://exa mple/a"],
                         ids=["bad ipv6", "bad port", "no host", "space"])
def test_a_malformed_url_is_a_policy_refusal(links, net: Net, url: str) -> None:
    net.host("site.example")
    result = _check(links, net, url)
    assert (result["result"], result["kind"]) == ("blocked", "policy")


def test_a_malformed_redirect_target_is_a_policy_refusal(links, net: Net) -> None:
    net.host("site.example")
    net.answer("https://site.example/a", (302, "http://[::1"))
    result = _check(links, net, "https://site.example/a")
    assert (result["result"], result["kind"], result["hops"]) == ("blocked", "policy", 1)


# ------------------------------------------------------------------ the exception list file


def test_the_exception_list_is_read_in_the_upstream_format(links, tmp_path: Path) -> None:
    (tmp_path / "external-links.allowed").write_text(
        "# listed on purpose\n\nhttps://Site.Example/a#part\nhttp://other.example\n", encoding="utf-8")
    allowed = links.load_exceptions(tmp_path)
    assert allowed == {"https://site.example/a", "http://other.example/"}


@contextlib.contextmanager
def _deadline(seconds: int):
    """Interrupt a call that blocks (opening a FIFO with no writer) instead of hanging the run."""
    def expired(signum, frame):
        raise TimeoutError("still blocked")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_no_exception_list_means_none_allowed(links, tmp_path: Path) -> None:
    assert links.load_exceptions(tmp_path) == set()


def _symlinked_list(folder: Path) -> None:
    real = folder.parent / "real.allowed"
    real.write_text("https://site.example/a\n", encoding="utf-8")
    (folder / "external-links.allowed").symlink_to(real)


@pytest.mark.parametrize("setup,code,line", [
    (_symlinked_list, "exceptions_symlink", None),
    (lambda f: (f / "external-links.allowed").write_text("https://a.example/\n" * 5000), "exceptions_too_large", None),
    (lambda f: (f / "external-links.allowed").write_bytes(b"\xff\xfe\n"), "exceptions_unreadable", None),
    (lambda f: (f / "external-links.allowed").write_text("https://a.example/\nftp://b.example/\n"),
     "exceptions_invalid", 2),
    (lambda f: (f / "external-links.allowed").mkdir(), "exceptions_unreadable", None),
    (lambda f: os.mkfifo(f / "external-links.allowed"), "exceptions_unreadable", None),
], ids=["symlink", "over 64KB", "not utf-8", "bad entry", "folder", "FIFO"])
def test_an_unusable_exception_list_is_refused(links, tmp_path: Path, setup, code: str, line) -> None:
    setup(tmp_path)
    with pytest.raises(links.LinkError) as caught, _deadline(5):
        links.load_exceptions(tmp_path)
    assert (caught.value.code, caught.value.line) == (code, line)
    assert "example" not in str(caught.value)


def test_a_resolver_value_error_is_a_policy_refusal(links, net: Net, monkeypatch) -> None:
    def idna_failure(host, port, *args, **kwargs):
        raise UnicodeError(f"label too long: {host}")
    monkeypatch.setattr(socket, "getaddrinfo", idna_failure)
    result = _check(links, net, "https://" + "a" * 70 + ".example/")
    assert (result["result"], result["kind"], result["attempts"]) == ("blocked", "policy", 1)


def test_an_unexpected_failure_is_refused_without_url_text(links, net: Net) -> None:
    class Broken:
        def open(self, request, timeout=None):
            raise RuntimeError("boom " + request.full_url)
    net.host("site.example")
    result = links.check_url("https://site.example/secret-path", opener=Broken(), pause=0)
    assert (result["result"], result["kind"], result["reason"], result["attempts"]) == (
        "blocked", "policy", "check_failed", 1)
    assert "secret-path" not in repr(result)
