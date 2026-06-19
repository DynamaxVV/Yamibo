from yamibo_mcp.db.repositories.audit_events import AuditEventsRepository


class TestRecord:
    def test_record_returns_event_id(self, db):
        repo = AuditEventsRepository(db)
        event_id = repo.record(
            actor="test", action="create", target_type="thread", target_id="123",
        )
        assert len(event_id) == 32  # uuid hex

    def test_record_with_before_after(self, db):
        repo = AuditEventsRepository(db)
        event_id = repo.record(
            actor="worker", action="update", target_type="job", target_id="j1",
            before={"status": "queued"}, after={"status": "running"},
        )
        rows = repo.list_recent(limit=1)
        assert len(rows) == 1
        assert rows[0]["event_id"] == event_id
        assert rows[0]["actor"] == "worker"
        assert rows[0]["action"] == "update"
        assert '"status": "queued"' in rows[0]["before_json"]
        assert '"status": "running"' in rows[0]["after_json"]

    def test_record_without_before_after(self, db):
        repo = AuditEventsRepository(db)
        repo.record(actor="sys", action="noop", target_type="system", target_id="0")
        rows = repo.list_recent(limit=1)
        assert rows[0]["before_json"] is None
        assert rows[0]["after_json"] is None


class TestListRecent:
    def test_returns_most_recent_first(self, db):
        repo = AuditEventsRepository(db)
        id1 = repo.record(actor="a", action="first", target_type="t", target_id="1")
        # Force id1 to an earlier timestamp to guarantee ordering
        db.execute(
            "UPDATE audit_events SET created_at = datetime(created_at, '-1 second') WHERE event_id = ?",
            (id1,),
        )
        db.commit()
        id2 = repo.record(actor="b", action="second", target_type="t", target_id="2")
        rows = repo.list_recent()
        assert rows[0]["event_id"] == id2
        assert rows[1]["event_id"] == id1

    def test_respects_limit(self, db):
        repo = AuditEventsRepository(db)
        for i in range(5):
            repo.record(actor="a", action=f"act_{i}", target_type="t", target_id=str(i))
        rows = repo.list_recent(limit=3)
        assert len(rows) == 3

    def test_empty_table(self, db):
        repo = AuditEventsRepository(db)
        rows = repo.list_recent()
        assert rows == []
