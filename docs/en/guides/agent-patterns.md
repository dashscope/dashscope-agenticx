---
description: "AgenticX agent patterns: subagents and delegation, lifecycle hooks, scheduled tasks, memory, rules, and prompt composition."
---
# Subagents, Hooks & Memory

## Subagents and delegation

### `subagent_invoke` (capability `local.subagent`)

The model can spin up a second-level agent with an **isolated context** to
run a subtask; only a summary comes back, so intermediate tool calls do not
pollute the main conversation.

| Parameter | Default | Description |
|---|---|---|
| `prompt` | required | The subtask, as a single user message |
| `system_prompt` | empty | Override the subagent's role (reviewer / researcher / …) |
| `max_turns` | 10 (cap 50) | Sub-loop turn limit to prevent runaway execution |

The subagent **shares** the main agent's provider, executor, tool table,
MemoryManager (plan progress, reflection hints), `/rule` rules, and
capability notices — but **not** `session_path`: subagents are disposable
and never write session history.

Enabling `local.subagent` also registers three expert tools with fixed
system prompts: `review_code` (bugs/security/performance/style),
`write_tests` (pytest generation), `generate_docs` (API docs and examples).

### `delegate` / `delegate_parallel` (capability `local.delegate`)

Split complex tasks across multiple isolated subagents and aggregate:

- `delegate` — sequential: one large prompt to a single subagent, full
  result returned.
- `delegate_parallel` — parallel: multiple `(name, prompt)` tasks at once;
  results are joined as `name: result` for the main agent.

| Parameter | Required | Description |
|---|---|---|
| `task` | yes | Task description |
| `system_prompt` | no | Role override |
| `tools` | no | Tool whitelist (e.g. `["read_file", "search_files"]`); empty inherits the main tool table |
| `context_files` | no | File paths whose contents are prepended to the subtask |
| `max_turns` | no | Default 10, cap 50 |
| `timeout` | no | Per-subtask seconds, default 120 |
| `model` | no | Subagent model; inherits by default |

```toml
# config.toml
delegation = { max_concurrent = 5, default_timeout = 120 }
```

Subagents are **always forbidden from delegating further** (created with
`allow_delegate=False`), preventing infinite cascades. The `allow_nested`
config field is parsed and stored but currently has no effect.

## Event hooks

`.agenticx/hooks.toml` inserts custom behavior at lifecycle points:
intercept dangerous tools, print warnings, run audit scripts, or ask for
confirmation.

| Event | Fires when |
|---|---|
| `before_tool_call` | Just before a tool executes |
| `after_tool_call` | After a tool execution completes |
| `on_error` | A tool raises or a subagent fails |
| `on_message` | A user message is received |
| `on_response` | An LLM response is received |

Governance actions dispatch first; a `block` hit short-circuits all
subsequent hooks:

| Category | Action | Behavior |
|---|---|---|
| Governance | `block` | Block the operation, return a custom message to the agent |
| Governance | `confirm` | Continue only after user confirmation |
| Governance | `alert` | Red alert; escalates on tool failure |
| Extension | `run` | Execute a shell command (`run = "..."` or argument array) |
| Extension | `warn` | Yellow warning |
| Extension | `log` | Write to the log, not the terminal |

```toml
# .agenticx/hooks.toml
[[hooks.before_tool_call]]
match = "write_file"
condition = "*.py"
action = "confirm"
message = "About to write {filename}, confirm?"

[[hooks.before_tool_call]]
match = "run_command"
content_pattern = "*rm -rf*"
action = "block"
message = "Dangerous command detected, blocked"

[[hooks.after_tool_call]]
match = "write_file"
condition = "*.py"
action = "run"
run = ["python", "-m", "py_compile", "{path}"]
on_fail = "warn"
silent = true
```

Matching fields: `match` (tool-name glob, default `"*"`), `condition`
(file-path glob extracted from arguments), `content_pattern` (content glob
over arguments JSON or results). Other fields: `message` (template),
`on_fail` (`warn` default / `alert` / `block`), `silent`.

Template variables include `{tool_name}`, `{args}`, `{result}`, `{error}`,
`{path}`, `{filename}`, `{filename_stem}`, `{content}`, `{exit_code}`,
`{timestamp}`.

!!! warning "Run-hook argument boundary"
    Run hooks pass template values as **literal process arguments**, without
    a shell. Command strings containing variables are parsed before
    substitution; pipes and redirections are rejected. The executable cannot
    be templated; templated hooks cannot invoke shell interpreters or
    interpolate Python code/module names; Windows targets must not be
    `.bat`/`.cmd`. Only constant command strings keep shell execution. This
    boundary does not sandbox a script that evaluates its arguments as code.

Skill packages can scope hooks to themselves via
`.agenticx/skills/<name>/hooks.toml`, mounted on the same HookBus. Hooks are
subject to trust and permission controls — see
[Security](security-and-platform.md).

## Scheduled tasks (`/cron`)

Run Skills on a schedule; each run defaults to its own isolated subagent.
Tasks persist across sessions in `~/.config/AgenticX/cron_jobs.json`.

```text
/cron add every 5m skill weather Hangzhou
/cron add at 14:30 skill search AI morning briefing      # one-shot
/cron add cron "0 9 * * 1-5" skill search morning briefing
/cron add every 1h skill weather Beijing skill search tech news
/cron add every 10m skill search service alerts condition "curl -sf http://health"
/cron add every 5m skill weather Hangzhou no-subagent    # main conversation
```

Management: `/cron list|pause <id>|resume <id>|remove <id>`.

`at` tasks auto-disable after execution; each run has a 300-second timeout;
cron tasks do not run while privacy mode is enabled.

## System prompt and project instructions

`.agenticx/system-prompt.md` (workspace) or
`~/.config/AgenticX/system-prompt.md` (global) replaces the **base** prompt;
workspace wins. The `PromptPipeline` then appends its sections — you do not
need to redeclare tool usage, rules, or identity:

```text
[base prompt / system-prompt.md]
[Project instructions]
─── stable prefix: byte-identical for the whole session (prompt-cache friendly) ───
[Skills templates] [Skill package rules] [Disabled-capability notice]
[Long-lived operation rules] [Current plan] [Experience recall]
[Tool-chain context] [Directive proposals] [Environment info]
─── ephemeral suffix: recomputed every turn ───
```

Project instruction files are auto-discovered upward from the CWD,
first match wins: `.agenticx/rules.jsonl` > `.agenticx/prompt.md` >
`.agenticx/prompt` > `.agenticx/instructions.md` > `.cursorrules` >
`.claude.md` > `CLAUDE.md`. YAML frontmatter is stripped; long files are
truncated 60/40 head/tail at 8000 characters.

## Rules vs profile

Two complementary memory forms:

| Dimension | `/profile` | `/rule` |
|---|---|---|
| Content | Identity, preferences, tech stack (facts) | Operation rules, process constraints |
| Injection | Keyword recall — only relevant turns | Enabled rules — every turn |
| Storage | `.agenticx/memory/<user_id>.json` | `.agenticx/rules.jsonl` |
| Example | "I'm a backend developer" | "Don't push after commit" |

Rule of thumb: **`/profile` for "who I am"; `/rule` for "how things should
be done".**

```text
/rule add "Do not push after each commit; wait until the user says push"
/rule list | edit | enable <n> | disable <n> | remove <n> | clear
/profile list | search <kw> | add <text> | remove <n> | clear
```

More than 15 enabled rules triggers a trimming hint (tokens keep growing).

**Directives auto-learning**: tool-call sequences are recorded to
`.agenticx/memory/behavior_patterns.json`; after 5+ turns, adjacent tool
pairs seen 3+ times become rule proposals (confidence =
`min(count / 10, 1.0)`, at most 2 pending at a time). Review with
`/directives proposals`, then `/directives accept <id>` or `reject <id>`.
Core does not learn directives or generate skills in the background by
default beyond this proposal mechanism; experimental skill evolution
requires the experimental pack and `AGENTICX_EXPERIMENTAL=1`.

## Memory tiers and sessions

| Tier | Content | Lifecycle |
|---|---|---|
| Working | Conversation messages; token-budget compression against the model context window (soft threshold → LLM summarization of old messages, hard threshold → truncation) | Within session |
| Mid-term | Plan progress (TTL expiry), reflection hints, tool-chain patterns | Within session |
| Long-term | Cross-session experience learning (`experiences.jsonl`, CJK-aware retrieval) | Across sessions |

Sessions (topics) isolate working memory:
`.agenticx/session/<topic>/history.json`. Long-term memories (profile,
skills, rules, experience) are shared across topics. Each topic can carry
**scene memory** (`scene.md`) injected whenever you switch back:

```text
/session new|list|switch|rename|fork|remove
/session scene Context: data platform, Python 3.11, pytest only
/session scene set <text>     # replace
/session scene clear
```

Experience memory records task outcomes and tool patterns; long tasks
(6+ messages) are summarized automatically, `/summarize` triggers manually,
and `/feedback good|bad` labels satisfaction. After 3+ consecutive tool
failures, a reflection hint nudges the model to change strategy.

Conversation history summaries live in `.agenticx/memory/history.json`
(`/memory list|search|remove|clear`). Every LLM call and tool execution is
traced to `.agenticx/traces/<session>.jsonl` for debugging and evaluation
(`/trace`, `/report`, `/stats`).

Context management commands: `/compress` replaces history with an LLM
summary; `/undo` reverts the last file write/delete via checkpoint
snapshots.