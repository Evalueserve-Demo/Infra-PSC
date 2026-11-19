"""Tests for the record kept of every agent call, and for it never changing the call's outcome."""

import importlib
import io
import json
import pathlib
import subprocess
import sys
import types
from uuid import uuid4

import pytest

from payment_query_platform.intake import classification, invoke_agents
from payment_query_platform.investigation import invoke_agent
from payment_query_platform.storage.database import AgentCallLogRepository


class FakeAudit:
    calls = []

    def __init__(self, _database):
        pass

    def try_record(self, **call):
        self.calls.append(call)


class BrokenDatabase:
    def cursor(self):
        raise RuntimeError("database paused")


class FakeAgentCoreClient:
    def __init__(self, payload=None, error=None):
        self.payload, self.error = payload, error

    def invoke_agent_runtime(self, **_kwargs):
        if self.error:
            raise self.error
        return {"response": io.BytesIO(json.dumps(self.payload).encode())}


def intake(monkeypatch, role, client, repository=FakeAudit, database=object):
    FakeAudit.calls = []
    monkeypatch.setattr(invoke_agents.boto3, "client", lambda *_a, **_k: client)
    monkeypatch.setattr(invoke_agents, "_get_database", database)
    monkeypatch.setattr(invoke_agents, "AgentCallLogRepository", repository)
    monkeypatch.setenv(invoke_agents.RUNTIME_ARNS[role], "test-runtime-arn")


def investigation(monkeypatch, client, repository=FakeAudit, database=object):
    FakeAudit.calls = []
    monkeypatch.setattr(invoke_agent.boto3, "client", lambda *_a, **_k: client)
    monkeypatch.setattr(invoke_agent, "_get_database", database)
    monkeypatch.setattr(invoke_agent, "AgentCallLogRepository", repository)
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "test-runtime-arn")


@pytest.mark.parametrize("role", ["classification", "extraction"])
def test_an_intake_call_records_what_the_agent_was_told_and_answered(monkeypatch, role):
    answer = {role: {"x": 1}, "model_id": "m", "input": "the email as laid out", "analysis": "reasoning",
              "output_schema": {"title": role}}
    intake(monkeypatch, role, FakeAgentCoreClient(payload=answer))

    assert invoke_agents.invoke_runtime(role, {"subject": "s", "body": "b"}, str(uuid4())) == answer

    call = FakeAudit.calls[0]
    assert (call["operation"], call["outcome"], call["model_id"]) == (role, "SUCCESS", "m")
    assert call["system_prompt"] == invoke_agents.AGENT_PROMPTS[role][0]
    assert call["user_input"] == "the email as laid out"
    assert call["output_schema"] == {"title": role}
    assert call["response_payload"] == {role: {"x": 1}, "model_id": "m", "analysis": "reasoning"}


def test_a_failed_intake_call_is_recorded_and_the_error_still_raised(monkeypatch):
    intake(monkeypatch, "classification", FakeAgentCoreClient(error=TimeoutError("runtime timeout")))

    with pytest.raises(TimeoutError, match="runtime timeout"):
        invoke_agents.invoke_runtime("classification", {"subject": "s", "body": "INV-12345"}, str(uuid4()))

    call = FakeAudit.calls[0]
    assert call["outcome"] == "FAILURE" and call["error"] is not None
    assert call["user_input"] == "INV-12345"


def test_an_audit_that_cannot_be_written_does_not_fail_the_intake_call(monkeypatch):
    answer = {"classification": {"x": 1}}
    intake(monkeypatch, "classification", FakeAgentCoreClient(payload=answer),
           repository=AgentCallLogRepository, database=BrokenDatabase)

    assert invoke_agents.invoke_runtime("classification", {"subject": "s", "body": "b"}, str(uuid4())) == answer


def test_an_audit_that_cannot_be_written_does_not_hide_the_intake_error(monkeypatch):
    intake(monkeypatch, "classification", FakeAgentCoreClient(error=TimeoutError("runtime timeout")),
           repository=AgentCallLogRepository, database=BrokenDatabase)

    with pytest.raises(TimeoutError, match="runtime timeout"):
        invoke_agents.invoke_runtime("classification", {"subject": "s", "body": "b"}, str(uuid4()))


def test_an_investigation_call_is_recorded_and_the_schema_kept_out_of_the_result(monkeypatch):
    schema = {"title": "Investigation"}
    investigation(monkeypatch, FakeAgentCoreClient(payload={"narrative": "overdue", "result": {}, "output_schema": schema}))

    result = invoke_agent.invoke_agent("Check invoice INV-12345", str(uuid4()), "key")

    assert result == {"narrative": "overdue", "result": {}}
    call = FakeAudit.calls[0]
    assert (call["component"], call["operation"], call["outcome"]) == ("investigation", "investigate", "SUCCESS")
    assert call["user_input"] == "Check invoice INV-12345" and call["output_schema"] == schema
    assert "output_schema" not in call["response_payload"]


def test_an_audit_that_cannot_be_written_does_not_fail_the_investigation_call(monkeypatch):
    investigation(monkeypatch, FakeAgentCoreClient(payload={"narrative": "ok", "result": {}}),
                  repository=AgentCallLogRepository, database=BrokenDatabase)

    assert invoke_agent.invoke_agent("q", str(uuid4()), "key") == {"narrative": "ok", "result": {}}


@pytest.mark.parametrize("role", ["classification", "extraction"])
def test_the_intake_runtime_returns_what_it_was_given_and_said_for_the_audit(monkeypatch, role):
    runtime = types.ModuleType("bedrock_agentcore.runtime")
    runtime.BedrockAgentCoreApp = type("App", (), {"entrypoint": lambda self, function: function})
    package = types.ModuleType("bedrock_agentcore")
    package.runtime = runtime
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", package)
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", runtime)
    agent_runtime = importlib.import_module("payment_query_platform.intake.agent_runtime")

    class FakeAgent:
        def __call__(self, _text):
            return "first-pass analysis"

        def structured_output(self, model, _instruction):
            if model is classification.Classification:
                return model(is_payment_related=True, request_type="PAYMENT_STATUS_REQUEST",
                             query_summary="Asks about an invoice payment.", confidence=9)
            return model(payment_info=[])

    monkeypatch.setattr(agent_runtime, "_agent", lambda _prompt: FakeAgent())

    answer = agent_runtime.ROLES[role]({"subject": "Status", "body": "Invoice INV-12345"})

    assert answer["analysis"] == "first-pass analysis"
    assert "Invoice INV-12345" in answer["input"]
    assert answer["output_schema"]["title"] == role.capitalize()
    assert role in answer


def test_lambda_modules_import_without_runtime_only_dependencies():
    """The Lambdas carry psycopg only; pydantic and the agent SDKs live in the runtimes."""
    lambdas = pathlib.Path(__file__).resolve().parent.parent / "lambda"
    code = (
        "import importlib.util, sys\n"
        "for name in ('pydantic', 'strands', 'bedrock_agentcore', 'mcp'):\n"
        "    sys.modules[name] = None\n"
        "for path in sys.argv[1:]:\n"
        "    spec = importlib.util.spec_from_file_location('lambda_under_test', path)\n"
        "    spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
    )
    files = [str(lambdas / "intake-agents" / "lambda_function.py"),
             str(lambdas / "investigation-invocation" / "lambda_function.py")]
    result = subprocess.run([sys.executable, "-c", code, *files], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
