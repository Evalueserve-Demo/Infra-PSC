"""Each agent is one self-contained main.py: the client deploys that file alone."""

import importlib.util
import inspect
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENTS = ("classification", "extraction", "investigation")


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("agent", AGENTS)
def test_an_agent_imports_with_only_its_own_main_py(agent, tmp_path):
    (tmp_path / "main.py").write_bytes((ROOT / "agents" / agent / "main.py").read_bytes())
    code = "import sys; sys.path[:0] = [sys.argv[1]]; import main; assert main.app and main.invoke"
    result = subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path)], capture_output=True, text=True,
                            cwd=tmp_path)

    assert result.returncode == 0, result.stderr


def test_classification_and_extraction_are_deployed_from_the_same_file():
    assert (ROOT / "agents" / "classification" / "main.py").read_bytes() == \
           (ROOT / "agents" / "extraction" / "main.py").read_bytes()


def test_the_intake_agent_keeps_the_prompts_and_layout_the_lambda_records():
    """The intake Lambda records what each agent was told, so its copies must not drift from the agent's."""
    agent = load(ROOT / "agents" / "classification" / "main.py", "agent_main")
    import guardrail, inputs, prompts

    for name in ("CLASSIFICATION_PROMPT", "CLASSIFICATION_OUTPUT_INSTRUCTION", "EXTRACTION_PROMPT",
                 "EXTRACTION_OUTPUT_INSTRUCTION"):
        assert getattr(agent, name) == getattr(prompts, name), name
    for name in ("classification_input", "extraction_input"):
        assert inspect.getsource(getattr(agent, name)) == inspect.getsource(getattr(inputs, name)), name
    assert inspect.getsource(agent.withheld) == inspect.getsource(guardrail.withheld)


def test_the_investigation_agent_keeps_the_prompt_the_lambda_records():
    agent = load(ROOT / "agents" / "investigation" / "main.py", "investigation_main")
    import prompt

    assert agent.PAYMENT_AGENT_PROMPT == prompt.PAYMENT_AGENT_PROMPT
    assert agent.INVESTIGATION_OUTPUT_INSTRUCTION == prompt.INVESTIGATION_OUTPUT_INSTRUCTION
