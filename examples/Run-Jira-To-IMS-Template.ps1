<#
Copy this file and edit the settings below.

This template is meant for operators who do not want to type the full command.
It processes Initiatives and Epics only by default.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

# Choose one: ValidateOnly, CreateNewIMS, UpdateExistingIMS
$Mode = "ValidateOnly"

# Required for all modes.
$JiraCsvPath = "C:\Path\To\jira-export.csv"

# Required only for UpdateExistingIMS.
$ExistingImsPath = "C:\Path\To\current-ims.mpp"

# All generated files go here: preview CSV, sync report CSV, log, and .mpp output.
$OutputFolder = "C:\Path\To\Jira-IMS-Run"

# Default scope. Leave this alone unless your organization uses different high-level Jira issue types.
$IncludedIssueTypes = @("Initiative", "Epic")

# Optional column-name overrides. Leave blank unless validation says a column was not found.
$JiraKeyColumn = ""
$IssueTypeColumn = ""
$SummaryColumn = ""
$StoryPointsColumn = ""
$RemainingStoryPointsColumn = ""

# Set to $true only if missing Initiative/Epic rows should be appended to an existing IMS.
$AddMissingInitiativesAndEpics = $false

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$syncScript = Join-Path -Path $repoRoot -ChildPath "scripts\Sync-JiraCsvToProject.ps1"

if ($JiraCsvPath -like "C:\Path\To\*") {
    throw "Edit JiraCsvPath at the top of this file before running."
}

if ($OutputFolder -like "C:\Path\To\*") {
    throw "Edit OutputFolder at the top of this file before running."
}

$arguments = @(
    "-JiraCsvPath", $JiraCsvPath,
    "-OutputFolder", $OutputFolder,
    "-IncludedIssueTypes", $IncludedIssueTypes
)

switch ($Mode) {
    "ValidateOnly" {
        $arguments += "-ValidateOnly"
    }
    "CreateNewIMS" {
    }
    "UpdateExistingIMS" {
        if ($ExistingImsPath -like "C:\Path\To\*") {
            throw "Edit ExistingImsPath at the top of this file before running UpdateExistingIMS mode."
        }
        $arguments += @("-ProjectPath", $ExistingImsPath)
    }
    default {
        throw "Mode must be ValidateOnly, CreateNewIMS, or UpdateExistingIMS."
    }
}

if ($AddMissingInitiativesAndEpics) {
    $arguments += "-AddMissingInitiativesAndEpics"
}

if (-not [string]::IsNullOrWhiteSpace($JiraKeyColumn)) {
    $arguments += @("-JiraKeyColumn", $JiraKeyColumn)
}

if (-not [string]::IsNullOrWhiteSpace($IssueTypeColumn)) {
    $arguments += @("-IssueTypeColumn", $IssueTypeColumn)
}

if (-not [string]::IsNullOrWhiteSpace($SummaryColumn)) {
    $arguments += @("-SummaryColumn", $SummaryColumn)
}

if (-not [string]::IsNullOrWhiteSpace($StoryPointsColumn)) {
    $arguments += @("-StoryPointsColumn", $StoryPointsColumn)
}

if (-not [string]::IsNullOrWhiteSpace($RemainingStoryPointsColumn)) {
    $arguments += @("-RemainingStoryPointsColumn", $RemainingStoryPointsColumn)
}

& $syncScript @arguments
