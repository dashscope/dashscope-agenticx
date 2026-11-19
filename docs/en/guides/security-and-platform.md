---
description: "AgenticX security model: permissions, privacy, audit, OS sandbox, MCP integration, Bailian CLI, and the localhost Server."
---
# Security, Platform & MCP

## Permissions

The default is `confirm_mode = "all"`: writes and CONFIRM tools require
approval. Explicit `deny` rules always win — including AUTO tools and
`auto_approve` runs. Parallel delegates receive a read-only tool allowlist
enforced during execution, not merely hidden from the model; children do
not inherit the parent's cached approvals.

Confirmation choices:

- `y` — allow once.
- `n` — deny once; the agent receives "operation cancelled" and moves on.
- `a` — trust this tool name for **the current turn only** (one
  `run_stream` call); cleared automatically at turn end. Inspect with
  `/trust`, clear with `/trust clear`.
- `s` — raises `UserAbortedTurn`; the loop ends and control returns to the
  prompt.

DANGEROUS tools (`delete_file`, `delete_directory`) accept only `y`/`n` —
never cached.

### Always-active protection layers

Independent of permission level:

- **Sensitive paths**: reads/writes of `.ssh`, `.aws`, `.gnupg`, `.env`,
  `.npmrc`, `shadow`, etc. are always rejected.
- **Hard-blocked commands**: `rm -rf /`, `mkfs`, `dd if=`, `> /dev/sd`,
  `chmod -R 777 /` and similar are rejected outright, without confirmation.
- **Read-only classification**: `run_command` auto-allows known read-only
  commands (`grep`, `ls`, `find`, `cat`, `git status`, `git log`,
  `git diff`, `pip list`, `docker ps`, …). Any write signal (`rm`, `mv`,
  `cp`, `chmod`, `sudo`, `pip install`, `make`, redirection `>`, command
  substitution `$(…)`) falls back to the confirmation flow.

### Policy engine

Preset per-tool / per-command-prefix rules in `permissions.toml` (workspace
`./.agenticx/` overrides global `~/.config/AgenticX/` on the same key).
Policy takes precedence over interactive confirmation; command rules match
by longest prefix:

```toml
# .agenticx/permissions.toml
[tools]
delete_file = "deny"

[commands]
"git status" = "allow"
"git push" = "deny"     # longest prefix wins over git = allow
```

App install/update previews new permissions before copying files. Expert
templates cannot install `skills/**/tools.py`, shell tools, or executable
run hooks — even with `--force`; use trusted App Bundles for executable
extensions. Permission metadata and checksums are **not** OS/filesystem/
network isolation or a publisher signature.

## OS sandbox

`sandbox = true` in config runs shell commands inside an OS sandbox when a
backend is available:

| Platform | Backend | Notes |
|---|---|---|
| macOS | Seatbelt (`sandbox-exec`, built in) | Profile confines writes to the workspace |
| Linux | bubblewrap (`bwrap`, if installed) | Read-only root with workspace bind |
| Windows | — | No backend; commands run unsandboxed |

Sandbox support is defense in depth, not a hardened isolation contract.
Availability and limitations depend on the backend; review the reported
execution boundary (`SANDBOX_STATUS` events, `/info`) before automation.

## Privacy mode

```text
/privacy on | off | status
```

| Effect | Description |
|---|---|
| Cloud capabilities disabled | `bailian.*` treated as not enabled (`/mcp` intercepted); local memory/storage unaffected |
| Scheduled tasks paused | cron runs skipped, marked "skipped due to privacy mode" |
| Audit sanitization | Tool arguments and user input truncated to 20 characters |

!!! note
    Privacy mode does **not** block LLM API calls — conversation content is
    still sent to the model provider. For fully offline use, switch to a
    local provider such as `ollama`.

## Audit log

Key operations append as JSONL to `./.agenticx/audit.log` (per workspace):

```jsonc
{
  "timestamp": "2026-11-19T10:00:00Z",
  "source":    "agent",        // user / agent / scheduler / system
  "action":    "tool_call",    // input / tool_call / permission / cron / mcp
  "subject":   "run_command",
  "decision":  "approved",     // approved / denied / executed / skipped / auto
  "reason":    "",
  "details":   { "args": { "command": "git status" } }
}
```

```text
/audit recent [n]      # latest records (default 20)
/audit query tool_call # filter by action
/audit clear
```

Under privacy mode, parameter values (`content`, `command`, `query`,
`text`, `prompt`, `url`, `path`, `file`) are truncated to 20 characters
with `…(redacted)`; tool names and decisions are always preserved.

## MCP integration

AgenticX is both an MCP **client** and **server**.

### Client (`/mcp`, capability `bailian.mcp`)

Connects over **SSE transport**, registering tools as
`mcp_<service>_<tool>`. Works with the Bailian MCP marketplace and any
SSE-compatible third-party/local service:

```text
/capability enable bailian.mcp        # requires DASHSCOPE_API_KEY for Bailian
/mcp list                             # marketplace services
/mcp add time                         # Bailian default endpoint
/mcp add my-server http://localhost:3000/sse   # custom SSE URL
/mcp                                  # connected services + tools
/mcp remove my-server
```

Connections persist to `[[mcp_servers]]` in workspace `config.toml` and
reconnect on startup. Third-party services that need no Bailian key can
leave `tongyi_api_key` empty; service-side auth is handled at the SSE
endpoint (e.g. token in URL).

!!! note "Transport limitation"
    The MCP client supports SSE over HTTP only — not stdio. Wrap a local
    stdio MCP server as a tool via `[[shell_tools]]` /
    `[[capabilities.tools]]` instead.

### Server (`agenticx mcp-server`)

Exposes registered tools to other MCP-compatible agents over stdio
(JSON-RPC), protocol version `2024-11-05`, methods `tools/list` and
`tools/call`.

!!! warning
    The MCP server executes tools in `auto_approve` mode (no interactive
    confirmation). Only expose it to trusted clients.

## Bailian CLI

With the `bailian.cli` capability enabled, `bl` on PATH and authenticated
(`bl auth login`), AgenticX registers the full Bailian command set as native
function-call tools (`bailian_text_chat`, `bailian_image_generate`, …). Schemas come from
`bl config export-schema`, cached 24h in
`~/.config/AgenticX/bl-schemas.json`; camelCase parameters convert to
`--kebab-case` flags automatically. Permissions tier by verb: read verbs
Auto, quota/file-producing verbs Confirm. AgenticX passes the configured
DashScope key through as `--api-key`, so authentication is configured once.
`bailian.cli` and `bailian.mcp` can coexist without tool-name collisions.

## Local HTTP API

`agenticx serve` starts a token-authenticated localhost Client API for
Runs, Sessions, Artifacts, and Traces:

```bash
agenticx serve --token <at-least-16-chars>   # default 127.0.0.1:8765
# token omitted → generated and printed at startup
```

| Route | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness |
| `/v1/sessions`, `/v1/sessions/<topic>` | GET | Session list / state |
| `/v1/artifacts?session_id=…` | GET | Artifacts |
| `/v1/traces` | GET | Trace file names |
| `/v1/runs` | POST | Run a turn (`input`, optional `session_id`, `system_prompt`); returns the typed event list |
| `/v1/permissions*` | POST | Always **403** — permission decisions are local-runtime only |

Requests need `Authorization: Bearer <token>` (constant-time comparison).
The server binds only to `127.0.0.1` / `localhost` / `::1`; other hosts are
rejected. Runs force `auto_approve = False` and `confirm_mode = "all"`, and
any action requiring approval is **denied** even when local CLI config
enables automatic execution. This is not an Internet-facing deployment
contract.

For Python clients, embed the [SDK](sdk.md) directly instead; for
non-Python clients, consume the same `AgentEventEnvelope` payloads from
`/v1/runs`.