<#
.SYNOPSIS
Runs no-module validation tests for Sync-JiraCsvToProject.ps1.

.DESCRIPTION
These tests do not open Microsoft Project and do not require Pester or any
third-party PowerShell module. They validate syntax, CSV parsing, column
detection, preview generation, sync report generation, and percent complete
calculations.

Run from the repository root:
  powershell.exe -ExecutionPolicy Bypass -File .\scripts\Test-Sync-JiraCsvToProject.ps1
#>

[CmdletBinding()]
param(
    [Parameter()]
    [switch]$KeepTestFiles
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Assert-True {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Condition,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Equal {
    param(
        [Parameter()]
        [object]$Expected,

        [Parameter()]
        [object]$Actual,

        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ([string]$Expected -ne [string]$Actual) {
        throw "$Message Expected '$Expected', got '$Actual'."
    }
}

function Get-PreviewRow {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Rows,

        [Parameter(Mandatory = $true)]
        [string]$JiraKey
    )

    $row = $Rows | Where-Object { $_.JiraKey -eq $JiraKey } | Select-Object -First 1
    Assert-True -Condition ($null -ne $row) -Message "Preview row '$JiraKey' was not found."
    return $row
}

$repoRoot = Split-Path -Path $PSScriptRoot -Parent
$scriptPath = Join-Path -Path $PSScriptRoot -ChildPath "Sync-JiraCsvToProject.ps1"
Assert-True -Condition (Test-Path -LiteralPath $scriptPath -PathType Leaf) -Message "Script under test was not found: $scriptPath"

$tokens = $null
$parseErrors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
Assert-Equal -Expected 0 -Actual $parseErrors.Count -Message "PowerShell parser found errors."
Write-Host "PASS syntax check"

$testRoot = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath "jira-ims-sync-tests-$([System.Guid]::NewGuid().ToString('N'))"
[void](New-Item -Path $testRoot -ItemType Directory -Force)

try {
    $defaultCsvPath = Join-Path -Path $testRoot -ChildPath "jira-default.csv"
    $defaultPreviewPath = Join-Path -Path $testRoot -ChildPath "jira-default-preview.csv"
    $defaultLogPath = Join-Path -Path $testRoot -ChildPath "jira-default.log"
    $defaultReportPath = Join-Path -Path $testRoot -ChildPath "jira-default-sync-report.csv"

    @"
Issue key,Issue Type,Summary,Story Points,Remaining Story Points
DEMO-1,Initiative,Partially complete work,8,2
DEMO-2,Epic,Not started work,5,5
DEMO-3,Epic,Finished work,3,0
DEMO-4,Initiative,Zero point work,0,0
DEMO-5,Epic,Remaining exceeds total,4,6
DEMO-6,Initiative,Negative remaining value,6,-1
DEMO-7,Epic,Missing point values,,
DEMO-8,Story,Task-level row to exclude,3,1
DEMO-1,Epic,Duplicate key row,1,0
,Task,Blank key row,2,1
"@ | Set-Content -LiteralPath $defaultCsvPath -Encoding UTF8

    & $scriptPath `
        -JiraCsvPath $defaultCsvPath `
        -ValidateOnly `
        -PreviewCsvPath $defaultPreviewPath `
        -LogPath $defaultLogPath `
        -SyncReportCsvPath $defaultReportPath

    Assert-True -Condition (Test-Path -LiteralPath $defaultPreviewPath -PathType Leaf) -Message "Preview CSV was not created."
    Assert-True -Condition (Test-Path -LiteralPath $defaultLogPath -PathType Leaf) -Message "Log file was not created."
    Assert-True -Condition (Test-Path -LiteralPath $defaultReportPath -PathType Leaf) -Message "Sync report CSV was not created."

    $defaultPreviewRows = @(Import-Csv -LiteralPath $defaultPreviewPath)
    Assert-Equal -Expected 7 -Actual $defaultPreviewRows.Count -Message "Default preview row count mismatch."

    $defaultReportRows = @(Import-Csv -LiteralPath $defaultReportPath)
    Assert-Equal -Expected 10 -Actual $defaultReportRows.Count -Message "Default sync report row count mismatch."
    Assert-Equal -Expected 1 -Actual @(($defaultReportRows | Where-Object { $_.Action -eq "DuplicateCsvJiraKey" })).Count -Message "Duplicate CSV key report count mismatch."
    Assert-Equal -Expected 1 -Actual @(($defaultReportRows | Where-Object { $_.Action -eq "CsvRowMissingJiraKey" })).Count -Message "Missing Jira key report count mismatch."
    Assert-Equal -Expected 1 -Actual @(($defaultReportRows | Where-Object { $_.Action -eq "ExcludedIssueType" -and $_.IssueType -eq "Story" })).Count -Message "Excluded Story report count mismatch."
    Assert-Equal -Expected 4 -Actual @(($defaultReportRows | Where-Object { $_.Severity -eq "Warning" -and $_.Action -eq "ValidatedInputOnly" })).Count -Message "Validation warning report count mismatch."

    Assert-Equal -Expected 75 -Actual (Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-1").PercentComplete -Message "DEMO-1 percent mismatch."
    Assert-Equal -Expected 0 -Actual (Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-2").PercentComplete -Message "DEMO-2 percent mismatch."
    Assert-Equal -Expected 100 -Actual (Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-3").PercentComplete -Message "DEMO-3 percent mismatch."
    Assert-Equal -Expected "" -Actual (Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-4").PercentComplete -Message "DEMO-4 percent should be blank."

    $demo5 = Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-5"
    Assert-Equal -Expected 0 -Actual $demo5.PercentComplete -Message "DEMO-5 percent should be clamped to 0."
    Assert-True -Condition ($demo5.ValidationStatus -like "*exceed*") -Message "DEMO-5 should flag remaining story points greater than total."

    $demo6 = Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-6"
    Assert-Equal -Expected 100 -Actual $demo6.PercentComplete -Message "DEMO-6 percent should be clamped to 100."
    Assert-True -Condition ($demo6.ValidationStatus -like "*below 0*") -Message "DEMO-6 should flag negative remaining story points."

    $demo7 = Get-PreviewRow -Rows $defaultPreviewRows -JiraKey "DEMO-7"
    Assert-Equal -Expected "" -Actual $demo7.PercentComplete -Message "DEMO-7 percent should be blank."
    Assert-True -Condition ($demo7.ValidationStatus -like "*Missing story points*") -Message "DEMO-7 should flag missing story points."
    Write-Host "PASS default Jira CSV validation and preview"

    $outputFolder = Join-Path -Path $testRoot -ChildPath "output-folder-mode"
    & $scriptPath `
        -JiraCsvPath $defaultCsvPath `
        -ValidateOnly `
        -OutputFolder $outputFolder

    Assert-True -Condition (Test-Path -LiteralPath (Join-Path -Path $outputFolder -ChildPath "jira-default.preview.csv") -PathType Leaf) -Message "OutputFolder preview CSV was not created."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path -Path $outputFolder -ChildPath "jira-default.sync-report.csv") -PathType Leaf) -Message "OutputFolder sync report CSV was not created."
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path -Path $outputFolder -ChildPath "jira-default.run.log") -PathType Leaf) -Message "OutputFolder log file was not created."
    Write-Host "PASS output folder creates default artifacts"

    $scenarioRoot = Join-Path -Path $repoRoot -ChildPath "examples\full-working-scenario"
    $scenarioInitialOutput = Join-Path -Path $testRoot -ChildPath "scenario-initial-output"
    $scenarioFollowOnOutput = Join-Path -Path $testRoot -ChildPath "scenario-follow-on-output"

    & $scriptPath `
        -JiraCsvPath (Join-Path -Path $scenarioRoot -ChildPath "jira-initial-import.csv") `
        -ValidateOnly `
        -OutputFolder $scenarioInitialOutput

    & $scriptPath `
        -JiraCsvPath (Join-Path -Path $scenarioRoot -ChildPath "jira-follow-on-update.csv") `
        -ValidateOnly `
        -OutputFolder $scenarioFollowOnOutput

    $actualInitialPreview = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioInitialOutput -ChildPath "jira-initial-import.preview.csv"))
    $expectedInitialPreview = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioRoot -ChildPath "expected-initial-preview.csv"))
    Assert-Equal -Expected $expectedInitialPreview.Count -Actual $actualInitialPreview.Count -Message "Scenario initial preview row count mismatch."

    foreach ($expected in $expectedInitialPreview) {
        $actual = Get-PreviewRow -Rows $actualInitialPreview -JiraKey $expected.JiraKey
        Assert-Equal -Expected $expected.IssueType -Actual $actual.IssueType -Message "Scenario initial issue type mismatch for $($expected.JiraKey)."
        Assert-Equal -Expected $expected.PercentComplete -Actual $actual.PercentComplete -Message "Scenario initial percent mismatch for $($expected.JiraKey)."
        Assert-Equal -Expected $expected.ValidationStatus -Actual $actual.ValidationStatus -Message "Scenario initial validation mismatch for $($expected.JiraKey)."
    }

    $actualInitialReport = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioInitialOutput -ChildPath "jira-initial-import.sync-report.csv"))
    Assert-Equal -Expected 3 -Actual @(($actualInitialReport | Where-Object { $_.Action -eq "ExcludedIssueType" })).Count -Message "Scenario initial excluded issue type count mismatch."
    Assert-Equal -Expected 1 -Actual @(($actualInitialReport | Where-Object { $_.Action -eq "ExcludedIssueType" -and $_.IssueType -eq "Story" })).Count -Message "Scenario initial Story exclusion count mismatch."
    Assert-Equal -Expected 1 -Actual @(($actualInitialReport | Where-Object { $_.Action -eq "ExcludedIssueType" -and $_.IssueType -eq "Task" })).Count -Message "Scenario initial Task exclusion count mismatch."
    Assert-Equal -Expected 1 -Actual @(($actualInitialReport | Where-Object { $_.Action -eq "ExcludedIssueType" -and $_.IssueType -eq "Bug" })).Count -Message "Scenario initial Bug exclusion count mismatch."

    $actualFollowOnPreview = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioFollowOnOutput -ChildPath "jira-follow-on-update.preview.csv"))
    $expectedFollowOnPreview = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioRoot -ChildPath "expected-follow-on-preview.csv"))
    Assert-Equal -Expected $expectedFollowOnPreview.Count -Actual $actualFollowOnPreview.Count -Message "Scenario follow-on preview row count mismatch."

    foreach ($expected in $expectedFollowOnPreview) {
        $actual = Get-PreviewRow -Rows $actualFollowOnPreview -JiraKey $expected.JiraKey
        Assert-Equal -Expected $expected.IssueType -Actual $actual.IssueType -Message "Scenario follow-on issue type mismatch for $($expected.JiraKey)."
        Assert-Equal -Expected $expected.PercentComplete -Actual $actual.PercentComplete -Message "Scenario follow-on percent mismatch for $($expected.JiraKey)."
        Assert-Equal -Expected $expected.ValidationStatus -Actual $actual.ValidationStatus -Message "Scenario follow-on validation mismatch for $($expected.JiraKey)."
    }

    $actualFollowOnReport = @(Import-Csv -LiteralPath (Join-Path -Path $scenarioFollowOnOutput -ChildPath "jira-follow-on-update.sync-report.csv"))
    Assert-Equal -Expected 3 -Actual @(($actualFollowOnReport | Where-Object { $_.Action -eq "ExcludedIssueType" })).Count -Message "Scenario follow-on excluded issue type count mismatch."
    Write-Host "PASS full working scenario validation outputs"

    $customCsvPath = Join-Path -Path $testRoot -ChildPath "jira-custom-columns.csv"
    $customPreviewPath = Join-Path -Path $testRoot -ChildPath "jira-custom-preview.csv"

    @"
Key,Work Item Type,Name,Custom field (Story point estimate),Custom field (Remaining story points)
OPS-101,Epic,Custom column example,13,8
"@ | Set-Content -LiteralPath $customCsvPath -Encoding UTF8

    & $scriptPath `
        -JiraCsvPath $customCsvPath `
        -ValidateOnly `
        -PreviewCsvPath $customPreviewPath `
        -SyncReportCsvPath (Join-Path -Path $testRoot -ChildPath "jira-custom-sync-report.csv")

    $customPreviewRows = @(Import-Csv -LiteralPath $customPreviewPath)
    Assert-Equal -Expected 1 -Actual $customPreviewRows.Count -Message "Custom preview row count mismatch."
    Assert-Equal -Expected 38 -Actual $customPreviewRows[0].PercentComplete -Message "Custom-column percent mismatch."
    Write-Host "PASS alternate Jira CSV column names"

    $badCsvPath = Join-Path -Path $testRoot -ChildPath "jira-missing-key.csv"
    @"
Summary,Story Points,Remaining Story Points
No key row,1,0
"@ | Set-Content -LiteralPath $badCsvPath -Encoding UTF8

    $missingKeyFailed = $false
    try {
        & $scriptPath -JiraCsvPath $badCsvPath -ValidateOnly
    }
    catch {
        $missingKeyFailed = $true
    }

    Assert-True -Condition $missingKeyFailed -Message "Missing Jira key column should fail validation."
    Write-Host "PASS missing Jira key column fails clearly"

    $missingIssueTypeCsvPath = Join-Path -Path $testRoot -ChildPath "jira-missing-issue-type.csv"
    @"
Issue key,Summary,Story Points,Remaining Story Points
NO-TYPE-1,No issue type column,3,1
"@ | Set-Content -LiteralPath $missingIssueTypeCsvPath -Encoding UTF8

    $missingIssueTypeFailed = $false
    try {
        & $scriptPath -JiraCsvPath $missingIssueTypeCsvPath -ValidateOnly
    }
    catch {
        $missingIssueTypeFailed = $true
    }

    Assert-True -Condition $missingIssueTypeFailed -Message "Missing issue type column should fail unless IncludeAllIssueTypes is used."

    $includeAllPreviewPath = Join-Path -Path $testRoot -ChildPath "include-all-preview.csv"
    & $scriptPath `
        -JiraCsvPath $missingIssueTypeCsvPath `
        -ValidateOnly `
        -IncludeAllIssueTypes `
        -PreviewCsvPath $includeAllPreviewPath

    $includeAllPreviewRows = @(Import-Csv -LiteralPath $includeAllPreviewPath)
    Assert-Equal -Expected 1 -Actual $includeAllPreviewRows.Count -Message "IncludeAllIssueTypes should process rows without an issue type column."
    Write-Host "PASS issue type filter requires a column unless IncludeAllIssueTypes is used"
}
finally {
    if ($KeepTestFiles) {
        Write-Host "Kept test files at: $testRoot"
    }
    else {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}

Write-Host "All non-COM tests passed."
