param([ValidateSet('all', 'voice', 'bot', 'memory')][string]$Component = 'all')
$ErrorActionPreference = 'Stop'
$xiaoxiaPython = Join-Path $PSScriptRoot '.venvs/bot/Scripts/python.exe'
& $xiaoxiaPython (Join-Path $PSScriptRoot 'xiaoxia.py') start $Component
if ($LASTEXITCODE -ne 0) { throw 'Xiaoxia startup failed. See .runtime/logs.' }
