#!/usr/bin/env bash
# Agent Meeting 技能一键安装脚本（macOS / Linux / Git Bash）
# 用法：
#   一键：  curl -sSL https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.sh | bash
#   手动：  bash install.sh
set -euo pipefail

SKILLS_DIR="${HOME}/.workbuddy/skills"
TARGET="${SKILLS_DIR}/agent-meeting"
REPO="https://github.com/shijieweb/agent-meeting.git"

echo "==> 安装目录：$TARGET"
mkdir -p "$SKILLS_DIR"

if [ -d "${TARGET}/.git" ]; then
  echo "==> 已存在，更新到最新..."
  git -C "$TARGET" pull --ff-only || echo "（更新失败，保留现有版本）"
else
  echo "==> 克隆仓库..."
  git clone "$REPO" "$TARGET"
fi

echo ""
echo "✅ 安装完成。"
echo ""
echo "下一步（在 WorkBuddy 对话里）："
echo "  1) 输入 Skill 命令：agent-meeting"
echo "  2) 对该技能说 \"开会\"，它会调用 init 上线"
echo ""
echo "⚙️  若你的 Agent Hub 不在本机默认位置，先设置环境变量："
echo "  export AGENT_HUB_URL=http://你的hub地址:端口"
echo "  export AGENT_HUB_DATA_DIR=/path/to/agent_hub/data"
echo ""
echo "📋 依赖：Python 3（仅标准库，零三方依赖）+ git。"
