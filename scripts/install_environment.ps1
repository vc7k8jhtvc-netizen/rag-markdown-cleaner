param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
$MinimumPython = [Version]"3.10"

function Write-InstallerMessage {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host $Message
}

function Read-RebuildConfirmation {
    try {
        return (Read-Host "现有 .venv 不可用，输入 Y 确认删除并重建；其他输入取消").Trim()
    }
    catch {
        return ""
    }
}

function Assert-VenvPathSafe {
    $resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
    $expected = [System.IO.Path]::GetFullPath((Join-Path $resolvedRoot ".venv")).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($VenvPath).TrimEnd('\')
    if (-not $candidate.Equals($expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作：.venv 路径不在项目根目录下。"
    }
    if (Test-Path -LiteralPath $VenvPath) {
        $item = Get-Item -LiteralPath $VenvPath -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "拒绝操作：.venv 是符号链接或 junction。"
        }
    }
}

function Get-PythonVersion {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    $versionCode = "import json,sys; print(json.dumps([sys.version_info[0],sys.version_info[1],sys.version_info[2]]))"
    try {
        $output = & $Executable @PrefixArguments -c $versionCode 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json
        return [Version]::new(
            [int]$parsed[0],
            [int]$parsed[1],
            [int]$parsed[2]
        )
    }
    catch {
        return $null
    }
}

function Find-QualifiedPython {
    $PythonCandidates = @("py.exe", "python.exe")
    $detected = [System.Collections.Generic.List[string]]::new()

    foreach ($candidate in $PythonCandidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            $detected.Add("$candidate：未找到")
            continue
        }

        if ($candidate -eq "py.exe") {
            $version = Get-PythonVersion -Executable $candidate -PrefixArguments @("-3")
            if ($null -ne $version) {
                $detected.Add("$candidate：$version")
                if ($version -ge $MinimumPython) {
                    return [pscustomobject]@{
                        Executable = $candidate
                        PrefixArguments = @("-3")
                        Version = $version
                    }
                }
            }
            else {
                $detected.Add("$candidate：无法读取版本")
            }
        }
        else {
            $version = Get-PythonVersion -Executable $candidate
            if ($null -ne $version) {
                $detected.Add("$candidate：$version")
                if ($version -ge $MinimumPython) {
                    return [pscustomobject]@{
                        Executable = $candidate
                        PrefixArguments = @()
                        Version = $version
                    }
                }
            }
            else {
                $detected.Add("$candidate：无法读取版本")
            }
        }
    }

    Write-InstallerMessage "未找到可用的 Python 3.10+。已检测结果："
    foreach ($item in $detected) { Write-InstallerMessage "  $item" }
    Write-InstallerMessage "需要 Python 3.10 或更高版本。请安装官方 Python 后重新运行一键安装.bat。"
    return $null
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][pscustomobject]$Python,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Python.Executable @($Python.PrefixArguments + $Arguments)
    return $LASTEXITCODE
}

function Test-VenvHealth {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        return $false
    }
    try {
        $version = Get-PythonVersion -Executable $VenvPython
        if ($null -eq $version -or $version -lt $MinimumPython) { return $false }
        & $VenvPython -m pip --version *> $null
        if ($LASTEXITCODE -ne 0) { return $false }
        & $VenvPython -c "import clean_auto; print(clean_auto.__version__)" *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function New-Venv {
    param([Parameter(Mandatory = $true)][pscustomobject]$Python)

    Assert-VenvPathSafe
    Write-InstallerMessage "正在创建项目虚拟环境：$VenvPath"
    $exitCode = Invoke-Python -Python $Python -Arguments @("-m", "venv", $VenvPath)
    if ($exitCode -ne 0) {
        throw "创建 .venv 失败，退出码：$exitCode"
    }
}

function Install-Project {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "prompt.md") -PathType Leaf)) {
        throw "缺少项目文件：prompt.md"
    }
    foreach ($directoryName in @("input", "output", "logs")) {
        [System.IO.Directory]::CreateDirectory((Join-Path $ProjectRoot $directoryName)) | Out-Null
    }

    Write-InstallerMessage "正在使用项目 .venv 安装项目依赖..."
    & $VenvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        throw "项目依赖安装失败，退出码：$LASTEXITCODE"
    }
    & $VenvPython -m pip --version
    if ($LASTEXITCODE -ne 0) { throw "项目 .venv 的 pip 不可用。" }
    & $VenvPython -c "import clean_auto; print(clean_auto.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "clean_auto 导入或版本读取失败。" }
}

function Invoke-Installer {
    $python = Find-QualifiedPython
    if ($null -eq $python) { return 1 }

    try {
        Assert-VenvPathSafe
        if (Test-Path -LiteralPath $VenvPath) {
            if (Test-VenvHealth) {
                Write-InstallerMessage "检测到健康的项目 .venv，将保留并检查项目依赖。"
            }
            else {
                Write-InstallerMessage "检测到损坏、不兼容或无法导入项目的 .venv。"
                $confirmation = Read-RebuildConfirmation
                if ($confirmation -ne "Y") {
                    Write-InstallerMessage "未确认重建，未删除 .venv，安装已取消。"
                    return 1
                }
                Assert-VenvPathSafe
                Remove-Item -LiteralPath $VenvPath -Recurse -Force
                New-Venv -Python $python
            }
        }
        else {
            New-Venv -Python $python
        }

        Install-Project
        Write-InstallerMessage ""
        Write-InstallerMessage "环境配置成功。现在可以双击一键菜单.bat。"
        return 0
    }
    catch {
        Write-InstallerMessage "环境配置失败：$($_.Exception.Message)"
        return 1
    }
}

exit (Invoke-Installer)
