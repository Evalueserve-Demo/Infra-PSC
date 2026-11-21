"""Tests for how the classification agent treats an email the model's own filter stops on."""

import pytest

from payment_query_platform.intake import agent_runtime


class FilteredAgent:
    def __init__(self, error):
        self._error = error

    def __call__(self, text):
        return None

    def structured_output(self, model, prompt):
        raise self._error


def classify_with(monkeypatch, error):
    monkeypatch.setattr(agent_runtime, "_agent", lambda prompt: FilteredAgent(error))
    return agent_runtime.classify({"subject": "Gift cards", "body": "Buy gift cards now"})


def test_a_content_filtered_email_is_withheld_not_a_crash(monkeypatch):
    result = classify_with(monkeypatch, ValueError('Model returned stop_reason: content_filtered instead of "tool_use".'))["classification"]
    assert result["request_type"] == "BLOCKED_BY_GUARDRAIL"
    assert result["is_payment_related"] is False


def test_any_other_failure_still_surfaces(monkeypatch):
    with pytest.raises(ValueError):
        classify_with(monkeypatch, ValueError("Model returned stop_reason: max_tokens"))


def test_the_intake_lambda_imports_without_pydantic():
    """The intake Lambda has no pydantic; only the agents do. An import that drags it in fails every run."""
    import pathlib, subprocess, sys
    lambda_file = pathlib.Path(__file__).resolve().parent.parent / "lambda" / "intake-agents" / "lambda_function.py"
    code = (
        "import importlib.util, sys\n"
        "sys.modules['pydantic'] = None\n"
        "spec = importlib.util.spec_from_file_location('intake_lambda', sys.argv[1])\n"
        "spec.loader.exec_module(importlib.util.module_from_spec(spec))\n"
    )
    from tests.conftest import SOURCE_PATH as src
    result = subprocess.run([sys.executable, "-c", code, str(lambda_file)], capture_output=True, text=True,
                            env={**__import__("os").environ, "PYTHONPATH": src})
    assert result.returncode == 0, result.stderr
