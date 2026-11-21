"""Which Bedrock model the agents run on."""

import os

# An inference profile, not a bare model ID: on-demand throughput is not
# supported without one. Every agent — classification, extraction and
# investigation — runs on this one model.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# Classification and extraction are rule-following work, and demo output that
# should not drift between runs.
MODEL_TEMPERATURE = 0.0


def resolve_model_id() -> str:
    return os.getenv("BEDROCK_MODEL_ID") or DEFAULT_MODEL_ID


def resolve_intake_model_id() -> str:
    """The model for the classification and extraction agents (Stages 5 and 6).

    The same model as investigation. Overridable with INTAKE_MODEL_ID or
    BEDROCK_MODEL_ID when one needs to be pinned separately.
    """
    return os.getenv("INTAKE_MODEL_ID") or resolve_model_id()
