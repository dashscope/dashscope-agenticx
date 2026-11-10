# agenticx — PowerShell one-line installer for Windows.
#
# Usage (public repo):
#   irm https://raw.githubusercontent.com/dashscope/dashscope-agenticx/main/scripts/install.ps1 | iex
#
# Override paths:
#   $env:LIB_DIR = "$env:LOCALAPPDATA\agenticx"
#   $env:BIN_DIR = "$env:LOCALAPPDATA\agenticx\bin"

$ErrorActionPreference = "Stop"

$Repo = "dashscope/dashscope-agenticx"
$LibDir = if ($env:LIB_DIR) { $env:LIB_DIR } else { Join-Path $env:LOCALAPPDATA "agenticx" }
$BinDir = if ($env:BIN_DIR) { $env:BIN_DIR } else { Join-Path $env:LOCALAPPDATA "agenticx\bin" }

$Arch = switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture) {
    "X64"   { "x86_64" }
    "Arm64" { "arm64" }
    default {
        Write-Error "Unsupported architecture: $_"
        return
    }
}

$Asset = "agenticx-windows-$Arch.tar.gz"
$DownloadUrl = "https://github.com/$Repo/releases/latest/download/$Asset"

$TmpDir = Join-Path $env:TEMP ("agenticx-install-" + [guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Force $TmpDir | Out-Null

    Write-Host "==> Downloading $Asset..."
    $TarPath = Join-Path $TmpDir $Asset
    try {
        Invoke-WebRequest -Uri $DownloadUrl -OutFile $TarPath -UseBasicParsing
    } catch {
        Write-Host ""
        Write-Host "==> Download failed."
        Write-Host "    Use one of these alternatives:"
        Write-Host "    1. Clone and install: git clone https://github.com/dashscope/dashscope-agenticx.git"
        Write-Host "    2. Clone and pip install: git clone ... && pip install -e ."
        Write-Host "    3. Download artifact from CI pipeline manually"
        return
    }

    # Check we didn't get an HTML error page
    $firstLine = Get-Content $TarPath -TotalCount 1 -ErrorAction SilentlyContinue
    if ($firstLine -match '<!DOCTYPE|<html') {
        Write-Host ""
        Write-Host "==> Got HTML instead of tarball."
        Write-Host "    GitHub returned HTML instead of a release artifact."
        Write-Host "    One-line install is not supported for private repos on 阿里 code 平台."
        Write-Host ""
        Write-Host "    Alternatives:"
        Write-Host "    1. Clone and install: git clone https://github.com/dashscope/dashscope-agenticx.git"
        Write-Host "    2. Clone and pip install: git clone ... && pip install -e ."
        Write-Host "    3. Download artifact from CI pipeline manually"
        return
    }

    Write-Host "==> Extracting..."
    tar -xzf $TarPath -C $TmpDir
    $ExePath = Join-Path $TmpDir "agenticx\agenticx.exe"
    if (-not (Test-Path $ExePath)) {
        Write-Error "Bad tarball: agenticx\agenticx.exe not found"
        return
    }

    # Install bundle
    Write-Host "==> Installing bundle -> $LibDir..."
    if (Test-Path $LibDir) { Remove-Item -Recurse -Force $LibDir }
    New-Item -ItemType Directory -Force (Split-Path $LibDir) | Out-Null
    Copy-Item -Recurse -Force (Join-Path $TmpDir "agenticx") $LibDir

    # Copy launcher into BinDir
    New-Item -ItemType Directory -Force $BinDir | Out-Null
    $BinExe = Join-Path $BinDir "agenticx.exe"
    Copy-Item -Force (Join-Path $LibDir "agenticx.exe") $BinExe
    Write-Host "==> Installed: $BinExe"

    # Add BinDir to user PATH if not already present
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$BinDir", "User")
        Write-Host "==> Added $BinDir to user PATH (takes effect in new terminals)"
    }

    # Also add to current session PATH so `agenticx` works immediately
    $env:Path = "$BinDir;$env:Path"

    Write-Host "==> Done! Run 'agenticx' to get started."
}
finally {
    if (Test-Path $TmpDir) { Remove-Item -Recurse -Force $TmpDir -ErrorAction SilentlyContinue }
}
