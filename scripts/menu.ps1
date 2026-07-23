param(
    [string]$BaseDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($BaseDir)) {
    $BaseDir = Split-Path -Parent $PSScriptRoot
}

$BaseDir = [System.IO.Path]::GetFullPath($BaseDir)
$VenvPython = Join-Path $BaseDir ".venv\Scripts\python.exe"
$SelectorScript = Join-Path $PSScriptRoot "select_input_files.ps1"
$InstallerBatch = Join-Path $BaseDir "一键安装.bat"
$Workers = 1

function Clear-MenuHost {
    try {
        Clear-Host
    }
    catch {
        return
    }
}

function Pause-Menu {
    try {
        [void](Read-Host "按 Enter 返回")
    }
    catch {
        return
    }
}

function Read-MenuChoice {
    param([Parameter(Mandatory = $true)][string]$Prompt)

    try {
        $value = Read-Host $Prompt
    }
    catch {
        return $null
    }

    if ($null -eq $value) {
        return $null
    }
    return $value.Trim()
}

function Test-MenuChoice {
    param(
        [AllowNull()][string]$Choice,
        [Parameter(Mandatory = $true)][string[]]$Allowed
    )

    return $null -ne $Choice -and $Allowed -contains $Choice
}

function Test-ProjectPython {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        return $false
    }

    try {
        & $VenvPython -c "import clean_auto.pipeline" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Show-EnvironmentRequired {
    Write-Host ""
    Write-Host "未检测到可用的项目运行环境。"
    Write-Host "请运行一键安装.bat 完成环境配置后，再重新打开菜单。"
    Write-Host "不会使用系统 Python，也不会启动处理任务。"
}

function Invoke-Installer {
    if (-not (Test-Path -LiteralPath $InstallerBatch -PathType Leaf)) {
        Write-Host "未找到一键安装.bat，未执行任何操作。"
        Pause-Menu
        return
    }
    Write-Host "正在启动一键安装.bat..."
    & $InstallerBatch
    $exitCode = $LASTEXITCODE
    Write-Host "安装器已返回，退出码：$exitCode"
    Pause-Menu
}

function Invoke-Cleaner {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    if (-not (Test-ProjectPython)) {
        Show-EnvironmentRequired
        Pause-Menu
        return
    }

    $commandArguments = @("-m", "clean_auto", "--base-dir", $BaseDir) + $Arguments
    Write-Host ""
    Write-Host "正在启动处理命令..."
    & $VenvPython @commandArguments
    $exitCode = $LASTEXITCODE
    Write-Host ""
    Write-Host "处理命令结束，退出码：$exitCode"
    Pause-Menu
}

function Test-MenuHasResumableBatch {
    $batchesDir = Join-Path $BaseDir "logs\batches"
    $latestPath = Join-Path $batchesDir "latest.json"

    if (-not (Test-Path -LiteralPath $latestPath -PathType Leaf)) {
        return $false
    }

    try {
        $latest = Get-Content -LiteralPath $latestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $batchId = [string]$latest.batch_id
        if ([string]::IsNullOrWhiteSpace($batchId)) {
            return $false
        }

        $manifestPath = Join-Path $batchesDir ($batchId + ".json")
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            return $false
        }

        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        foreach ($item in @($manifest.files)) {
            if ([string]$item.status -in @("pending", "interrupted", "running")) {
                return $true
            }
        }
    }
    catch {
        # 损坏或尚未写完的状态文件不应阻止正常启动全量扫描。
        return $false
    }

    return $false
}

function Invoke-MenuStart {
    if (Test-MenuHasResumableBatch) {
        Write-Host "检测到上次未完成任务，正在自动继续..."
        Invoke-Cleaner -Arguments @("--yes", "--resume-batch", "--workers", "$Workers")
        return
    }

    Invoke-Cleaner -Arguments @("--yes", "--workers", "$Workers")
}

function Open-MenuDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        [System.IO.Directory]::CreateDirectory($Path) | Out-Null
    }
    Start-Process -FilePath "explorer.exe" -ArgumentList @($Path)
}

function Open-BatchLog {
    $batchLog = Join-Path $BaseDir "logs\batch.jsonl"
    if (-not (Test-Path -LiteralPath $batchLog -PathType Leaf)) {
        Write-Host "尚无批次日志，未打开文件。"
        Pause-Menu
        return
    }
    Start-Process -FilePath "notepad.exe" -ArgumentList @($batchLog)
}

function Get-HostPowerShell {
    if ($PSVersionTable.PSEdition -eq "Core") {
        return Join-Path $PSHOME "pwsh.exe"
    }
    return Join-Path $PSHOME "powershell.exe"
}

function New-SelectionFile {
    param([Parameter(Mandatory = $true)][ValidateSet("files", "directory")][string]$Mode)

    if (-not (Test-Path -LiteralPath $SelectorScript -PathType Leaf)) {
        Write-Host "未找到文件选择脚本，未启动处理任务。"
        Pause-Menu
        return $null
    }

    $selectionDir = Join-Path $BaseDir "logs\selections"
    [System.IO.Directory]::CreateDirectory($selectionDir) | Out-Null
    $selectionFile = Join-Path $selectionDir ("menu-" + [guid]::NewGuid().ToString("N") + ".json")
    $hostPowerShell = Get-HostPowerShell

    & $hostPowerShell -NoProfile -STA -ExecutionPolicy Bypass -File $SelectorScript `
        -InputRoot (Join-Path $BaseDir "input") -OutputPath $selectionFile -Mode $Mode
    $selectionExitCode = $LASTEXITCODE

    if ($selectionExitCode -eq 2) {
        Write-Host "已取消选择，未启动处理任务。"
        Pause-Menu
        return $null
    }
    if ($selectionExitCode -ne 0 -or -not (Test-Path -LiteralPath $selectionFile -PathType Leaf)) {
        Write-Host "选择失败，未启动处理任务。"
        Pause-Menu
        return $null
    }
    return $selectionFile
}

function Invoke-SelectedFiles {
    param([Parameter(Mandatory = $true)][ValidateSet("files", "directory")][string]$Mode)

    $selectionFile = New-SelectionFile -Mode $Mode
    if ($null -eq $selectionFile) {
        return
    }
    Invoke-Cleaner -Arguments @("--yes", "--workers", "$Workers", "--selection-file", $selectionFile)
}

function Show-SelectionMenu {
    while ($true) {
        Clear-MenuHost
        Write-Host "1. 选择一个或多个 Markdown 文件"
        Write-Host "2. 选择 input 中的子文件夹"
        Write-Host "0. 返回"
        $choice = Read-MenuChoice -Prompt "请输入选项"
        if ($null -eq $choice -or $choice -eq "0") { return }
        switch ($choice) {
            "1" { Invoke-SelectedFiles -Mode files; return }
            "2" { Invoke-SelectedFiles -Mode directory; return }
            default { Write-Host "输入无效，未启动处理任务。"; Pause-Menu }
        }
    }
}

function Show-RecoveryMenu {
    while ($true) {
        Clear-MenuHost
        Write-Host "1. 继续上次未完成任务"
        Write-Host "2. 重试上次失败文件"
        Write-Host "0. 返回"
        $choice = Read-MenuChoice -Prompt "请输入选项"
        if ($null -eq $choice -or $choice -eq "0") { return }
        switch ($choice) {
            "1" { Invoke-Cleaner -Arguments @("--yes", "--resume-batch", "--workers", "$Workers"); return }
            "2" { Invoke-Cleaner -Arguments @("--yes", "--retry-failed", "--workers", "$Workers"); return }
            default { Write-Host "输入无效，未启动处理任务。"; Pause-Menu }
        }
    }
}

function Set-MenuWorkers {
    while ($true) {
        $choice = Read-MenuChoice -Prompt "请输入同时处理数量（1-5，0 返回）"
        if ($null -eq $choice -or $choice -eq "0") { return }
        if (Test-MenuChoice -Choice $choice -Allowed @("1", "2", "3", "4", "5")) {
            $script:Workers = [int]$choice
            Write-Host "已设置为 $Workers，仅在本次菜单会话有效。"
            Pause-Menu
            return
        }
        Write-Host "输入无效，未更改设置。"
    }
}

function Clear-ControlFlags {
    $choice = Read-MenuChoice -Prompt "确认清除暂停和停止标志？输入 Y 确认"
    if ($choice -ne "Y") {
        Write-Host "已取消。"
        Pause-Menu
        return
    }
    Remove-Item -LiteralPath (Join-Path $BaseDir "pause.flag") -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $BaseDir "stop.flag") -Force -ErrorAction SilentlyContinue
    Write-Host "暂停和停止标志已清除。"
    Pause-Menu
}

function Show-MoreMenu {
    while ($true) {
        Clear-MenuHost
        Write-Host "1. 预检查，不正式处理"
        Write-Host "2. 测试处理一个文件"
        Write-Host "3. 打开 input 文件夹"
        Write-Host "4. 打开 logs 文件夹"
        Write-Host "5. 查看批次日志"
        Write-Host "6. 清除暂停和停止标志"
        Write-Host "0. 返回"
        $choice = Read-MenuChoice -Prompt "请输入选项"
        if ($null -eq $choice -or $choice -eq "0") { return }
        switch ($choice) {
            "1" { Invoke-Cleaner -Arguments @("--dry-run", "--workers", "1"); return }
            "2" { Invoke-Cleaner -Arguments @("--yes", "--max-files", "1", "--workers", "$Workers"); return }
            "3" { Open-MenuDirectory -Path (Join-Path $BaseDir "input"); return }
            "4" { Open-MenuDirectory -Path (Join-Path $BaseDir "logs"); return }
            "5" { Open-BatchLog; return }
            "6" { Clear-ControlFlags; return }
            default { Write-Host "输入无效，未执行操作。"; Pause-Menu }
        }
    }
}

while ($true) {
    Clear-MenuHost
    Write-Host "========================================"
    Write-Host "        RAG Markdown 清理工具"
    Write-Host "========================================"
    Write-Host ""
    Write-Host "1. 开始处理全部文件"
    Write-Host "2. 选择文件或文件夹"
    Write-Host "3. 继续或重试任务"
    Write-Host "4. 查看处理状态"
    Write-Host "5. 设置同时处理数量（当前：$Workers）"
    Write-Host "6. 打开处理结果"
    Write-Host "7. 安装或修复运行环境"
    Write-Host "8. 更多功能"
    Write-Host "0. 退出"
    if (-not (Test-ProjectPython)) {
        Write-Host ""
        Write-Host "提示：当前缺少可用 .venv，请选择 7 安装或修复运行环境。"
    }

    $choice = Read-MenuChoice -Prompt "请输入选项"
    if ($null -eq $choice -or $choice -eq "0") { exit 0 }
    switch ($choice) {
        "1" { Invoke-MenuStart }
        "2" { Show-SelectionMenu }
        "3" { Show-RecoveryMenu }
        "4" { Invoke-Cleaner -Arguments @("--batch-status") }
        "5" { Set-MenuWorkers }
        "6" { Open-MenuDirectory -Path (Join-Path $BaseDir "output") }
        "7" { Invoke-Installer }
        "8" { Show-MoreMenu }
        default { Write-Host "输入无效，未执行操作。"; Pause-Menu }
    }
}
