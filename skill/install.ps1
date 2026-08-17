# Agent Meeting 技能一键安装脚本（Windows PowerShell）
# 仅把仓库内的 skill/ 子目录部署到 WorkBuddy 用户级技能目录。
# 用法：
#   一键：  irm https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.ps1 | iex
#   手动：  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"

$SkillsDir = Join-Path $env:USERPROFILE ".workbuddy/skills"
$Target    = Join-Path $SkillsDir "agent-meeting"
$Repo      = "https://github.com/shijieweb/agent-meeting.git"

Write-Host "==> 安装目录：$Target"
if (-not (Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }

# 判定脚本来源：若同目录有 SKILL.md，走本地复制；否则远程克隆取 skill/
$Tmp = $null
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $ScriptDir "SKILL.md")) {
    $Src = $ScriptDir
    Write-Host "==> 使用本地仓库部署（skill/ 子目录）"
} else {
    $Tmp = Join-Path $env:TEMP ("agent-meeting-" + [guid]::NewGuid().ToString("N"))
    Write-Host "==> 克隆仓库到临时目录..."
    git clone --depth 1 $Repo $Tmp
    $Src = Join-Path $Tmp "skill"
}

if (-not (Test-Path $Target)) { New-Item -ItemType Directory -Path $Target -Force | Out-Null }
Copy-Item -Path "$Src\*" -Destination $Target -Recurse -Force

if ($Tmp) { Remove-Item $Tmp -Recurse -Force }

Write-Host ""
Write-Host "✅ 技能已安装到 $Target"
Write-Host ""
Write-Host "下一步（在 WorkBuddy 对话里）："
Write-Host "  1) 输入 Skill 命令：agent-meeting"
Write-Host "  2) 对该技能说 ""开会""，它会调用 init 上线"
Write-Host ""
Write-Host "⚙️  后端（server/）需另行启动："
Write-Host "  cd server ; pip install -r requirements.txt ; uvicorn app.main:app --port 8000"
Write-Host ""
Write-Host "📋 依赖：Python 3（仅标准库，零三方依赖）+ git。"
