---
description: "AgenticX layered configuration: global and workspace config.toml, environment variables, providers, and API key management."
---
# Configuration

AgenticX uses **layered configuration**:

- **Global** — the platform-standard `AgenticX` app directory
  (`~/.config/AgenticX` on Linux; `AGENTICX_HOME` overrides it on any
  platform): per-provider API key slots, voice/TTS settings, shared
  across projects.
- **Workspace** — `./.agenticx/config.toml`: provider, model, platform
  capabilities, MCP services, username.

Priority: **workspace config > global config > environment variables
(fill empty slots only) > interactive input**. `AGENTICX_PROVIDER` and
`AGENTICX_MODEL` always force-override. The same resolution is exposed
programmatically as [`Config.load()`](reference/sdk.md#config).

## Workspace config

```toml
# ./.agenticx/config.toml
user_name = "agenticx"
provider = "tongyi"
model = "qwen3.8-flash"
fallback_providers = "openai,anthropic"   # tried in order on primary failure
enabled_capabilities = "bailian.mcp,bailian.cli"
max_turns = 100                  # tool-call turns per conversation (default 50)
session_persist = true           # cross-session history persistence
tui = false                      # false = inline CLI REPL instead of TUI
user_directives = ["Don't push after each commit"]   # written by /rule
delegation = { max_concurrent = 5, default_timeout = 120 }

[[mcp_servers]]
service = "time"
```

The full field list with types and defaults is in the
[Config reference](reference/sdk.md#config).

## Global config & API keys

Each provider has an independent key slot, so switching providers never
overwrites another key. New secrets are stored in the OS keyring under
the `dashscope-agenticx` service; TOML holds only references:

```toml
# ~/.config/AgenticX/config.toml
tongyi_api_key    = "KEYRING:v1:..."
anthropic_api_key = "KEYRING:v1:..."
openai_api_key    = "KEYRING:v1:..."
```

The `/provider` wizard enters keys without terminal echo and offers
three sources: OS keyring, environment variable, or configure later.
Legacy single `api_key` lines are migrated to the current provider's
slot on first launch; legacy `ENC:` values remain readable for
migration but are never written for new secrets.

## Environment variables

API-key variables only fill empty slots; `AGENTICX_PROVIDER` /
`AGENTICX_MODEL` always override.

| Variable | Description |
|---|---|
| `DASHSCOPE_API_KEY` | Tongyi / Bailian API key |
| `ANTHROPIC_API_KEY` | Claude API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_BASE_URL` | Custom OpenAI-compatible endpoint (highest priority) |
| `AGENTICX_PROVIDER` | Force the provider |
| `AGENTICX_MODEL` | Force the model |
| `AGENTICX_MCP_SERVERS` | Pre-connect MCP services (comma-separated) |
| `AGENTICX_MEMORY_USER_ID` | User identifier for profile isolation |
| `AGENTICX_MEMORY_LIBRARY_ID` | Bailian profile library ID |
| `AGENTICX_COMMAND_TIMEOUT` | `run_command` timeout seconds (default 600) |
| `AGENTICX_HOME` | Override the global config directory |

## Built-in providers

| Provider | Protocol | Default model |
|---|---|---|
| `tongyi` | DashScope native | `qwen3.8-flash` |
| `anthropic` | Anthropic Messages | `claude-sonnet-4-20250514` |
| `openai` | OpenAI | `gpt-4o` |

Switch with `/provider` (wizard) or `/model <name>` (model-only).
Reasoning models (`reasoning_content`) are supported; the reasoning
content is passed back on the next request to avoid provider 400s.

## Custom providers

DeepSeek, Kimi, Ollama, and any OpenAI/Anthropic-compatible endpoint are
declared in `custom-extensions.toml` (workspace overrides global by
`name`) — no code required:

```toml
# ./.agenticx/custom-extensions.toml
[[providers]]
name          = "moonshot"
base_url      = "https://api.moonshot.cn/v1"
api_key_env   = "MOONSHOT_API_KEY"   # env var name; no plaintext on disk
default_model = "kimi-k2"
protocol      = "openai"             # openai / anthropic / dashscope

[[providers]]
name          = "ollama"
base_url      = "http://localhost:11434/v1"
default_model = "llama3"
protocol      = "openai"
auth          = false                # local, no key needed
```

Plaintext `api_key = "sk-..."` is rejected by the loader; use
`api_key_env` or a `KEYRING:v1:` reference. `/dev provider add` writes
these blocks interactively. See
[Tools, Skills & Experts](guides/tools-and-skills.md#custom-tools)
for HTTP tool groups, shell tools, and skills in the same file.

**base_url precedence** (primary provider): `OPENAI_BASE_URL` env >
`base_url` in `config.toml` > matching `[[providers]]` entry > built-in
default. Fallback providers always use their own declared endpoint.

## Workspace file map

| File | Purpose |
|---|---|
| `.agenticx/config.toml` | Models, capabilities, interaction settings |
| `.agenticx/system-prompt.md` | Domain role and behavioral instructions |
| `.agenticx/skills/` | Markdown skills and skill packages |
| `.agenticx/custom-extensions.toml` | Declarative providers and HTTP/shell tools |
| `.agenticx/permissions.toml` | Tool/command allow-deny-confirm policy |
| `.agenticx/hooks.toml` | Event hooks under trust and permission controls |
| `.agenticx/session/` | Persistent conversations and run records |