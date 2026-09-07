# Downloads gyan.dev FFmpeg full shared build (Windows) for libfdk_aac support.
# Does NOT bundle binaries in repo (license-safe). Run once per machine.
# Usage: powershell -ExecutionPolicy Bypass -File tools/fetch_ffmpeg.ps1
$ErrorActionPreference = "Stop"
$outDir = Join-Path $PSScriptRoot ".." "downloads"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Write-Host "Download a FFmpeg full shared build from https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full-shared.7z"
Write-Host "Extract to $outDir\\ffmpeg-full and add bin\\ to PATH."
Write-Host "Then verify:"
Write-Host "  ffmpeg -encoders | Select-String aac"
Write-Host "  ffmpeg -encoders | Select-String libfdk_aac"
Write-Host "Expected: aac, libfdk_aac (HE/HEv2), libmp3lame available."
