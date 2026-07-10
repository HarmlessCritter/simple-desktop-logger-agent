$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
  $distRoot = Join-Path $PSScriptRoot "dist\SimpleDesktopLoggerAgent"
  $workRoot = Join-Path $PSScriptRoot "build\SimpleDesktopLoggerAgent"
  foreach ($generatedPath in @($distRoot, $workRoot)) {
    if (Test-Path -LiteralPath $generatedPath) {
      Remove-Item -LiteralPath $generatedPath -Recurse -Force
    }
  }

  python -m unittest discover -s tests -v
  if ($LASTEXITCODE -ne 0) {
    throw "Agent regression tests failed."
  }

  $buildStartedAt = Get-Date
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
  --hidden-import pywinauto `
  --hidden-import pywinauto.application `
  --hidden-import pywinauto.controls.uiawrapper `
  --hidden-import pywinauto.findwindows `
  --hidden-import pywinauto.uia_defines `
  --name SimpleDesktopLoggerAgent `
  --distpath dist `
  --workpath build `
  --specpath build `
  agent_server.py
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller build failed."
}

$internalPath = "dist\SimpleDesktopLoggerAgent\_internal"
$executablePath = "dist\SimpleDesktopLoggerAgent\SimpleDesktopLoggerAgent.exe"
$requiredPaths = @(
  $executablePath,
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

if ((Get-Item -LiteralPath $executablePath).LastWriteTime -lt $buildStartedAt.AddSeconds(-1)) {
  throw "Build validation failed. Executable was not regenerated for this build."
}

$warningPath = "build\SimpleDesktopLoggerAgent\warn-SimpleDesktopLoggerAgent.txt"
if ((Test-Path -LiteralPath $warningPath) -and (Select-String -Path $warningPath -Pattern "tkinter|_tkinter|broken" -Quiet)) {
  throw "Build validation failed. PyInstaller warning contains tkinter/Tcl/Tk issues."
}

Write-Host "SimpleDesktopLoggerAgent build validated."
} finally {
  Pop-Location
}
