# Dev launcher for the RepoLexicon backend. Run it instead of a bare `uvicorn` call:
#   .\backend\run_dev.ps1
#
# --reload-dir app is load-bearing, not cosmetic.
#
# uvicorn's watcher defaults to the current working directory. This script runs
# from backend\, so a plain `uvicorn app.main:app --reload` makes WatchFiles
# track all of backend\ -- including backend\.data, where repository clones are
# written during an import. Every clone that contains a .py file then trips a
# reload in the middle of an indexing job. The reloader's graceful shutdown
# waits for that job (clone -> parse -> embed runs as one in-worker
# BackgroundTask), so the server appears to hang for minutes and can end up
# listening on its port with no worker alive.
#
# Scoping the watcher to backend\app means writes under .data can never trigger
# a reload. Add --reload-exclude only if you also need to watch something
# outside app\ (for example backend\alembic); note that requirement changes and
# tests/*.py will then no longer hot-reload.

$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtualenv interpreter not found at $python. Expected the repo-level .venv."
}

Write-Host "Starting backend from $PSScriptRoot (watching app\ only)" -ForegroundColor Cyan

& $python -m uvicorn app.main:app --reload --reload-dir app --port 8000
