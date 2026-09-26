"""Reader-facing identity and editorial scope for independent forum reports."""

SECTIONS = {
    33: ("海域区", "话题与生活交流", "写清具体经历、争点与可用的经验建议；保护私人细节，不把个人倾诉推广为群体画像。"),
    5: ("动漫区", "作品资讯与剧情讨论", "展开具体剧情、人物关系和观点理由；剧透须在专题标题或正文开头提示，观众猜测不能写成官方事实。"),
    30: ("漫画区", "漫画更新与读者讨论", "区分章节、汉化、单行本或特典更新与读者讨论。写清作品和章节信息；仅有标题或图片占位时不编造剧情，不把读者回复当作亲自阅读漫画的结论。"),
}


def report_section(facts):
    coverage = facts.get("coverage") or {}
    ids = coverage.get("requested_forum_ids") or coverage.get("local_archived_forum_ids") or sorted({c.get("forum_id") for c in facts.get("candidates", []) if c.get("forum_id")})
    if len(ids) == 1 and ids[0] in SECTIONS:
        name, subtitle, instruction = SECTIONS[ids[0]]
        return {"name": name, "subtitle": subtitle, "instruction": instruction}
    return {"name": "讨论", "subtitle": "讨论动态", "instruction": "按来源板块区分主题，不将不同板块写成同一个社区共识。"}
