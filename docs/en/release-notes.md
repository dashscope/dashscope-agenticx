---
description: "DashScope AgenticX release notes for 1.0.2, 1.0.1, and 1.0.0."
---
# Release Notes

AgenticX follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The full history lives in the repository
[`CHANGELOG.md`](https://github.com/dashscope/dashscope-agenticx/blob/main/CHANGELOG.md);
this page summarizes the stable 1.0 releases.

## 1.0.2 — 2026-11-19

### Added

- Bilingual (English / 中文) documentation site: quickstart, configuration,
  CLI and slash-command reference, migration, guides (embedding the SDK,
  tools & skills, subagents & memory, security & platform), developer guide,
  and an API reference generated from source.
- GitHub Actions workflow building the site with `mkdocs build --strict` on
  pull requests and deploying to GitHub Pages on pushes to `main`.
- README links to the published documentation site.

### Changed

- The doc-code consistency guard accepts the remote DashScope logo URL used
  by the READMEs.

### Fixed

- Browser tool docstring argument indentation so the API reference parses
  cleanly under `mkdocs build --strict`.

## 1.0.1 — 2026-11-11

### Added

- End-to-end App Catalog installation and verification quick starts,
  including the separate Expert activation step and one-shot `agenticx -c`
  usage.
- DashScope and MaaS request attribution through `x-dashscope-sdk-client`
  and `x-dashscope-sdk-session-id` across Native, OpenAI-compatible,
  Anthropic-compatible, MCP, ASR, and TTS requests. Third-party endpoints do
  not receive these headers.

### Changed

- Expert management consistently uses `agenticx expert`, `/expert`,
  `expert_repo`, and `expert_branch`; legacy Example command and
  configuration names were removed.
- English and Chinese guides cover the current CLI, interaction, Skill,
  Session, and Server commands and clarify App installation versus Expert
  activation.

## 1.0.0 — 2026-11-10

The first stable release of the DashScope AgenticX Runtime.

### Added

- TUI transcript blocks: Markdown replies, numbered turns, collapsible tool
  output, and expanded per-turn/session statistics.
- Tool-result truncation metadata and prompt-composition/usage-call details.
- Full unit-test matrix and repository-wide pre-commit checks on main-branch
  pushes and pull requests.
- Tag builds prepare wheel/source distributions and platform binaries with
  SHA-256 checksums in a draft GitHub Release for final review.

### Changed

- Package, CLI, SDK, and bundled App runtime requirements use the stable
  `1.0.0` version.
- Expert downloads default to the public
  `dashscope/dashscope-agenticx-experts` repository on branch `main`;
  explicit repository overrides remain supported. App Catalog downloads read
  only `apps/<name>/`, with no fallback to legacy top-level Expert
  directories.
- Public guides and migration documentation live at the repository root;
  source distributions exclude the internal `md/` directory.

### Fixed (security & runtime highlights)

- Runtime-local MCP/session/permission/extension state, child authorization
  caches, and deterministic cleanup on exceptions and early stream closure.
- Default confirmation covers write tools; explicit deny policies cannot be
  bypassed through AUTO or automatic approval. Parallel delegates default to
  read-only tools and do not import authorization grants.
- App bundle path validation, manifest permission types, and permission
  confirmation before installation writes. Expert templates reject implicit
  executable code even when overwrite is requested.
- Local Server execution refuses automatic local grants and rejects invalid
  HTTP body lengths; IPv6 loopback and error-response redaction are covered.
- Run hooks and custom shell tools substitute model-controlled values as
  native process arguments, not shell syntax; parameterized pipelines, shell
  interpreters, and dynamic executable/code/module names are rejected.
- Credential assignments and private-key blocks are redacted from text,
  event, and session payloads regardless of length or known key patterns;
  TUI input history redacts credentials on save and migration.
- Expert download/restore reject path traversal, symbolic links, and
  overwrite of the managed backup directory.
- Structured reasoning, plan, compression, and provider-fallback events
  complement message/tool/permission events with sanitized payloads.
- Pillow-backed vision/media require Python 3.11+; Python 3.10 retains base
  Runtime support and the `all` extra omits Pillow on that version.

## Upgrading

See [Migrating to 1.0](migration.md) for legacy naming, one-way directory
migration, and credential handling.