#!/bin/bash
# Install the artifact produced by scripts/build.sh.
#
# build.sh now uses PyInstaller --onedir, so dist/agenticx is a *directory*
# (faster startup than --onefile, no per-run unzip). We install the whole
# directory to LIB_DIR and symlink the inner binary into BIN_DIR so users
# can still run `agenticx` from the shell.
#
# Defaults: LIB_DIR=/usr/local/lib/agenticx, BIN_DIR=/usr/local/bin (sudo if
# not writable). Override either with the matching env var, e.g.:
#   LIB_DIR=~/.local/lib/agenticx BIN_DIR=~/.local/bin bash scripts/install-local.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$PROJECT_DIR/dist/agenticx"
LIB_DIR="${LIB_DIR:-/usr/local/lib/agenticx}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"

# ---------- OS detection ----------
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) OS="windows" ;;
    *)                     OS="$(uname -s | tr '[:upper:]' '[:lower:]')" ;;
esac

if [ "$OS" = "windows" ]; then
    EXE_NAME="agenticx.exe"
else
    EXE_NAME="agenticx"
fi

if [ ! -d "$SRC_DIR" ] || [ ! -f "$SRC_DIR/$EXE_NAME" ]; then
    echo "PyInstaller --onedir output not found at: $SRC_DIR"
    echo "Run 'bash scripts/build.sh' first."
    exit 1
fi

# Pick sudo only when needed (skip on Windows).
SUDO=""
if [ "$OS" != "windows" ]; then
    _writable_or_sudo() {
        local target="$1"
        local parent
        parent="$(dirname "$target")"
        [ -w "$parent" ] || [ -w "$target" ] 2>/dev/null
    }
    if ! _writable_or_sudo "$LIB_DIR" || ! _writable_or_sudo "$BIN_DIR"; then
        echo "==> Target dirs not writable, using sudo..."
        SUDO="sudo"
    fi
fi

echo "==> Installing onedir bundle to $LIB_DIR..."
$SUDO rm -rf "$LIB_DIR"
$SUDO mkdir -p "$(dirname "$LIB_DIR")"
$SUDO cp -R "$SRC_DIR" "$LIB_DIR"

if [ "$OS" = "windows" ]; then
    # Windows: copy exe into BIN_DIR (no symlink support in most setups)
    echo "==> Copying $BIN_DIR/$EXE_NAME -> $LIB_DIR/$EXE_NAME..."
    mkdir -p "$BIN_DIR"
    cp "$LIB_DIR/$EXE_NAME" "$BIN_DIR/$EXE_NAME"
else
    echo "==> Symlinking $BIN_DIR/agenticx -> $LIB_DIR/agenticx..."
    $SUDO mkdir -p "$BIN_DIR"
    $SUDO ln -sf "$LIB_DIR/agenticx" "$BIN_DIR/agenticx"
fi

echo "==> Installed:"
echo "    bundle : $LIB_DIR"
echo "    launch : $BIN_DIR/$EXE_NAME"
echo "==> Verify: $(command -v agenticx 2>/dev/null || echo "$BIN_DIR/$EXE_NAME (ensure $BIN_DIR is on PATH)")"
