#!/usr/bin/env python3
"""获取真实论坛数据用于离线测试"""

import json
import subprocess
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


def run_command(args: list[str]) -> dict | None:
    """运行命令并返回 JSON 结果"""
    try:
        result = subprocess.run(
            ["uv", "run", "yamibo-mcp-server"] + args,
            capture_output=True,
            text=True,
            timeout=120
        )
        if result.returncode == 0:
            # 找到 JSON 开始的位置
            output = result.stdout.strip()
            
            # 尝试直接解析整个输出
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                pass
            
            # 尝试找到 JSON 开始的位置
            json_start = output.find("{")
            if json_start >= 0:
                try:
                    return json.loads(output[json_start:])
                except json.JSONDecodeError:
                    pass
            
            # 尝试找到第二个 JSON 对象（标题解析输出有两个 JSON）
            second_json = output.find("{", json_start + 1)
            if second_json >= 0:
                try:
                    return json.loads(output[second_json:])
                except json.JSONDecodeError:
                    pass
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def save_fixture(data: dict, path: Path):
    """保存测试数据"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {path}")


def fetch_forum_pages():
    """获取论坛列表页"""
    print("\n=== 获取论坛列表页 ===")
    
    # 选择有代表性的页面
    pages = [
        (1, "最新帖子"),
        (2, "包含已导出帖子"),
        (3, "包含章节范围帖子"),
        (5, "包含高回复帖子"),
        (10, "包含特殊标题前缀"),
        (20, "旧帖子"),
    ]
    
    for page, desc in pages:
        print(f"获取第 {page} 页 ({desc})...")
        data = run_command(["browse-forum-page", "--page", str(page)])
        if data:
            save_fixture(data, FIXTURES_DIR / "forum_pages" / f"page_{page}.json")
        else:
            print(f"  Failed to fetch page {page}")


def fetch_threads():
    """获取帖子详情"""
    print("\n=== 获取帖子详情 ===")
    
    # 选择有代表性的帖子
    threads = [
        (572627, "简单帖子，2层楼"),
        (572617, "13层楼，有回复引用"),
        (572458, "摇曳百合233，有章节名"),
        (569610, "0回复，只有1层楼"),
        (571281, "标题带【!】前缀"),
        (572427, "章节范围51~60"),
        (572530, "已导出帖子"),
        (572448, "长标题，多作者"),
    ]
    
    for tid, desc in threads:
        print(f"获取 TID {tid} ({desc})...")
        data = run_command(["get-thread", "--tid", str(tid)])
        if data:
            save_fixture(data, FIXTURES_DIR / "threads" / f"thread_{tid}.json")
        else:
            print(f"  Failed to fetch thread {tid}")


def fetch_title_parses():
    """获取标题解析结果"""
    print("\n=== 获取标题解析结果 ===")
    
    titles = [
        ("【超时空辉夜姬】[ポテトルス] ray", "simple"),
        ("【金城まち】 对残香施以獠牙 Kakukuroi汉化组", "with_group"),
        ("【提灯喵汉化组】[なもり]摇曳百合 233 两个人的活动记录", "chapter_name"),
        ("【!】【汉化工房九九组】[花野ちあき]校内恋爱 13话其2", "special_prefix"),
        ("[原作:りんご饴サードX漫画:川田暁生]公爵千金的笼络任务—第08话", "complex_title"),
        ("【个人汉化】[きさらぎ壱吾]撩拨", "short_title"),
    ]
    
    for title, name in titles:
        print(f"解析标题: {name}...")
        data = run_command(["parse-thread-title", title])
        if data:
            save_fixture(data, FIXTURES_DIR / "title_parses" / f"{name}.json")
        else:
            print(f"  Failed to parse title: {name}")


def create_edge_cases():
    """创建边界条件测试数据"""
    print("\n=== 创建边界条件测试数据 ===")
    
    # 空楼层
    empty_floor = {
        "tid": 999999,
        "url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=999999",
        "display_title": "空楼层帖子",
        "raw_title": "空楼层帖子",
        "publisher": "test_user",
        "pub_time": "2026-1-1 00:00",
        "core_title": None,
        "chapter_name": None,
        "chapter_title": None,
        "series_id": None,
        "series_key": None,
        "archive_status": None,
        "validation_status": None,
        "sync_time": None,
        "export_path": None,
        "resources": None,
        "publisher_uid": "000000",
        "image_count": 0,
        "context_path": None,
        "export_path_abs": None,
        "title_parse": {
            "group_name": None,
            "author_guess": None,
            "core_title_guess": None,
            "normalized_core_title": None,
            "series_key": None,
            "title_aliases_json": "[]",
            "chapter_name": None,
            "chapter_index": None,
            "chapter_title": None,
            "subtitle": None,
            "tags_json": "[]",
            "confidence": 0.0,
            "needs_review": True
        },
        "floors": [],
        "floor_count": 0,
        "series": None
    }
    save_fixture(empty_floor, FIXTURES_DIR / "edge_cases" / "empty_floor.json")
    
    # 缺失字段
    missing_fields = {
        "tid": 999998,
        "display_title": "缺失字段帖子"
    }
    save_fixture(missing_fields, FIXTURES_DIR / "edge_cases" / "missing_fields.json")
    
    # 畸形数据
    malformed = {
        "tid": "not_a_number",
        "url": "invalid_url",
        "display_title": "",
        "raw_title": None,
        "publisher": "",
        "pub_time": "invalid_date",
        "core_title": "",
        "chapter_name": "",
        "chapter_title": "",
        "series_id": "not_a_number",
        "series_key": "",
        "archive_status": "invalid_status",
        "validation_status": "invalid_status",
        "sync_time": "invalid_datetime",
        "export_path": "",
        "resources": "not_a_dict",
        "publisher_uid": "",
        "image_count": -1,
        "context_path": "",
        "export_path_abs": "",
        "title_parse": "not_a_dict",
        "floors": "not_a_list",
        "floor_count": -1,
        "series": "not_a_dict"
    }
    save_fixture(malformed, FIXTURES_DIR / "edge_cases" / "malformed.json")
    
    # 单层楼帖子
    single_floor = {
        "tid": 999997,
        "url": "https://bbs.yamibo.com/forum.php?mod=viewthread&tid=999997",
        "display_title": "单层楼帖子",
        "raw_title": "单层楼帖子",
        "publisher": "test_user",
        "pub_time": "2026-1-1 00:00",
        "core_title": "测试作品",
        "chapter_name": "1",
        "chapter_title": None,
        "series_id": 999,
        "series_key": "测试作品",
        "archive_status": "complete",
        "validation_status": "valid",
        "sync_time": "2026-01-01T00:00:00+00:00",
        "export_path": None,
        "resources": {
            "context": "yamibo://threads/999997/context",
            "metadata": "yamibo://threads/999997/metadata"
        },
        "publisher_uid": "000000",
        "image_count": 1,
        "context_path": "/tmp/test/context.md",
        "export_path_abs": None,
        "title_parse": {
            "group_name": None,
            "author_guess": "测试作者",
            "core_title_guess": "测试作品",
            "normalized_core_title": "测试作品",
            "series_key": "测试作品",
            "title_aliases_json": "[]",
            "chapter_name": "1",
            "chapter_index": 1.0,
            "chapter_title": None,
            "subtitle": None,
            "tags_json": "[]",
            "confidence": 0.9,
            "needs_review": False
        },
        "floors": [
            {
                "pid": 9999999,
                "floor_no": 1,
                "publisher": "test_user",
                "pub_time": "2026-1-1 00:00",
                "has_images": True,
                "content": "测试内容",
                "content_preview": "测试内容"
            }
        ],
        "floor_count": 1,
        "series": {
            "series_id": 999,
            "canonical_title": "测试作品",
            "resources": {
                "index": "yamibo://series/index",
                "chapters": "yamibo://series/999/chapters"
            }
        }
    }
    save_fixture(single_floor, FIXTURES_DIR / "edge_cases" / "single_floor.json")


def main():
    """主函数"""
    print("开始获取真实论坛数据...")
    
    fetch_forum_pages()
    fetch_threads()
    fetch_title_parses()
    create_edge_cases()
    
    print("\n=== 完成 ===")
    print(f"数据已保存到: {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
