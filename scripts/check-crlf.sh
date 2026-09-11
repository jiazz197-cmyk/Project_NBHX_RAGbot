#!/usr/bin/env bash
# ============================================================================
# CRLF 行尾检测脚本
# 用法: bash scripts/check-crlf.sh
# CI:   在 CI pipeline 中运行，发现 CRLF 即失败退出
# ============================================================================
set -euo pipefail

# 仅检查 git 追踪的文本文件（排除 node_modules, .git, 二进制）
TRACKED_FILES=$(git ls-files -- '*.py' '*.vue' '*.ts' '*.js' '*.yaml' '*.yml' '*.toml' '*.md' '*.json' '*.sh' '*.txt' '*.css' '*.html' '*.scss' '*.env' '*.cfg' '*.ini' '*.conf' '*.sql' 2>/dev/null)

VIOLATIONS=0
echo ":: Checking line endings for CRLF violations..."

while IFS= read -r file; do
    [ -z "$file" ] && continue
    if git show ":0:$file" 2>/dev/null | grep -q $'\r'; then
        echo "  ❌ CRLF: $file"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done <<< "$TRACKED_FILES"

if [ "$VIOLATIONS" -gt 0 ]; then
    echo ""
    echo "❌ Found $VIOLATIONS file(s) with CRLF line endings."
    echo "   Run:  git add --renormalize ."
    echo "   Then: git commit -m 'chore: normalize line endings to LF'"
    exit 1
else
    echo "  ✅ All tracked text files are LF."
    exit 0
fi
