# DashScope AgenticX

![DashScope AgenticX](logo.svg)

[English](README.md) | [中文](README_zh.md)

**Enterprise AI experts, built to act.**

AgenticX 1.0 is a Python Agent Runtime for building and running domain experts.
It connects models, tools, Skills, permissions, and persistent sessions through
one runtime, with a terminal interface and APIs for embedded applications.

Use it directly as a local assistant, embed it in a Python application, or
package a domain expert as a versioned App Bundle. DashScope SDK Expert and
Bailian Expert are bundled Expert templates illustrating this application layer.

## What AgenticX Provides

| Layer | Capability |
|---|---|
| Runtime | Instance-owned tool, Skill, hook, and session state; shared construction through `RuntimeBuilder` |
| Agent execution | Model invocation, tool coordination, run guards, and session persistence |
| Events | Typed `AgentEventEnvelope` events for messages, tools, permissions, usage, and artifacts |
| Applications | Versioned App manifests, Runtime compatibility, permission diffs, and checksum verification |
| Interfaces | CLI, Textual TUI, Python SDK, embedded entry points, and a token-authenticated localhost Server |
| Model access | Tongyi/DashScope, plus optional provider SDKs and declarative provider configuration |
| Extension points | System prompts, Skills, HTTP/Shell tools, MCP services, and governed hooks |

Voice, camera, browser, web search, scheduler, and experimental features are
optional. They are not prerequisites for the core Runtime. AgenticX is not a
hosted service, a hardened OS sandbox, or a complete workflow platform.

## Installation

Python **3.10+** is required. Pillow-backed `vision` and `media` extras require
**Python 3.11+**; `all` omits Pillow on Python 3.10.
CI includes Python 3.10–3.13 tests and
Ubuntu/macOS/Windows smoke checks; those checks are not a guarantee that every
optional device or sandbox backend works on every OS.

### Install the Python Package

Package installation requires a published distribution. For a source-only
release or an unpublished checkout, use **Install from Source** below.

```bash
python -m pip install "dashscope-agenticx[tui]"
agenticx --version
```

For a minimal, non-TUI installation, use
`python -m pip install dashscope-agenticx` and start with `agenticx --cli`.

### Install from Source

For development, run these commands in your repository checkout:

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[tui]"
agenticx --version
```

The Python distribution is **`dashscope-agenticx`**, the import namespace is
**`dashscope_agenticx`**, and the only official command is **`agenticx`**.
The bare `agenticx` PyPI distribution is a different project. See the
[release process](RELEASE.md) for binary builds and release requirements.

### Optional Dependencies

Choose only what you need; for a source checkout, use the equivalent extras
with `python -m pip install -e ".[<extras>]"`.

| Extra | Install | Purpose |
|---|---|---|
| TUI | `python -m pip install "dashscope-agenticx[tui]"` | Fixed input panel and rich terminal output |
| Provider SDKs | `python -m pip install "dashscope-agenticx[provider-openai,provider-anthropic]"` | Provider-specific SDK routes |
| Web / browser | `python -m pip install "dashscope-agenticx[web,browser]"` | Search and browser tools; browser runtime setup is separate |
| Media | `python -m pip install "dashscope-agenticx[voice,camera,vision]"` | Audio, camera, and image dependencies |
| Development | `python -m pip install -e ".[dev,all]"` | Test/build tooling and optional feature dependencies |

See the [user guide](user-guide.md) for backend setup and capability
configuration. Installing dependencies does not grant an App permissions.

## Quick Start

For the default Tongyi/DashScope provider, supply an API key through your
process environment:

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
# Windows PowerShell: $env:DASHSCOPE_API_KEY="<your-api-key>"
agenticx
```

Use `/setup` to configure your workspace, or `/provider` to select a model.
Do not paste real API keys into tracked configuration or example files.

```bash
agenticx --cli                      # Inline REPL with terminal scrollback
agenticx --tui                      # Textual interface (requires the TUI extra)
agenticx -c "List the files in this directory and summarize the project"
```

Inside an interactive session:

```text
agenticx> Summarize this project's structure before making any edits
agenticx> /help
agenticx> /copy
agenticx> /session
```

One-shot runs can execute tools, not just generate text. Start in a suitable
workspace and choose permissions appropriate to the task.

### Terminal Output

- Responses render as Markdown, with numbered turns and completion status.
- Tool details collapse by default; failures remain expanded. Click the
  summary or use `Ctrl+O` to toggle retained details.
- `Ctrl+G` expands token, API, tool, and prompt-composition statistics.
  Prompt sizes are characters, not exact model-token counts; cached tokens
  are included in input usage, not an additional chargeable token total.
- Selection copying uses `Cmd+C` on macOS and `Ctrl+C` on Windows/Linux.
  The bottom **Copy** button copies the captured selection without relying on
  terminal key forwarding. Selecting and successful copying show no toast.
- On macOS, `Ctrl+C` clears/cancels/quits; it does not copy a selection.
- `/copy` copies the latest response source. For native terminal scrollback
  and selection, choose `--cli` rather than the alternate-screen TUI.

## Python API

The convenience API runs a complete turn, including tool calls:

```python
from dashscope_agenticx import run_once_sync

reply = run_once_sync("Inspect this project's structure without changing files")
print(reply)
```

For application integration, consume structured events instead of parsing
terminal output:

```python
import asyncio

from dashscope_agenticx import run_events

async def main():
    async for event in run_events("Summarize the project"):
        print(event.type.value, event.payload)

asyncio.run(main())
```

`create_agent` supports explicit configuration, a system prompt, and a
session ID. Use `run_interactive` for embedding the interactive experience.
Lower-level integrations can construct a Runtime with `RuntimeBuilder`;
see the [API reference](user-guide.md#programmatic-sdk).

## Configuration and Apps

Workspace configuration lives under `.agenticx/`. Global configuration uses
the platform-standard `AgenticX` directory; `AGENTICX_HOME` overrides it.

| File | Purpose |
|---|---|
| `config.toml` | Models, capabilities, and interaction settings |
| `system-prompt.md` | Domain role and behavioral instructions |
| `skills/` | Markdown Skills and directory-based Skill packages |
| `custom-extensions.toml` | Declarative providers and HTTP/Shell tools |
| `permissions.toml` | Tool and command allow/deny/confirm policies |
| `hooks.toml` | Event hooks subject to trust and permission controls |
| `session/` | Persistent conversations and run records |

A production App Bundle combines a versioned `app.toml` manifest, an
`.agenticx/` profile, and optional eval/acceptance files. Manage local Apps
with:

```bash
agenticx app list
agenticx app install ./path/to/app-bundle
agenticx app info <app-name>
agenticx app verify <app-name>
agenticx app update <app-name>
```

For example, install and verify DashScope SDK Expert from the official App
Catalog:

```bash
python -m pip install "dashscope-agenticx>=1.0.1,<2"
git clone https://github.com/dashscope/dashscope-agenticx-experts.git
cd dashscope-agenticx-experts
agenticx app install ./apps/dashscope-sdk-expert
agenticx app info dashscope-sdk-expert
agenticx app verify dashscope-sdk-expert
```

`app install` only installs and registers the versioned App under the current
workspace's `.agenticx/apps/`; it does not activate the App profile. After the
`cd` above, the cloned repository is that workspace. To run DashScope SDK Expert,
merge its profile into the workspace and start AgenticX:

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
agenticx expert download dashscope-sdk-expert
agenticx
```

Expert downloads are not registered as Apps and cannot be managed with `app
list` or `app update`. `app update` reinstalls from the recorded source, so run
`git pull` in the clone before updating a locally cloned App.

The source tree includes `dashscope-sdk-expert` and `bailian-expert` bundles.
Inspect the manifest and requested permissions before installation. A checksum
proves content identity, not publisher authenticity; a local trust flag is not
a verified digital signature. `/expert` manages role templates, not a substitute
for the versioned App installation contract.

## Security and Compatibility

- Store new credentials through the OS keyring or supply environment variables.
  Do not treat legacy `ENC:` values as secure encryption.
- Trust and permission checks govern App code, shell hooks, and tool execution.
  Do not label an unknown bundle verified merely to bypass those checks.
- Default `confirm_mode = "all"` requires approval for write/confirm tools.
  Explicit policy denies also apply to AUTO tools and `auto_approve` runs.
- Expert downloads contain role/configuration data, not executable Python,
  Shell tools, or run hooks. App installation previews new permissions before
  writing files; granting trust is not an OS sandbox or a network allowlist.
- OS sandbox support is defense in depth. Availability and limitations depend
  on the backend; review the reported execution boundary before automation.
- The localhost Server requires a bearer token and does not accept remote
  permission approvals; it denies actions requiring approval even when the
  local CLI configuration enables automatic execution. It is not an
  Internet-facing deployment contract.
- Legacy directory migration is one-way; existing destinations are not merged
  or overwritten. Imports and environment variables need an explicit update.

Read the [security policy](SECURITY.md) and
[1.0 migration guide](migration-to-1.0.md) before upgrading or installing
third-party applications.

## Development

```bash
python -m pip install -e ".[dev,all]"
python -m pip install pre-commit
python -m pytest tests/ -q
python scripts/release_check.py
pre-commit run --all-files
```

The release contract checks package identity, version, imports, CLI, and
bundled Apps. Coverage gates, cross-platform CI, App validation, and
supply-chain checks are separate release requirements; passing local unit
tests alone does not publish or certify a final 1.0 release.

## Documentation

| Document | Contents |
|---|---|
| [User guide](user-guide.md) / [中文手册](user-guide_zh.md) | Full commands, configuration, tools, modules, and development reference |
| [Migration](migration-to-1.0.md) | Legacy naming, directories, credentials, and compatibility |
| [Changelog](CHANGELOG.md) | 1.0 release notes, upcoming changes, and archived release history |
| [Contributing](CONTRIBUTING.md) | Development conventions and contribution requirements |
| [Security](SECURITY.md) | Security boundaries and private reporting |
| [Release process](RELEASE.md) | Packaging, checksums, SBOM, and publication gates |

## License

MIT, as declared in [pyproject.toml](pyproject.toml).
