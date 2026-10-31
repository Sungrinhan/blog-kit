---
name: blog-doctor
description: Check that the blog-kit engine actually running in this blog repo is the release the repo pins, and its runtime prerequisites, by running the installed doctor script and showing its result. Use for "blog doctor", "install check", "which engine version is running", or after installing, upgrading, or downgrading blog-kit. Read-only; it never edits settings. Not for content quality checks (blog-analyze) or delivery gates.
argument-hint: "[blog repo path]"
user-invocable: true
license: MIT
metadata:
  author: Sungrinhan
  version: "2.3.1-kit.4"
---

# Blog Doctor

Bundled paths below use host Markdown substitution of `${CLAUDE_SKILL_DIR}`;
it is not an exported shell variable. Resolve it before execution and quote
paths. Run the copy that ships with this installed skill: do not substitute a
development checkout or an override directory, because the doctor checks that
it runs from inside the installed plugin.

## Run

The blog repo is the argument when given, otherwise the project root of the
current session (the folder holding `.claude/settings.json`), not the plugin
folder.

```bash
BLOG_KIT_DOCTOR="${CLAUDE_SKILL_DIR}/../../scripts/doctor.py"
python3 "$BLOG_KIT_DOCTOR" --blog-repo "<blog repo>"
```

Do not substitute `CLAUDE_BLOG_SCRIPTS_DIR` here, unlike other skills: the
doctor must run from the installed copy, which check `f_exec_path` verifies.

It prints one JSON object and exits 0 only when every check is `ok`. A check
that is `unknown` could not be verified and counts as a failure.

## Report

Show a short table of every check: id, status, and reason, plus the version,
SHA, scope, and paths fields it returned. Then give the overall result. For
each check that is not `ok`, say what the reason points at:

| Check | Usually means |
|---|---|
| `a_shared_settings` | `.claude/settings.json` does not enable `claude-blog@blog-kit` from marketplace `Sungrinhan/blog-kit` pinned to a `v<version>` tag |
| `b_cli_install` | no single enabled project-scope install here, or `claude-blog` from another marketplace is also active |
| `c_registry` | the installed-plugins registry does not match the CLI install or this blog repo |
| `d_version` | the installed version is not the pinned tag; update or reinstall, then restart Claude Code |
| `e_commit` | the installed commit is not the commit the tag points to, or the tag could not be read |
| `f_exec_path` | the doctor did not run from the installed plugin |
| `g_python` | Python is older than 3.11 |
| `h_runtime_dir` | `CLAUDE_BLOG_RUNTIME_DIR` is missing from `.claude/settings.local.json` or this session, the two differ, it is not an absolute resolved path, or it points inside the Claude plugin folder; restart Claude Code after editing settings |
| `i_verifier_copy` | `<blog repo>/scripts/blogkit_verify.py` is missing, a symlink, or not byte-identical to the installed verifier, or names another version; run `python3 "<installPath>/adapters/astro/blogkit.py" sync-verifier --blog-repo "<blog repo>"` and commit the copy |

## Boundaries

- Read-only. Do not edit settings, reinstall, or set environment variables to
  make a check pass; tell the user what to change.
- Report only the fields the doctor prints. Do not print environment
  variables, settings files, or registry contents alongside it.
