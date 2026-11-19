---
description: "AgenticX tools, permission tiers, custom extensions, the Skills system, and Expert templates."
---
# Tools, Skills & Experts

## Built-in tools and permission tiers

Every tool has a permission tier that decides how it is confirmed:

| Tier | Behavior |
|---|---|
| **Auto** | No prompt; the model calls it directly |
| **Confirm** | Prompts `[y]es / [n]o / [a]lways (this conversation) / [s]top (abort turn)` |
| **Dangerous** | Only `y` / `n` — destructive operations are asked every time; the "always" cache cannot bypass them |

Explicit policy denies in `permissions.toml` apply even to AUTO tools and
`auto_approve` runs. See [Permissions](security-and-platform.md#permissions).

### Filesystem

| Tool | Permission | Description |
|---|---|---|
| `read_file` | Auto | Read file contents (line offset/limit) |
| `write_file` | Confirm | Write a file, returns a unified diff |
| `list_directory` | Auto | List directory contents with types and sizes |
| `create_directory` | Confirm | Create directories including nested parents |
| `delete_file` | Dangerous | Delete a file (irreversible) |
| `delete_directory` | Dangerous | Delete a directory tree (irreversible) |
| `search_files` | Auto | Search files by glob pattern |
| `move_file` | Confirm | Move / rename a file or directory |

### Shell

`run_command` (Confirm) executes shell commands. Dangerous-mode commands are
intercepted and read-only commands are classified automatically. A `timeout`
argument (seconds, default 600, `AGENTICX_COMMAND_TIMEOUT`) caps the run; on
expiry the whole process group is killed, so a pipeline does not outlive the
timeout.

### Network (optional `web` / `browser` extras)

| Tool | Permission | Description |
|---|---|---|
| `web_search` | Auto | DuckDuckGo search: titles, URLs, snippets |
| `scrape_web` | Auto | Page plain text via Playwright headless browser (renders JS SPAs) |
| `scrape_web_html` | Auto | Rendered HTML source |
| `scrape_web_screenshot` | Auto | Screenshot saved as an image file |

Browser tools need a separate Playwright runtime setup after installing the
extra. Camera tools (`camera_capture`, `camera_record`, both Confirm) need
the `camera`/`vision` media extras.

### Session & configuration (always available)

`switch_model`, `switch_provider`, `list_models`, `capability_list` (Auto);
`capability_enable`, `capability_disable`, `mcp_connect`, `mcp_disconnect`
(Confirm); `mcp_list_services`, `create_plan`, `complete_step`,
`get_plan_status` (Auto).

### Subagents (after enabling `local.subagent` / `local.delegate`)

`subagent_invoke`, `review_code`, `write_tests`, `generate_docs`, `delegate`,
`delegate_parallel` — all Auto. See
[Subagents, Hooks & Memory](agent-patterns.md#subagents-and-delegation).

### Platform tools (after enabling `bailian.cli`)

The full Bailian CLI command set is registered as `bailian_<command_path>`
tools (e.g. `bailian_text_chat`, `bailian_image_generate`). Read verbs
(list/get/status/search) are Auto; write verbs (generate/upload/delete) are
Confirm. Schemas come from `bl config export-schema` and are cached for 24h.
See [Platform integration](security-and-platform.md#bailian-cli).

## Capabilities

Optional tool groups are gated by capabilities:

```text
agenticx> /capability list
agenticx> /capability enable bailian.cli    # takes effect after restart
agenticx> /capability disable local.subagent
agenticx> /capability reload
```

Commands for disabled capabilities are intercepted with an enable hint
instead of failing on credentials. Installing an extra never grants an App
permissions by itself.

## Custom tools

### Layer 1 — configuration as program (no code)

`custom-extensions.toml` (workspace `./.agenticx/` overrides global
`~/.config/AgenticX/`; files are chmod 600) registers providers, HTTP tool
groups, and shell tools at startup. `/dev reload` hot-reloads it.

HTTP tool group:

```toml
[[capabilities]]
key = "moonshot.web"
display = "Moonshot web search"
auth = "bearer:$MOONSHOT_API_KEY"

[[capabilities.tools]]
name = "web_search"
description = "Web search for real-time information"
endpoint = "https://api.moonshot.cn/v1/tools/web_search"
http_method = "POST"
permission = "auto"                    # auto / confirm / dangerous
params = [
  {name="query", type="string", required=true, description="Search keywords"},
  {name="count", type="integer", default=10, description="Number of results"},
]
body_template = '{"query": {{query}}, "count": {{count}}}'
result_jsonpath = "data.results"       # optional dot-path extraction
```

Auth formats: `bearer:$ENV`, `apikey-header:HeaderName:$ENV`, `none`.
Body templates use Mustache `{{var}}` placeholders (values are JSON-encoded
before substitution).

Shell tool — wrap a command as an LLM-callable tool:

```toml
[[shell_tools]]
name = "check_port"
description = "Check whether a TCP port is open"
command_template = "nc -z -w 3 {{host}} {{port}}"
params = [
  {name="host", type="string", required=true},
  {name="port", type="integer", required=true},
]
permission = "auto"
```

Parameterized `{{name}}` values are passed as literal process arguments,
even when the placeholder is quoted. Parameterized pipelines, redirections,
shell interpreters, and executable Python code/module names are rejected;
for complex workflows call a trusted executable script with literal
arguments. Constant commands without parameters keep explicit shell
behavior.

Provider declarations (`[[providers]]`) and API-key security rules are
covered in [Configuration](../configuration.md#custom-providers).

### Layer 2 — real Python tools

Register a `ToolDefinition` with the runtime registry, or use the `@tool`
decorator (the parameter schema is derived from type hints):

```python
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    tool,
)

@tool("check_complexity", "Check cyclomatic complexity of a file",
      permission=PermissionLevel.AUTO)
async def check_complexity(path: str) -> str:
    ...
```

Inside a Skill package, expose a `register(registry)` function in `tools.py`;
tool names are automatically prefixed with the package name
(`my-review_check_complexity`):

```python
from dashscope_agenticx.tools.registry import PermissionLevel, ToolDefinition

async def check_complexity(path: str) -> str:
    ...

def register(registry):
    registry.register(ToolDefinition(
        name="check_complexity",
        description="Check the cyclomatic complexity of a file",
        func=check_complexity,
        permission=PermissionLevel.AUTO,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ))
```

The `/dev` command family scaffolds all of this interactively:
`/dev provider|capability|skill|tool add|list|rm`, `/dev model add`,
`/dev debug tools|schema|call|prompt`, `/dev test provider <name>`.
The legacy `.agenticx/plugins/` auto-loading mechanism is removed — use
Layer-1 TOML, hooks, or Layer-2 modules instead.

## Skills

Skills are preset prompt templates invoked with `/skill`. Two forms:
single-file `.md` and directory packages. At startup
`~/.config/AgenticX/skills/` and `.agenticx/skills/` are scanned; a
same-named workspace skill overrides the global one.

### Single-file skill

YAML frontmatter defines metadata; the body is the prompt template with
`{arg}` placeholder substitution:

```markdown
---
name: review
description: Code review with improvement suggestions
arguments: [file]
---
Please perform a code review of the following file, focusing on:
1. Potential bugs
2. Performance issues
3. Readability

File: {file}
```

Invoke with `/skill review src/main.py`. Single-file skills are injected
into the system prompt as a compact catalog (args, description, MCP
dependency status).

### Directory-package skill

For custom Python tools or dedicated hooks:

```text
.agenticx/skills/my-review/
├── skill.toml       # metadata, activation rules
├── prompt.md        # prompt template
├── tools.py         # optional: custom tools (prefixed my-review_<tool>)
└── hooks.toml       # optional: hooks scoped to this skill
```

```toml
# skill.toml
name = "my-review"
version = "1.0.0"
description = "Custom code review with a complexity check tool"
author = "your-name"
always_active = false
triggers = ["*review*", "*audit*"]
```

- `always_active = true`: `prompt.md` is injected every turn.
- `triggers`: glob patterns; the skill activates when the user message
  matches any pattern.

### Lifecycle

```text
/skill list                     # single files + packages
/skill reload                   # rescan directories
/skill enable|disable <name>    # force state (ignore triggers)
/skill install ./my-review      # local path / git+URL / tar.gz URL / registry name[@version]
/skill link ./my-review         # dev symlink; /skill reload picks up edits
/skill update <name>|--all
/skill uninstall <name>
/skill search review            # installed + registry
/skill publish ./my-review      # validate + package tar.gz into .dist/
```

Installation records live in `.agenticx/skills/.agenticx-skills.json`.
URL packages undergo tar path-traversal validation (including link targets)
and a 50 MB size limit.

A registry is a static TOML/JSON/YAML index — no server dependency:

```toml
# index.toml
[[skills]]
name = "code-review"
version = "1.2.0"
description = "Code review (security+style+performance)"
source = "git+https://github.com/agenticx/skills/code-review.git"
tags = ["python", "review"]
```

Point `skill_registry` in `.agenticx/config.toml` at the index URL or a
local file. Without a registry, `/skill install <name>` requires a local
path or git URL.

## Experts

**Configuration is the Agent.** An Expert is a ready-to-use `.agenticx/`
profile — persona, providers, skills, knowledge index — with no bootstrap
code. Downloading one is the recommended starting point for a new Agent:

```bash
agenticx expert                          # list templates
agenticx expert download basic-chat      # merge into ./.agenticx/
agenticx                                 # already the Expert's Agent
```

- The Expert's `.agenticx/` tree is merged into the workspace; root-level
  files (README etc.) are collected into `.agenticx/` too.
- Same-named files are backed up to `.agenticx/backup/` (single rolling
  backup); `/expert restore` undoes the merge — restoring overwritten files
  and removing added ones.
- Customize by plain-text editing: `system-prompt.md` (persona),
  `skills/*.md` (task templates), `references/*.md` (always-on knowledge,
  injected at startup), `custom-extensions.toml` (providers/tools).

Profiles download from `apps/<name>/` in
[dashscope/dashscope-agenticx-experts](https://github.com/dashscope/dashscope-agenticx-experts)
(branch `main` by default). Only the 1.0 App Catalog layout is supported.
Override the source with `expert_repo` / `expert_branch` in global config
or `--repo`.

Bundled examples: `basic-chat` (minimal general assistant with web search),
`dashscope-sdk-expert` (SDK Q&A with an auto-generated knowledge index and
`/skill update-sdk` refresh), and `bailian-expert`.

### Experts vs App Bundles

Expert downloads contain role/configuration data — not executable Python,
shell tools, or run hooks — and are **not** registered as Apps. A production
App Bundle adds a versioned `app.toml` manifest, Runtime compatibility
range, permission diffs, and checksum verification, managed with
`agenticx app list|install|info|verify|update`. `app install` previews new
permissions before writing files and registers the bundle under
`.agenticx/apps/` without activating its profile. A checksum proves content
identity, not publisher authenticity; a local trust flag is not a verified
digital signature.