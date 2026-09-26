# 远端论坛检索

适用：用户要求搜索、浏览某板块当前帖子，或核对远端单帖。

1. `read_forum_profiles` 核对板块名称与 `forum_id`；用户直接提供 ID 时仍按该 ID 调用。多个板块分别检索。
2. 关键词用 `search_forum_threads`，指定页用 `browse_forum_page`，单帖预览用 `inspect_remote_thread`。搜索一次只处理一页，遵守远端限流。
3. 用户问某日发布的帖子（如“昨天”）时，先按论坛所在时区将相对日期换成明确日期；用 `browse_forum_page(order="dateline", forum_id=..., page=...)` 从第 1 页顺序浏览，只保留 `posted_at` 落在目标日期的帖子，遇到更早日期即可停止翻页。不要用默认排序代替发帖时间排序。
4. 只有工具确认远端成功且 `source=forum` 时才说取得实时结果。`local_fallback` 是本地候选；`REMOTE_ACCESS_PAUSED` 等错误时停止远端调用。
5. 需要核对是否归档时再用 `probe_archived_threads`。不要把发现候选自动升级为归档操作。
