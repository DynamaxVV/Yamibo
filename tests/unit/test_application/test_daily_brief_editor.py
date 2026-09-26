from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from yamibo_mcp.application.daily_brief_editor import DailyBriefEditor


@pytest.fixture
def facts():
    return {
        "target_day": "2026-09-24",
        "timezone": "Asia/Shanghai",
        "window_start_utc": "2026-09-23T16:00:00+00:00",
        "window_end_utc": "2026-09-24T16:00:00+00:00",
        "counts": {"candidate_count_returned": 1, "target_day_pid_count": 4},
        "coverage": {"complete": False, "coverage_status": "local_snapshot_only"},
        "candidates": [
            {
                "tid": 10,
                "title": "候选讨论",
                "target_day_pid_count": 2,
                "source_receipts": [{"tid": 10, "pid": 101}, {"tid": 10, "pid": 102}],
            }
        ],
    }


def read_receipts():
    return [
        {
            "receipt_id": "day-1",
            "tid": 10,
            "pid": 101,
            "published_at": "2026-09-24T02:00:00+00:00",
            "content": "目标日原文。",
        },
        {
            "receipt_id": "old-1",
            "tid": 10,
            "pid": 90,
            "published_at": "2026-09-22T02:00:00+00:00",
            "content": "更早背景。",
        },
        {
            "receipt_id": "later-1",
            "tid": 10,
            "pid": 120,
            "published_at": "2026-09-25T02:00:00+00:00",
            "content": "目标日之后的更正。",
        },
    ]


class FakeAgent:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.prompt = None

    async def run(self, prompt):
        self.prompt = prompt
        if self.error:
            raise self.error
        return SimpleNamespace(output=self.output)


def test_preserves_facts_and_validates_receipt_time_roles(facts):
    facts_before = {**facts, "counts": dict(facts["counts"])}
    agent = FakeAgent(
        {
            "recommendations": [
                {
                    "tid": 10,
                    "summary": "昨日讨论了一个重要分歧，之后出现更正。",
                    "reason": "更正改变了原先的事实判断。",
                    "citations": [
                        {"receipt_id": "day-1", "time_role": "target_day"},
                        {"receipt_id": "old-1", "time_role": "background"},
                        {"receipt_id": "later-1", "time_role": "followup"},
                    ],
                }
            ]
        }
    )

    result = asyncio.run(DailyBriefEditor(model=agent).edit(facts, read_receipts()))

    assert result["facts"] == facts_before
    assert result["facts"]["counts"] == facts["counts"]
    assert result["status"] == "partial"  # deterministic local coverage is incomplete
    assert result["editorial"]["recommendations"][0]["tid"] == 10
    assert result["rejected_recommendations"] == []
    assert "receipt_id" in agent.prompt


def test_rejects_fabricated_wrong_thread_and_misclassified_citations(facts):
    agent = FakeAgent(
        {
            "recommendations": [
                {
                    "tid": 10,
                    "summary": "没有真实来源支撑。",
                    "reason": "编造回执。",
                    "citations": [{"receipt_id": "made-up", "time_role": "target_day"}],
                },
                {
                    "tid": 999,
                    "summary": "越界讨论。",
                    "reason": "不在候选内。",
                    "citations": [{"receipt_id": "day-1", "time_role": "target_day"}],
                },
                {
                    "tid": 10,
                    "summary": "把更正冒充昨天。",
                    "reason": "时间归属不正确。",
                    "citations": [{"receipt_id": "later-1", "time_role": "target_day"}],
                },
            ]
        }
    )

    result = asyncio.run(DailyBriefEditor(model=agent).edit(facts, read_receipts()))

    assert result["editorial"]["recommendations"] == []
    assert len(result["rejected_recommendations"]) == 3
    assert all(result["rejected_recommendations"])
    assert "模型推荐引用无效" in result["gaps"][0]
    assert result["facts"] == facts


def test_model_error_returns_persistable_partial_with_original_facts(facts):
    result = asyncio.run(
        DailyBriefEditor(model=FakeAgent(error=RuntimeError("provider failed"))).edit(
            facts, read_receipts()
        )
    )

    assert result["status"] == "partial"
    assert result["facts"] == facts
    assert result["facts"]["counts"] == {"candidate_count_returned": 1, "target_day_pid_count": 4}
    assert result["editorial"] == {"recommendations": []}
    assert result["editor_error"].startswith("日报模型编辑失败（RuntimeError）")


def test_timeout_returns_partial_instead_of_empty_success(facts):
    class SlowAgent:
        async def run(self, prompt):
            import asyncio

            await asyncio.sleep(0.1)

    result = asyncio.run(
        DailyBriefEditor(model=SlowAgent(), timeout_seconds=0.001).edit(
            facts, read_receipts()
        )
    )

    assert result["status"] == "partial"
    assert result["facts"] == facts
    assert result["editor_error"].startswith("日报模型编辑超时")


def test_pydantic_ai_repairs_one_invalid_structured_output(facts):
    calls = 0

    def respond(messages, agent_info):
        nonlocal calls
        calls += 1
        output_tool = agent_info.output_tools[0]
        args = (
            {"unexpected": "first attempt fails schema"}
            if calls == 1
            else {
                "recommendations": [
                    {
                        "tid": 10,
                        "summary": "目标日讨论了关键分歧。",
                        "reason": "已读原文包含明确的不同立场。",
                        "citations": [{"receipt_id": "day-1", "time_role": "target_day"}],
                    }
                ]
            }
        )
        return ModelResponse(
            parts=[ToolCallPart(tool_name=output_tool.name, args=args)]
        )

    model = FunctionModel(respond)
    result = asyncio.run(DailyBriefEditor(model=model).edit(facts, read_receipts()))

    assert calls == 3
    assert result["status"] == "partial"  # coverage remains incomplete
    assert result["editorial"]["recommendations"][0]["tid"] == 10
    assert result["rejected_recommendations"] == []


def topic_claim(receipt_id, role="target_day"):
    return {"text": "具体观点", "kind": "opinion", "temporal_role": role,
            "citations": [{"receipt_id": receipt_id, "time_role": role}]}


def test_topics_keep_background_separate_and_merge_cited_threads(facts):
    facts["candidates"].append({"tid": 20, "source_receipts": [{"pid": 201}]})
    receipts = read_receipts() + [{"receipt_id": "day-2", "tid": 20, "pid": 201,
                                  "published_at": "2026-09-24T03:00:00+00:00", "content": "另一观点"}]
    topic = {"title": "具体议题", "source_tids": [10, 20],
             "claims": [topic_claim("old-1", "background"), topic_claim("day-1"), topic_claim("day-2")]}
    result = asyncio.run(DailyBriefEditor(model=FakeAgent({"recommendations": [], "topics": [topic]})).edit(facts, receipts))
    assert result["editorial"]["topics"] == [topic]
    assert result["rejected_topics"] == []


@pytest.mark.parametrize("claims,source_tids,reason", [
    ([topic_claim("old-1", "background")], [10], "no_target_day_evidence"),
    ([topic_claim("later-1")], [10], "time_role_mismatch"),
    ([topic_claim("invented")], [10], "receipt_not_read_or_invalid"),
    ([topic_claim("day-1")], [999], "receipt_tid_mismatch"),
    ([topic_claim("day-1")], [10, 20], "uncited_source_tid"),
])
def test_rejects_invalid_topic_evidence(facts, claims, source_tids, reason):
    topic = {"title": "议题", "source_tids": source_tids, "claims": claims}
    result = asyncio.run(DailyBriefEditor(model=FakeAgent({"recommendations": [], "topics": [topic]})).edit(facts, read_receipts()))
    assert result["editorial"]["topics"] == []
    assert reason in result["rejected_topics"][0]["reasons"]
    assert result["status"] == "partial"


def test_audit_pass_returns_revised_copy_with_original_evidence(facts):
    class AuditAgent:
        def __init__(self):
            self.prompts = []

        async def run(self, prompt):
            self.prompts.append(prompt)
            summary = "多数读者都反对" if len(self.prompts) == 1 else "一条回复提出反对意见"
            return SimpleNamespace(output={"recommendations": [{
                "tid": 10, "summary": summary, "reason": "可阅读具体观点",
                "citations": [{"receipt_id": "day-1", "time_role": "target_day"}],
            }]})

    agent = AuditAgent()
    facts["candidates"][0]["activity_kind"] = "old_thread_active"
    facts["candidates"][0]["thread_created_at"] = "2020-01-01"
    receipts = read_receipts()
    receipts[0]["truncated"] = True
    result = asyncio.run(DailyBriefEditor(model=agent).edit(facts, receipts))
    assert len(agent.prompts) == 2
    assert "多数读者都反对" in agent.prompts[1]
    assert "目标日原文" in agent.prompts[1]
    assert '"historical_body_version_verified":false' in agent.prompts[1]
    assert '"truncated":true' in agent.prompts[1]
    assert '"activity_kind":"old_thread_active"' in agent.prompts[1]
    assert result["editorial"]["recommendations"][0]["summary"] == "一条回复提出反对意见"


def test_prompt_carries_exact_output_contract(facts):
    from yamibo_mcp.application.daily_brief_editor import _build_prompt, _validate_input
    candidates, receipts = _validate_input(facts, read_receipts())
    prompt = _build_prompt(facts, candidates, receipts)
    assert '禁止使用 thread_id、post_id、title 替代' in prompt
    assert '完整输出 schema' in prompt
    assert '"required":["tid","summary","reason","citations"]' in prompt


def test_output_error_summary_omits_input_and_provider_body():
    from pydantic import ValidationError
    from yamibo_mcp.application.daily_brief_editor import DailyBriefEditorialDraft, _output_error_summary
    try:
        DailyBriefEditorialDraft.model_validate({'recommendations': [{'thread_id': 'secret-value'}]})
    except ValidationError as cause:
        error = RuntimeError('provider secret body')
        error.__cause__ = cause
        summary = _output_error_summary(error)
    assert 'recommendations.0.tid' in summary
    assert 'secret' not in summary


def test_model_gets_feedback_for_wrong_evidence_time_role(facts):
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        claim = topic_claim('old-1' if calls == 1 else 'day-1')
        return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args={
            'recommendations': [], 'topics': [{'title': '当日讨论', 'source_tids': [10], 'claims': [claim]}],
        })])

    result = asyncio.run(DailyBriefEditor(model=FunctionModel(respond)).edit(facts, read_receipts()))
    assert calls == 3  # invalid draft, repaired draft, independent audit
    assert result['editor_error'] is None
    assert len(result['editorial']['topics']) == 1
    assert result['rejected_topics'] == []
