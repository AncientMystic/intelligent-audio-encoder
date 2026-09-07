# Fetches legal standalone encoders (no nonfree FFmpeg build needed).
# BtbN nonfree (libfdk_aac in ffmpeg) is UNDISTRIBUTABLE - must build locally, not needed here.
# exhale (xHE-AAC mono) + fdkaac (HE/LC stereo) cover all tiers via WAV pipe.
# Usage: powershell -ExecutionPolicy Bypass -File tools/fetch_tools.ps1
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path "tools/bin" | Out-Null
if (!(Test-Path "tools/bin/exhale/exhale.exe")) {
  Invoke-WebRequest -Uri "https://www.rarewares.org/files/aac/exhale-v1.2.2-x64.zip" -OutFile "tools/bin/exhale.zip"
  Expand-Archive -Force "tools/bin/exhale.zip" -DestinationPath "tools/bin/exhale"
}
if (!(Test-Path "tools/bin/fdkaac/fdkaac.exe")) {
  Invoke-WebRequest -Uri "https://www.rarewares.org/files/aac/fdkaac-1.0.5-x64.zip" -OutFile "tools/bin/fdkaac.zip"
  Expand-Archive -Force "tools/bin/fdkaac.zip" -DestinationPath "tools/bin/fdkaac"
}
& tools/bin/exhale/exhale.exe -h 2>&1 | Select-Object -First 3
& tools/bin/fdkaac/fdkaac.exe --help 2>&1 | Select-Object -First 3
ffmpeg -hide_banner -encoders 2>&1 | Select-String "aac|libmp3lame" | ForEach-Object { $_.Line.Trim() }
Write-Host "OK: ffmpeg GPL + exhale + fdkaac ready. No compile needed."
