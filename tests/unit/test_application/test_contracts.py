from __future__ import annotations

from yamibo_mcp.application.contracts import AgentResponse, failure, success


class TestSuccess:
    def test_success_returns_ok_true_with_data(self):
        # Arrange
        data = {"tid": 123, "found": True}
        # Act
        result = success(data)
        # Assert
        assert result["ok"] is True
        assert result["data"] == data
        assert "error" not in result

    def test_success_includes_resources_when_provided(self):
        # Arrange
        data = {"tid": 1}
        resources = {"context": "yamibo://threads/1/context"}
        # Act
        result = success(data, resources=resources)
        # Assert
        assert result["resources"] == resources

    def test_success_includes_next_actions_when_provided(self):
        # Arrange
        data = {"tid": 1}
        next_actions = ["archive_thread"]
        # Act
        result = success(data, next_actions=next_actions)
        # Assert
        assert result["next_actions"][0]["tool"] == "archive_thread"

    def test_success_includes_warnings_when_provided(self):
        # Arrange
        data = {"tid": 1}
        warnings = ["some warning"]
        # Act
        result = success(data, warnings=warnings)
        # Assert
        assert result["warnings"] == warnings

    def test_success_omits_empty_optional_fields(self):
        # Arrange & Act
        result = success({"a": 1})
        # Assert
        assert "resources" not in result
        assert "next_actions" not in result
        assert "warnings" not in result


class TestFailure:
    def test_failure_returns_ok_false_with_error(self):
        # Arrange & Act
        result = failure("RemoteFetchError", "connection refused")
        # Assert
        assert result["ok"] is False
        assert result["error"]["code"] == "RemoteFetchError"
        assert result["error"]["message"] == "connection refused"
        assert result["error"]["agent_hint"] == "connection refused"
        assert "data" not in result

    def test_failure_marks_retryable(self):
        # Arrange & Act
        result = failure("Timeout", "timed out", retryable=True)
        # Assert
        assert result["error"]["retryable"] is True

    def test_failure_omits_retryable_when_false(self):
        # Arrange & Act
        result = failure("Timeout", "timed out", retryable=False)
        # Assert
        assert result["error"]["retryable"] is False

    def test_failure_includes_suggested_action(self):
        # Arrange & Act
        result = failure("AuthError", "login required", suggested_action="check cookie")
        # Assert
        assert result["error"]["suggested_actions"][0]["reason"] == "check cookie"

    def test_failure_omits_suggested_action_when_none(self):
        # Arrange & Act
        result = failure("Error", "msg")
        # Assert
        assert "suggested_actions" not in result["error"]


class TestAgentResponse:
    def test_agent_response_dataclass(self):
        # Arrange & Act
        resp = AgentResponse(ok=True, data={"a": 1})
        # Assert
        assert resp.ok is True
        assert resp.data == {"a": 1}
        assert resp.error is None
        assert resp.resources == {}
        assert resp.next_actions == []
        assert resp.warnings == []
        assert resp.side_effects == []
