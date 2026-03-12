$ErrorActionPreference = 'Stop'

$project = 'C:\Users\zhang\miniprograms\ks-ai-auto-commenter'
Set-Location $project
Write-Host "[1/5] Project: $project"

$launcher = $null
$prefix = @()

if (Get-Command py -ErrorAction SilentlyContinue) {
  & py -3.11 -V *> $null
  if ($LASTEXITCODE -eq 0) {
    $launcher = 'py'
    $prefix = @('-3.11')
  } else {
    & py -3 -V *> $null
    if ($LASTEXITCODE -eq 0) {
      $launcher = 'py'
      $prefix = @('-3')
    }
  }
}

if (-not $launcher -and (Get-Command python -ErrorAction SilentlyContinue)) {
  $launcher = 'python'
  $prefix = @()
}

if (-not $launcher) {
  throw 'No Windows Python found (py/python).'
}

Write-Host "[2/5] Python launcher: $launcher $($prefix -join ' ')"

if (Test-Path '.venv') {
  Write-Host '[3/5] Removing old .venv ...'
  Remove-Item '.venv' -Recurse -Force
}

Write-Host '[3/5] Creating new .venv ...'
& $launcher @prefix -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }

$venvPython = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
  throw "venv python not found: $venvPython"
}

Write-Host '[4/5] Installing dependencies ...'
& $venvPython -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed' }

& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'requirements install failed' }

Write-Host '[5/5] Installing Playwright Chromium ...'
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'playwright chromium install failed' }

Write-Host 'DONE: Windows venv rebuilt successfully.'
