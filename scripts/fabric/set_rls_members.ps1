<#
.SYNOPSIS
Adds members to the roles of the HomeBridgeMortgage semantic model through the XMLA endpoint (TOM).

.DESCRIPTION
UNTESTED. Nobody has run this against Fabric. It needs:
  - XMLA read-write enabled on the capacity (Fabric admin setting "XMLA Endpoint").
  - The PowerShell module SqlServer (it ships Microsoft.AnalysisServices.Tabular.dll): Install-Module SqlServer -Scope CurrentUser
  - An access token for https://analysis.windows.net/powerbi/api in the environment variable HB_XMLA_TOKEN.
    scripts/fabric/deploy_fabric.py sets it for this child process only. To run the script alone, set it yourself:
      $env:HB_XMLA_TOKEN = (az account get-access-token --resource https://analysis.windows.net/powerbi/api --query accessToken -o tsv)
The script never prints or stores the token. The plan file holds group object IDs only.

Why this exists: the REST API cannot set role members. TMDL, Fabric Git and fabric-cicd deploy roles with empty members.
Run this script after every deploy of the semantic model, because a model deploy can reset the members.

The script only adds. It never removes a member. It skips a member that the role already holds.

UNVERIFIED: whether TOM accepts a group by object ID. Microsoft documents mail-enabled groups by e-mail address. Reports exist that a
non-mail-enabled group fails with "invalid rolememberships". If SaveChanges fails, add the group in the portal instead
(semantic model > Security). See docs/fabric-deployment.md.

.PARAMETER PlanPath
JSON file: { "workspace": "<name>", "model": "<semantic model>", "memberType": "Group", "members": { "<role>": ["<object id>", ...] } }

.PARAMETER WhatIf
Prints what would change. Changes nothing.
#>
[CmdletBinding(SupportsShouldProcess)]
param([Parameter(Mandatory)] [string] $PlanPath)

$ErrorActionPreference = 'Stop'
$token = $env:HB_XMLA_TOKEN
if (-not $token) { throw 'Set HB_XMLA_TOKEN to an access token for https://analysis.windows.net/powerbi/api.' }
$plan = Get-Content -Raw -Path $PlanPath | ConvertFrom-Json

$module = Get-Module -ListAvailable -Name SqlServer | Select-Object -First 1
if (-not $module) { throw 'The SqlServer module is not installed. Run: Install-Module SqlServer -Scope CurrentUser' }
$dll = Get-ChildItem -Path $module.ModuleBase -Recurse -Filter 'Microsoft.AnalysisServices.Tabular.dll' | Select-Object -First 1
if (-not $dll) { throw 'Microsoft.AnalysisServices.Tabular.dll was not found in the SqlServer module.' }
Add-Type -Path (Join-Path $dll.DirectoryName 'Microsoft.AnalysisServices.Core.dll') -ErrorAction SilentlyContinue
Add-Type -Path $dll.FullName

$server = New-Object Microsoft.AnalysisServices.Tabular.Server
try {
    $server.Connect("Data Source=powerbi://api.powerbi.com/v1.0/myorg/$($plan.workspace);Password=$token")
    $db = $server.Databases.FindByName($plan.model)
    if (-not $db) { throw "Semantic model '$($plan.model)' was not found in workspace '$($plan.workspace)'." }
    $changed = 0
    foreach ($roleName in $plan.members.PSObject.Properties.Name) {
        $role = $db.Model.Roles.Find($roleName)
        if (-not $role) { throw "Role '$roleName' does not exist in the model." }
        $have = @($role.Members | ForEach-Object { $_.MemberID; $_.MemberName } | Where-Object { $_ })
        foreach ($id in $plan.members.$roleName) {
            if ($have -contains $id) { Write-Output "kept   $roleName $id"; continue }
            if ($PSCmdlet.ShouldProcess("$roleName", "Add $($plan.memberType) $id")) {
                $m = New-Object Microsoft.AnalysisServices.Tabular.ExternalModelRoleMember
                $m.IdentityProvider = 'AzureAD'
                $m.MemberType = [Microsoft.AnalysisServices.Tabular.RoleMemberType]::$($plan.memberType)
                $m.MemberID = $id
                $m.MemberName = $id
                $role.Members.Add($m)
                Write-Output "added  $roleName $id"
                $changed++
            }
        }
    }
    if ($changed -gt 0) { $db.Model.SaveChanges() }
    Write-Output "Done. $changed member(s) added."
}
finally {
    $server.Disconnect()
}
