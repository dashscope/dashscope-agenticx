---
description: "Platform protocols, Bailian CLI/MCP clients, MCP server mode, and the local HTTP API reference."
---
# Platforms, MCP & Server

Platforms supply memory, knowledge, and CLI capabilities. The default
`bailian` platform wraps the Bailian CLI and MCP services; `local`
keeps everything on disk. AgenticX can also expose its own tools to
other MCP clients (`agenticx mcp-server`) and serve a token-authenticated
localhost HTTP API (`agenticx serve`).

See [Security, Platform & MCP](../guides/security-and-platform.md) for
setup and usage.

## Platform protocols

::: dashscope_agenticx.platforms.base.MemoryProvider

::: dashscope_agenticx.platforms.base.MemoryNode

::: dashscope_agenticx.platforms.base.MemoryLibrary

::: dashscope_agenticx.platforms.base.SearchResult

::: dashscope_agenticx.platforms.base.KBProvider

::: dashscope_agenticx.platforms.base.DataProvider

::: dashscope_agenticx.platforms.base.PromptProvider

## Factories

::: dashscope_agenticx.platforms.get_memory_provider

::: dashscope_agenticx.platforms.get_cli_provider

## Bailian

::: dashscope_agenticx.platforms.bailian.cli.BailianCLIClient

::: dashscope_agenticx.platforms.bailian.cli.CLIResult

::: dashscope_agenticx.platforms.bailian.cli.BailianCLIError

::: dashscope_agenticx.platforms.bailian.mcp.MCPClient

::: dashscope_agenticx.platforms.bailian.mcp.MCPError

## MCP server mode

Expose AgenticX tools over MCP stdio (`agenticx mcp-server`):

::: dashscope_agenticx.mcp_server.tools_to_mcp_schema

::: dashscope_agenticx.mcp_server.handle_mcp_request

::: dashscope_agenticx.mcp_server.run_stdio_server

::: dashscope_agenticx.mcp_server.main

::: dashscope_agenticx.mcp_stdio.StdioMCPClient

## Local HTTP API

`agenticx serve` binds to localhost only and requires a bearer token;
non-localhost bind addresses are rejected.

::: dashscope_agenticx.server.LocalAPI

::: dashscope_agenticx.server.create_server

::: dashscope_agenticx.server.serve

::: dashscope_agenticx.server.generate_server_token