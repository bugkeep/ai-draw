[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$PythonPath = "",
    [switch]$CheckOnly,
    [string]$LogDir = ""
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($Root)) {
    $Root = Split-Path $PSScriptRoot -Parent
}
$Root = (Resolve-Path -LiteralPath $Root).Path

if ([string]::IsNullOrEmpty($LogDir)) {
    $runsDir = Join-Path $Root "runs"
    $LogDir = Join-Path $runsDir "windows-install"
}

$venvDir = Join-Path $Root ".venv"
$scriptsDir = Join-Path $Root "scripts"
$venvScripts = Join-Path $venvDir "Scripts"
$venvPython = Join-Path $venvScripts "python.exe"
$checkerScript = Join-Path $scriptsDir "check_environment.py"
$frontendDir = Join-Path $Root "frontend-app"
$frontendDist = Join-Path $Root "frontend-dist"
$healthLogDir = Join-Path $LogDir "healthcheck"

if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogDir ("install_{0}.log" -f $timestamp)

function Write-Step {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Stop-Install {
    param([string]$Message, [int]$Code = 1)
    Write-Step ("[失败] " + $Message)
    Write-Host ("失败: " + $Message) -ForegroundColor Red
    exit $Code
}

function Invoke-Native {
    param(
        [string]$Description,
        [scriptblock]$Action,
        [int]$FailCode = 1
    )
    Write-Step $Description
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Action 2>&1 | ForEach-Object {
            $text = $_.ToString()
            Add-Content -LiteralPath $logFile -Value $text -Encoding UTF8
            Write-Host $text
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($code -ne 0) {
        Stop-Install ("{0} 失败，退出码 {1}" -f $Description, $code) $FailCode
    }
}

Write-Step "========== Draw Agent Studio Windows 安装 =========="
Write-Step ("项目根目录: " + $Root)
Write-Step ("日志文件: " + $logFile)
if ($CheckOnly) {
    Write-Step "检查模式：只检查环境，不安装任何内容"
}

if (-not (Test-Path -LiteralPath (Join-Path $Root "requirements.txt"))) {
    Stop-Install "项目根目录无效，缺少 requirements.txt"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "run.py"))) {
    Stop-Install "项目根目录无效，缺少 run.py"
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDir "package.json"))) {
    Stop-Install "项目根目录无效，缺少 frontend-app\package.json"
}
if (-not (Test-Path -LiteralPath $checkerScript)) {
    Stop-Install ("缺少环境检查脚本: " + $checkerScript)
}

# 1. 选择用于创建虚拟环境的 Python
Write-Step "---------- 1/6 选择 Python ----------"
$basePython = ""
if (-not [string]::IsNullOrEmpty($PythonPath)) {
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        Stop-Install ("指定的 Python 文件不存在: " + $PythonPath) 2
    }
    $basePython = (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command python3 -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        Stop-Install "未找到 Python，请安装 Python 3.11+ 或将其加入 PATH" 2
    }
    $basePython = $pythonCommand.Source
}

# 2. 基础环境检查（检查模式仅运行到此）
Write-Step "---------- 2/6 基础环境检查 ----------"
Invoke-Native -Description "检查 Python、Node.js、npm 与 Chrome" -FailCode 2 -Action {
    & $basePython $checkerScript --root $Root --check-only --python $basePython
}
if ($CheckOnly) {
    Write-Step "========== 检查完成，未执行安装 =========="
    exit 0
}

# 解析 npm.cmd（与 node.exe 同目录）
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    Stop-Install "未找到 Node.js" 3
}
$nodeDir = Split-Path $nodeCommand.Source -Parent
$npmCmd = Join-Path $nodeDir "npm.cmd"
if (-not (Test-Path -LiteralPath $npmCmd)) {
    $npmFallback = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmFallback) {
        $npmCmd = $npmFallback.Source
    }
}
if (-not (Test-Path -LiteralPath $npmCmd)) {
    Stop-Install "未找到 npm.cmd，请修复 Node.js 安装" 4
}
Write-Step ("npm.cmd: " + $npmCmd)

# 3. 创建或复用虚拟环境
Write-Step "---------- 3/6 虚拟环境 ----------"
if (Test-Path -LiteralPath $venvPython) {
    Write-Step ("发现已有虚拟环境，进行校验: " + $venvDir)
    & $venvPython -c "import sys; assert sys.prefix != sys.base_prefix, 'The existing directory is not a Python virtual environment'"
    if ($LASTEXITCODE -ne 0) {
        Stop-Install ("已有虚拟环境无效: " + $venvDir) 6
    }
    $venvVersionOut = & $venvPython --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Install ("已有虚拟环境无法运行: " + ($venvVersionOut -join " ")) 6
    }
    $venvPipOut = & $venvPython -m pip --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Stop-Install ("已有虚拟环境缺少可用 pip，请人工修复该环境（安装程序不会删除或覆盖）: " + ($venvPipOut -join " ")) 6
    }
    if (($venvVersionOut -join " ") -notmatch 'Python\s+(\d+)\.(\d+)') {
        Stop-Install ("无法识别已有虚拟环境版本: " + ($venvVersionOut -join " ")) 6
    }
    $vMajor = [int]$Matches[1]
    $vMinor = [int]$Matches[2]
    if ($vMajor -lt 3 -or ($vMajor -eq 3 -and $vMinor -lt 11)) {
        Stop-Install ("已有虚拟环境 Python 版本需要 3.11+，当前 {0}.{1}；请人工处理，安装程序不会覆盖" -f $vMajor, $vMinor) 6
    }
    Write-Step ("复用虚拟环境: " + ($venvVersionOut -join " "))
} else {
    if (Test-Path -LiteralPath $venvDir) {
        Stop-Install ("虚拟环境目录已存在但缺少 Scripts\python.exe，疑似损坏；请人工处理，安装程序不会删除: " + $venvDir) 6
    }
    Invoke-Native -Description ("创建虚拟环境: " + $venvDir) -FailCode 6 -Action {
        & $basePython -m venv $venvDir
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Stop-Install ("虚拟环境创建后仍缺少: " + $venvPython) 6
    }
    Write-Step "虚拟环境创建成功"
}

# 4. 安装 Python 依赖
Write-Step "---------- 4/6 安装 Python 依赖 ----------"
$requirements = Join-Path $Root "requirements.txt"
Invoke-Native -Description ("pip install -r " + $requirements) -FailCode 7 -Action {
    & $venvPython -m pip install -r $requirements
}

# 5. 前端依赖与构建
Write-Step "---------- 5/6 前端 npm ci 与构建 ----------"
Push-Location $frontendDir
try {
    Invoke-Native -Description "npm ci" -FailCode 8 -Action {
        & $npmCmd ci
    }
    Invoke-Native -Description "npm run build" -FailCode 9 -Action {
        & $npmCmd run build
    }
} finally {
    Pop-Location
}

$indexFile = Join-Path $frontendDist "index.html"
$renderFile = Join-Path $frontendDist "render.html"
if (-not (Test-Path -LiteralPath $indexFile)) {
    Stop-Install ("构建完成但未找到 " + $indexFile) 9
}
if (-not (Test-Path -LiteralPath $renderFile)) {
    Stop-Install ("构建完成但未找到 " + $renderFile) 9
}
Write-Step ("前端构建产物已验证: " + $frontendDist)

# 6. 真实服务健康检查（独立端口、独立数据库、真实 Chrome）
Write-Step "---------- 6/6 真实服务健康检查 ----------"
Invoke-Native -Description "验证本地服务与浏览器渲染" -FailCode 10 -Action {
    & $venvPython $checkerScript --health-check --root $Root --venv-python $venvPython --log-dir $healthLogDir
}

Write-Step "========== 安装成功 =========="
Write-Step ("虚拟环境: " + $venvDir)
Write-Step ("前端构建: " + $frontendDist)
Write-Step ("安装日志: " + $logFile)
Write-Step ("健康检查: " + $healthLogDir)
Write-Step "下一步：双击 start.bat 启动服务，浏览器打开 http://127.0.0.1:8000"
exit 0
