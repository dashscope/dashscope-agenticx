---
description: "DashScope AgenticX — Python agent runtime: terminal CLI, embeddable SDK, tools, skills, and governed execution."
---
# DashScope AgenticX

<p class="hero-badges" markdown>
[**v{{ agenticx_version }}**](release-notes.md){ .hero-badge }
[**Python 3.10+**](about.md#requirements){ .hero-badge }
[**MIT**](https://github.com/dashscope/dashscope-agenticx/blob/main/pyproject.toml){ .hero-badge }
</p>

**Enterprise AI experts, built to act.** AgenticX is a Python agent
runtime for building and running domain experts. It connects models,
tools, skills, permissions, and persistent sessions through one runtime,
with a terminal interface and APIs for embedded applications.

<div class="hero-cta" markdown>
[Quickstart](quickstart.md){ .md-button .md-button--primary }
[Embed the SDK](guides/sdk.md){ .md-button }
</div>

Use it directly as a local assistant, embed it in a Python application,
or package a domain expert as a versioned App Bundle. DashScope SDK
Expert and Bailian Expert are bundled expert templates illustrating this
application layer.

## Features

<div class="grid cards" markdown>

-   :material-robot:{ .lg .middle } **One runtime**

    ---

    Instance-owned tool, skill, hook, and session state; shared
    construction through `RuntimeBuilder`.

-   :material-console:{ .lg .middle } **Terminal & TUI**

    ---

    A full interactive CLI with 40+ slash commands, plus an optional
    Textual TUI with rich Markdown output.

-   :material-code-braces:{ .lg .middle } **Embeddable SDK**

    ---

    `run_once`, `run_stream`, and typed `run_events` for scripts, CI,
    IDE plugins, and applications.

-   :material-toolbox:{ .lg .middle } **Tools & skills**

    ---

    Filesystem, shell, web, browser, camera, MCP services, markdown
    skills, and declarative TOML extensions — no code required.

-   :material-shield-check:{ .lg .middle } **Governed execution**

    ---

    Permission tiers, policy files, OS sandbox backends, audit log,
    privacy mode, and event hooks.

-   :material-package-variant:{ .lg .middle } **Apps & experts**

    ---

    Versioned App manifests with checksum verification and permission
    diffs; one-command expert workspace initialization.

</div>

## Requirements

- Python 3.10+ (Pillow-backed `vision`/`media` extras need 3.11+).
- A model API key for your provider — for the default Tongyi/DashScope
  provider:

```shell
export DASHSCOPE_API_KEY="<your-api-key>"
```

## Installation

```shell
python -m pip install "dashscope-agenticx[tui]"
agenticx --version
```

For a minimal, non-TUI install use `pip install dashscope-agenticx` and
start with `agenticx --cli`. Optional extras (provider SDKs, web,
browser, media) are listed in [Quickstart](quickstart.md#optional-extras).

## Examples

### Run it in the terminal

```shell
agenticx                                    # interactive session
agenticx -c "Summarize this project"        # one-shot, then exit
```

### Run it from Python

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Inspect this project's structure without changing files")
print(reply)
```

### Consume typed events

```python
import asyncio

from dashscope_agenticx.sdk import run_events


async def main():
    async for event in run_events("Summarize the project"):
        print(event.type.value, event.payload)


asyncio.run(main())
```

One-shot runs execute tools, not just generate text — start in a
suitable workspace and choose permissions appropriate to the task.

## Where to go next

- [Quickstart](quickstart.md) — install, first run, first SDK call.
- [Configuration](configuration.md) — config layers, providers, keys.
- [CLI & Commands](cli.md) — every slash command and CLI option.
- [Reference](reference/sdk.md) — the full API surface.

AgenticX is not a hosted service, a hardened OS sandbox, or a complete
workflow platform. Voice, camera, browser, web search, scheduler, and
experimental features are optional, not prerequisites for the core
runtime.