param([switch]$SkipModels, [switch]$WithCosy)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Get-Command uv -ErrorAction Stop | Out-Null
Get-Command npm -ErrorAction Stop | Out-Null
$xiaoxiaComponents = @('bot', 'gateway', 'index', 'singing', 'memory')
if ($WithCosy) { $xiaoxiaComponents += 'cosy' }
foreach ($xiaoxiaComponent in $xiaoxiaComponents) {
    $xiaoxiaVersion = if ($xiaoxiaComponent -in @('bot', 'memory')) { '3.12' } else { '3.11' }
    $xiaoxiaEnv = Join-Path $PSScriptRoot ".venvs/$xiaoxiaComponent"
    $xiaoxiaPython = Join-Path $xiaoxiaEnv 'Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $xiaoxiaPython)) {
        & uv venv --python $xiaoxiaVersion $xiaoxiaEnv
        if ($LASTEXITCODE -ne 0) { throw "Environment creation failed: $xiaoxiaComponent" }
    }
    $xiaoxiaBuildArgs = @()
    if ($xiaoxiaComponent -eq 'cosy') { $xiaoxiaBuildArgs = @('--build-constraint', 'requirements/cosy-build.txt') }
    & uv pip install --python $xiaoxiaPython --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match @xiaoxiaBuildArgs -r "requirements/$xiaoxiaComponent.txt"
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed: $xiaoxiaComponent" }
}
$xiaoxiaPython = Join-Path $PSScriptRoot '.venvs/bot/Scripts/python.exe'
& $xiaoxiaPython xiaoxia.py init
if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed.' }
Push-Location dashboard
try {
    & npx --yes pnpm@10.15.1 install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard dependency install failed.' }
    & npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard build failed.' }
} finally { Pop-Location }
$xiaoxiaDashboard = Join-Path $PSScriptRoot 'astrbot/dashboard/dist'
& $xiaoxiaPython -c "from pathlib import Path; from astrbot import __version__; Path('dashboard/dist/assets/version').write_text(__version__, encoding='utf-8')"
if ($LASTEXITCODE -ne 0) { throw 'Dashboard version stamp failed.' }
New-Item -ItemType Directory -Force -Path $xiaoxiaDashboard | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot 'dashboard/dist/*') -Destination $xiaoxiaDashboard -Recurse -Force
Push-Location maintenance/browser
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Browser dependency install failed.' }
} finally { Pop-Location }
if (-not $SkipModels) {
    $xiaoxiaModelArgs = @()
    if ($WithCosy) { $xiaoxiaModelArgs += '--include-cosy' }
    & $xiaoxiaPython tools/download_models.py @xiaoxiaModelArgs
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed.' }
}
Write-Output 'Installed. Run .\start.ps1 and open http://127.0.0.1:6185.'
