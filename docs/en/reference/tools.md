---
description: "Built-in tools, the @tool registry, skills, apps, and runtime extensions API reference."
---
# Tools & Skills

Tools are plain Python functions registered with the `@tool` decorator;
the registry offers their schemas to the model and enforces permission
levels at execution time. Skills are markdown playbooks the agent can
load on demand; apps and experts package skills plus configuration.

For walkthroughs see
[Tools, Skills & Experts](../guides/tools-and-skills.md).

## Registry

::: dashscope_agenticx.tools.registry.tool

::: dashscope_agenticx.tools.registry.ToolRegistry

::: dashscope_agenticx.tools.registry.ToolDefinition

::: dashscope_agenticx.tools.registry.PermissionLevel

::: dashscope_agenticx.tools.registry.RuntimeToolRegistry

::: dashscope_agenticx.tools.registry.bind_tool_registry

## Built-in tools

Core tools ship with the base install:

::: dashscope_agenticx.tools.filesystem

::: dashscope_agenticx.tools.shell

::: dashscope_agenticx.tools.session

::: dashscope_agenticx.tools.checkpoint

::: dashscope_agenticx.tools.skills

::: dashscope_agenticx.tools.platform

Optional tools need extras; their modules import lazily, so the
reference below is safe to browse on a core install:

| Module | Extra |
|---|---|
| `tools.web_search` | `dashscope-agenticx[web]` |
| `tools.browser` | `dashscope-agenticx[browser]` |
| `tools.camera`, `tools.vision` | `dashscope-agenticx[media]` |

::: dashscope_agenticx.tools.web_search

::: dashscope_agenticx.tools.browser

::: dashscope_agenticx.tools.camera

::: dashscope_agenticx.tools.vision

## Skills

::: dashscope_agenticx.skills.base.Skill

::: dashscope_agenticx.skills.base.register

::: dashscope_agenticx.skills.base.load_skill_files

::: dashscope_agenticx.skills.base.register_skill_file

::: dashscope_agenticx.skills.base.list_skills

::: dashscope_agenticx.skills.base.render_skill

::: dashscope_agenticx.skills.manager.SkillManager

::: dashscope_agenticx.skills.manager.get_skill_manager

## Apps & experts

::: dashscope_agenticx.apps.manifest.AppManifest

::: dashscope_agenticx.apps.manifest.AppTrust

::: dashscope_agenticx.apps.manifest.AppPermissions

::: dashscope_agenticx.apps.manifest.load_app_manifest

::: dashscope_agenticx.apps.manifest.compute_app_checksum

::: dashscope_agenticx.apps.manifest.permission_diff

::: dashscope_agenticx.apps.store.install

::: dashscope_agenticx.apps.store.AppInstallResult

::: dashscope_agenticx.apps.store.load_registry

::: dashscope_agenticx.apps.store.runtime_compatible

## Runtime extensions

Declarative providers, tools, capabilities, and skills from
`.agenticx/custom-extensions.toml`:

::: dashscope_agenticx.extensions.CustomExtensions

::: dashscope_agenticx.extensions.CustomProvider

::: dashscope_agenticx.extensions.CustomTool

::: dashscope_agenticx.extensions.CustomCapability

::: dashscope_agenticx.extensions.CustomSkill

::: dashscope_agenticx.extensions.CustomShellTool

::: dashscope_agenticx.extensions.load_extensions