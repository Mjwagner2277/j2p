<#
.SYNOPSIS
Creates or updates a Microsoft Project IMS from a Jira CSV export.

.DESCRIPTION
This script uses Microsoft Project COM automation, so it must be run on a
Windows machine with Microsoft Project installed.

Create mode:
  If -ProjectPath is omitted, the Jira CSV is imported into a new .mpp file.
  By default, only Jira Initiative and Epic rows become tasks. The Jira key is
  stored in a task text custom field renamed to "jira-key".

Update mode:
  If -ProjectPath is supplied, the existing .mpp is opened, tasks are matched
  by the jira-key field, and task % Complete is calculated from:

    (Story Points - Remaining Story Points) / Story Points

  By default, update mode saves to a side-by-side *.updated.mpp file. Use
  -InPlace to update the existing schedule.

Issue type scope:
  By default, only Jira rows with issue type Initiative or Epic are processed.
  Other Jira rows are excluded and logged as ExcludedIssueType. Use
  -IncludedIssueTypes to change the allowed types, or -IncludeAllIssueTypes to
  intentionally process every Jira row.

Audit output:
  Use -PreviewCsvPath to review calculated percentages before touching Project.
  Use -SyncReportCsvPath for a row-level audit of items updated, created,
  missing from Project, missing from Jira, duplicated, skipped, or failed.
  Jira rows excluded by issue type are logged as ExcludedIssueType.
  If -SyncReportCsvPath is omitted during a real create/update run, a default
  *.sync-report.csv file is created beside the output schedule.

.EXAMPLE
.\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\jira.csv `
  -OutputFolder .\Jira-IMS-Run

Creates a new Microsoft Project schedule from jira.csv.

.EXAMPLE
.\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\jira.csv `
  -ProjectPath .\schedule.mpp `
  -OutputFolder .\Jira-IMS-Run

Updates schedule.mpp and writes schedule.updated.mpp.

.EXAMPLE
.\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\jira.csv `
  -ValidateOnly `
  -OutputFolder .\Jira-IMS-Run

Validates the Jira CSV and writes a preview of the calculated percent complete
values without opening Microsoft Project.

.EXAMPLE
.\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\jira.csv `
  -ProjectPath .\schedule.mpp `
  -OutputFolder .\Jira-IMS-Run `
  -StoryPointsColumn "Custom field (Story Points)" `
  -RemainingStoryPointsColumn "Custom field (Remaining Story Points)" `
  -InPlace

Updates schedule.mpp in place using explicit Jira CSV column names.
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$JiraCsvPath,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$ProjectPath,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputPath,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$OutputFolder,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$PreviewCsvPath,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$LogPath,

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$SyncReportCsvPath,

    [Parameter()]
    [string[]]$JiraKeyColumn = @(
        "Issue key",
        "Issue Key",
        "Key",
        "Jira key",
        "Jira Key",
        "jira-key"
    ),

    [Parameter()]
    [string[]]$SummaryColumn = @(
        "Summary",
        "Issue summary",
        "Name",
        "Title"
    ),

    [Parameter()]
    [string[]]$IssueTypeColumn = @(
        "Issue Type",
        "Issue type",
        "Type",
        "Work Item Type",
        "Work item type"
    ),

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string[]]$IncludedIssueTypes = @(
        "Initiative",
        "Epic"
    ),

    [Parameter()]
    [string[]]$StoryPointsColumn = @(
        "Story Points",
        "Story points",
        "Story point estimate",
        "Custom field (Story Points)",
        "Custom field (Story point estimate)"
    ),

    [Parameter()]
    [string[]]$RemainingStoryPointsColumn = @(
        "Remaining Story Points",
        "Remaining story points",
        "Remaining Story points",
        "Custom field (Remaining Story Points)",
        "Custom field (Remaining story points)"
    ),

    [Parameter()]
    [ValidatePattern("^Text([1-9]|[12][0-9]|30)$")]
    [string]$JiraKeyTaskTextField = "Text30",

    [Parameter()]
    [ValidatePattern("^Number([1-9]|1[0-9]|20)$")]
    [string]$StoryPointsTaskNumberField = "Number1",

    [Parameter()]
    [ValidatePattern("^Number([1-9]|1[0-9]|20)$")]
    [string]$RemainingStoryPointsTaskNumberField = "Number2",

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$JiraKeyFieldName = "jira-key",

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$StoryPointsFieldName = "Story Points",

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$RemainingStoryPointsFieldName = "Remaining Story Points",

    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$DefaultDuration = "1d",

    [Parameter()]
    [Alias("AddMissingInitiativesAndEpics")]
    [switch]$AddMissingTasks,

    [Parameter()]
    [switch]$IncludeAllIssueTypes,

    [Parameter()]
    [switch]$ValidateOnly,

    [Parameter()]
    [switch]$InPlace,

    [Parameter()]
    [switch]$Force,

    [Parameter()]
    [switch]$Visible,

    [Parameter()]
    [switch]$LeaveOpen
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Resolve-ExistingFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description file was not found: $Path"
    }

    return (Resolve-Path -LiteralPath $Path).ProviderPath
}

function Resolve-OutputFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Ensure-ParentDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $parentDirectory = Split-Path -Path $Path -Parent
    if ([string]::IsNullOrWhiteSpace($parentDirectory)) {
        return
    }

    if (-not (Test-Path -LiteralPath $parentDirectory -PathType Container)) {
        [void](New-Item -Path $parentDirectory -ItemType Directory -Force)
    }
}

function Test-IsWindowsHost {
    return [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Assert-ProjectAutomationPrerequisites {
    if (-not (Test-IsWindowsHost)) {
        throw "Microsoft Project automation requires Windows with Microsoft Project desktop installed. Use -ValidateOnly on non-Windows systems to check the Jira CSV and preview calculations."
    }

    if (-not (Get-Command -Name "New-Object" -ErrorAction SilentlyContinue)) {
        throw "PowerShell cannot find New-Object. Run this script from Windows PowerShell or PowerShell on Windows."
    }
}

function Get-MissingColumnGuidance {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    switch -Regex ($Description) {
        "Jira key" {
            return "Export Jira with the issue key column, usually named 'Issue key'. If your CSV uses a different header, rerun with -JiraKeyColumn ""Exact Header Name""."
        }
        "issue type" {
            return "Export Jira with the issue type column, usually named 'Issue Type'. This is required because the script only includes Initiative/Epic rows by default. If your CSV uses a different header, rerun with -IssueTypeColumn ""Exact Header Name"". Use -IncludeAllIssueTypes only if task-level rows should also be processed."
        }
        "story points" {
            return "Export Jira with total story points. If your CSV uses a different header, rerun with -StoryPointsColumn ""Exact Header Name""."
        }
        "remaining story points" {
            return "Export Jira with remaining story points. If your CSV uses a different header, rerun with -RemainingStoryPointsColumn ""Exact Header Name""."
        }
        default {
            return "If your CSV uses a different header, rerun with the matching column parameter and the exact header name from the CSV."
        }
    }
}

function Resolve-ColumnName {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Candidates,

        [Parameter(Mandatory = $true)]
        [string[]]$Headers,

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [Parameter()]
        [switch]$Required
    )

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }

        $match = $Headers |
            Where-Object { $_.Trim() -ieq $candidate.Trim() } |
            Select-Object -First 1

        if ($match) {
            return $match
        }
    }

    if ($Required) {
        $guidance = Get-MissingColumnGuidance -Description $Description
        throw "Could not find the $Description column. Tried: $($Candidates -join ', '). Available columns: $($Headers -join ', '). $guidance"
    }

    return $null
}

function Get-CellValue {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Row,

        [Parameter()]
        [string]$ColumnName
    )

    if ([string]::IsNullOrWhiteSpace($ColumnName)) {
        return $null
    }

    $property = $Row.PSObject.Properties |
        Where-Object { $_.Name -eq $ColumnName } |
        Select-Object -First 1

    if ($null -eq $property -or $null -eq $property.Value) {
        return $null
    }

    return [string]$property.Value
}

function ConvertTo-NullableDouble {
    param(
        [Parameter()]
        [object]$Value,

        [Parameter(Mandatory = $true)]
        [string]$ColumnName,

        [Parameter(Mandatory = $true)]
        [string]$JiraKey
    )

    if ($null -eq $Value) {
        return $null
    }

    $text = ([string]$Value).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    $styles = [System.Globalization.NumberStyles]::Float -bor [System.Globalization.NumberStyles]::AllowThousands
    $number = 0.0

    if ([double]::TryParse($text, $styles, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
        return $number
    }

    if ([double]::TryParse($text, $styles, [System.Globalization.CultureInfo]::CurrentCulture, [ref]$number)) {
        return $number
    }

    $numericText = $text -replace "[^\d\.,\-]", ""
    if (-not [string]::IsNullOrWhiteSpace($numericText)) {
        if ([double]::TryParse($numericText, $styles, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number)) {
            return $number
        }

        if ([double]::TryParse($numericText, $styles, [System.Globalization.CultureInfo]::CurrentCulture, [ref]$number)) {
            return $number
        }
    }

    throw "Column '$ColumnName' for Jira key '$JiraKey' is not numeric: '$text'"
}

function ConvertTo-ProjectPercentComplete {
    param(
        [Parameter()]
        [object]$StoryPoints,

        [Parameter()]
        [object]$RemainingStoryPoints
    )

    if ($null -eq $StoryPoints -or $null -eq $RemainingStoryPoints) {
        return $null
    }

    $total = [double]$StoryPoints
    $remaining = [double]$RemainingStoryPoints

    if ($total -le 0) {
        return $null
    }

    $percent = (($total - $remaining) / $total) * 100
    $percent = [Math]::Max(0, [Math]::Min(100, $percent))

    return [int][Math]::Round($percent, 0, [System.MidpointRounding]::AwayFromZero)
}

function Get-IssueValidationStatus {
    param(
        [Parameter()]
        [object]$StoryPoints,

        [Parameter()]
        [object]$RemainingStoryPoints
    )

    $messages = New-Object System.Collections.Generic.List[string]

    if ($null -eq $StoryPoints) {
        [void]$messages.Add("Missing story points")
    }
    elseif ([double]$StoryPoints -le 0) {
        [void]$messages.Add("Story points must be greater than 0 to calculate percent complete")
    }

    if ($null -eq $RemainingStoryPoints) {
        [void]$messages.Add("Missing remaining story points")
    }

    if ($null -ne $StoryPoints -and $null -ne $RemainingStoryPoints) {
        if ([double]$RemainingStoryPoints -lt 0) {
            [void]$messages.Add("Remaining story points are below 0; percent complete is clamped to 100")
        }

        if ([double]$StoryPoints -gt 0 -and [double]$RemainingStoryPoints -gt [double]$StoryPoints) {
            [void]$messages.Add("Remaining story points exceed story points; percent complete is clamped to 0")
        }
    }

    if ($messages.Count -eq 0) {
        return "OK"
    }

    return ($messages -join "; ")
}

function Format-ProjectNumber {
    param(
        [Parameter()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }

    return ([double]$Value).ToString("0.########", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Write-CsvFile {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Rows,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $parentDirectory = Split-Path -Path $Path -Parent
    if (-not [string]::IsNullOrWhiteSpace($parentDirectory)) {
        [void][System.IO.Directory]::CreateDirectory($parentDirectory)
    }

    $csvLines = [string[]]($Rows | ConvertTo-Csv -NoTypeInformation)
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines($Path, $csvLines, $utf8NoBom)
}

function Export-IssuePreview {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Issues,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $previewRows = $Issues |
        Select-Object `
            @{ Name = "JiraKey"; Expression = { $_.Key } },
            @{ Name = "IssueType"; Expression = { $_.IssueType } },
            @{ Name = "Summary"; Expression = { $_.Summary } },
            @{ Name = "StoryPoints"; Expression = { Format-ProjectNumber $_.StoryPoints } },
            @{ Name = "RemainingStoryPoints"; Expression = { Format-ProjectNumber $_.RemainingStoryPoints } },
            @{ Name = "PercentComplete"; Expression = { if ($null -eq $_.PercentComplete) { "" } else { $_.PercentComplete } } },
            @{ Name = "ValidationStatus"; Expression = { $_.ValidationStatus } }

    Write-CsvFile -Rows @($previewRows) -Path $Path
}

function New-SyncReportRow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Action,

        [Parameter(Mandatory = $true)]
        [ValidateSet("Info", "Warning", "Error")]
        [string]$Severity,

        [Parameter()]
        [object]$Issue,

        [Parameter()]
        [object]$Task,

        [Parameter()]
        [string]$Detail
    )

    $jiraKey = ""
    $issueType = ""
    $summary = ""
    $storyPoints = ""
    $remainingStoryPoints = ""
    $percentComplete = ""
    $validationStatus = ""

    if ($null -ne $Issue) {
        $jiraKey = [string]$Issue.Key
        try { $issueType = [string]$Issue.IssueType } catch { $issueType = "" }
        $summary = [string]$Issue.Summary
        $storyPoints = Format-ProjectNumber $Issue.StoryPoints
        $remainingStoryPoints = Format-ProjectNumber $Issue.RemainingStoryPoints
        if ($null -ne $Issue.PercentComplete) {
            $percentComplete = [string]$Issue.PercentComplete
        }
        $validationStatus = [string]$Issue.ValidationStatus
    }

    $projectTaskId = ""
    $projectTaskUniqueId = ""
    $projectTaskName = ""

    if ($null -ne $Task) {
        try { $projectTaskId = [string]$Task.ID } catch { $projectTaskId = "" }
        try { $projectTaskUniqueId = [string]$Task.UniqueID } catch { $projectTaskUniqueId = "" }
        try { $projectTaskName = [string]$Task.Name } catch { $projectTaskName = "" }

        if ([string]::IsNullOrWhiteSpace($summary)) {
            $summary = $projectTaskName
        }
    }

    return [pscustomobject]@{
        Timestamp                 = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        Action                    = $Action
        Severity                  = $Severity
        JiraKey                   = $jiraKey
        IssueType                 = $issueType
        JiraSummary               = $summary
        StoryPoints               = $storyPoints
        RemainingStoryPoints      = $remainingStoryPoints
        PercentComplete           = $percentComplete
        ValidationStatus          = $validationStatus
        ProjectTaskId             = $projectTaskId
        ProjectTaskUniqueId       = $projectTaskUniqueId
        ProjectTaskName           = $projectTaskName
        Detail                    = $Detail
    }
}

function Test-IncludedIssueType {
    param(
        [Parameter()]
        [string]$IssueType,

        [Parameter(Mandatory = $true)]
        [string[]]$IncludedTypes
    )

    if ([string]::IsNullOrWhiteSpace($IssueType)) {
        return $false
    }

    foreach ($includedType in $IncludedTypes) {
        if ($IssueType.Trim() -ieq $includedType.Trim()) {
            return $true
        }
    }

    return $false
}

function Add-SyncReportRow {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Rows,

        [Parameter(Mandatory = $true)]
        [string]$Action,

        [Parameter(Mandatory = $true)]
        [ValidateSet("Info", "Warning", "Error")]
        [string]$Severity,

        [Parameter()]
        [object]$Issue,

        [Parameter()]
        [object]$Task,

        [Parameter()]
        [string]$Detail
    )

    [void]$Rows.Add((New-SyncReportRow -Action $Action -Severity $Severity -Issue $Issue -Task $Task -Detail $Detail))
}

function Export-SyncReport {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Rows,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ($Rows.Count -eq 0) {
        Add-SyncReportRow -Rows $Rows -Action "NoActions" -Severity "Info" -Detail "No sync actions were recorded."
    }

    Write-CsvFile -Rows $Rows.ToArray() -Path $Path
}

function Write-SyncReportSummary {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [System.Collections.Generic.List[object]]$Rows
    )

    if ($Rows.Count -eq 0) {
        return
    }

    $concerningRows = @($Rows | Where-Object { $_.Severity -ne "Info" })
    if ($concerningRows.Count -eq 0) {
        Write-Host "No concerning sync report items were recorded."
        return
    }

    Write-Warning "$($concerningRows.Count) concerning sync report items were recorded. Review the sync report CSV for the full details."

    foreach ($group in ($concerningRows | Group-Object -Property Action | Sort-Object -Property Name)) {
        Write-Warning "$($group.Count) item(s): $($group.Name)"
        foreach ($row in $group.Group) {
            $keyOrTask = if (-not [string]::IsNullOrWhiteSpace($row.JiraKey)) {
                $row.JiraKey
            }
            elseif (-not [string]::IsNullOrWhiteSpace($row.ProjectTaskName)) {
                $row.ProjectTaskName
            }
            else {
                "(unknown)"
            }

            Write-Warning "  $keyOrTask - $($row.Detail)"
        }
    }
}

function Get-ProjectApplication {
    try {
        $projectApplication = New-Object -ComObject "MSProject.Application"
    }
    catch {
        throw "Could not start Microsoft Project. Run this script on Windows with Microsoft Project installed. $($_.Exception.Message)"
    }

    $projectApplication.Visible = [bool]$Visible

    try {
        $projectApplication.DisplayAlerts = $false
    }
    catch {
        Write-Verbose "Could not disable Microsoft Project alerts: $($_.Exception.Message)"
    }

    return $projectApplication
}

function Get-TaskFieldId {
    param(
        [Parameter(Mandatory = $true)]
        [object]$ProjectApplication,

        [Parameter(Mandatory = $true)]
        [string[]]$FieldName
    )

    foreach ($name in $FieldName) {
        if ([string]::IsNullOrWhiteSpace($name)) {
            continue
        }

        try {
            return [int]$ProjectApplication.FieldNameToFieldConstant($name)
        }
        catch {
            Write-Verbose "Could not resolve Microsoft Project task field '$name': $($_.Exception.Message)"
        }
    }

    throw "Could not resolve any of these Microsoft Project task fields: $($FieldName -join ', ')"
}

function Rename-TaskCustomField {
    param(
        [Parameter(Mandatory = $true)]
        [object]$ProjectApplication,

        [Parameter(Mandatory = $true)]
        [int]$FieldId,

        [Parameter(Mandatory = $true)]
        [string]$FriendlyName
    )

    try {
        [void]$ProjectApplication.CustomFieldRename($FieldId, $FriendlyName)
    }
    catch {
        Write-Warning "Could not rename custom field to '$FriendlyName'. Continuing with the existing field name. $($_.Exception.Message)"
    }
}

function Set-TaskCustomField {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Task,

        [Parameter(Mandatory = $true)]
        [int]$FieldId,

        [Parameter()]
        [object]$Value
    )

    if ($null -eq $Value) {
        return
    }

    [void]$Task.SetField($FieldId, [string]$Value)
}

function Set-TaskPercentComplete {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Task,

        [Parameter(Mandatory = $true)]
        [int]$PercentComplete
    )

    try {
        $Task.PercentComplete = $PercentComplete
        return $true
    }
    catch {
        Write-Warning "Could not set % Complete for task '$($Task.Name)'. Summary tasks and read-only tasks may reject direct percent updates. $($_.Exception.Message)"
        return $false
    }
}

function Get-ProjectTasks {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Project
    )

    $tasks = New-Object System.Collections.Generic.List[object]

    for ($index = 1; $index -le $Project.Tasks.Count; $index++) {
        $task = $Project.Tasks.Item($index)
        if ($null -ne $task) {
            [void]$tasks.Add($task)
        }
    }

    return $tasks
}

function Add-JiraTask {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Project,

        [Parameter(Mandatory = $true)]
        [object]$ProjectApplication,

        [Parameter(Mandatory = $true)]
        [psobject]$Issue,

        [Parameter(Mandatory = $true)]
        [int]$JiraKeyFieldId,

        [Parameter(Mandatory = $true)]
        [int]$StoryPointsFieldId,

        [Parameter(Mandatory = $true)]
        [int]$RemainingStoryPointsFieldId
    )

    $task = $Project.Tasks.Add($Issue.Summary)

    if (-not [string]::IsNullOrWhiteSpace($DefaultDuration)) {
        try {
            $task.Duration = $ProjectApplication.DurationValue($DefaultDuration)
        }
        catch {
            Write-Warning "Could not set default duration '$DefaultDuration' for Jira key '$($Issue.Key)'. $($_.Exception.Message)"
        }
    }

    Set-TaskCustomField -Task $task -FieldId $JiraKeyFieldId -Value $Issue.Key
    Set-TaskCustomField -Task $task -FieldId $StoryPointsFieldId -Value (Format-ProjectNumber $Issue.StoryPoints)
    Set-TaskCustomField -Task $task -FieldId $RemainingStoryPointsFieldId -Value (Format-ProjectNumber $Issue.RemainingStoryPoints)

    if ($null -ne $Issue.PercentComplete) {
        [void](Set-TaskPercentComplete -Task $task -PercentComplete $Issue.PercentComplete)
    }

    return $task
}

function Update-JiraTaskFields {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Task,

        [Parameter(Mandatory = $true)]
        [psobject]$Issue,

        [Parameter(Mandatory = $true)]
        [int]$StoryPointsFieldId,

        [Parameter(Mandatory = $true)]
        [int]$RemainingStoryPointsFieldId
    )

    Set-TaskCustomField -Task $Task -FieldId $StoryPointsFieldId -Value (Format-ProjectNumber $Issue.StoryPoints)
    Set-TaskCustomField -Task $Task -FieldId $RemainingStoryPointsFieldId -Value (Format-ProjectNumber $Issue.RemainingStoryPoints)
}

$resolvedLogPath = $null
$transcriptStarted = $false

try {
$resolvedCsvPath = Resolve-ExistingFile -Path $JiraCsvPath -Description "Jira CSV"
$resolvedProjectPath = $null
$resolvedOutputPath = $null
$resolvedPreviewCsvPath = $null
$resolvedSyncReportCsvPath = $null
$resolvedOutputFolder = $null
$jiraBaseName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedCsvPath)

if ($OutputFolder) {
    $resolvedOutputFolder = Resolve-OutputFile -Path $OutputFolder
    [void][System.IO.Directory]::CreateDirectory($resolvedOutputFolder)
}

if ($ProjectPath) {
    $resolvedProjectPath = Resolve-ExistingFile -Path $ProjectPath -Description "Microsoft Project IMS"
    $projectBaseName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedProjectPath)

    if ($InPlace) {
        $resolvedOutputPath = $resolvedProjectPath
    }
    elseif ($OutputPath) {
        $resolvedOutputPath = Resolve-OutputFile -Path $OutputPath
    }
    elseif ($resolvedOutputFolder) {
        $resolvedOutputPath = Join-Path -Path $resolvedOutputFolder -ChildPath "$projectBaseName.updated.mpp"
    }
    else {
        $directory = Split-Path -Path $resolvedProjectPath -Parent
        $resolvedOutputPath = Join-Path -Path $directory -ChildPath "$projectBaseName.updated.mpp"
    }
}
else {
    if ($OutputPath) {
        $resolvedOutputPath = Resolve-OutputFile -Path $OutputPath
    }
    elseif ($resolvedOutputFolder) {
        $resolvedOutputPath = Join-Path -Path $resolvedOutputFolder -ChildPath "$jiraBaseName.mpp"
    }
    else {
        $directory = Split-Path -Path $resolvedCsvPath -Parent
        $resolvedOutputPath = Join-Path -Path $directory -ChildPath "$jiraBaseName.mpp"
    }
}

if ($PreviewCsvPath) {
    $resolvedPreviewCsvPath = Resolve-OutputFile -Path $PreviewCsvPath
}
elseif ($resolvedOutputFolder) {
    $resolvedPreviewCsvPath = Join-Path -Path $resolvedOutputFolder -ChildPath "$jiraBaseName.preview.csv"
}

if ($resolvedPreviewCsvPath) {
    Ensure-ParentDirectory -Path $resolvedPreviewCsvPath
}

if ($SyncReportCsvPath) {
    $resolvedSyncReportCsvPath = Resolve-OutputFile -Path $SyncReportCsvPath
}
elseif ($resolvedOutputFolder) {
    $resolvedSyncReportCsvPath = Join-Path -Path $resolvedOutputFolder -ChildPath "$jiraBaseName.sync-report.csv"
}
elseif (-not $ValidateOnly -and -not $WhatIfPreference) {
    $reportDirectory = Split-Path -Path $resolvedOutputPath -Parent
    $reportBaseName = [System.IO.Path]::GetFileNameWithoutExtension($resolvedOutputPath)
    $resolvedSyncReportCsvPath = Join-Path -Path $reportDirectory -ChildPath "$reportBaseName.sync-report.csv"
}

if ($resolvedSyncReportCsvPath) {
    Ensure-ParentDirectory -Path $resolvedSyncReportCsvPath
}

if ($LogPath) {
    $resolvedLogPath = Resolve-OutputFile -Path $LogPath
}
elseif ($resolvedOutputFolder) {
    $resolvedLogPath = Join-Path -Path $resolvedOutputFolder -ChildPath "$jiraBaseName.run.log"
}

if ($resolvedLogPath) {
    Ensure-ParentDirectory -Path $resolvedLogPath
    Start-Transcript -Path $resolvedLogPath -Force | Out-Null
    $transcriptStarted = $true
    Write-Host "Logging run output to: $resolvedLogPath"
}

if (-not $ValidateOnly) {
    if ($OutputPath -or (-not $ProjectPath)) {
        Ensure-ParentDirectory -Path $resolvedOutputPath
    }

    if ([System.IO.Path]::GetExtension($resolvedOutputPath) -ine ".mpp") {
        Write-Warning "Output path does not end in .mpp: $resolvedOutputPath"
    }

    if ($ProjectPath -and -not $InPlace) {
        $fullProjectPath = [System.IO.Path]::GetFullPath($resolvedProjectPath).TrimEnd([char[]]@('\', '/'))
        $fullOutputPath = [System.IO.Path]::GetFullPath($resolvedOutputPath).TrimEnd([char[]]@('\', '/'))

        if ($fullProjectPath -ieq $fullOutputPath) {
            throw "OutputPath is the same as ProjectPath. Use -InPlace if you want to update the existing IMS directly."
        }
    }

    if ((-not ($ProjectPath -and $InPlace)) -and (Test-Path -LiteralPath $resolvedOutputPath) -and (-not $Force)) {
        throw "Output file already exists: $resolvedOutputPath. Use -Force to overwrite it."
    }
}

$jiraRows = @(Import-Csv -LiteralPath $resolvedCsvPath)
if ($jiraRows.Count -eq 0) {
    throw "Jira CSV has no data rows: $resolvedCsvPath"
}

$headers = @($jiraRows[0].PSObject.Properties.Name)
$jiraKeyColumnName = Resolve-ColumnName -Candidates $JiraKeyColumn -Headers $headers -Description "Jira key" -Required
$summaryColumnName = Resolve-ColumnName -Candidates $SummaryColumn -Headers $headers -Description "summary"
$issueTypeColumnName = Resolve-ColumnName -Candidates $IssueTypeColumn -Headers $headers -Description "issue type" -Required:(-not $IncludeAllIssueTypes)
$storyPointsColumnName = Resolve-ColumnName -Candidates $StoryPointsColumn -Headers $headers -Description "story points"
$remainingStoryPointsColumnName = Resolve-ColumnName -Candidates $RemainingStoryPointsColumn -Headers $headers -Description "remaining story points"

if ($ProjectPath -and (-not $storyPointsColumnName -or -not $remainingStoryPointsColumnName)) {
    throw "Updating an existing IMS requires both total story points and remaining story points columns. Use -StoryPointsColumn and -RemainingStoryPointsColumn if your Jira export uses custom names."
}

if (-not $storyPointsColumnName) {
    Write-Warning "Story points column was not found. Tasks will be created without story point values or calculated % Complete."
}

if (-not $remainingStoryPointsColumnName) {
    Write-Warning "Remaining story points column was not found. Tasks will be created without remaining values or calculated % Complete."
}

$issueByKey = @{}
$issues = New-Object System.Collections.Generic.List[object]
$syncReportRows = New-Object System.Collections.Generic.List[object]
$skippedRows = 0
$excludedIssueTypeRows = 0
$csvDataRowNumber = 1

foreach ($row in $jiraRows) {
    $csvDataRowNumber++
    $key = (Get-CellValue -Row $row -ColumnName $jiraKeyColumnName)

    if ([string]::IsNullOrWhiteSpace($key)) {
        $skippedRows++
        Add-SyncReportRow `
            -Rows $syncReportRows `
            -Action "CsvRowMissingJiraKey" `
            -Severity "Warning" `
            -Detail "CSV row $csvDataRowNumber has no Jira key and was skipped."
        continue
    }

    $key = $key.Trim()

    $summary = Get-CellValue -Row $row -ColumnName $summaryColumnName
    if ([string]::IsNullOrWhiteSpace($summary)) {
        $summary = $key
    }

    $issueType = ""
    if ($issueTypeColumnName) {
        $issueType = Get-CellValue -Row $row -ColumnName $issueTypeColumnName
    }

    if ($null -eq $issueType) {
        $issueType = ""
    }

    $issueType = $issueType.Trim()

    if (-not $IncludeAllIssueTypes -and -not (Test-IncludedIssueType -IssueType $issueType -IncludedTypes $IncludedIssueTypes)) {
        $excludedIssueTypeRows++
        $severity = if ([string]::IsNullOrWhiteSpace($issueType)) { "Warning" } else { "Info" }
        $issueTypeDetail = if ([string]::IsNullOrWhiteSpace($issueType)) {
            "CSV row $csvDataRowNumber has no issue type and was excluded. Included issue types are: $($IncludedIssueTypes -join ', ')."
        }
        else {
            "CSV row $csvDataRowNumber has issue type '$issueType' and was excluded. Included issue types are: $($IncludedIssueTypes -join ', ')."
        }

        Add-SyncReportRow `
            -Rows $syncReportRows `
            -Action "ExcludedIssueType" `
            -Severity $severity `
            -Issue ([pscustomobject]@{
                Key                  = $key
                IssueType            = $issueType
                Summary              = $summary.Trim()
                StoryPoints          = $null
                RemainingStoryPoints = $null
                PercentComplete      = $null
                ValidationStatus     = "Excluded by issue type filter"
            }) `
            -Detail $issueTypeDetail
        continue
    }

    if ($issueByKey.ContainsKey($key)) {
        Write-Warning "Duplicate Jira key '$key' found in the CSV. Keeping the first row."
        Add-SyncReportRow `
            -Rows $syncReportRows `
            -Action "DuplicateCsvJiraKey" `
            -Severity "Warning" `
            -Issue ([pscustomobject]@{
                Key                  = $key
                IssueType            = $issueType
                Summary              = $summary.Trim()
                StoryPoints          = $null
                RemainingStoryPoints = $null
                PercentComplete      = $null
                ValidationStatus     = "Duplicate Jira key in CSV"
            }) `
            -Detail "CSV row $csvDataRowNumber duplicates an earlier Jira key. The duplicate row was skipped."
        continue
    }

    $storyPoints = $null
    if ($storyPointsColumnName) {
        $storyPoints = ConvertTo-NullableDouble `
            -Value (Get-CellValue -Row $row -ColumnName $storyPointsColumnName) `
            -ColumnName $storyPointsColumnName `
            -JiraKey $key
    }

    $remainingStoryPoints = $null
    if ($remainingStoryPointsColumnName) {
        $remainingStoryPoints = ConvertTo-NullableDouble `
            -Value (Get-CellValue -Row $row -ColumnName $remainingStoryPointsColumnName) `
            -ColumnName $remainingStoryPointsColumnName `
            -JiraKey $key
    }

    $percentComplete = ConvertTo-ProjectPercentComplete -StoryPoints $storyPoints -RemainingStoryPoints $remainingStoryPoints

    $issue = [pscustomobject]@{
        Key                  = $key
        IssueType            = $issueType
        Summary              = $summary.Trim()
        StoryPoints          = $storyPoints
        RemainingStoryPoints = $remainingStoryPoints
        PercentComplete      = $percentComplete
        ValidationStatus     = Get-IssueValidationStatus -StoryPoints $storyPoints -RemainingStoryPoints $remainingStoryPoints
    }

    $issueByKey[$key] = $issue
    [void]$issues.Add($issue)
}

if ($issues.Count -eq 0) {
    if ($resolvedSyncReportCsvPath) {
        Export-SyncReport -Rows $syncReportRows -Path $resolvedSyncReportCsvPath
        Write-Host "Wrote sync report: $resolvedSyncReportCsvPath"
        Write-SyncReportSummary -Rows $syncReportRows
    }

    if ($IncludeAllIssueTypes) {
        throw "No Jira issues with keys were found in $resolvedCsvPath"
    }

    throw "No Jira issues matched the included issue types ($($IncludedIssueTypes -join ', ')) in $resolvedCsvPath"
}

Write-Host ""
Write-Host "Run summary"
Write-Host "-----------"
Write-Host "Jira CSV: $resolvedCsvPath"
Write-Host "CSV rows read: $($jiraRows.Count)"
Write-Host "Included rows that will be sent to IMS: $($issues.Count)"
Write-Host "Excluded rows that will not be sent to IMS: $excludedIssueTypeRows"
if ($ProjectPath) {
    Write-Host "IMS input file: $resolvedProjectPath"
}
if (-not $ValidateOnly) {
    Write-Host "IMS output file: $resolvedOutputPath"
}
if ($resolvedPreviewCsvPath) {
    Write-Host "Preview CSV: $resolvedPreviewCsvPath"
}
if ($resolvedSyncReportCsvPath) {
    Write-Host "Sync report CSV: $resolvedSyncReportCsvPath"
}
if ($resolvedLogPath) {
    Write-Host "Log file: $resolvedLogPath"
}

Write-Host ""
Write-Host "CSV column mapping used"
Write-Host "-----------------------"
Write-Host "Jira key: $jiraKeyColumnName"
if ($IncludeAllIssueTypes) {
    Write-Host "Issue type: not required because IncludeAllIssueTypes was used"
}
else {
    Write-Host "Issue type: $issueTypeColumnName"
}
if ($summaryColumnName) {
    Write-Host "Task name/summary: $summaryColumnName"
}
else {
    Write-Warning "Task name/summary column was not found. Task names will use the Jira key."
}
if ($storyPointsColumnName) {
    Write-Host "Total story points: $storyPointsColumnName"
}
else {
    Write-Host "Total story points: not found"
}
if ($remainingStoryPointsColumnName) {
    Write-Host "Remaining story points: $remainingStoryPointsColumnName"
}
else {
    Write-Host "Remaining story points: not found"
}

Write-Host ""
Write-Host "Issue type filter"
Write-Host "-----------------"
if ($IncludeAllIssueTypes) {
    Write-Warning "IncludeAllIssueTypes was used. Initiative/Epic filtering is disabled."
}
else {
    Write-Host "Included issue types: $($IncludedIssueTypes -join ', ')"
    Write-Host "All other issue types are excluded and listed as ExcludedIssueType in the sync report."
}

if ($skippedRows -gt 0) {
    Write-Warning "Skipped $skippedRows CSV rows without a Jira key."
}

if ($excludedIssueTypeRows -gt 0) {
    Write-Host "Excluded $excludedIssueTypeRows Jira rows because their issue type was not included."
    foreach ($group in ($syncReportRows | Where-Object { $_.Action -eq "ExcludedIssueType" } | Group-Object -Property IssueType | Sort-Object -Property Name)) {
        $issueTypeLabel = if ([string]::IsNullOrWhiteSpace($group.Name)) { "(blank)" } else { $group.Name }
        Write-Host "  Excluded $issueTypeLabel rows: $($group.Count)"
    }
}

$validationWarnings = @($issues | Where-Object { $_.ValidationStatus -ne "OK" })
if ($validationWarnings.Count -gt 0) {
    Write-Warning "$($validationWarnings.Count) Jira issues have missing or unusual story point data. Use -PreviewCsvPath to review row-level details."
}

if ($resolvedPreviewCsvPath) {
    Export-IssuePreview -Issues $issues.ToArray() -Path $resolvedPreviewCsvPath
    Write-Host "Wrote calculation preview: $resolvedPreviewCsvPath"
}

if ($resolvedSyncReportCsvPath -and ($ValidateOnly -or $WhatIfPreference)) {
    foreach ($issue in $issues) {
        $severity = if ($issue.ValidationStatus -eq "OK") { "Info" } else { "Warning" }
        $detail = if ($issue.ValidationStatus -eq "OK") {
            "Jira issue passed CSV validation. Microsoft Project was not opened."
        }
        else {
            "Jira issue has validation concerns. Microsoft Project was not opened."
        }

        Add-SyncReportRow `
            -Rows $syncReportRows `
            -Action "ValidatedInputOnly" `
            -Severity $severity `
            -Issue $issue `
            -Detail $detail
    }

    Export-SyncReport -Rows $syncReportRows -Path $resolvedSyncReportCsvPath
    Write-Host "Wrote sync report: $resolvedSyncReportCsvPath"
    Write-SyncReportSummary -Rows $syncReportRows
}

if ($ValidateOnly) {
    Write-Host "Validation complete. No IMS was created or updated."
    return
}

if ($WhatIfPreference) {
    if ($ProjectPath) {
        Write-Host "What if: would update IMS '$resolvedProjectPath' and save '$resolvedOutputPath'."
    }
    else {
        Write-Host "What if: would create new IMS '$resolvedOutputPath'."
    }

    return
}

Assert-ProjectAutomationPrerequisites

$projectApplication = $null
$saved = $false

try {
    $projectApplication = Get-ProjectApplication

    if ($ProjectPath) {
        [void]$projectApplication.FileOpen($resolvedProjectPath)
    }
    else {
        [void]$projectApplication.FileNew()
    }

    $project = $projectApplication.ActiveProject

    $jiraKeyFieldId = Get-TaskFieldId -ProjectApplication $projectApplication -FieldName @($JiraKeyTaskTextField, $JiraKeyFieldName)
    $storyPointsFieldId = Get-TaskFieldId -ProjectApplication $projectApplication -FieldName @($StoryPointsTaskNumberField, $StoryPointsFieldName)
    $remainingStoryPointsFieldId = Get-TaskFieldId -ProjectApplication $projectApplication -FieldName @($RemainingStoryPointsTaskNumberField, $RemainingStoryPointsFieldName)

    Rename-TaskCustomField -ProjectApplication $projectApplication -FieldId $jiraKeyFieldId -FriendlyName $JiraKeyFieldName
    Rename-TaskCustomField -ProjectApplication $projectApplication -FieldId $storyPointsFieldId -FriendlyName $StoryPointsFieldName
    Rename-TaskCustomField -ProjectApplication $projectApplication -FieldId $remainingStoryPointsFieldId -FriendlyName $RemainingStoryPointsFieldName

    $created = 0
    $updated = 0
    $missing = 0
    $noPercent = 0
    $projectOnly = 0
    $duplicateProjectKeys = 0
    $failedPercentUpdates = 0
    $matchedTaskByKey = @{}

    if ($ProjectPath) {
        foreach ($task in (Get-ProjectTasks -Project $project)) {
            $taskJiraKey = $null

            try {
                $taskJiraKey = [string]$task.GetField($jiraKeyFieldId)
            }
            catch {
                Write-Verbose "Could not read jira-key from a task: $($_.Exception.Message)"
                continue
            }

            if ([string]::IsNullOrWhiteSpace($taskJiraKey)) {
                continue
            }

            $taskJiraKey = $taskJiraKey.Trim()

            if ($matchedTaskByKey.ContainsKey($taskJiraKey)) {
                Write-Warning "Multiple Project tasks have jira-key '$taskJiraKey'. Only the first matching task will be updated."
                $duplicateProjectKeys++
                Add-SyncReportRow `
                    -Rows $syncReportRows `
                    -Action "DuplicateProjectJiraKey" `
                    -Severity "Warning" `
                    -Issue ([pscustomobject]@{
                        Key                  = $taskJiraKey
                        IssueType            = ""
                        Summary              = $task.Name
                        StoryPoints          = $null
                        RemainingStoryPoints = $null
                        PercentComplete      = $null
                        ValidationStatus     = "Duplicate jira-key in IMS"
                    }) `
                    -Task $task `
                    -Detail "More than one Project task has jira-key '$taskJiraKey'. Only the first matching task was eligible for update."
                continue
            }

            $matchedTaskByKey[$taskJiraKey] = $task
        }

        foreach ($taskJiraKey in $matchedTaskByKey.Keys) {
            if (-not $issueByKey.ContainsKey($taskJiraKey)) {
                $projectOnly++
                $projectOnlyTask = $matchedTaskByKey[$taskJiraKey]
                Add-SyncReportRow `
                    -Rows $syncReportRows `
                    -Action "ProjectTaskNotInJiraCsv" `
                    -Severity "Warning" `
                    -Issue ([pscustomobject]@{
                        Key                  = $taskJiraKey
                        IssueType            = ""
                        Summary              = $projectOnlyTask.Name
                        StoryPoints          = $null
                        RemainingStoryPoints = $null
                        PercentComplete      = $null
                        ValidationStatus     = "jira-key was not present in Jira CSV"
                    }) `
                    -Task $projectOnlyTask `
                    -Detail "The IMS contains jira-key '$taskJiraKey', but that key was not found in the included Jira CSV rows. The task was not updated."
            }
        }

        foreach ($issue in $issues) {
            if ($matchedTaskByKey.ContainsKey($issue.Key)) {
                $task = $matchedTaskByKey[$issue.Key]
                Update-JiraTaskFields `
                    -Task $task `
                    -Issue $issue `
                    -StoryPointsFieldId $storyPointsFieldId `
                    -RemainingStoryPointsFieldId $remainingStoryPointsFieldId

                if ($null -eq $issue.PercentComplete) {
                    $noPercent++
                    Add-SyncReportRow `
                        -Rows $syncReportRows `
                        -Action "MatchedNoCalculatedPercent" `
                        -Severity "Warning" `
                        -Issue $issue `
                        -Task $task `
                        -Detail "The Jira issue matched an IMS task, but % Complete was not updated because story point data was missing or invalid."
                    continue
                }

                if (Set-TaskPercentComplete -Task $task -PercentComplete $issue.PercentComplete) {
                    $updated++
                    $severity = if ($issue.ValidationStatus -eq "OK") { "Info" } else { "Warning" }
                    Add-SyncReportRow `
                        -Rows $syncReportRows `
                        -Action "UpdatedPercentComplete" `
                        -Severity $severity `
                        -Issue $issue `
                        -Task $task `
                        -Detail "Updated IMS task % Complete to $($issue.PercentComplete)%."
                }
                else {
                    $failedPercentUpdates++
                    Add-SyncReportRow `
                        -Rows $syncReportRows `
                        -Action "PercentUpdateFailed" `
                        -Severity "Error" `
                        -Issue $issue `
                        -Task $task `
                        -Detail "The Jira issue matched an IMS task, but Microsoft Project rejected the % Complete update. See the transcript log for the Project error."
                }
            }
            elseif ($AddMissingTasks) {
                $newTask = Add-JiraTask `
                    -Project $project `
                    -ProjectApplication $projectApplication `
                    -Issue $issue `
                    -JiraKeyFieldId $jiraKeyFieldId `
                    -StoryPointsFieldId $storyPointsFieldId `
                    -RemainingStoryPointsFieldId $remainingStoryPointsFieldId

                $created++
                Add-SyncReportRow `
                    -Rows $syncReportRows `
                    -Action "AddedMissingTask" `
                    -Severity "Warning" `
                    -Issue $issue `
                    -Task $newTask `
                    -Detail "The Jira issue was not found in the IMS, so a new Project task was added because the add-missing option was used."
            }
            else {
                $missing++
                Add-SyncReportRow `
                    -Rows $syncReportRows `
                    -Action "MissingInProject" `
                    -Severity "Warning" `
                    -Issue $issue `
                    -Detail "The Jira issue was present in the CSV but no IMS task with this jira-key was found. The issue was not added because the add-missing option was not used."
            }
        }
    }
    else {
        foreach ($issue in $issues) {
            $newTask = Add-JiraTask `
                -Project $project `
                -ProjectApplication $projectApplication `
                -Issue $issue `
                -JiraKeyFieldId $jiraKeyFieldId `
                -StoryPointsFieldId $storyPointsFieldId `
                -RemainingStoryPointsFieldId $remainingStoryPointsFieldId

            $created++
            $severity = if ($issue.ValidationStatus -eq "OK") { "Info" } else { "Warning" }
            Add-SyncReportRow `
                -Rows $syncReportRows `
                -Action "CreatedTask" `
                -Severity $severity `
                -Issue $issue `
                -Task $newTask `
                -Detail "Created a new IMS task from the Jira CSV row."
        }
    }

    if ($PSCmdlet.ShouldProcess($resolvedOutputPath, "Save Microsoft Project schedule")) {
        if ($ProjectPath -and $InPlace) {
            [void]$projectApplication.FileSave()
        }
        else {
            [void]$projectApplication.FileSaveAs($resolvedOutputPath)
        }

        $saved = $true
    }

    Write-Host "Saved IMS: $resolvedOutputPath"
    Write-Host "Created tasks: $created"
    Write-Host "Updated task % Complete values: $updated"
    Write-Host "Project tasks with jira-key not present in Jira CSV: $projectOnly"
    Write-Host "Duplicate jira-key values found in IMS: $duplicateProjectKeys"
    Write-Host "Failed % Complete updates: $failedPercentUpdates"

    if ($resolvedSyncReportCsvPath) {
        Export-SyncReport -Rows $syncReportRows -Path $resolvedSyncReportCsvPath
        Write-Host "Wrote sync report: $resolvedSyncReportCsvPath"
        Write-SyncReportSummary -Rows $syncReportRows
    }

    if ($missing -gt 0) {
        Write-Warning "$missing Jira issues were not found in the IMS. Re-run with -AddMissingInitiativesAndEpics to append included Initiative/Epic rows."
    }

    if ($noPercent -gt 0) {
        Write-Warning "$noPercent matched Jira issues did not have enough story point data to calculate % Complete."
    }
}
finally {
    if ($null -ne $projectApplication -and -not $LeaveOpen) {
        try {
            if ($saved) {
                [void]$projectApplication.FileClose()
            }
            else {
                [void]$projectApplication.FileClose(0)
            }
        }
        catch {
            Write-Verbose "Could not close the active Project file: $($_.Exception.Message)"
        }

        try {
            [void]$projectApplication.Quit()
        }
        catch {
            Write-Verbose "Could not quit Microsoft Project: $($_.Exception.Message)"
        }

        try {
            [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($projectApplication)
        }
        catch {
            Write-Verbose "Could not release Microsoft Project COM object: $($_.Exception.Message)"
        }
    }
}
}
finally {
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            Write-Verbose "Could not stop transcript: $($_.Exception.Message)"
        }
    }
}
