# Build a self-contained BPA Portal installer ZIP for Windows.
#   .\build.ps1                # full build + package (exe ZIP + universal wheel)
#   .\build.ps1 -NoPackage     # binary only, skip the ZIP step
#   .\build.ps1 -WheelOnly     # universal wheel only
#
# Prereqs: Python 3.9+ on PATH.
#
# NOTE: PyInstaller does not cross-compile. The .exe produced here runs only on
# Windows. For one artifact that installs everywhere, use -WheelOnly.

param(
  [switch]$NoPackage,
  [switch]$WheelOnly
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$AppName = "BPA Portal"
$Version = if ($env:BPA_VERSION) { $env:BPA_VERSION } else { "1.1.0" }
$VenvDir = if ($env:BPA_VENV) { $env:BPA_VENV } else { ".venv" }

# Real host architecture - do not hardcode into artifact names.
$Arch = switch ($env:PROCESSOR_ARCHITECTURE) {
  "AMD64" { "x64" }
  "ARM64" { "arm64" }
  "x86"   { "x86" }
  default { $env:PROCESSOR_ARCHITECTURE.ToLower() }
}
Write-Host "==> Host OS: windows ($Arch)"

if (-not (Test-Path $VenvDir)) {
  Write-Host "==> Creating virtualenv at $VenvDir"
  python -m venv $VenvDir
}

$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
. $Activate

Write-Host "==> Installing dependencies"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt pyinstaller build

if (-not (Test-Path release)) { New-Item -ItemType Directory -Path release | Out-Null }

function Build-Wheel {
  Write-Host "==> Building universal wheel (py3-none-any)"
  Get-ChildItem dist -Filter *.whl -ErrorAction SilentlyContinue | Remove-Item -Force
  python -m build --outdir dist | Out-Null
  $whl = Get-ChildItem dist -Filter *.whl | Select-Object -First 1
  if ($whl.Name -notlike "*-py3-none-any.whl") {
    throw "Wheel is not universal: $($whl.Name)"
  }
  Copy-Item $whl.FullName release\
  Get-ChildItem dist -Filter *.tar.gz -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName release\ }
  Write-Host "    -> release\$($whl.Name)  (installs on macOS/Windows/Linux, any arch)"
}

if ($WheelOnly) {
  Build-Wheel
  Write-Host "Done."
  exit 0
}

Write-Host "==> Running PyInstaller"
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }
pyinstaller --clean --noconfirm bpa.spec | Out-Null
Write-Host "    Built: $(Get-ChildItem dist | ForEach-Object Name)"

if ($NoPackage) {
  Write-Host "==> Skipping packaging. Artifacts in .\dist\"
  exit 0
}

Get-ChildItem release | Remove-Item -Recurse -Force
Build-Wheel

$Exe = "dist\$AppName.exe"
if (-not (Test-Path $Exe)) { throw "Missing $Exe" }

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) "BPA-Portal-$([Guid]::NewGuid())"
New-Item -ItemType Directory -Path $Stage | Out-Null
Copy-Item $Exe (Join-Path $Stage "$AppName.exe")

@"
BPA Portal - Install

1. Extract the entire ZIP somewhere stable (e.g. C:\Program Files\BPA Portal\).
2. Double-click "BPA Portal.exe".
   Windows SmartScreen will warn the first time (unsigned binary):
     Click "More info" -> "Run anyway".
3. The app opens your browser to http://127.0.0.1:5057/

If the browser does not open, the address is printed in the console window
and saved to:
  %LOCALAPPDATA%\PaloAltoNetworks\BPA Portal\portal-url.txt

Data is stored at:  %LOCALAPPDATA%\PaloAltoNetworks\BPA Portal\
Close the console window to stop the server.

Optional Start-Menu shortcut: right-click the .exe -> Pin to Start.
"@ | Out-File -FilePath (Join-Path $Stage "Quickstart.txt") -Encoding UTF8

$Zip = "release\BPA-Portal-$Version-windows-$Arch.zip"
Write-Host "==> Creating $Zip"
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -Force
Remove-Item -Recurse -Force $Stage
Write-Host "    -> $Zip"
Write-Host "Done."
