<#
Runs the full working scenario in validation mode.

This does not create or update a Microsoft Project file. It creates preview,
sync report, and log files for both the initial Jira CSV and follow-on Jira CSV.

From the repository root:
  powershell.exe -ExecutionPolicy Bypass -File .\examples\full-working-scenario\Run-Scenario-Validation.ps1
#>

[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputRoot = (Join-Path -Path $PSScriptRoot -ChildPath "scenario-output")
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Path (Split-Path -Path $PSScriptRoot -Parent) -Parent
$syncScript = Join-Path -Path $repoRoot -ChildPath "scripts\Sync-JiraCsvToProject.ps1"

$initialCsv = Join-Path -Path $PSScriptRoot -ChildPath "jira-initial-import.csv"
$followOnCsv = Join-Path -Path $PSScriptRoot -ChildPath "jira-follow-on-update.csv"

$initialOutput = Join-Path -Path $OutputRoot -ChildPath "01-initial-import-validation"
$followOnOutput = Join-Path -Path $OutputRoot -ChildPath "02-follow-on-update-validation"

& $syncScript `
    -JiraCsvPath $initialCsv `
    -ValidateOnly `
    -OutputFolder $initialOutput

& $syncScript `
    -JiraCsvPath $followOnCsv `
    -ValidateOnly `
    -OutputFolder $followOnOutput

Write-Host ""
Write-Host "Scenario validation complete."
Write-Host "Initial import output: $initialOutput"
Write-Host "Follow-on update output: $followOnOutput"
