<#
Runs the sample Jira CSV through validation mode and writes a preview CSV and
log file into this examples folder.

From the repository root:
  powershell.exe -ExecutionPolicy Bypass -File .\examples\Run-Validation-Example.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$syncScript = Join-Path -Path $repoRoot -ChildPath "scripts\Sync-JiraCsvToProject.ps1"
$sampleCsv = Join-Path -Path $PSScriptRoot -ChildPath "jira-export-sample.csv"
$outputFolder = Join-Path -Path $PSScriptRoot -ChildPath "validation-output"

& $syncScript `
    -JiraCsvPath $sampleCsv `
    -ValidateOnly `
    -OutputFolder $outputFolder

Write-Host ""
Write-Host "Output folder: $outputFolder"
