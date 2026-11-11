<#
.SYNOPSIS
Deploys the HomeBridge build to Microsoft Fabric. Wrapper for scripts/fabric/deploy_fabric.py.

.DESCRIPTION
Creates .venv if needed, installs the deploy tooling, and runs the deploy script. You sign in once, in the browser,
when the script needs Fabric. Nothing is deployed with -Validate (offline) or -DryRun (read-only).
The workspace, core, upload, notebooks, pipeline and model steps ran in a real workspace on 5 November 2026.
The optional steps are not confirmed in Fabric yet. See docs/fabric-deployment.md.

Every other argument passes through to deploy_fabric.py. Run with --help to see them all.

.EXAMPLE
powershell -File scripts\deploy_fabric.ps1 --validate
powershell -File scripts\deploy_fabric.ps1 --dry-run --workspace-name HomeBridge-Dev --capacity-name <your capacity>
powershell -File scripts\deploy_fabric.ps1 --workspace-name HomeBridge-Dev --capacity-name <your capacity>
powershell -File scripts\deploy_fabric.ps1 --only notebooks pipeline     # run the notebooks and the pipeline again
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path .venv)) { python -m venv .venv }
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py -m pip install -q -r scripts\fabric\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install the deploy tooling.' }

& $py scripts\fabric\deploy_fabric.py @args
exit $LASTEXITCODE
