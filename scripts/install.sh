#!/bin/bash
# agenticx — one-line installer.
#
# Downloads the latest GitHub Release artifact, extracts the PyInstaller
# bundle, and installs it to the system path.
#
# Usage: bash install.sh
#
# Override paths:
#   LIB_DIR=~/.local/lib/agenticx BIN_DIR=~/.local/bin \
#     bash install.sh
set -e

GITHUB_REPO="dashscope/dashscope-agenticx"
LIB_DIR="${LIB_DIR:-/usr/local/lib/agenticx}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$ARCH" in
    x86_64)  ARCH="x86_64" ;;
    arm64|aarch64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

case "$OS" in
    darwin)         PLATFORM="macos-${ARCH}" ;;
    linux)          PLATFORM="linux-${ARCH}" ;;
    mingw*|msys*|cygwin*)
                    PLATFORM="windows-${ARCH}"
                    LIB_DIR="${LIB_DIR:-$LOCALAPPDATA/agenticx}"
                    BIN_DIR="${BIN_DIR:-$LOCALAPPDATA/agenticx/bin}" ;;
    *) echo "Unsupported OS: $OS"; exit 1 ;;
esac

if echo "$OS" | grep -qE 'mingw|msys|cygwin'; then
    EXE_NAME="agenticx.exe"
else
    EXE_NAME="agenticx"
fi

TMPDIR="$(mktemp -d)"
trap "rm -rf '$TMPDIR'" EXIT

ASSET="agenticx-${PLATFORM}.tar.gz"
ONEDIR_TAR="${TMPDIR}/${ASSET}"
DOWNLOAD_URL="https://github.com/${GITHUB_REPO}/releases/latest/download/${ASSET}"
echo "==> Downloading ${DOWNLOAD_URL}..."
curl -fsSL -L "$DOWNLOAD_URL" -o "$ONEDIR_TAR"

tar -xzf "$ONEDIR_TAR" -C "$TMPDIR"

if [ ! -f "${TMPDIR}/agenticx/${EXE_NAME}" ]; then
    echo "错误：解压后未找到 ${TMPDIR}/agenticx/${EXE_NAME}"
    exit 1
fi

# Pick sudo only when needed (skip on Windows)
SUDO=""
if ! echo "$OS" | grep -qE 'mingw|msys|cygwin'; then
    _writable() { [ -w "$1" ] || [ -w "$(dirname "$1")" ]; }
    if ! _writable "$LIB_DIR" || ! _writable "$BIN_DIR"; then
        echo "==> Target dirs not writable, using sudo..."
        SUDO="sudo"
    fi
fi

echo "==> Installing bundle → ${LIB_DIR}..."
$SUDO rm -rf "$LIB_DIR"
$SUDO mkdir -p "$(dirname "$LIB_DIR")"
$SUDO cp -R "${TMPDIR}/agenticx" "$LIB_DIR"

if echo "$OS" | grep -qE 'mingw|msys|cygwin'; then
    echo "==> Copying ${BIN_DIR}/${EXE_NAME} ← ${LIB_DIR}/${EXE_NAME}..."
    mkdir -p "$BIN_DIR"
    cp "${LIB_DIR}/${EXE_NAME}" "${BIN_DIR}/${EXE_NAME}"
    if ! echo "$PATH" | tr ':' '\n' | grep -qF "$BIN_DIR"; then
        echo "==> Adding $BIN_DIR to PATH for current session..."
        export PATH="$BIN_DIR:$PATH"
        echo "    (To persist, add $BIN_DIR to your system PATH)"
    fi
else
    echo "==> Symlinking ${BIN_DIR}/agenticx → ${LIB_DIR}/agenticx..."
    $SUDO mkdir -p "$BIN_DIR"
    $SUDO ln -sf "${LIB_DIR}/agenticx" "${BIN_DIR}/agenticx"
fi

if [ "$OS" = "darwin" ]; then
    $SUDO xattr -dr com.apple.quarantine "$LIB_DIR" 2>/dev/null || true
fi

echo "==> Done! Run 'agenticx' to get started."
echo "    ($(command -v agenticx 2>/dev/null || echo "${BIN_DIR}/${EXE_NAME}; ensure ${BIN_DIR} is on PATH"))"
