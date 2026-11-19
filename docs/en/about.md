---
description: "About DashScope AgenticX: identity, requirements, scope, and project boundaries."
---
# About AgenticX

**DashScope AgenticX** is a Python Agent Runtime for building and running
domain experts. It connects models, tools, Skills, permissions, and
persistent sessions through one runtime, with a terminal interface and APIs
for embedded applications.

Current version: **{{ agenticx_version }}**.

## Identity

| Aspect | Value |
|---|---|
| Product | AgenticX / DashScope AgenticX |
| Distribution | [`dashscope-agenticx`](https://pypi.org/project/dashscope-agenticx/) |
| Import namespace | `dashscope_agenticx` |
| Command | `agenticx` (no official short alias) |
| Workspace | `.agenticx/` |
| Environment | `AGENTICX_*` |
| Global app name | `AgenticX` (platform-standard directory via `platformdirs`) |
| License | MIT |
| Source | [github.com/dashscope/dashscope-agenticx](https://github.com/dashscope/dashscope-agenticx) |

The bare `agenticx` PyPI distribution is a **different, unrelated project**.
Install `dashscope-agenticx`.

## Requirements

- **Python 3.10+** for the core Runtime, CLI, and SDK.
- The Pillow-backed `vision` and `media` extras require **Python 3.11+**;
  the `all` extra omits Pillow on Python 3.10.
- A model provider API key for cloud providers (e.g. `DASHSCOPE_API_KEY`
  for the default Tongyi/DashScope provider), or a local provider such as
  `ollama` for fully offline use.
- Optional capabilities (TUI, provider SDKs, web, browser, voice, camera)
  are installed as extras and are not prerequisites for the core Runtime.

CI tests Python 3.10–3.13 with Ubuntu/macOS/Windows smoke checks. Those
checks are not a guarantee that every optional device or sandbox backend
works on every OS.

## What AgenticX is

- A local assistant you run directly in a terminal.
- An embeddable Python runtime (`run_once`, `run_events`, `create_agent`,
  `RuntimeBuilder`) for application integration.
- A packaging layer for domain experts as versioned App Bundles, with
  DashScope SDK Expert and Bailian Expert as bundled templates.

## What AgenticX is not

- **Not a hosted service.** It runs in your process; the localhost Server
  is a token-authenticated local API, not an Internet-facing deployment.
- **Not a hardened OS sandbox.** OS sandbox support (Seatbelt/bubblewrap)
  is defense in depth; availability and limitations depend on the backend.
- **Not a complete workflow platform.** Voice, camera, browser, web search,
  scheduler, and experimental features are optional.

A checksum proves content identity, not publisher authenticity; a local
trust flag is not a verified digital signature. Read the
[security guide](guides/security-and-platform.md) and the repository
`SECURITY.md` before installing third-party applications.

## Dependencies

The core install stays small. Optional packs add provider SDKs, the Textual
TUI, web/browser tooling, and media (voice/camera/vision) dependencies.
Installing dependencies does not grant an App permissions. See
[Quickstart](quickstart.md#optional-extras) for the extras table.

## License

MIT, as declared in `pyproject.toml`.