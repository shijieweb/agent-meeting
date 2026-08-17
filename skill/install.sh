#!/usr/bin/env bash
# Agent Meeting 技能一键安装脚本（macOS / Linux / Git Bash）
# 仅把仓库内的 skill/ 子目录部署到 WorkBuddy 用户级技能目录。
# 用法：
#   一键：  curl -sSL https://raw.githubusercontent.com/shijieweb/agent-meeting/main/install.sh | bash
#   手动：  bash install.sh   （从仓库 skill/ 内运行，走本地复制，免联网）
set -euo pipefail

SKILLS_DIR="${HOME}/.workbuddy/skills"
TARGET="${SKILLS_DIR}/agent-meeting"
REPO="https://github.com/shijieweb/agent-meeting.git"

echo "==> 安装目录：$TARGET"
mkdir -p "$SKILLS_DIR"

# 判定脚本来源：若在仓库 skill/ 内运行（同目录有 SKILL.md），走本地复制，免联网
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP=""
if [ -f "${SCRIPT_DIR}/SKILL.md" ]; then
  SRC="${SCRIPT_DIR}"
  echo "==> 使用本地仓库部署（skill/ 子目录）"
else
  # 远程克隆到临时目录再取 skill/
  TMP="$(mktemp -d)"
  echo "==> 克隆仓库到临时目录..."
  git clone --depth 1 "$REPO" "$TMP"
  SRC="${TMP}/skill"
fi

mkdir -p "$TARGET"
cp -R "${SRC}/." "$TARGET/"

if [ -n "${TMP}" ]; then rm -rf "$TMP"; fi

echo ""
echo "✅ 技能已安装到 $TARGET"
echo ""
echo "下一步（在 WorkBuddy 对话里）："
echo "  1) 输入 Skill 命令：agent-meeting"
echo "  2) 对该技能说 \"开会\"，它会调用 init 上线"
echo ""
echo "⚙️  后端（server/）需另行启动："
echo "  cd server && pip install -r requirements.txt && uvicorn app.main:app --port 8000"
echo ""
echo "📋 依赖：Python 3（仅标准库，零三方依赖）+ git。"
