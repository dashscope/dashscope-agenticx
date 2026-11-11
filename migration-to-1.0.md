# Migrating to DashScope AgenticX 1.0.1

This guide describes migration from legacy AgenticCLI versions to
DashScope AgenticX `1.0.1`.

## Current identities

- Product: AgenticX / DashScope AgenticX.
- Distribution and protocol/package identity: `dashscope-agenticx`.
- Installation: `pip install dashscope-agenticx`; add extras as appropriate,
  for example `pip install "dashscope-agenticx[all]"`.
- Imports: use `dashscope_agenticx` and `dashscope_agenticx.sdk`.
- Command: use `agenticx`; there is no official short alias. Do not install the
  unrelated bare `agenticx` PyPI distribution.
- Apps: use `agenticx app install`; `/expert` manages role templates.
- Workspace: use `.agenticx/`.
- Environment: use `AGENTICX_*` variables; rename legacy `AGENTICCLI_*` and
  `ACLI_*` variables. `AGENTICX_HOME` overrides the global config location.
- Global configuration: use the platform-standard directory for app name
  `AgenticX` (provided by `platformdirs`).
- Events: Clients should consume `AgentEventEnvelope` via `run_events()`.
- Optional packs: install TUI/provider/web/browser/media extras explicitly.

Expert downloads default to
`https://github.com/dashscope/dashscope-agenticx-experts.git` on branch `main`.
An explicit `expert_repo` setting or `--repo` flag can select another source.
The catalog uses only `apps/<name>/`; legacy top-level Expert directories
are not supported.

## One-way directory migration

Workspace migration prefers `.agenticcli/` and otherwise falls back to
`.acli/`. Global migration prefers the legacy platform-standard `AgenticCLI`
directory and otherwise falls back to `~/.acli/`. The selected source is
migrated once to `.agenticx/` for a workspace or the platform-standard
`AgenticX` global directory.

- Use an atomic rename when the source and destination support it.
- If an atomic rename is not possible, use a verified copy. Preserve the source
  after copying and leave its removal to manual cleanup after verification.
- If the destination already exists, leave both directories untouched: do not
  merge, overwrite, or migrate into it.
- Writes go only to the new location; there is no dual write to legacy
  directories. Migration does not run repeatedly or copy changes back.

## Credentials

New credentials are written as OS-keyring references under the
`dashscope-agenticx` service. Reads retain fallback to the legacy `agenticcli`
keyring service so existing references continue to resolve; legacy `ENC:`
values are also read for migration. New saves do not dual-write secrets
to legacy services or persist reversible API-key ciphertext in TOML.
