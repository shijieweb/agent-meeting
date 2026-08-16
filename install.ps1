# Agent Meeting 技能一键安装脚本（Windows PowerShell）
# 用法：
#   一键：  irm https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.ps1 | iex
#   手动：  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"

$SkillsDir = Join-Path $env:USERPROFILE ".workbuddy/skills"
$Target    = Join-Path $SkillsDir "agent-meeting"
$Repo      = "https://github.com/shijieweb/agent-meeting.git"

Write-Host "==> 安装目录：$Target"
if (-not (Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }

if (Test-Path (Join-Path $Target ".git")) {
    Write-Host "==> 已存在，更新到最新..."
    git -C $Target pull --ff-only
} else {
    Write-Host "==> 克隆仓库..."
    git clone $Repo $Target
}

Write-Host ""
Write-Host "✅ 安装完成。"
Write-Host ""
Write-Host "下一步（在 WorkBuddy 对话里）："
Write-Host "  1) 输入 Skill 命令：agent-meeting"
Write-Host "  2) 对该技能说 ""开会""，它会调用 init 上线"
Write-Host ""
Write-Host "⚙️  若你的 Agent Hub 不在本机默认位置，先设置环境变量："
Write-Host "  $env:AGENT_HUB_URL = ""http://你的hub地址:端口"""
Write-Host "  $env:AGENT_HUB_DATA_DIR = ""C:\path\to\agent_hub\data"""
Write-Host ""
Write-Host "📋 依赖：Python 3（仅标准库，零三方依赖）+ git。"
