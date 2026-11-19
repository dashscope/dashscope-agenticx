---
description: "评测框架与控制流异常 API 参考。"
---
# 评测

评测框架针对 Agent 运行脚本化用例并比较各 Provider。用例从 YAML/JSON
套件加载；结果携带逐用例的分数与对话记录。

## 评测框架

::: dashscope_agenticx.eval.EvalCase

::: dashscope_agenticx.eval.EvalResult

::: dashscope_agenticx.eval.EvalRunner

::: dashscope_agenticx.eval.ProviderComparator

::: dashscope_agenticx.eval.load_eval_suite

## 异常 { #exceptions }

AgenticX 将模型与工具失败以 `error` 事件的形式呈现在
[Agent 事件流](agent.md#event-types)中，而非统一的异常体系。少数确实
存在的异常属于控制流信号或平台专属异常：

- `UserAbortedTurn` / `UserSupplement` — 交互式轮次控制。
- `BailianCLIError` / `MCPError` — 百炼平台故障
  （[平台](platforms.md#bailian)）。
- `RetriesExhausted` — Provider 加固层放弃重试
  （[Provider](providers.md#hardening)）。

::: dashscope_agenticx.utils.exceptions.UserAbortedTurn

::: dashscope_agenticx.utils.exceptions.UserSupplement