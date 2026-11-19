---
description: "Develop and contribute to DashScope AgenticX: environment setup, tests, lint, and the release contract."
---
# Developer Guide

## Environment

Use Python **3.10–3.13**. Install the package editable with the development
and optional-feature extras:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,all]"
python -m pip install pre-commit
```

`all` omits Pillow on Python 3.10 (the Pillow-backed `vision`/`media` extras
require 3.11+). Core dependencies are intentionally small; new heavy
dependencies belong in extras.

## Tests and lint

```bash
python -m pytest tests/ -q       # unit + e2e suite
pre-commit run --all-files       # Black, isort, flake8, encoding pragmas, …
```

Run Black, isort, and flake8 before submitting. The pre-commit config also
enforces UTF-8 encoding pragmas, docstring-first ordering, and trailing-
whitespace cleanup. `mkdocs.yml` uses `!!python/name:` tags, so the
`check-yaml` hook runs with `--unsafe`.

### Test layout

- `tests/ut/` — unit tests, including `test_docs_consistency.py`, which
  guards the root README/user-guide/migration/CHANGELOG contracts.
- `tests/e2e/` — end-to-end runs against a fake provider
  (`tests/e2e/fake_provider.py`), so the suite exercises the agent loop
  offline without real cloud model calls.

## Documentation site

The bilingual docs site (this site) builds with MkDocs Material:

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install -r requirements-doc.txt -e .
mkdocs serve                     # live preview at http://127.0.0.1:8000
mkdocs build --strict            # CI-equivalent build (warnings are errors)
```

English pages live at `docs/en/` (site root), Chinese at `docs/zh/`
(`/zh/`). Reference pages use mkdocstrings `:::` identifiers resolved
against `src/`; keep the en/zh `:::` blocks identical and translate only
headings and prose. The version placeholder `{{ agenticx_version }}` is
injected from `src/dashscope_agenticx/_version.py` by `docs/hooks.py`.

CI (`.github/workflows/docs.yml`) builds with `--strict` on pull requests
and deploys to GitHub Pages on pushes to `main`.

## Contribution rules

1. Use Python 3.10–3.13.
2. Create focused changes with tests.
3. Run `pytest -q`, Black, isort, and flake8 before submitting.
4. Keep Core dependencies small; new heavy dependencies belong in extras.
5. Preserve the typed Event, Runtime, App Manifest, and Session contracts.
6. Security-sensitive changes require negative tests for untrusted input.

## Identity contract

The canonical distribution and protocol/package identity is
`dashscope-agenticx`, the import namespace is `dashscope_agenticx`, and the
only official command is `agenticx` (no official short alias). Workspaces
use `.agenticx/`, environment variables use `AGENTICX_*`, and the global app
name is `AgenticX`. Do not introduce the unrelated bare `agenticx` PyPI
distribution.

## Release contract

```bash
python scripts/release_check.py
```

The release check validates package identity, version, imports, CLI, and
bundled Apps. Coverage gates, cross-platform CI (Python 3.10–3.13;
Ubuntu/macOS/Windows smoke checks), App validation, and supply-chain checks
are separate release requirements — passing local unit tests alone does not
publish or certify a release. Tag builds prepare wheel/source distributions
and platform binaries with SHA-256 checksums in a draft GitHub Release for
final review. See the repository `RELEASE.md` for packaging, checksums,
SBOM, and publication gates.