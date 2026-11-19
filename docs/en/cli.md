---
description: "AgenticX CLI options, subcommands, and the complete slash command reference."
---
# CLI & Commands

## Startup options

```shell
agenticx                          # interactive session (TUI if installed)
agenticx --cli                    # inline REPL with native scrollback
agenticx --tui                    # force the Textual interface
agenticx -c "Summarize this project"   # one prompt, then exit
agenticx --dry-run                # preview loaded configuration
agenticx --protocol anthropic     # startup protocol: openai (default) / anthropic
agenticx --max-turns 20           # override the conversation turn limit
agenticx --version
```

## Subcommands

```shell
agenticx app list                 # versioned App Bundles
agenticx app install <path>       # install + permission diff preview
agenticx app info <name>          # publisher, permissions, runtime range, checksum
agenticx app verify <name>        # checksum + runtime compatibility
agenticx app update <name>        # reinstall from the recorded source

agenticx expert                   # list expert templates
agenticx expert download <name>   # merge an expert profile into ./.agenticx/
agenticx expert restore           # undo the most recent merge

agenticx mcp-server               # expose tools over MCP stdio (trusted clients only)
agenticx serve --token <token>    # localhost Client API (default 127.0.0.1:8765)
```

`app install` registers the bundle under `.agenticx/apps/` but does not
activate its profile; expert downloads are role/configuration data, not
registered Apps. See
[Tools, Skills & Experts](guides/tools-and-skills.md#experts) and
[Security, Platform & MCP](guides/security-and-platform.md#local-http-api).

## Slash commands

Commands are organized in four tiers: Session → Configuration →
Capability → Development. `/help` lists them in this order.

### Session

| Command | Description |
|---|---|
| `/help` | Show help, grouped by category |
| `/clear` | Clear conversation history (persists the empty history) |
| `/info` | Runtime info: provider/model/protocol/base URL/capabilities/privacy |
| `/copy` | Copy the last reply to the clipboard |
| `/save [path]` | Save the last reply to a file |
| `/json on\|off` | Force JSON replies (`response_format` passthrough) |
| `/compress` | Replace history with an LLM summary to save tokens |
| `/summarize` | Summarize the current task into experience memory |
| `/stats` | Token usage, prompt composition, API/tool counts, duration |
| `/report` | Trace performance report (calls, success rate, latency) |
| `/feedback good\|bad` | Mark task satisfaction (experience memory) |
| `/history [subcmd]` | History stats/list/search/export/clear |
| `/undo` | Undo the last file write/delete (checkpoint snapshots) |
| `/session [subcmd]` | Topics: new/list/switch/rename/fork/remove/scene |
| `/privacy [on\|off\|status]` | Privacy mode |
| `/audit [subcmd]` | Audit log: recent/query/clear |
| `/voice [subcmd]` | Voice input: on/off/model/silence/max/threshold |
| `/tts [subcmd]` | Voice output: on/off/model/voice/speed/say/last |
| `/camera capture\|record` | Camera photo / video (media extras) |
| `/log [subcmd]` | Full prompts recorded under `/debug on`: tail/search/clear |
| `/trace [subcmd]` | Execution trace: tail/search/clear |
| `/exit` | Exit (aliases `/quit`, `/q`) |

`/v` is the voice-input shortcut (`Ctrl+T`): record → ASR → send.

### Configuration

| Command | Description |
|---|---|
| `/setup` | Interactive workspace initialization wizard |
| `/provider` | Wizard: provider → model → connection → API key |
| `/model [name]` | Switch model within the current provider |
| `/capability [subcmd]` | list/enable/disable/reload/config |
| `/subagents [subcmd]` | list/reload/enable/disable/config |
| `/trust [subcmd]` | This turn's tool trust cache: list/clear/allow/deny |
| `/rule [subcmd]` | Long-lived operation rules: list/add/remove/edit/enable/disable/clear |
| `/theme [subcmd]` | list/set/custom colors (dark/light/monokai/dracula/nord/solarized) |
| `/debug [on\|off\|status]` | Record full sanitized prompts to the session trace |
| `/directives [subcmd]` | Custom directives + auto-learning proposals/accept/reject |

### Capability

| Command | Description |
|---|---|
| `/profile [subcmd]` | User profile: list/search/add/remove/clear |
| `/memory [subcmd]` | Conversation history memory: list/search/remove/clear |
| `/mcp [subcmd]` | MCP services: add/remove/list (requires `bailian.mcp`) |
| `/skill [name] [args]` | List or invoke skills |
| `/skill install\|link\|update\|uninstall` | Skill package lifecycle |
| `/skill search\|publish\|reload` | Registry search, packaging, rescan |
| `/skill enable\|disable <name>` | Force skill package state |
| `/app list\|info\|install\|update\|verify` | Versioned App Bundle management |
| `/cron [subcmd]` | Scheduled tasks: add/list/remove/pause/resume |

Commands for disabled capabilities are intercepted with an enable hint
instead of failing on credentials.

### Development

| Command | Description |
|---|---|
| `/dev model add\|rm\|list` | Register models at runtime |
| `/dev provider add\|list\|rm` | Extend providers |
| `/dev capability add\|list\|rm` | Extend HTTP tool groups |
| `/dev skill add\|list\|rm` | Extend skills |
| `/dev tool add\|list\|rm` | Extend shell tools |
| `/dev debug tools\|schema\|call\|prompt` | Debugging helpers |
| `/dev test provider <name>` | Test provider connectivity |
| `/dev reload` | Hot-reload `custom-extensions.toml` |
| `/dev log` | Tool registration statistics |
| `/expert [list]` | List expert templates |
| `/expert download <name>` | Merge an expert into `./.agenticx/` (backup on conflict) |
| `/expert restore` | Undo the most recent merge |

## Terminal output

- Markdown replies with numbered turns; tool details collapse by
  default (`Ctrl+O` toggles), failures stay expanded.
- `Ctrl+G` expands token/API/tool/prompt-composition statistics. Prompt
  sizes are characters, not exact token counts.
- Selection copying: `Cmd+C` on macOS, `Ctrl+C` on Windows/Linux; on
  macOS `Ctrl+C` clears/cancels/quits instead. `/copy` copies the latest
  reply source; `--cli` gives native scrollback.