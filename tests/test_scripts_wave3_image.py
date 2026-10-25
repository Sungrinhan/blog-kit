"""Wave 3 regression tests: blog-image validator and MCP setup key handling."""

from __future__ import annotations

import datetime
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "blog-image" / "scripts"
VALIDATE = SCRIPTS / "validate_image_setup.py"
SETUP = SCRIPTS / "setup_image_mcp.py"

GOOGLE_KEY = "AIza" + "A" * 35
OTHER_KEY = "sk-" + "b" * 30


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"wave3_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def validate():
    return _load(VALIDATE)


@pytest.fixture(scope="module")
def setup():
    return _load(SETUP)


EARLY = datetime.date(2026, 1, 1)
LATE = datetime.date(2026, 10, 12)


@pytest.mark.parametrize("model", ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview"])
def test_accepted_model_passes_while_google_still_serves_it(validate, model):
    ok, detail = validate.validate_nanobanana_model(model, EARLY)
    assert ok is True
    assert "shuts down on 2026-06-25" in detail


@pytest.mark.parametrize("model", ["gemini-3.1-flash-image-preview", "gemini-3-pro-image-preview", ""])
def test_accepted_model_fails_after_the_shutdown_with_a_clear_reason(validate, model):
    ok, detail = validate.validate_nanobanana_model(model, LATE)
    assert ok is False
    assert "shut it down on 2026-06-25" in detail


def test_unset_model_reports_the_package_default(validate):
    ok, detail = validate.validate_nanobanana_model("", EARLY)
    assert ok is True and "pinned package default" in detail


def test_direct_ids_are_valid_google_models_the_package_cannot_use(validate):
    ok, detail = validate.validate_nanobanana_model("gemini-3.1-flash-image", LATE)
    assert ok is False
    assert "falls back" in detail and "direct API" in detail
    for name in ("gemini-nano-banana-2.1", "gemini-3.1-flash-lite-image", "gemini-3-pro-image"):
        assert name in validate.DIRECT_API_MODELS


@pytest.mark.parametrize("model", ["flash", "pro"])
def test_aliases_are_not_env_values(validate, model):
    ok, detail = validate.validate_nanobanana_model(model, EARLY)
    assert ok is False and "set_model" in detail


def test_stale_ids_name_current_replacements_and_drop_unverified_ones(validate):
    assert "gemini-2.0-flash-exp" not in validate.DEPRECATED_MODEL_REPLACEMENTS
    ok, detail = validate.validate_nanobanana_model("imagen-4.0-generate-001", LATE)
    assert not ok and "2026-08-17" in detail


def test_recommendation_points_at_direct_path_and_cites_google(validate):
    text = validate.RECOMMENDATION
    assert "scripts/generate_hero.py" in text
    assert "gemini-3.1-flash-image" in text
    assert "https://ai.google.dev/gemini-api/docs/models" in text
    assert "https://ai.google.dev/gemini-api/docs/deprecations" in text


def test_cli_json_reports_failure_and_recommendation(tmp_path):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"mcpServers": {"nanobanana-mcp": {
        "command": "npx",
        "args": ["-y", "@ycse/nanobanana-mcp@1.1.1"],
        "env": {"GOOGLE_AI_API_KEY": GOOGLE_KEY, "NANOBANANA_MODEL": "gemini-3.1-flash-image-preview"},
    }}}))
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run(
        [sys.executable, str(VALIDATE), "--json"], capture_output=True, text=True,
        env=env, cwd=tmp_path,
    )
    payload = json.loads(result.stdout)
    labels = {c["label"]: c for c in payload["checks"]}
    assert labels["NANOBANANA_MODEL is supported"]["passed"] is False
    assert "shut it down" in labels["NANOBANANA_MODEL is supported"]["detail"]
    assert "generate_hero.py" in payload["recommendation"]
    assert result.returncode == 1


# ---- setup_image_mcp key handling ----------------------------------------

def test_extract_single_key_forms(setup):
    assert setup.extract_api_key(GOOGLE_KEY + "\n") == GOOGLE_KEY
    assert setup.extract_api_key(f"GOOGLE_AI_API_KEY={GOOGLE_KEY}\n") == GOOGLE_KEY
    assert setup.extract_api_key(f"# my key\n\nexport GEMINI_API_KEY='{GOOGLE_KEY}'\n") == GOOGLE_KEY
    assert setup.extract_api_key("a" * 40) == "a" * 40


def test_extract_refuses_files_with_a_second_secret(setup):
    with pytest.raises(ValueError, match="more than one secret") as exc:
        setup.extract_api_key(f"GOOGLE_AI_API_KEY={GOOGLE_KEY}\nOPENAI_API_KEY={OTHER_KEY}\n")
    assert OTHER_KEY not in str(exc.value) and GOOGLE_KEY not in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)
    with pytest.raises(ValueError, match="more than one secret"):
        setup.extract_api_key(f"{GOOGLE_KEY}\nAIza{'B' * 35}\n")
    with pytest.raises(ValueError, match="more than one secret"):
        setup.extract_api_key(f"{GOOGLE_KEY}\nSLACK_TOKEN=xox" + "b-123456789012-abcdef\n")


def test_extract_refuses_when_no_key(setup):
    with pytest.raises(ValueError, match="No Google AI API key"):
        setup.extract_api_key("hello world\nFOO=bar\n")
    with pytest.raises(ValueError, match="empty"):
        setup.extract_api_key("\n# only a comment\n")


def _run_setup(tmp_path, key_text):
    key_file = tmp_path / "keys.env"
    key_file.write_text(key_text)
    env = dict(os.environ, HOME=str(tmp_path))
    env.pop("GOOGLE_AI_API_KEY", None)
    return subprocess.run(
        [sys.executable, str(SETUP), "--key-file", str(key_file), "--global", "--json"],
        capture_output=True, text=True, env=env, cwd=tmp_path,
    )


def test_multi_secret_key_file_never_reaches_settings(tmp_path):
    result = _run_setup(tmp_path, f"GOOGLE_AI_API_KEY={GOOGLE_KEY}\nOPENAI_API_KEY={OTHER_KEY}\n")
    assert result.returncode == 1
    assert "more than one secret" in result.stdout
    assert OTHER_KEY not in result.stdout + result.stderr
    assert not (tmp_path / ".claude.json").exists()
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_single_key_file_writes_only_the_key_with_mode_600(tmp_path):
    result = _run_setup(tmp_path, f"# comment\nGOOGLE_AI_API_KEY={GOOGLE_KEY}\n")
    assert result.returncode == 0, result.stdout
    # Fork: --global targets the user-scope servers in ~/.claude.json.
    settings = tmp_path / ".claude.json"
    config = json.loads(settings.read_text())
    assert config["mcpServers"]["nanobanana-mcp"]["env"]["GOOGLE_AI_API_KEY"] == GOOGLE_KEY
    assert "comment" not in settings.read_text()
    assert stat.S_IMODE(settings.stat().st_mode) == 0o600
    assert "generate_hero.py" in json.loads(result.stdout)["warning"]


def test_setup_rejects_a_multiline_key_passed_directly(setup, tmp_path, monkeypatch, capsys):
    target = tmp_path / ".claude.json"
    with pytest.raises(SystemExit):
        setup.setup_mcp(f"{GOOGLE_KEY}\nOPENAI_API_KEY={OTHER_KEY}", target, True, True)
    assert not target.exists()


# ---- setup_image_mcp target selection (fork, spec S3) -----------------------
# Two mutually exclusive targets: --project-dir <blog repo> writes that
# folder's .mcp.json, --global writes the user-scope servers in ~/.claude.json.
# An installed copy (inside the plugin cache) with neither refuses to write.

OPS = {
    "setup": ["--key-file", "KEYFILE"],
    "check": ["--check"],
    "remove": ["--remove"],
}


def _plugin_root(root: Path) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "claude-blog"}))
    scripts = root / "skills" / "blog-image" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / SETUP.name).write_bytes(SETUP.read_bytes())
    return scripts / SETUP.name


@pytest.fixture
def world(tmp_path):
    """A temp HOME with an installed plugin copy, user settings, and a blog repo."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(json.dumps({"theme": "dark"}))
    (home / ".claude.json").write_text(json.dumps({
        "numStartups": 3,
        "projects": {"/x": {"allowedTools": []}},
        "mcpServers": {"other-server": {"command": "other"}},
    }))
    installed = _plugin_root(home / ".claude" / "plugins" / "cache" / "blog-kit" / "claude-blog" / "2.3.1-kit.1")
    blog = tmp_path / "blog"
    blog.mkdir()
    key_file = tmp_path / "key.txt"
    key_file.write_text(GOOGLE_KEY + "\n")
    return {"tmp": tmp_path, "home": home, "installed": installed, "blog": blog, "key": key_file}


def _snapshot(*roots: Path) -> dict[str, bytes]:
    return {
        str(p): p.read_bytes()
        for root in roots for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix != ".py"
    }


def _run_target(world, script: Path, op: str, *target: str, cwd: Path | None = None):
    args = [a.replace("KEYFILE", str(world["key"])) for a in OPS[op]]
    env = dict(os.environ, HOME=str(world["home"]))
    for name in ("GOOGLE_AI_API_KEY", "CLAUDE_CONFIG_DIR"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, str(script), *args, *target, "--json"],
        capture_output=True, text=True, env=env, cwd=cwd or world["blog"],
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    return result, payload


@pytest.mark.parametrize("op", sorted(OPS))
def test_installed_copy_without_a_target_refuses_and_writes_nothing(world, op):
    before = _snapshot(world["tmp"])
    result, payload = _run_target(world, world["installed"], op)
    assert result.returncode != 0
    assert payload.get("status") == "error"
    assert "--project-dir" in payload["error"] and "--global" in payload["error"]
    assert _snapshot(world["tmp"]) == before


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}


def _lifecycle(world, script: Path, target_args: tuple[str, ...], target: Path, cwd=None):
    """Run setup, check and remove against one target; only it may change."""
    before = _snapshot(world["tmp"])
    result, payload = _run_target(world, script, "setup", *target_args, cwd=cwd)
    assert result.returncode == 0, result.stdout
    assert payload["config"] == str(target)
    assert _changed(before, _snapshot(world["tmp"])) == {str(target)}
    assert "nanobanana-mcp" in json.loads(target.read_text())["mcpServers"]

    _, payload = _run_target(world, script, "check", *target_args, cwd=cwd)
    assert payload["configured"] is True and payload["path"] == str(target)

    result, payload = _run_target(world, script, "remove", *target_args, cwd=cwd)
    assert result.returncode == 0 and payload["removed"] is True
    assert payload["path"] == str(target)
    assert "nanobanana-mcp" not in json.loads(target.read_text())["mcpServers"]
    changed = _changed(before, _snapshot(world["tmp"]))
    assert changed <= {str(target)}


def test_project_dir_targets_only_that_folders_mcp_json(world):
    target = world["blog"] / ".mcp.json"
    _lifecycle(world, world["installed"], ("--project-dir", str(world["blog"])), target)


def test_project_dir_never_writes_a_literal_key(world):
    _run_target(world, world["installed"], "setup", "--project-dir", str(world["blog"]))
    text = (world["blog"] / ".mcp.json").read_text()
    assert GOOGLE_KEY not in text and "${GOOGLE_AI_API_KEY}" in text


def test_global_targets_user_scope_servers_and_preserves_the_rest(world):
    target = world["home"] / ".claude.json"
    settings_before = (world["home"] / ".claude" / "settings.json").read_bytes()
    _lifecycle(world, world["installed"], ("--global",), target)
    config = json.loads(target.read_text())
    assert config["numStartups"] == 3
    assert config["projects"] == {"/x": {"allowedTools": []}}
    assert config["mcpServers"] == {"other-server": {"command": "other"}}
    assert (world["home"] / ".claude" / "settings.json").read_bytes() == settings_before
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("op", sorted(OPS))
def test_both_targets_is_an_error_and_writes_nothing(world, op):
    before = _snapshot(world["tmp"])
    result, payload = _run_target(
        world, world["installed"], op, "--project-dir", str(world["blog"]), "--global"
    )
    assert result.returncode != 0 and payload["status"] == "error"
    assert _snapshot(world["tmp"]) == before


@pytest.mark.parametrize("op", sorted(OPS))
def test_checkout_without_a_findable_root_refuses_instead_of_going_global(world, op):
    loose = world["tmp"] / "loose"
    loose.mkdir()
    script = loose / SETUP.name
    script.write_bytes(SETUP.read_bytes())
    before = _snapshot(world["tmp"])
    result, payload = _run_target(world, script, op, cwd=loose)
    assert result.returncode != 0 and payload["status"] == "error"
    assert "--project-dir" in payload["error"]
    assert _snapshot(world["tmp"]) == before


def test_checkout_without_a_target_uses_its_own_root(world):
    checkout = world["tmp"] / "checkout"
    script = _plugin_root(checkout)
    _lifecycle(world, script, (), checkout / ".mcp.json", cwd=checkout)


@pytest.mark.parametrize("op", sorted(OPS))
def test_upstream_project_flag_follows_the_no_target_rule(world, op):
    """--project (upstream docs) means "this checkout's own .mcp.json"."""
    before = _snapshot(world["tmp"])
    result, payload = _run_target(world, world["installed"], op, "--project")
    assert result.returncode != 0 and payload["status"] == "error"
    assert _snapshot(world["tmp"]) == before
    result, payload = _run_target(world, world["installed"], op, "--project", "--global")
    assert result.returncode != 0 and payload["status"] == "error"
