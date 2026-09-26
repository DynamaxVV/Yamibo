from __future__ import annotations

import os
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from yamibo_mcp.application.daily_brief_facts import (
    _FACT_QUERY,
    build_daily_brief_facts,
    create_manual_daily_issue,
)
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.daily_briefs import DailyBriefsRepository


def _walk_plan(node):
    yield node
    for child in node.get("Plans", []):
        yield from _walk_plan(child)


def test_manual_daily_brief_facts_and_migration_on_isolated_postgres(pg_engine):
    if os.environ.get("YAMIBO_TEST_PG_URL") and os.environ.get("YAMIBO_TEST_PG_ISOLATED", "").lower() not in {"1", "true", "yes", "on"}:
        pytest.skip("this mutating PostgreSQL test requires YAMIBO_TEST_PG_ISOLATED=1")

    suffix = uuid.uuid4().hex[:8]
    forum_id = 700_000 + int(suffix, 16) % 100_000
    disabled_forum_id = forum_id + 200_000
    comic_forum_id = forum_id + 400_000
    bench_forum_id = forum_id + 600_000
    base_tid = 1_400_000_000 + int(suffix, 16) % 100_000_000
    target_day = date(2026, 9, 24)
    start = "2026-09-23 16:00:00+00"
    end = "2026-09-24 16:00:00+00"
    db = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        migrate(db, schema="public")
    finally:
        db.close()

    with pg_engine.begin() as raw:
        schedule_params = {
            "issue_id": f"scheduled-{suffix}-1",
            "owner_id": "acceptance-owner",
            "day": target_day,
            "start": start,
            "end": end,
            "rule_id": f"rule-{suffix}",
        }
        scheduled_insert = text("""INSERT INTO daily_issues
            (issue_id,source_kind,manual_issue_key,rule_id,rule_revision,owner_id,target_day,timezone,
             window_start,window_end,config_snapshot_json,state)
            VALUES (:issue_id,'scheduled',NULL,:rule_id,:rule_revision,:owner_id,:day,'Asia/Shanghai',
             :start,:end,'{}'::jsonb,'created') ON CONFLICT DO NOTHING""")
        first_scheduled = raw.execute(scheduled_insert, {**schedule_params, "rule_revision": 1})
        duplicate_scheduled = raw.execute(
            scheduled_insert,
            {**schedule_params, "issue_id": f"scheduled-{suffix}-2", "rule_revision": 2},
        )
        assert first_scheduled.rowcount == 1
        assert duplicate_scheduled.rowcount == 0
        raw.execute(
            text("""INSERT INTO forums(forum_id,name,content_kind,base_url,enabled)
               VALUES (:fid,'daily facts test','discussion','https://example.invalid',TRUE),
                      (:disabled,'disabled discussion test','discussion','https://example.invalid',FALSE),
                      (:comic,'comic test','comic','https://example.invalid',TRUE),
                      (:bench,'performance test','discussion','https://example.invalid',TRUE)"""),
            {"fid": forum_id, "disabled": disabled_forum_id, "comic": comic_forum_id, "bench": bench_forum_id},
        )
        # Old thread: target-day replies count even though the opening post predates the window.
        raw.execute(
            text("""INSERT INTO threads(tid,page_type,raw_title,pub_time,forum_id,content_kind)
               VALUES (:old,'discussion','old thread','2026-09-01 00:00:00+00',:fid,'discussion'),
                      (:new,'discussion','new thread','2026-09-23 16:00:00+00',:fid,'discussion'),
                      (:disabled_tid,'discussion','disabled forum','2026-09-23 16:00:00+00',:disabled,'discussion'),
                          (:comic_tid,'discussion','wrong forum kind','2026-09-23 16:00:00+00',:comic,'discussion'),
                          (:wrong_kind_tid,'discussion','wrong thread kind','2026-09-23 16:00:00+00',:fid,'comic'),
                          (:extra1,'discussion','extra one','2026-09-23 16:00:00+00',:fid,'discussion'),
                          (:extra2,'discussion','extra two','2026-09-23 16:00:00+00',:fid,'discussion')"""),
                {
                    "old": base_tid, "new": base_tid + 1, "disabled_tid": base_tid + 2,
                    "comic_tid": base_tid + 3, "wrong_kind_tid": base_tid + 4,
                    "extra1": base_tid + 5, "extra2": base_tid + 6,
                    "fid": forum_id, "disabled": disabled_forum_id, "comic": comic_forum_id,
                },
            )
        pids = [base_tid + 100_000, base_tid + 100_001, base_tid + 100_002, base_tid + 100_003,
                base_tid + 100_004, base_tid + 100_005]
        raw.execute(
            text("""INSERT INTO floors(pid,tid,floor_no,publisher,publisher_uid,content,pub_time)
               VALUES (:p1,:old,1,'a','1','a',:start),
                      (:p2,:old,2,'b','2','b','2026-09-24 15:59:59+00'),
                      (:p3,:old,3,'c','3','future correction',:end),
                          (:p4,:new,1,'a','1','opening exactly at target-day start',:start),
                          (:p5,:extra1,1,'d','4','extra one',:start),
                          (:p6,:extra2,1,'e','5','extra two',:start)"""),
            {"p1": pids[0], "p2": pids[1], "p3": pids[2], "p4": pids[3], "p5": pids[4], "p6": pids[5],
             "old": base_tid, "new": base_tid + 1, "extra1": base_tid + 5, "extra2": base_tid + 6,
             "start": start, "end": end},
        )
        raw.execute(
            text("""INSERT INTO threads(tid,page_type,raw_title,pub_time,forum_id,content_kind)
               SELECT :tid + n, 'discussion', 'bench ' || n, :start, :bench, 'discussion'
               FROM generate_series(10, 309) AS n"""),
            {"tid": base_tid, "start": start, "bench": bench_forum_id},
        )
        raw.execute(
            text("""INSERT INTO floors(pid,tid,floor_no,publisher,publisher_uid,content,pub_time)
               SELECT :pid_base + ((n - 10) * 100) + k,
                      :tid + n, k, 'bench-user-' || (k % 12), (k % 12)::text,
                      'synthetic performance row', :floor_time
               FROM generate_series(10, 309) AS n
               CROSS JOIN generate_series(1, 100) AS k"""),
            {"pid_base": base_tid + 300_000, "tid": base_tid, "floor_time": "2026-09-24 12:00:00+00"},
        )

    db = DatabaseConnection(pg_engine.connect(), backend="postgres")
    try:
        first, created = create_manual_daily_issue(
            db, owner_id="acceptance-owner", target_day=target_day, forum_ids=[forum_id], top_n=3
        )
        repeated, repeated_created = create_manual_daily_issue(
            db, owner_id="acceptance-owner", target_day=target_day, forum_ids=[forum_id], top_n=3
        )
        assert created is True
        assert repeated_created is False
        assert first["issue_id"] == repeated["issue_id"]
        assert first["window_start"].isoformat() == "2026-09-23T16:00:00+00:00"
        assert first["window_end"].isoformat() == "2026-09-24T16:00:00+00:00"

        facts = build_daily_brief_facts(
            db,
            target_day=target_day,
            forum_ids=[forum_id],
            top_n=3,
            source_pid_limit=5,
            statement_timeout_ms=20000,
        )
        # The two historical target-day replies count; the later correction does not.
        old_thread = next(item for item in facts["candidates"] if item["tid"] == base_tid)
        assert old_thread["target_day_pid_count"] == 2
        assert old_thread["activity_kind"] == "old_thread"
        new_thread = next(item for item in facts["candidates"] if item["tid"] == base_tid + 1)
        assert new_thread["activity_kind"] == "new_thread"
        assert facts["counts"]["old_thread_count_returned"] == 1
        assert facts["counts"]["new_thread_count_returned"] == 2
        assert {item["pid"] for item in old_thread["source_receipts"]} == set(pids[:2])
        assert all(item["target_day"] == "2026-09-24" for item in old_thread["source_receipts"])
        assert all(item["tid"] == base_tid for item in old_thread["source_receipts"])
        assert all(item["facts_version"] == "daily-brief-facts-v2" for item in old_thread["source_receipts"])
        assert facts["coverage"]["coverage_status"] == "local_snapshot_only"
        assert facts["coverage"]["complete"] is False
        assert facts["coverage"]["full_forum_coverage_proven"] is False
        assert facts["coverage"]["excluded_forum_ids"] == []
        assert facts["counts"]["candidate_list_truncated"] is True
        assert len(facts["candidates"]) == 3
        assert [item["tid"] for item in facts["candidates"]] == [base_tid, base_tid + 1, base_tid + 5]

        sampled = build_daily_brief_facts(
            db, target_day=target_day, forum_ids=[bench_forum_id],
            top_n=1, source_pid_limit=3, statement_timeout_ms=20000,
        )["candidates"][0]
        assert [r["pid"] for r in sampled["source_receipts"]] == [
            base_tid + 300_001, base_tid + 300_051, base_tid + 300_100,
        ]
        assert sampled["source_receipt_truncated"] is True
        assert all(r["published_at"] == "2026-09-24T12:00:00+00:00"
                   for r in sampled["source_receipts"])
        single = build_daily_brief_facts(
            db, target_day=target_day, forum_ids=[forum_id],
            top_n=3, source_pid_limit=1, statement_timeout_ms=20000,
        )
        assert all(len(c["source_receipts"]) == 1 for c in single["candidates"])

        attempt = DailyBriefsRepository(db).start_attempt(issue_id=first["issue_id"])
        DailyBriefsRepository(db).finish_attempt(
            attempt_id=attempt["attempt_id"], status="partial", coverage=facts["coverage"]
        )
        revision_1 = DailyBriefsRepository(db).append_report_revision(
            issue_id=first["issue_id"], report={"candidates": facts["candidates"]}, body_markdown="facts",
            stats_receipt=facts, source_receipts=old_thread["source_receipts"],
            coverage_status="local_snapshot_only", gap_reasons=facts["coverage"]["gap_reasons"],
            attempt_id=attempt["attempt_id"],
        )
        revision_2 = DailyBriefsRepository(db).append_report_revision(
            issue_id=first["issue_id"], report={"candidates": facts["candidates"]}, body_markdown="facts amended",
            stats_receipt=facts, source_receipts=old_thread["source_receipts"],
            coverage_status="local_snapshot_only", gap_reasons=facts["coverage"]["gap_reasons"],
            attempt_id=attempt["attempt_id"], regeneration_reason="explicit manual regeneration",
        )
        assert (revision_1["report_revision"], revision_2["report_revision"]) == (1, 2)
        attempts = DailyBriefsRepository(db).list_attempts(first["issue_id"])
        assert [row["attempt_no"] for row in attempts] == [1]
        assert attempts[0]["status"] == "partial"
        assert attempts[0]["coverage_json"]["coverage_status"] == "local_snapshot_only"
        assert DailyBriefsRepository(db).get_issue(first["issue_id"])["state"] == "report_ready"
        db.commit()

        persisted = DatabaseConnection(pg_engine.connect(), backend="postgres")
        try:
            persisted_repo = DailyBriefsRepository(persisted)
            assert len(persisted_repo.list_attempts(first["issue_id"])) == 1
            assert [item["report_revision"] for item in persisted_repo.list_report_revisions(first["issue_id"])] == [1, 2]
        finally:
            persisted.close()

        explain_params = {
            "window_start": "2026-09-23 16:00:00+00",
            "window_end": "2026-09-24 16:00:00+00",
            "scan_limit": 11,
            "source_pid_limit": 5,
                "requested_forums": [bench_forum_id],
        }
        explain_query = _FACT_QUERY.replace("  GROUP BY", " AND t.forum_id = ANY(:requested_forums)\n  GROUP BY")
        plan_value = db.execute(
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + explain_query,
            explain_params,
        ).fetchone()["QUERY PLAN"][0]
        plan_nodes = list(_walk_plan(plan_value["Plan"]))
        execution_ms = plan_value["Execution Time"]
        floor_indexes = sorted({node.get("Index Name") for node in plan_nodes if node.get("Index Name")})
        sequential_scans = [node.get("Relation Name") for node in plan_nodes if node.get("Node Type") == "Seq Scan"]
        # Existing (tid, pub_time) index should serve per-thread receipt lookups.
        assert "idx_floors_tid_pub_time" in floor_indexes
        assert execution_ms < 20000
        print(
            f"daily facts EXPLAIN ANALYZE: 30,000 synthetic floors; "
            f"{execution_ms:.2f} ms; floor indexes={floor_indexes}; "
            f"sequential scan relations={sequential_scans}"
        )
    finally:
        db.close()
        with pg_engine.begin() as raw:
            raw.execute(text("DELETE FROM daily_issues WHERE owner_id = 'acceptance-owner'"))
            raw.execute(text("DELETE FROM floors WHERE tid BETWEEN :lo AND :hi"), {"lo": base_tid, "hi": base_tid + 400})
            raw.execute(text("DELETE FROM threads WHERE tid BETWEEN :lo AND :hi"), {"lo": base_tid, "hi": base_tid + 400})
            raw.execute(text("DELETE FROM forums WHERE forum_id IN (:fid, :disabled, :comic, :bench)"), {"fid": forum_id, "disabled": disabled_forum_id, "comic": comic_forum_id, "bench": bench_forum_id})
