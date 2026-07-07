# Simple Desktop Logger Agent

Public Windows agent for Simple Desktop Logger.

## What It Does

- Tracks the current foreground Windows app.
- Stores activity history in the user's local SQLite DB.
- Serves local activity data through `ws://127.0.0.1:17373`.
- Runs as a tray app with optional Windows startup registration.

## Local Data

Activity data is stored outside this repository:

```text
%LOCALAPPDATA%\SimpleDesktopLogger\activity.db
```

## Build

Run the build from this folder or by calling the script path:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_agent_server.ps1
```

The build script checks `tkinter.Tk()` before PyInstaller runs and validates Tk/Tcl artifacts after build.
In Codex, run this build with host-level/escalated execution.
