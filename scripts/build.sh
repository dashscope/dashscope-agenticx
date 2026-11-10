#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# ---------- OS detection ----------
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) OS="windows" ;;
    *)                     OS="$(uname -s | tr '[:upper:]' '[:lower:]')" ;;
esac

# ---------- venv ----------
# Prefer the project's local venv so PEP-668-protected system Pythons
# don't block `pip install`. Auto-create one if missing.
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating venv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

# Windows venv uses Scripts/ instead of bin/
if [ "$OS" = "windows" ]; then
    PYTHON="$VENV_DIR/Scripts/python"
    PIP="$VENV_DIR/Scripts/pip"
    EXE_NAME="agenticx.exe"
else
    PYTHON="$VENV_DIR/bin/python"
    PIP="$VENV_DIR/bin/pip"
    EXE_NAME="agenticx"
fi

echo "==> Installing build dependencies into $VENV_DIR..."
$PIP install --upgrade pip build >/dev/null
# setuptools is bundled with pip, so no build-isolation workaround needed
# even when the Aliyun mirror lacks hatchling.
$PIP install -e ".[build,all]"

echo "==> Building wheel + sdist (PEP 517 via setuptools)..."
# `python -m build` reads pyproject.toml [build-system] and produces both
# dashscope_agenticx-<version>-py3-none-any.whl and
# dashscope_agenticx-<version>.tar.gz under dist/.
# --no-isolation avoids hitting the Aliyun mirror for build deps since
# setuptools is already installed in the venv.
$PYTHON -m build --wheel --sdist --no-isolation --outdir dist

echo "==> Building single-file binary..."
# --collect-submodules dashscope_agenticx is the durable knob: scans every
# subpackage (tools/, providers/, platforms/, skills/, dev) so we don't have
# to maintain a hand-curated --hidden-import list that rots whenever a
# module is renamed.
# --collect-all dashscope is still needed: dashscope ships data files
# (templates, configs) that PyInstaller's static import analysis
# wouldn't otherwise package.
# --log-level INFO (was WARN) so CI logs capture WHY pyinstaller might
# silently skip files; warnings show whatever stays out of the bundle.
$PYTHON -m PyInstaller \
    --onedir \
    --name agenticx \
    --collect-submodules dashscope_agenticx \
    --collect-all dashscope \
    --hidden-import anthropic \
    --hidden-import openai \
    --log-level INFO \
    --clean \
    -y \
    src/dashscope_agenticx/__main__.py

# Fail-fast: if PyInstaller silently produced an empty dist/agenticx (e.g.
# bootloader missing, no gcc / binutils, sandbox blocked the temp exec),
# the build looks "successful" to set -e but the artifact is useless.
# Assert the launcher binary actually lives where install.sh + the tar.gz
# step expects.
if [ ! -f "dist/agenticx/${EXE_NAME}" ]; then
    echo "==> ERROR: PyInstaller didn't produce dist/agenticx/${EXE_NAME}. Check CI log for the actual error."
    echo "==> Common causes:"
    echo "      * Build image missing gcc / binutils (PyInstaller bootloader needs them on Linux)"
    echo "      * Build image's Python differs from agenticx's requires-python"
    echo "      * tmpfs / noexec on /tmp blocking PyInstaller's bootloader test"
    ls -la dist/ 2>/dev/null
    ls -la dist/agenticx/ 2>/dev/null
    exit 1
fi

# Show only the artifacts for the current version so old dist/ builds don't clutter the summary.
VERSION="$($PYTHON -c 'from dashscope_agenticx import __version__; print(__version__)')"

echo "==> Build complete (v${VERSION}):"
ls -lh "dist/agenticx/${EXE_NAME}" "dist/dashscope_agenticx-${VERSION}-py3-none-any.whl" "dist/dashscope_agenticx-${VERSION}.tar.gz" 2>/dev/null
