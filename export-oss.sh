#!/usr/bin/env bash
# export-oss.sh —— 把私有开发仓库导出为可公开的开源快照。
# 剔除规则在 .gitattributes 的 export-ignore（vendor 逆向代码 / frontend-legacy）；
# 导出后对残留的服务器 IP 做脱敏。私仓本体不受任何影响。
# 用法：bash export-oss.sh [目标目录，默认 ../StaringAgent]
set -euo pipefail
TARGET="${1:-../StaringAgent}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$TARGET"
git -C "$ROOT" archive HEAD | tar -x -C "$TARGET"
grep -rlI "120\.27\.226\.47" "$TARGET" 2>/dev/null | while read -r f; do
  sed -i 's/120\.27\.226\.47/<SERVER_IP>/g' "$f"
  echo "已脱敏: ${f#"$TARGET"/}"
done
echo "导出完成 → $TARGET"
