"""Reader-facing Markdown rendering for daily discussion reports."""

from __future__ import annotations

from typing import Any

from yamibo_mcp.application.daily_brief_sections import report_section


def render_daily_brief_markdown(report: dict[str, Any]) -> str:
    facts = report["facts"]
    editorial = report.get("editorial") or {}
    sources = {r["receipt_id"]: r for r in report.get("source_receipts", [])}
    section = report_section(facts)
    lines = [f"# {section['name']}日报｜{facts['target_day']}", "",
             f"**{section['name']} · {section['subtitle']}**", "",
             "本报包含当天新帖与旧帖的当天讨论；此前内容仅作为背景。", ""]

    citation_numbers: dict[tuple[int, int], int] = {}

    def citations(values):
        links = []
        for cite in values:
            source = sources.get(cite.get("receipt_id"))
            if source:
                tid, pid = int(source["tid"]), int(source["pid"])
                number = citation_numbers.setdefault((tid, pid), len(citation_numbers) + 1)
                links.append(f"[来源 {number}](https://bbs.yamibo.com/forum.php?mod=redirect&goto=findpost&ptid={tid}&pid={pid})")
        return " · ".join(dict.fromkeys(links))

    topics = editorial.get("topics") or []
    if topics:
        for topic in topics:
            lines += [f"### {topic['title']}", ""]
            topic_citations = []
            for claim in topic.get("claims", []):
                # Evidence metadata belongs in storage; prose carries attribution.
                prefix = {"background": "此前背景：", "followup": "后续补充："}.get(claim.get("temporal_role"), "")
                lines += [prefix + claim['text'], ""]
                topic_citations.extend(claim.get("citations", []))
            lines += ["核查来源：" + citations(topic_citations), ""]
    covered_tids = {tid for topic in topics for tid in topic.get("source_tids", [])}
    recommendations = [item for item in editorial.get("recommendations", []) if item["tid"] not in covered_tids]
    if recommendations:
        lines += ["## 其他动态", ""]
        titles = {c["tid"]: c.get("title") for c in facts.get("candidates", [])}
        for item in recommendations:
            lines += [f"### {titles.get(item['tid']) or item['tid']}", "", item["summary"], "", citations(item.get("citations", [])), ""]
    if not topics and not editorial.get("recommendations"):
        lines += ["尚无通过证据校验的专题正文；以下仅为统计候选。", ""]
    lines += ["## 当日活跃", "", "按当日楼层数排序（包含首楼），不是累计回复数。", ""]
    for rank, item in enumerate(facts.get("candidates", []), 1):
        title = item.get("title") or "未命名帖子"
        kind = {"new_thread": "新帖", "old_thread": "旧帖新讨论"}.get(item.get("activity_kind"), "创建时间未确认")
        lines.append(f"{rank}. **[{title}](https://bbs.yamibo.com/forum.php?mod=viewthread&tid={item['tid']})**（{kind}）— {item['target_day_pid_count']} 个目标日楼层，{item['participant_count']} 位参与者。")
    reasons = facts.get("coverage", {}).get("gap_reasons", [])
    lines += ["", "## 来源与范围", "", "楼层正文为当前归档版本，可能包含事后编辑；发布时间不证明正文为当时版本。"]
    lines.extend(f"- {reason}" for reason in reasons)
    return "\n".join(lines)
