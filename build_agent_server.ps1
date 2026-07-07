$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
python -c "import tkinter; root = tkinter.Tk(); root.destroy(); print('tkinter ok')"
if ($LASTEXITCODE -ne 0) {
  throw "tkinter check failed. Run this build with host-level execution after verifying Python/Tcl/Tk."
}

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --hidden-import tkinter `
  --hidden-import tkinter.font `
  --name SimpleDesktopLoggerAgent `
  --distpath dist `
  --workpath build `
  --specpath build `
  agent_server.py
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed."
}

$internalPath = "dist\SimpleDesktopLoggerAgent\_internal"
$requiredPaths = @(
  "$internalPath\_tkinter.pyd",
  "$internalPath\tcl86t.dll",
  "$internalPath\tk86t.dll",
  "$internalPath\_tcl_data",
  "$internalPath\_tk_data"
)

foreach ($path in $requiredPaths) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Build validation failed. Missing required Tk/Tcl artifact: $path"
  }
}

$warningPath = "build\SimpleDesktopLoggerAgent\warn-SimpleDesktopLoggerAgent.txt"
if ((Test-Path -LiteralPath $warningPath) -and (Select-String -Path $warningPath -Pattern "tkinter|_tkinter|broken" -Quiet)) {
  throw "Build validation failed. PyInstaller warning contains tkinter/Tcl/Tk issues."
}

Write-Host "SimpleDesktopLoggerAgent build validated."
} finally {
  Pop-Location
}
