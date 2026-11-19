---
description: "平台协议、百炼 CLI/MCP 客户端、MCP 服务端模式与本地 HTTP API 参考。"
---
# 平台、MCP 与本地服务

平台提供记忆、知识与 CLI 能力。默认的 `bailian` 平台封装百炼 CLI 与
MCP 服务；`local` 将一切保存在磁盘上。AgenticX 也可以向其他 MCP
客户端暴露自身工具（`agenticx mcp-server`），并提供基于 token 认证的
localhost HTTP API（`agenticx serve`）。

安装与使用参见[安全、平台与 MCP](../guides/security-and-platform.md)。

## 平台协议

::: dashscope_agenticx.platforms.base.MemoryProvider

::: dashscope_agenticx.platforms.base.MemoryNode

::: dashscope_agenticx.platforms.base.MemoryLibrary

::: dashscope_agenticx.platforms.base.SearchResult

::: dashscope_agenticx.platforms.base.KBProvider

::: dashscope_agenticx.platforms.base.DataProvider

::: dashscope_agenticx.platforms.base.PromptProvider

## 工厂函数

::: dashscope_agenticx.platforms.get_memory_provider

::: dashscope_agenticx.platforms.get_cli_provider

## 百炼（Bailian） { #bailian }

::: dashscope_agenticx.platforms.bailian.cli.BailianCLIClient

::: dashscope_agenticx.platforms.bailian.cli.CLIResult

::: dashscope_agenticx.platforms.bailian.cli.BailianCLIError

::: dashscope_agenticx.platforms.bailian.mcp.MCPClient

::: dashscope_agenticx.platforms.bailian.mcp.MCPError

## MCP 服务端模式

通过 MCP stdio 暴露 AgenticX 工具（`agenticx mcp-server`）：

::: dashscope_agenticx.mcp_server.tools_to_mcp_schema

::: dashscope_agenticx.mcp_server.handle_mcp_request

::: dashscope_agenticx.mcp_server.run_stdio_server

::: dashscope_agenticx.mcp_server.main

::: dashscope_agenticx.mcp_stdio.StdioMCPClient

## 本地 HTTP API

`agenticx serve` 仅绑定 localhost 并要求 bearer token；非 localhost
的绑定地址会被拒绝。

::: dashscope_agenticx.server.LocalAPI

::: dashscope_agenticx.server.create_server

::: dashscope_agenticx.server.serve

::: dashscope_agenticx.server.generate_server_token