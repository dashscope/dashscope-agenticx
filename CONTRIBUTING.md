# Contributing

1. Use Python 3.10-3.13.
2. Create focused changes with tests.
3. Run `pytest -q`, Black, isort, and flake8 before submitting.
4. Keep Core dependencies small; new heavy dependencies belong in extras.
5. Preserve the typed Event, Runtime, App Manifest, and Session contracts.
6. Security-sensitive changes require negative tests for untrusted input.

The canonical distribution and protocol/package identity are
`dashscope-agenticx`, the import namespace is `dashscope_agenticx`, and the only
official command is `agenticx` (there is no official short alias). Workspaces
use `.agenticx/`, environment variables use `AGENTICX_*`, and the global app
name is `AgenticX`.
