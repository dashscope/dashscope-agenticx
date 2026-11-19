---
description: "Install AgenticX, configure an API key, run the first terminal session and the first Python SDK call."
---
# Quickstart

## Install

Python **3.10+** is required.

=== "Package"

    ```shell
    python -m pip install "dashscope-agenticx[tui]"
    agenticx --version
    ```

    For a minimal, non-TUI installation:

    ```shell
    python -m pip install dashscope-agenticx
    agenticx --cli
    ```

=== "From source"

    ```shell
    python -m venv .venv
    # macOS / Linux
    source .venv/bin/activate
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    python -m pip install -e ".[tui]"
    agenticx --version
    ```

The distribution is **`dashscope-agenticx`**, the import namespace is
**`dashscope_agenticx`**, and the only official command is **`agenticx`**.
The bare `agenticx` PyPI distribution is a different project.

### Optional extras

| Extra | Purpose |
|---|---|
| `tui` | Textual interface with fixed input panel and rich output |
| `provider-openai`, `provider-anthropic` | Provider-specific SDK routes |
| `web`, `browser` | Search and browser tools (browser runtime setup is separate) |
| `voice`, `camera`, `vision` (or `media`) | Audio, camera, and image dependencies |
| `dev`, `all` | Test/build tooling and all optional features |

```shell
python -m pip install "dashscope-agenticx[web,browser]"
```

Installing dependencies does not grant an App permissions.

## Set an API key

For the default Tongyi/DashScope provider:

```shell
export DASHSCOPE_API_KEY="<your-api-key>"
# Windows PowerShell: $env:DASHSCOPE_API_KEY="<your-api-key>"
```

Other providers use `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`, or the
per-provider key slots managed by `/provider` (stored in the OS
keyring). See [Configuration](configuration.md#global-config-api-keys). Do not paste
real keys into tracked configuration files.

## First terminal run

```shell
agenticx
```

A new workspace without `.agenticx/config.toml` starts the `/setup`
wizard: username, configuration mode (Default — Alibaba/Bailian, or
Custom), provider/model, API key, capabilities, and an optional expert
template. Existing configured workspaces skip straight to the prompt.

```text
agenticx> Summarize this project's structure before making any edits
agenticx> /help
agenticx> /provider
agenticx> /session
```

Other entry modes:

```shell
agenticx --cli                      # inline REPL with native scrollback
agenticx --tui                      # Textual interface (tui extra)
agenticx -c "List the files here and summarize the project"
agenticx --dry-run                  # preview loaded configuration
```

One-shot runs can execute tools. Start in a suitable workspace and keep
the default `confirm_mode = "all"` until you trust the task.

## First SDK call

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Summarize the key points of README.md")
print(reply)
```

`run_once_sync` runs a complete turn — including tool calls — and
returns the final assistant text. For streaming and typed events see
[Embedding the SDK](guides/sdk.md).

## Next steps

- [Configuration](configuration.md) — config files, providers, keys.
- [CLI & Commands](cli.md) — the full command surface.
- [Tools, Skills & Experts](guides/tools-and-skills.md) — extend the agent.
- [Security, Platform & MCP](guides/security-and-platform.md) — permissions and platform capabilities.