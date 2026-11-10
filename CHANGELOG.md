# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-10

### Added

- TUI transcript blocks for Markdown replies, numbered turns, collapsible tool
  output, and expanded per-turn/session statistics.
- Tool-result truncation metadata and prompt-composition/usage-call details.
- Main-branch pushes and pull requests run the full unit-test matrix and
  repository-wide pre-commit checks.
- Tag builds prepare Python wheel/source distributions and platform binaries
  with SHA-256 checksums in a draft GitHub Release for final review.

### Changed

- Package, CLI, SDK, and bundled App runtime requirements now use the stable
  `1.0.0` version.
- Both terminal startup logos use AgenticX and share the same definition.
- Normal text selection and successful copying no longer show right-side
  notifications; selection actions remain available and errors still warn.
- English and Chinese READMEs now introduce the 1.0 Runtime with package
  installation and a source-development quick start. They share a local
  DashScope AgenticX SVG logo and no longer display branch-development notices.
  Detailed reference material lives in root-level `user-guide.md`
  and `user-guide_zh.md`.
- Public guides and migration documentation live at the repository root.
  Source distributions exclude the internal `md/` directory.
- Example downloads default to the public `dashscope/dashscope-agenticx-experts`
  repository on branch `main`; explicit repository overrides remain supported.
- App Catalog downloads read only `apps/<name>/`, with no fallback to legacy
  top-level example directories or an interactive repository setup prompt.

### Fixed

- Runtime-local MCP/session/permission/extension state, child authorization
  caches, and deterministic cleanup on exceptions and early stream closure.
- OFFICIAL bundle path validation, manifest permission types, and permission
  confirmation before installation writes. Expert templates reject implicit
  executable code even when overwrite is requested.
- Default confirmation now covers write tools; explicit deny policies cannot
  be bypassed through AUTO or automatic approval. Parallel delegates default
  to read-only tools and report usage without importing authorization grants.
- Structured reasoning, plan, compression, and provider fallback events now
  complement message/tool/permission events with sanitized payloads.
- Local Server execution refuses automatic local grants and rejects invalid
  HTTP body lengths; IPv6 loopback and error-response redaction are covered.

- Run hooks substitute model-controlled values as native process arguments,
  not shell syntax. Rules editor paths are passed as separate arguments.
- Custom Shell tool parameters also use literal native process arguments,
  including quoted placeholders. Parameterized shell pipelines, shell
  interpreters, and dynamic executable/code/module names are rejected.
- ASR diagnostic logs use private, randomly named temporary files instead
  of a shared predictable log path.
- Release security jobs install and audit the built Runtime, including its
  optional dependencies, and retain audit reports alongside environment SBOMs.
- Python 3.10 audit environments upgrade their bootstrap setuptools instead
  of retaining the runner's vulnerable version. Linux unit-test runners
  install PortAudio so voice degradation tests exercise their intended paths.
- Audio backend initialization failures now degrade gracefully. Offline voice
  tests mock sounddevice rather than requiring a running audio server or
  physical device, and release-document checks use the actual release date.
- Dependency requirements exclude the security scan's affected Runtime
  versions. Pillow-backed vision/media require Python 3.11+; Python 3.10
  retains base Runtime support and the `all` extra omits Pillow on that version.
- TUI input history now redacts credential assignments on save and migration.
  Structured event/session payloads redact credential fields even when values
  do not match a known API-key pattern.
- Credential assignments are redacted regardless of length, and complete or
  truncated multiline private-key blocks are removed from text payloads.
- Example download and restore reject path traversal, symbolic links, and
  attempts to overwrite their managed backup directory.
- PyCharm rolling selections preserve content offsets and flush pending wheel
  batches before mouse release.
- macOS/PyCharm native Cmd+C copies the complete selection even when the IDE
  intercepts the key, the gesture crosses an empty container, or a visible
  overlay changes the copied screen text. Clipboard restoration is limited to
  matching active selections; unrelated changes stop recovery.
- macOS Ctrl+C no longer copies selections; Windows/Linux retain Ctrl+C copy.
- Redundant tool-argument wrappers are normalized only against a compatible
  declared schema. Invalid, ambiguous, and truncated calls are not guessed.

## [1.0.0rc3] - 2026-11-02

Release candidate for the DashScope AgenticX identity and one-way legacy
migration. The final `1.0.0` release has not been published.

### Changed

- The product is AgenticX / DashScope AgenticX. The distribution and
  protocol/package identity are `dashscope-agenticx`, the Python namespace is
  `dashscope_agenticx`, and the only official CLI command is `agenticx` (there
  is no official short alias).
- Workspaces use `.agenticx/`, environment variables use `AGENTICX_*`, and the
  platform-standard global configuration uses the app name `AgenticX` with
  `AGENTICX_HOME` override support.
- Legacy directory migration is one-way and prefers `.agenticcli/` over
  `.acli/`. It uses an atomic rename when possible, or a verified copy that
  preserves the source for manual cleanup. An existing destination is left
  untouched and directories are never merged or dual-written.
- Credential writes use the `dashscope-agenticx` keyring service; reads retain
  fallback support for the legacy `agenticcli` service and `ENC:` values.

## [1.0.0rc2] - 2026-11-02

Release candidate for the unified `agenticcli` namespace, command, workspace,
configuration, App profile, and open-source repository layout.

### Added

- Optional short command `agcli` and one-time atomic migration from legacy
  workspace/global directories.
- Platform-standard global configuration through `platformdirs`, with
  `AGENTICCLI_HOME` override support.
- Release hardening report with coverage, performance, wheel size, App eval,
  Client trial, benchmark baseline, and frozen-contract evidence.

### Changed

- The complete implementation now lives under `src/agenticcli`; the old Python
  package and console command have been removed before public release.
- Workspace and App profiles use `.agenticcli/`; environment variables use the
  `AGENTICCLI_` prefix; internal manifests, headers, benchmark adapters, build
  scripts, and vendored metadata use the same product name.
- Core coverage floor is 80%; Engine, Security, and Session/Memory aggregate
  coverage floors are each 90% and enforced in CI.
- Final `1.0.0` is intentionally deferred until the namespace migration and
  subsequent public RC optimization cycle complete.

## [1.0.0rc1] - 2026-11-02

Release candidate freezing distribution, namespace, SDK, typing, packaging,
cross-platform CI, vendoring, and supply-chain metadata contracts.

### Added

- Canonical `agenticcli` distribution/command/namespace plus a `py.typed`
  marker.
- Security, contributing, release, and 1.0 migration documentation.
- Python 3.10-3.13 and Ubuntu/macOS/Windows CI smoke matrix, App Catalog CI,
  SBOM generation, dependency audit, package validation, and vendoring script.

### Changed

- Package, CLI, SDK, wheel, and vendored metadata now read version
  `1.0.0rc1` from `agenticcli._version` as the single source of truth.

## [1.0.0b1] - 2026-11-02

First beta of the stable Runtime, Event, App, Session, and localhost Client
contracts.

### Added

- Token-authenticated localhost Server APIs for runs, sessions, artifacts, and
  traces. Remote permission decisions are rejected; only the local Runtime can
  approve tools.
- Explicit slash-command registry used by new command families instead of
  extending the monolithic dispatch chain.
- Versioned App Bundle store and `acli app list/info/install/update/verify`
  commands with Runtime compatibility checks, permission diffs, and tamper
  verification.
- Offline bundled manifests, profiles, eval cases, and acceptance metadata for
  DashScope SDK Expert and Bailian Expert.
- Versioned `AppManifest`, publisher/checksum identity, explicit App
  permissions, trust tiers, and install/update permission diffs.
- Deterministic App directory checksums and symlink rejection for local and
  archive installs.
- Structured permission request/resolution, sandbox status, and artifact
  events in the canonical Agent event stream.
- OS-keyring-backed credential references with automatic read/migrate support
  for legacy `ENC:` values; new saves no longer persist reversible API-key
  ciphertext in TOML.
- Public `AgentEventEnvelope` name and schema version, with compatibility for
  the alpha `AgentEvent` name and reserved permission/artifact event types.
- Instance-owned `ModelInvoker`, `ToolCoordinator`, `RunGuards`, and
  `SessionPersistence` engine services.

### Changed

- Core dependencies no longer install Textual, OpenAI, Anthropic, DDGS, or
  Playwright; TUI, provider SDKs, web search, browser, media, scheduler, MCP
  server, and experimental learning are explicit extras/import boundaries.
- Camera and browser tools register only when their optional dependencies are
  present. Skill evolution and directive auto-learning are off by default and
  require explicit experimental environment gates.
- The Example command is explicitly a teaching workflow; production profiles
  use versioned Apps. The examples repository now exposes an `apps/` catalog
  schema for Basic Chat, DashScope SDK Expert, and Bailian Expert.
- Session events now project messages, plan, goal, artifacts, and unified
  runtime/agent/session/run/turn identifiers; forks replay the same state.
- Reflection and stagnation execution ownership moved to `RunGuards`, tool
  chains are treated as operational runbooks, and child-memory workspaces are
  removed when delegated runs finish.
- Persistent experiences now require verified outcomes for recall and expose
  bounded retention, TTL, hit/miss statistics, and deletion APIs. The legacy
  global conversation-summary double write is no longer automatic.
- Skill `tools.py` now requires externally verified trust plus the explicit
  `permissions.python_tools` grant; shell-running hooks likewise require
  verified trust and `permissions.shell_hooks`.
- Extracted provider retry/stream assembly, guard hint generation, tool batch
  coordination, and completed-turn persistence from the monolithic Agent loop.

## [1.0.0a2] - 2026-11-02

Second runtime-foundation alpha. This release completes entrypoint convergence
and introduces the first typed event contract used by the text frontends.

### Added

- Typed `AgentEvent` envelopes and `Agent.run_events()` / `acli.sdk.run_events()`
  for CLI, TUI, IDE, automation, and future Bailian Expert Client frontends.
- Initial structured events for run start, message deltas, tool lifecycle, usage,
  turn completion, and errors with runtime/agent/session/run/turn identifiers.

### Changed

- CLI, TUI, and oneshot entrypoints now construct Agents through
  `RuntimeBuilder` instead of duplicating provider, memory, hook, skill, vision,
  session, and delegation wiring.
- Subagent and delegate parent/config resolution now uses task-local
  `ContextVar` bindings; legacy process globals remain only as compatibility
  fallbacks.
- Capability-to-tool registration bookkeeping is runtime-local, preventing
  enable/disable operations in one Agent runtime from changing another.
- CLI and Textual TUI response rendering now consumes the typed event stream,
  with a compatibility adapter for legacy/fake Agents that only implement
  `run_stream()`.

### Validation

- 2,920 tests passed and 2 skipped.
- mypy, Black, flake8, and pylint gates passed.

## [1.0.0a1] - 2026-10-30

First alpha of the AgenticCLI 1.0 runtime-foundation work. This release
starts the breaking migration from a process-global CLI implementation to an
instance-owned Python Agent Runtime / Harness.

### Added

- `RuntimeContext`, `Runtime`, and `RuntimeBuilder` as the construction seam for
  SDK, embedded, CLI, and future Client frontends.
- Runtime-local `ToolRegistry` clones with `ContextVar` binding, allowing
  concurrent tasks to resolve dynamic tool registrations without overwriting
  another runtime's registry.
- Agent injection points for runtime-owned tool registries, skill managers, and
  session managers.
- Runtime-isolation regression tests covering cloned registries, async task
  context separation, and Agent schema visibility.

### Changed

- The programmatic SDK now constructs Agents through `RuntimeBuilder`.
- Subagents and delegates inherit the parent Agent's runtime-owned tool, skill,
  and session state.
- The legacy global `registry` remains as a compatibility facade while the 1.0
  migration moves remaining entrypoints to explicit runtime context.

### Validation

- 2,915 tests passed and 2 skipped.
- mypy, Black, flake8, and pylint gates passed.

---

Pre-1.0 development history is internal and is not included in this file.
