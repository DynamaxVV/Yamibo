from datetime import datetime, timezone
import pytest
from yamibo_mcp.db.repositories.scheduled_tasks import cron_occurrence, ScheduledTasksRepository
from yamibo_mcp.application.scheduled_task_actions import validate_action


def dt(value):
    return datetime.fromisoformat(value)


def test_cron_timezone_and_dst_policy():
    assert cron_occurrence('0 9 * * *','Asia/Shanghai',dt('2026-09-25T01:00:00+00:00')) == dt('2026-09-26T01:00:00+00:00')
    # Missing local 02:30 skips the spring transition day.
    assert cron_occurrence('30 2 * * *','America/New_York',dt('2026-03-08T05:00:00+00:00')) == dt('2026-03-09T06:30:00+00:00')
    # Repeated 01:30 occurs once, in fold zero.
    assert cron_occurrence('30 1 * * *','America/New_York',dt('2026-11-01T04:00:00+00:00')) == dt('2026-11-01T05:30:00+00:00')
    assert cron_occurrence('30 1 * * *','America/New_York',dt('2026-11-01T05:30:00+00:00')) == dt('2026-11-02T06:30:00+00:00')
    assert cron_occurrence('0 * * * *','UTC',dt('2026-09-25T01:00:00+00:00'),previous=True) == dt('2026-09-25T01:00:00+00:00')


@pytest.mark.parametrize('expr', ['* * * * * *','@daily','0 0 31 2 *','bad','*/5 * * * *'])
def test_invalid_cron(expr):
    with pytest.raises(ValueError):
        cron_occurrence(expr,'UTC',datetime.now(timezone.utc))


@pytest.mark.parametrize('args', [{'tid':True},{'tid':1,'url':'http://x'},{'tid':1,'forum_id':0},{'tid':1,'mode':'bad'}])
def test_action_restrictions(args):
    with pytest.raises(ValueError):
        validate_action('archive_thread',args)


def test_action_defaults_and_unknown():
    assert validate_action('archive_thread',{'tid':1}) == {'tid':1,'mode':'text_only'}
    with pytest.raises(ValueError):
        validate_action('prompt',{'prompt':'anything'})
    with pytest.raises(ValueError,match='PostgreSQL'):
        ScheduledTasksRepository(object())
