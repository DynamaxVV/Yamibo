from datetime import datetime, timedelta, timezone
import os
import pytest
from yamibo_mcp.db.connection import DatabaseConnection
from yamibo_mcp.db.migrations import migrate
from yamibo_mcp.db.repositories.scheduled_tasks import ScheduledTasksRepository
from yamibo_mcp.application.scheduled_task_scheduler import schedule_due_tasks, trigger_task


def test_scheduled_atomicity_claim_and_catchup(pg_engine):
    if os.environ.get('YAMIBO_TEST_PG_URL') and os.environ.get('YAMIBO_TEST_PG_ISOLATED') != '1':
        pytest.skip('requires explicitly isolated database')
    conn = DatabaseConnection(pg_engine.connect(), backend='postgres')
    migrate(conn, schema='public')
    conn.commit()
    now = datetime(2026,9,26,1,tzinfo=timezone.utc)
    repo = ScheduledTasksRepository(conn)
    args = dict(owner_id='test', name='archive', action='archive_thread',arguments={'tid':99999123},schedule_kind='cron',timezone_name='Asia/Shanghai',cron='0 9 * * *',now=now-timedelta(days=5))
    with conn.begin():
        task = repo.create(**args)
    # Failure after queue insertion rolls back Job and occurrence and cursor.
    tx = conn.begin()
    with pytest.raises(RuntimeError,match='injected'):
        schedule_due_tasks(conn,now=now,after_job_insert=lambda: (_ for _ in ()).throw(RuntimeError('injected')))
    tx.rollback()
    assert repo.occurrences(task['task_id'],owner_id='test') == []
    conn.commit()
    tx = conn.begin()
    result = schedule_due_tasks(conn,now=now)
    assert len(result)==1 and result[0]['scheduled_at']==now and result[0]['result']['job_id']
    other = DatabaseConnection(pg_engine.connect(), backend='postgres')
    try:
        with other:
            assert schedule_due_tasks(other,now=now)==[]
    finally:
        other.close()
    tx.commit()
    with conn.begin():
        assert schedule_due_tasks(conn,now=now)==[]
        assert len(repo.occurrences(task['task_id'],owner_id='test'))==1
        before=repo.get(task['task_id'],owner_id='test')['next_run_at']
        with pytest.raises(ValueError,match='REVISION_CONFLICT'):
            trigger_task(conn,task_id=task['task_id'],owner_id='test',now=now,expected_revision=99)
        trigger_task(conn,task_id=task['task_id'],owner_id='test',now=now,expected_revision=1)
        assert repo.get(task['task_id'],owner_id='test')['next_run_at']==before
        assert len(repo.occurrences(task['task_id'],owner_id='test'))==2
        assert repo.delete(task['task_id'],owner_id='test',expected_revision=1)
        assert repo.list(owner_id='test')==[]
        assert len(repo.occurrences(task['task_id'],owner_id='test'))==2
    with conn.begin():
        one=repo.create(**{**args,'schedule_kind':'at','cron':None,'at':now-timedelta(days=3),'now':now-timedelta(days=4)})
    with conn.begin():
        result=schedule_due_tasks(conn,now=now)
        assert result[0]['status']=='skipped'
        assert not repo.get(one['task_id'],owner_id='test')['enabled']
    conn.close()


def test_failed_action_does_not_starve_later_task(pg_engine, monkeypatch):
    if os.environ.get('YAMIBO_TEST_PG_URL') and os.environ.get('YAMIBO_TEST_PG_ISOLATED') != '1':
        pytest.skip('requires explicitly isolated database')
    import yamibo_mcp.application.scheduled_task_scheduler as scheduler
    from yamibo_mcp.errors import RemoteAccessPausedError
    conn = DatabaseConnection(pg_engine.connect(), backend='postgres')
    migrate(conn, schema='public')
    conn.commit()
    repo = ScheduledTasksRepository(conn)
    now = datetime(2026,10,1,1,tzinfo=timezone.utc)
    values = dict(owner_id='failure-test', action='archive_thread', schedule_kind='at',
                  timezone_name='UTC', now=now-timedelta(hours=1))
    with conn.begin():
        failing = repo.create(**values, name='first', arguments={'tid':99999125}, at=now-timedelta(minutes=2))
        succeeding = repo.create(**values, name='second', arguments={'tid':99999126}, at=now-timedelta(minutes=1))
    original = scheduler.execute_action
    def execute(connection, action, arguments):
        result = original(connection, action, arguments)
        if arguments['tid'] == 99999125:
            raise RemoteAccessPausedError('sensitive diagnostic should not be persisted')
        return result
    monkeypatch.setattr(scheduler, 'execute_action', execute)
    with conn.begin():
        results = schedule_due_tasks(conn, now=now)
        matched = {item['task_id']: item for item in results}
        assert matched[failing['task_id']]['status'] == 'failed'
        assert matched[failing['task_id']]['result']['error_code'] == 'REMOTE_ACCESS_PAUSED'
        assert matched[succeeding['task_id']]['status'] == 'queued'
        assert not repo.get(failing['task_id'],owner_id='failure-test')['enabled']
        assert conn.execute("SELECT count(*) AS n FROM jobs WHERE tid=99999125").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM jobs WHERE tid=99999126").fetchone()['n'] == 1
        assert repo.occurrences(failing['task_id'],owner_id='failure-test')[0]['status'] == 'failed'
    conn.close()
