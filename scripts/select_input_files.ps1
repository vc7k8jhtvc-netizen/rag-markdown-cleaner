param(
    [Parameter(Mandatory = $true)]
    [string]$InputRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("files", "directory")]
    [string]$Mode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$SelectionSchema = "rag-cleaner/selection"
$SelectionSchemaVersion = 1

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Get-InputRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $full = Get-NormalizedFullPath -Path $Candidate
    $prefix = $Root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Selected path is outside input/: $full"
    }
    return $full.Substring($prefix.Length).Replace('\', '/')
}

function Test-HasReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $relative = Get-InputRelativePath -Candidate $Candidate -Root $Root
    $current = $Root
    foreach ($part in $relative.Split('/')) {
        $current = Join-Path -Path $current -ChildPath $part
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $true
            }
        }
    }
    return $false
}

function Test-EligibleMarkdown {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not $Path.EndsWith(".md", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    return -not $stem.EndsWith(
        "_cleaned",
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Import-WindowsForms {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        return $true
    }
    catch {
        return $false
    }
}

function Select-Files {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Import-WindowsForms)) {
        Write-Host "Unable to load the Windows file picker. Use CLI --selection-file."
        exit 3
    }

    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.InitialDirectory = $Root
    $dialog.Filter = "Markdown files (*.md)|*.md"
    $dialog.Multiselect = $true
    $dialog.CheckFileExists = $true
    $dialog.RestoreDirectory = $true

    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        exit 2
    }
    return @($dialog.FileNames)
}

function Select-Directory {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (Import-WindowsForms) {
        try {
            $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
            $dialog.SelectedPath = $Root
            $dialog.ShowNewFolderButton = $false
            $dialog.Description = "Select a subdirectory inside input/"
            if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
                exit 2
            }
            return $dialog.SelectedPath
        }
        catch {
            Write-Host "The graphical folder picker is unavailable."
        }
    }

    $relative = Read-Host "Enter a subdirectory path relative to input/ (blank cancels)"
    if ([string]::IsNullOrWhiteSpace($relative)) {
        exit 2
    }
    return Join-Path -Path $Root -ChildPath $relative
}

$inputFull = Get-NormalizedFullPath -Path $InputRoot
if (-not [System.IO.Directory]::Exists($inputFull)) {
    [System.IO.Directory]::CreateDirectory($inputFull) | Out-Null
}

if ((Get-Item -LiteralPath $inputFull -Force).Attributes -band
    [System.IO.FileAttributes]::ReparsePoint) {
    throw "input/ must not be a symbolic link or junction: $inputFull"
}

$relativePaths = [System.Collections.Generic.List[string]]::new()

if ($Mode -eq "files") {
    foreach ($selected in (Select-Files -Root $inputFull)) {
        $relative = Get-InputRelativePath -Candidate $selected -Root $inputFull
        if (-not (Test-EligibleMarkdown -Path $selected)) {
            throw "Only Markdown source files are allowed: $relative"
        }
        if (Test-HasReparsePoint -Candidate $selected -Root $inputFull) {
            throw "Symbolic links and junctions are not allowed: $relative"
        }
        if (-not $relativePaths.Contains($relative)) {
            $relativePaths.Add($relative)
        }
    }
}
else {
    $selectedDirectory = Get-NormalizedFullPath -Path (
        Select-Directory -Root $inputFull
    )
    if ($selectedDirectory.Equals(
        $inputFull,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Select a subdirectory inside input/, not input/ itself."
    }
    $null = Get-InputRelativePath -Candidate $selectedDirectory -Root $inputFull
    if (-not [System.IO.Directory]::Exists($selectedDirectory)) {
        throw "Selected directory does not exist: $selectedDirectory"
    }
    if (Test-HasReparsePoint -Candidate $selectedDirectory -Root $inputFull) {
        throw "Symbolic links and junctions are not allowed: $selectedDirectory"
    }

    foreach ($file in Get-ChildItem -LiteralPath $selectedDirectory -Recurse -File) {
        if (-not (Test-EligibleMarkdown -Path $file.FullName)) {
            continue
        }
        if (Test-HasReparsePoint -Candidate $file.FullName -Root $inputFull) {
            continue
        }
        $relativePaths.Add(
            (Get-InputRelativePath -Candidate $file.FullName -Root $inputFull)
        )
    }
    $sorted = $relativePaths.ToArray()
    [System.Array]::Sort($sorted, [System.StringComparer]::OrdinalIgnoreCase)
    $relativePaths = [System.Collections.Generic.List[string]]::new()
    foreach ($relative in $sorted) {
        $relativePaths.Add($relative)
    }
}

if ($relativePaths.Count -eq 0) {
    Write-Host "No eligible Markdown files were selected."
    exit 2
}

$payload = [ordered]@{
    schema = $SelectionSchema
    schema_version = $SelectionSchemaVersion
    source = [ordered]@{
        kind = "files"
        root = $null
    }
    paths = @($relativePaths)
}

$outputFull = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = [System.IO.Path]::GetDirectoryName($outputFull)
[System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
if ([System.IO.File]::Exists($outputFull)) {
    throw "Selection output already exists: $outputFull"
}

$temporaryPath = Join-Path -Path $outputDirectory -ChildPath (
    ".selection-" + [guid]::NewGuid().ToString("N") + ".tmp"
)

try {
    $json = $payload | ConvertTo-Json -Depth 4
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporaryPath, $json, $utf8)
    [System.IO.File]::Move($temporaryPath, $outputFull)
}
finally {
    if ([System.IO.File]::Exists($temporaryPath)) {
        [System.IO.File]::Delete($temporaryPath)
    }
}

Write-Host "Selection written: $outputFull"
exit 0
