---
description: "Evaluation harness and control-flow exceptions API reference."
---
# Eval

The eval harness runs scripted cases against the agent and compares
providers. Cases load from YAML/JSON suites; results carry per-case
scores and transcripts.

## Harness

::: dashscope_agenticx.eval.EvalCase

::: dashscope_agenticx.eval.EvalResult

::: dashscope_agenticx.eval.EvalRunner

::: dashscope_agenticx.eval.ProviderComparator

::: dashscope_agenticx.eval.load_eval_suite

## Exceptions

AgenticX surfaces model and tool failures as `error` events in the
[agent event stream](agent.md#event-types), not as a unified exception
tree. The few exceptions that do exist are control-flow signals or
platform-specific:

- `UserAbortedTurn` / `UserSupplement` — interactive turn control.
- `BailianCLIError` / `MCPError` — Bailian platform failures
  ([Platforms](platforms.md#bailian)).
- `RetriesExhausted` — provider hardening gave up
  ([Providers](providers.md#hardening)).

::: dashscope_agenticx.utils.exceptions.UserAbortedTurn

::: dashscope_agenticx.utils.exceptions.UserSupplement