#!/bin/bash
# 生成测试数据
# 用法: bash scripts/generate_fixtures.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FIXTURES_DIR="$PROJECT_DIR/tests/fixtures"

echo "生成测试数据到 $FIXTURES_DIR"

# 论坛列表页
echo "获取论坛列表页..."
for page in 1 2 3 5 10 20; do
  echo "  - 第 $page 页"
  uv run yamibo-mcp-server browse-forum-page --page "$page" > "$FIXTURES_DIR/forum_page_$page.json"
done

# 帖子详情
echo "获取帖子详情..."
for tid in 572627 572617 572458 569610 571281 572427; do
  echo "  - TID $tid"
  uv run yamibo-mcp-server get-thread --tid "$tid" > "$FIXTURES_DIR/thread_$tid.json"
done

# 标题解析
echo "解析标题..."
uv run yamibo-mcp-server parse-thread-title "【超时空辉夜姬】[ポテトルス] ray" > "$FIXTURES_DIR/title_parse_simple.json"
uv run yamibo-mcp-server parse-thread-title "【金城まち】 对残香施以獠牙 Kakukuroi汉化组" > "$FIXTURES_DIR/title_parse_with_group.json"
uv run yamibo-mcp-server parse-thread-title "【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录" > "$FIXTURES_DIR/title_parse_chapter_name.json"
uv run yamibo-mcp-server parse-thread-title "【!】【汉化工房九九组】[花野ちあき]校内恋爱 13话其2" > "$FIXTURES_DIR/title_parse_special_prefix.json"

echo "完成！"
