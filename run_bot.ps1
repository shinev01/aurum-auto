$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python environment not found. Follow README.md first."
}

& $PythonExe -m aurum_bot.main --config (Join-Path $ProjectRoot "config.yaml")
