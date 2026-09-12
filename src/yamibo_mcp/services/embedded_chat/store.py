from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager

from yamibo_mcp.db.chat_schema import TABLES

TERMINAL = {"completed", "failed", "cancelled", "limited", "interrupted"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, settings):
        self.settings = settings

    @contextmanager
    def transaction(self):
        from yamibo_mcp.db.connection import connect

        with connect(self.settings, bootstrap=False) as conn:
            if conn.backend == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
            yield conn

    @contextmanager
    def reader(self):
        from yamibo_mcp.db.connection import connect

        conn = connect(self.settings, bootstrap=False)
        try:
            yield conn
        finally:
            conn.close()

    def get(self, kind, ident, conn=None, *, lock=False):
        assert kind in TABLES
        if conn is None:
            with self.reader() as conn:
                return self.get(kind, ident, conn)
        suffix = " FOR UPDATE" if lock and conn.backend == "postgres" else ""
        row = conn.execute(
            f"SELECT data FROM chat_{kind} WHERE id=?{suffix}", (ident,)
        ).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row["data"])

    def save(self, kind, value, conn):
        assert kind in TABLES
        conn.execute(
            f"""INSERT INTO chat_{kind}(id,parent_id,data) VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id,data=excluded.data""",
            (value["id"], value.get("parent_id", ""), encode(value)),
        )

    def list(self, kind, parent=None, conn=None):
        assert kind in TABLES
        if conn is None:
            with self.reader() as conn:
                return self.list(kind, parent, conn)
        sql = f"SELECT data FROM chat_{kind}"
        rows = conn.execute(
            sql + (" WHERE parent_id=?" if parent is not None else ""),
            (parent,) if parent is not None else (),
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def event(self, run_id, typ, payload=None, conn=None):
        if conn is None:
            with self.transaction() as conn:
                return self.event(run_id, typ, payload, conn)
        run = self.get("runs", run_id, conn, lock=True)
        run["last_seq"] = run.get("last_seq", 0) + 1
        run["updated_at"] = time.time()
        event = dict(
            payload or {},
            seq=run["last_seq"],
            run_id=run_id,
            type=typ,
            timestamp=run["updated_at"],
        )
        conn.execute(
            "INSERT INTO chat_events(run_id,seq,data) VALUES(?,?,?)",
            (run_id, run["last_seq"], encode(event)),
        )
        self.save("runs", run, conn)
        return event

    def events(self, run_id, after):
        with self.reader() as conn:
            return [
                json.loads(r["data"])
                for r in conn.execute(
                    "SELECT data FROM chat_events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 200",
                    (run_id, after),
                ).fetchall()
            ]

    def update(self, kind, ident, **patch):
        with self.transaction() as conn:
            value = self.get(kind, ident, conn, lock=True)
            value.update(patch)
            self.save(kind, value, conn)
            return value

    def guard(self, run_id, conn):
        run = self.get("runs", run_id, conn, lock=True)
        if run["status"] in TERMINAL or run.get("stop_requested"):
            raise ValueError("RUN_STOPPED")
        if time.time() >= run.get("deadline", float("inf")):
            raise ValueError("RUN_LIMIT_REACHED")
        return run
