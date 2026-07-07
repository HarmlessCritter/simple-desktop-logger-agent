$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
  python -c "import tkinter; root = tkinter.Tk(); root.destroy(); print('tkinter ok')"
  if ($LASTEXITCODE -ne 0) {
    throw "tkinter validation failed. Run this build from a normal host PowerShell, not the Codex sandbox."
  }

  python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name SimpleDesktopLoggerAgentDebug `
    --distpath dist `
    --workpath build `
    --specpath build `
    focus_watcher_gui.py

  if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller debug build failed."
  }
}
finally {
  Pop-Location
}
