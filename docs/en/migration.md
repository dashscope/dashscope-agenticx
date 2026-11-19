---
description: "Migrate from legacy AgenticCLI to DashScope AgenticX 1.0: naming, directories, credentials, and compatibility."
---
# Migrating to AgenticX 1.0

This guide describes migration from legacy AgenticCLI versions to
DashScope AgenticX `{{ agenticx_version }}`.

## Current identities

| Aspect | AgenticX 1.0 |
|---|---|
| Product | AgenticX / DashScope AgenticX |
| Distribution | `pip install dashscope-agenticx` (extras as needed, e.g. `"dashscope-agenticx[all]"`) |
| Import | `dashscope_agenticx`, `dashscope_agenticx.sdk` |
| Command | `agenticx` — there is no official short alias |
| Apps | `agenticx app install`; `/expert` manages role templates |
| Workspace | `.agenticx/` |
| Environment | `AGENTICX_*` variables |
| Global config | Platform-standard directory for app name `AgenticX` (via `platformdirs`); `AGENTICX_HOME` overrides |
| Events | Consume `AgentEventEnvelope` via `run_events()` |

!!! warning "Do not install the bare `agenticx` PyPI package"
    The unrelated bare `agenticx` distribution is a different project.
    The official distribution is `dashscope-agenticx`.

Rename legacy `AGENTICCLI_*` and `ACLI_*` environment variables to their
`AGENTICX_*` equivalents. Optional packs (TUI, provider SDKs, web, browser,
media) are installed explicitly — see [Quickstart](quickstart.md#optional-extras).

Expert downloads default to
`https://github.com/dashscope/dashscope-agenticx-experts.git` on branch `main`.
An explicit `expert_repo` setting or `--repo` flag selects another source.
The catalog uses only `apps/<name>/`; legacy top-level Expert directories are
not supported.

## One-way directory migration

Workspace migration prefers `.agenticcli/` and otherwise falls back to
`.acli/`. Global migration prefers the legacy platform-standard `AgenticCLI`
directory and otherwise falls back to `~/.acli/`. The selected source is
migrated once to `.agenticx/` (workspace) or the platform-standard `AgenticX`
directory (global).

- An atomic rename is used when source and destination support it.
- Otherwise a verified copy is made. The source is preserved after copying;
  remove it manually once you have verified the result.
- If the destination already exists, both directories are left untouched:
  no merge, no overwrite, no migration into it.
- Writes go only to the new location. There is no dual write to legacy
  directories; migration does not run repeatedly or copy changes back.

## Credentials

New credentials are written as OS-keyring references under the
`dashscope-agenticx` service. Reads retain fallback to the legacy `agenticcli`
keyring service so existing references continue to resolve; legacy `ENC:`
values are also read for migration.

New saves do not dual-write secrets to legacy services and do not persist
reversible API-key ciphertext in TOML. Do not treat legacy `ENC:` values as
secure encryption — re-save keys to move them into the OS keyring.

## What does not migrate automatically

- Import statements (`agenticcli` → `dashscope_agenticx`) and environment
  variable names need an explicit update in your code and shell profiles.
- Existing destination directories are never merged; resolve conflicts
  manually.
- Expert/App installation contracts changed: role templates live under
  `apps/<name>/` in the catalog; versioned App Bundles are managed with
  `agenticx app …`, not `/expert`.

See [Security & Compatibility](guides/security-and-platform.md) for the
trust and permission model that governs migrated configuration.