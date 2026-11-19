---
description: "内置工具、@tool 注册表、技能、应用与运行时扩展 API 参考。"
---
# 工具与技能

工具是通过 `@tool` 装饰器注册的普通 Python 函数；注册表向模型提供其
Schema，并在执行时强制权限级别。Skill 是 Agent 可按需加载的 Markdown
操作手册；App 与 Expert 将技能与配置打包在一起。

演练参见[工具、技能与专家](../guides/tools-and-skills.md)。

## 注册表

::: dashscope_agenticx.tools.registry.tool

::: dashscope_agenticx.tools.registry.ToolRegistry

::: dashscope_agenticx.tools.registry.ToolDefinition

::: dashscope_agenticx.tools.registry.PermissionLevel

::: dashscope_agenticx.tools.registry.RuntimeToolRegistry

::: dashscope_agenticx.tools.registry.bind_tool_registry

## 内置工具

核心工具随基础安装一起提供：

::: dashscope_agenticx.tools.filesystem

::: dashscope_agenticx.tools.shell

::: dashscope_agenticx.tools.session

::: dashscope_agenticx.tools.checkpoint

::: dashscope_agenticx.tools.skills

::: dashscope_agenticx.tools.platform

可选工具需要安装扩展；其模块采用惰性导入，因此在核心安装下浏览下方
参考也是安全的：

| 模块 | 扩展 |
|---|---|
| `tools.web_search` | `dashscope-agenticx[web]` |
| `tools.browser` | `dashscope-agenticx[browser]` |
| `tools.camera`, `tools.vision` | `dashscope-agenticx[media]` |

::: dashscope_agenticx.tools.web_search

::: dashscope_agenticx.tools.browser

::: dashscope_agenticx.tools.camera

::: dashscope_agenticx.tools.vision

## 技能

::: dashscope_agenticx.skills.base.Skill

::: dashscope_agenticx.skills.base.register

::: dashscope_agenticx.skills.base.load_skill_files

::: dashscope_agenticx.skills.base.register_skill_file

::: dashscope_agenticx.skills.base.list_skills

::: dashscope_agenticx.skills.base.render_skill

::: dashscope_agenticx.skills.manager.SkillManager

::: dashscope_agenticx.skills.manager.get_skill_manager

## 应用与专家

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

## 运行时扩展

来自 `.agenticx/custom-extensions.toml` 的声明式 Provider、工具、
能力与技能：

::: dashscope_agenticx.extensions.CustomExtensions

::: dashscope_agenticx.extensions.CustomProvider

::: dashscope_agenticx.extensions.CustomTool

::: dashscope_agenticx.extensions.CustomCapability

::: dashscope_agenticx.extensions.CustomSkill

::: dashscope_agenticx.extensions.CustomShellTool

::: dashscope_agenticx.extensions.load_extensions