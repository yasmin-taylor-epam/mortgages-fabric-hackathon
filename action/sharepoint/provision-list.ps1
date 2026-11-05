<#
.SYNOPSIS
    Creates the HomeBridge review-case lists from review-cases.list.json.

.DESCRIPTION
    Proposed script. Nobody has run it against a tenant yet.
    It uses PnP.PowerShell and interactive sign-in. It stores no credentials.
    It is idempotent: it skips lists and columns that already exist.
    It does not set permissions. A site owner must secure the lists as each list's
    write_access note describes.

.PARAMETER SiteUrl
    The SharePoint site URL, for example https://contoso.sharepoint.com/sites/homebridge-review.

.PARAMETER ClientId
    The Entra app registration ID that PnP.PowerShell uses for interactive sign-in.

.EXAMPLE
    ./provision-list.ps1 -SiteUrl https://contoso.sharepoint.com/sites/homebridge-review -ClientId <app-id> -WhatIf
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [ValidatePattern('^https://')] [string] $SiteUrl,
    [Parameter(Mandatory)] [string] $ClientId,
    [string] $SchemaPath = (Join-Path $PSScriptRoot 'review-cases.list.json')
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Module -ListAvailable -Name PnP.PowerShell)) {
    throw 'PnP.PowerShell is not installed. Run: Install-Module PnP.PowerShell -Scope CurrentUser'
}

$schema = Get-Content -Raw -Path $SchemaPath | ConvertFrom-Json

Connect-PnPOnline -Url $SiteUrl -ClientId $ClientId -Interactive

foreach ($list in $schema.lists) {
    $existing = Get-PnPList -Identity $list.title -ErrorAction SilentlyContinue
    if (-not $existing) {
        if ($PSCmdlet.ShouldProcess($list.title, 'Create list')) {
            New-PnPList -Title $list.title -Template GenericList | Out-Null
        }
    }

    foreach ($column in $list.columns) {
        $field = Get-PnPField -List $list.title -Identity $column.name -ErrorAction SilentlyContinue
        if ($field) {
            Write-Verbose "Column $($column.name) already exists in $($list.title)."
            continue
        }
        if (-not $PSCmdlet.ShouldProcess("$($list.title).$($column.name)", "Add $($column.type) column")) {
            continue
        }

        $fieldArgs = @{
            List         = $list.title
            DisplayName  = $column.name
            InternalName = $column.name
            Type         = $column.type
            AddToDefaultView = $true
        }
        if ($column.required) { $fieldArgs.Required = $true }
        if ($column.type -eq 'Choice') { $fieldArgs.Choices = [string[]] $column.choices }
        Add-PnPField @fieldArgs | Out-Null

        if ($column.indexed -or $column.unique) {
            $values = @{ Indexed = $true }
            # INV-3: SharePoint enforces one row per case_key.
            if ($column.unique) { $values.EnforceUniqueValues = $true }
            Set-PnPField -List $list.title -Identity $column.name -Values $values | Out-Null
        }
    }
}

Write-Host "Provisioned $($schema.lists.Count) lists on $SiteUrl. Secure each list before use."
