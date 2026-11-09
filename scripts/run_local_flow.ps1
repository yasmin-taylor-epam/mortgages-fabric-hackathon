<#
.SYNOPSIS
Runs the whole HomeBridge build locally, from synthetic data to report inputs.

.DESCRIPTION
Nothing here runs in Fabric. The script deletes data/output/ and rebuilds it. It uses synthetic data only.
Steps: generate and validate data, train, evaluate, score one checkpoint run, copy context, replay into the
case writer, then run the test suite of each work package and of the Fabric deploy tooling (scripts/fabric).

.PARAMETER SkipTests
Run the flow but not the pytest suites.

.PARAMETER Demo
After the tests, run a scheduled scoring run and a failed run (T05 demo), then replay again.
Run this last. The extra runs add scores and cases beyond the plain checkpoint run.
Rerun the script without -Demo to restore the state that the tests expect.
#>
param([switch]$SkipTests, [switch]$Demo)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($name, [scriptblock]$body) {
    Write-Host "`n=== $name" -ForegroundColor Cyan
    & $body
    if ($LASTEXITCODE -ne 0) { throw "Step failed: $name" }
}

if (-not (Test-Path .venv)) { python -m venv .venv }
$py = Join-Path $root '.venv\Scripts\python.exe'
Step 'Install dependencies' {
    & $py -m pip install -q -r data\requirements.txt -r notebooks\models_eval\requirements.txt -r action\requirements.txt -r reports\requirements.txt -r scripts\fabric\requirements.txt
}

if (Test-Path data\output) { Remove-Item data\output -Recurse -Force }

Push-Location data
Step 'Data: generate, load, validate, snapshot, copy context' { & $py -m hbdata all | Select-Object -Last 3 }
Pop-Location

Push-Location notebooks
Step 'Models: train' { & $py -m models_eval train | Select-Object -Last 12 }
Step 'Models: routing policy (threshold from the release)' { & $py -m models_eval policy | Select-Object -Last 8 }
Step 'Models: evaluate' { & $py -m models_eval evaluate | Select-Object -Last 14 }
Step 'Models: score one checkpoint run and publish' {
    & $py -m models_eval pipeline --cutoff 2026-10-15T00:00:00Z --kind checkpoint --started-at 2026-10-15T00:05:00Z | Select-Object -Last 18
}
Pop-Location

Push-Location data
Step 'Data: copy completed score summaries into the context' { & $py -m hbdata copy-context }
Pop-Location

function Replay {
    Push-Location action
    try {
        $json = & $py -m homebridge_action.cli --hbdata-dir ..\data --policy ..\data\output\policy\routing-policy.json --now 2026-10-15T12:00:00Z | ConvertFrom-Json
        $json.decisions | Group-Object outcome, reason | Select-Object Count, Name | Format-Table -AutoSize
        Write-Host "Cases: $($json.cases.Count). Exceptions: $($json.exceptions.Count)."
    } finally { Pop-Location }
}
Step 'Action: replay events into the local case writer' { Replay; $global:LASTEXITCODE = 0 }

if (-not $SkipTests) {
    foreach ($suite in 'data', 'notebooks\models_eval', 'action', 'reports', 'scripts\fabric') {
        Push-Location $suite
        Step "Tests: $suite" { & $py -m pytest -q -rs }
        Pop-Location
    }
}

if ($Demo) {
    Push-Location notebooks
    Step 'Demo: scheduled run' {
        & $py -m models_eval pipeline --cutoff 2026-10-15T00:15:00Z --kind scheduled --started-at 2026-10-15T00:16:00Z | Select-Object -Last 8
    }
    Step 'Demo: failed run (T05). No risk_updated event fires.' {
        & $py -m models_eval pipeline --cutoff 2026-10-15T00:30:00Z --kind scheduled --started-at 2026-10-15T00:31:00Z --inject-failure after_outputs | Select-Object -Last 12
    }
    Pop-Location
    Push-Location data
    Step 'Data: copy context again' { & $py -m hbdata copy-context }
    Pop-Location
    Step 'Action: replay again' { Replay; $global:LASTEXITCODE = 0 }
}

Write-Host "`nLocal flow finished." -ForegroundColor Green
