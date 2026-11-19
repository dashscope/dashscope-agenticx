---
description: "开发 DashScope AgenticX 并参与贡献：环境搭建、测试、代码检查与发布契约。"
---
# 开发者指南

## 环境

使用 Python **3.10–3.13**。以可编辑模式安装包，并带上开发与可选功能
扩展：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,all]"
python -m pip install pre-commit
```

在 Python 3.10 上，`all` 不包含 Pillow（基于 Pillow 的 `vision`/`media`
扩展需要 3.11+）。核心依赖刻意保持精简；新的重量级依赖应放入扩展。

## 测试与代码检查

```bash
python -m pytest tests/ -q       # unit + e2e suite
pre-commit run --all-files       # Black, isort, flake8, encoding pragmas, …
```

提交前请运行 Black、isort 与 flake8。pre-commit 配置还会强制 UTF-8
编码声明、docstring 优先的顺序，以及行尾空白清理。`mkdocs.yml` 使用了
`!!python/name:` 标签，因此 `check-yaml` Hook 以 `--unsafe` 运行。

### 测试布局

- `tests/ut/` — 单元测试，包括 `test_docs_consistency.py`，用于守护
  根目录 README/user-guide/migration/CHANGELOG 的契约。
- `tests/e2e/` — 针对假 Provider（`tests/e2e/fake_provider.py`）的
  端到端运行，使测试套件可以在离线状态下演练 Agent 循环，而无需真实
  的云端模型调用。

## 文档站点

双语文档站点（即本站点）使用 MkDocs Material 构建：

```bash
python -m venv .venv-docs
source .venv-docs/bin/activate
python -m pip install -r requirements-doc.txt -e .
mkdocs serve                     # live preview at http://127.0.0.1:8000
mkdocs build --strict            # CI-equivalent build (warnings are errors)
```

英文页面位于 `docs/en/`（站点根路径），中文页面位于 `docs/zh/`
（`/zh/`）。参考页面使用 mkdocstrings 的 `:::` 标识符，从 `src/` 解析；
请保持 en/zh 的 `:::` 块完全一致，仅翻译标题与正文。版本占位符
`{{ agenticx_version }}` 由 `docs/hooks.py` 从
`src/dashscope_agenticx/_version.py` 注入。

CI（`.github/workflows/docs.yml`）在 Pull Request 上以 `--strict`
构建，并在推送到 `main` 时部署到 GitHub Pages。

## 贡献规则

1. 使用 Python 3.10–3.13。
2. 提交聚焦的变更并附带测试。
3. 提交前运行 `pytest -q`、Black、isort 与 flake8。
4. 保持核心依赖精简；新的重量级依赖应放入扩展。
5. 维护带类型的 Event、Runtime、App Manifest 与 Session 契约。
6. 涉及安全的变更需要针对不可信输入的负向测试。

## 标识契约

规范的发行包与协议/包标识为 `dashscope-agenticx`，导入命名空间为
`dashscope_agenticx`，唯一的官方命令是 `agenticx`（无官方短别名）。
工作区使用 `.agenticx/`，环境变量使用 `AGENTICX_*`，全局应用名为
`AgenticX`。不要引入无关的 `agenticx` PyPI 发行包。

## 发布契约

```bash
python scripts/release_check.py
```

发布检查会验证包标识、版本、导入、CLI 与内置 App。覆盖率门槛、
跨平台 CI（Python 3.10–3.13；Ubuntu/macOS/Windows 冒烟检查）、App
校验与供应链检查是独立的发布要求 — 仅通过本地单元测试并不构成发布
或认证。Tag 构建会准备 wheel/源码发行包与平台二进制文件，并附带
SHA-256 校验和，放入草稿 GitHub Release 供最终审核。打包、校验和、
SBOM 与发布门槛参见仓库中的 `RELEASE.md`。