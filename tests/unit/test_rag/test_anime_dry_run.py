from __future__ import annotations

from yamibo_mcp.rag.anime_dry_run import (
    build_anime_dry_run_thread,
    classify_anime_thread,
    clean_anime_rag_text,
    clean_anime_reply_text,
    clean_anime_quote_text,
)


def _thread(**overrides):
    row = {
        "tid": 100,
        "raw_title": "测试帖",
        "display_title": "测试帖",
        "category": "[动画讨论]",
        "publisher": "楼主",
        "pub_time": "2026-01-01",
        "forum_id": 5,
        "content_kind": "discussion",
        "series_id": None,
    }
    row.update(overrides)
    return row


def _floor(**overrides):
    row = {
        "pid": 200,
        "tid": 100,
        "floor_no": 1,
        "publisher": "回帖人",
        "pub_time": "2026-01-01",
        "content": "我觉得这一话的节奏不错，角色关系也更明确了。",
        "has_images": False,
        "quote_text": None,
        "reply_text": None,
    }
    row.update(overrides)
    return row


def test_clean_anime_rag_text_removes_forum_residue():
    result = clean_anime_rag_text(
        "cujohmarisa 发表于 2025-3-28 03:12\n\n"
        "我是喜欢等动画完结后一口气看完 yamiboqe009\n"
        "本帖最后由 abc 于 2025-3-29 11:20 编辑"
    )

    assert result.changed
    assert "发表于" not in result.text
    assert "yamiboqe" not in result.text
    assert "本帖最后由" not in result.text
    assert set(result.rules) >= {"orig_post_line", "yamibo_emoji", "inline_edit_notice"}


def test_clean_anime_rag_text_removes_inline_legacy_quote_prefixes():
    result = clean_anime_rag_text(
        "原帖由MP 于 2008-8-19 00:08 发表 书在资源区已经有了。\n"
        "葵 在 2005/5/15 23:05 发表: XDD 我开动了。"
    )

    assert "原帖由" not in result.text
    assert "发表" not in result.text
    assert "书在资源区已经有了" in result.text
    assert "XDD 我开动了" in result.text
    assert set(result.rules) >= {"inline_legacy_quote_prefix", "inline_original_post_prefix"}


def test_clean_anime_rag_text_removes_empty_bracket_after_edit_notice():
    result = clean_anime_rag_text("很好看 [ 本帖最后由 abc 于 2007-1-23 22:43 编辑 ]")

    assert result.text == "很好看"
    assert "empty_bracket" in result.rules


def test_clean_anime_rag_text_removes_attached_yamibo_and_ll_emoji_codes():
    result = clean_anime_rag_text("致郁啊yamiboqe029 监督很懂yamibohk02 真的很萌....ll9 但剧情不错")

    assert result.text == "致郁啊 监督很懂 真的很萌.... 但剧情不错"
    assert set(result.rules) >= {"yamibo_emoji", "legacy_ll_emoji"}


def test_clean_anime_rag_text_removes_digitless_yamibo_emoji_families():
    result = clean_anime_rag_text("太好笑了yamibohu 结尾很甜yamiboshiho yamibohuyamibohu")

    assert result.text == "太好笑了 结尾很甜"
    assert "yamibo_emoji" in result.rules


def test_clean_anime_rag_text_keeps_yamibo_domain_names():
    result = clean_anime_rag_text("参考 https://bbs.yamibo.com/thread-1-1.html 的讨论。")

    assert result.text == "参考 https://bbs.yamibo.com/thread-1-1.html 的讨论。"
    assert "yamibo_emoji" not in result.rules


def test_clean_anime_rag_text_removes_english_last_edited_tail():
    result = clean_anime_rag_text("我就是喜欢这首歌 [ Last edited by 東葉月 on 2004-12-30 at 21:35 ]")

    assert result.text == "我就是喜欢这首歌"
    assert "last_edited_by" in result.rules


def test_clean_anime_rag_text_removes_truncated_last_edited_and_repeated_ll_codes():
    result = clean_anime_rag_text("很好看 ll17ll17 [ Last edited by dio799 on 2005-9-27 ... 后面正文")

    assert result.text == "很好看 后面正文"
    assert set(result.rules) >= {"last_edited_by", "legacy_ll_emoji"}


def test_clean_anime_rag_text_removes_inline_published_prefix():
    result = clean_anime_rag_text("前文保留。 zxc6931 发表于 2010-7-26 14:20 Y得好~ 这个世界多的是选择")

    assert result.text == "前文保留。\nY得好~ 这个世界多的是选择"
    assert "inline_published_prefix" in result.rules


def test_clean_anime_rag_text_removes_discuz_toolbar_published_residue():
    result = clean_anime_rag_text("我的看法基本和 jovita 发表于 2005-9-18 10:38 资料 文集 短消息 一样")

    assert result.text == "我的看法基本和 一样"
    assert "发表于" not in result.text
    assert "资料 文集 短消息" not in result.text
    assert "inline_published_prefix" in result.rules


def test_clean_anime_rag_text_removes_loose_legacy_quote_headers():
    result = clean_anime_rag_text(
        "原帖由 MissingPiece发表 GL是百合的转化层面\n"
        "原帖由 依文洁琳 于 12/12/05 10:42 PM 发表。 是要赞都筑老师还是草川"
    )

    assert "原帖由" not in result.text
    assert result.text == "GL是百合的转化层面\n是要赞都筑老师还是草川"
    assert "inline_legacy_quote_prefix" in result.rules


def test_clean_anime_rag_text_removes_fuzzy_original_post_prefix():
    result = clean_anime_rag_text("TAKI 在 2004-12-2X XX:XX:XX发表: 马上就要到圣母TV一周年纪念日了")

    assert result.text == "马上就要到圣母TV一周年纪念日了"
    assert "inline_original_post_prefix" in result.rules


def test_clean_anime_rag_text_removes_attached_quote_prefixes():
    result = clean_anime_rag_text(
        "[code]原帖由 matsuri 于 2007-8-3 19:41 发表\n楼主认为玖我不该出现\n"
        "我还是认为那都是以前的事原帖由 貝林 于 2006-9-27 21:52 发表\n死编剧"
    )

    assert "原帖由" not in result.text
    assert result.text == "楼主认为玖我不该出现\n我还是认为那都是以前的事 死编剧"
    assert "inline_legacy_quote_prefix" in result.rules


def test_clean_anime_rag_text_removes_attached_published_headers():
    result = clean_anime_rag_text(
        "发表于 2014-10-10 21:01 | 只看该作者\n小学开始玩冒险岛\n"
        "[/url]momo2000y 发表于 2015-1-10 00:11\n凉和麒麟超可爱"
    )

    assert "发表于" not in result.text
    assert "只看该作者" not in result.text
    assert "[/url]" not in result.text
    assert "小学开始玩冒险岛" in result.text
    assert "凉和麒麟超可爱" in result.text
    assert "inline_published_prefix" in result.rules


def test_clean_anime_rag_text_removes_html_style_quote_tags():
    result = clean_anime_rag_text(
        "<quote><url=forum.php?mod=redirect>倒在麦田 发表于 2015-4-9 22:03</url>\n"
        "你的标准比我松</quote>"
    )

    assert "<quote>" not in result.text
    assert "<url" not in result.text
    assert "发表于" not in result.text
    assert result.text == "你的标准比我松"
    assert set(result.rules) >= {"bbcode_quote_tag", "inline_published_prefix"}


def test_clean_anime_rag_text_removes_no_break_attached_author_headers():
    result = clean_anime_rag_text(
        "邦邦和拉拉了解一下hanxin 发表于 2018-5-18 12:11不错的一部漫画\n"
        "崩溃撒娇中catleftear 在 2005-7-29 10:04 PM 发表: 楼主大"
    )

    assert "发表于" not in result.text
    assert "发表:" not in result.text
    assert "邦邦和拉拉了解一下 不错的一部漫画" in result.text
    assert "崩溃撒娇中 楼主大" in result.text
    assert set(result.rules) >= {"inline_published_prefix", "inline_original_post_prefix"}


def test_episode_impression_title_stays_discussion_evidence():
    classification = classify_anime_thread(
        _thread(display_title="土豆蛋黃醬第二話觀後感", raw_title="土豆蛋黃醬第二話觀後感", category=None),
        [_floor(content="这一话的演出有点赶，但是最后的告白很有效。")],
    )

    assert classification.lane == "discussion_evidence"


def test_source_category_without_discussion_marker_becomes_source_text():
    classification = classify_anime_thread(
        _thread(display_title="原创短篇 第一章", raw_title="原创短篇 第一章", category="[原创]"),
        [_floor(content="第一章\n\n" + "她推开门，看见了久违的街道。" * 80)],
    )

    assert classification.lane == "source_text"


def test_low_signal_floor_is_excluded_from_candidate_chunks_by_default():
    result = build_anime_dry_run_thread(
        thread_row=_thread(),
        floor_rows=[_floor(content="yamiboqe001 !!!!!")],
    )

    assert result.floors[0].lane == "low_signal"
    assert result.floors[0].skipped_reason == "low_signal_excluded"
    assert [chunk.chunk_type for chunk in result.chunks] == ["thread_title"]


def test_discussion_floor_uses_lane_aware_split_limit():
    result = build_anime_dry_run_thread(
        thread_row=_thread(display_title="本季动画讨论"),
        floor_rows=[_floor(content="我觉得这一话很值得讨论。" * 80)],
    )

    floor_chunks = [chunk for chunk in result.chunks if chunk.chunk_type == "floor"]
    assert len(floor_chunks) >= 2
    assert all(chunk.lane == "discussion_evidence" for chunk in floor_chunks)
    assert all(len(chunk.text) <= 600 for chunk in floor_chunks)


def test_structured_cleaning_uses_reply_text_as_body_and_keeps_quote_separate():
    result = build_anime_dry_run_thread(
        thread_row=_thread(),
        floor_rows=[
            _floor(
                content="御神乐舞 在 2004-12-23 06:14 PM 发表: 前面的引用正文 人家就要新的就要就要嘛～～～～～！",
                quote_text="御神乐舞 在 2004-12-23 06:14 PM 发表: 前面的引用正文",
                reply_text="人家就要新的就要就要嘛～～～～～！",
            )
        ],
        structured_cleaning=True,
    )

    floor = result.floors[0]
    assert floor.body_source == "reply_text"
    assert floor.quote_source == "quote_text"
    assert floor.clean.text == "人家就要新的就要就要嘛～～～～～！"
    assert floor.quote_clean is not None
    assert floor.quote_clean.text == "前面的引用正文"
    assert floor.quote_policy == "brief"
    assert "发表" not in floor.clean.text


def test_quote_cleaner_removes_non_ascii_and_ascii_quote_headers():
    non_ascii = clean_anime_quote_text("月之炎 发表于 2024-6-22 23:24 终于更新了，撒花~~~")
    ascii_name = clean_anime_quote_text("原帖由llysander 于 2007-12-23 00:02 发表 说起这个她很喜欢用燕型probe")

    assert non_ascii.text == "终于更新了，撒花~~~"
    assert ascii_name.text == "说起这个她很喜欢用燕型probe"
    assert "quote_header" in non_ascii.rules
    assert "quote_header" in ascii_name.rules


def test_reply_cleaner_does_not_remove_normal_published_sentence():
    result = clean_anime_reply_text("这个访谈发表于 2024-6-22，我觉得信息量很大。")

    assert result.text == "这个访谈发表于 2024-6-22，我觉得信息量很大。"
    assert "quote_header" not in result.rules


def test_reply_cleaner_removes_malformed_quote_markup_residue():
    result = clean_anime_reply_text(
        "[quote]原帖由 李小盖 于 2007-11-9 18:19 发表 今天早上别人发给我看后就狂笑一上午[/quote] "
        "能登还是应该留给川澄的"
    )

    assert result.text == "今天早上别人发给我看后就狂笑一上午 能登还是应该留给川澄的"
    assert "quote_header" in result.rules


def test_reply_cleaner_removes_leading_published_header_residue():
    result = clean_anime_reply_text("瓜哥瓜瓜叫 发表于 2016-9-29 01:00 是跟心跳同一個作者喔 是的 心跳也很好看")

    assert result.text == "是跟心跳同一個作者喔 是的 心跳也很好看"
    assert "quote_header" in result.rules


def test_structured_cleaning_marks_long_quote_suppressed():
    result = build_anime_dry_run_thread(
        thread_row=_thread(),
        floor_rows=[
            _floor(
                content="长梧 发表于 2015-5-28 20:59 " + "很长的引用内容" * 80 + " 我只回复一句",
                quote_text="长梧 发表于 2015-5-28 20:59 " + "很长的引用内容" * 80,
                reply_text="我只回复一句",
            )
        ],
        structured_cleaning=True,
    )

    floor = result.floors[0]
    assert floor.quote_policy == "long_suppressed"
    assert floor.quote_heavy_ratio >= 2.0
    assert floor.clean.text == "我只回复一句"
