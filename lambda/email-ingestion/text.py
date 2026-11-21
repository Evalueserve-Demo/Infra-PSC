"""Reducing an email body to something safe to carry between stages.

Outlook sends HTML, and a marketing or phishing message can run to hundreds of
kilobytes of it. Step Functions and SQS both cap a payload at 256 KB, so the
raw body cannot travel through the pipeline: an oversized one failed
StartExecution outright and orphaned the message.

The full body is kept in Aurora. What travels is this: tags stripped,
whitespace collapsed, and a hard cap well inside the limit. Everything
downstream reads the text a person would have read.
"""

import re

TAGS = re.compile(r"<[^>]+>")
SCRIPT_AND_STYLE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
WHITESPACE = re.compile(r"\s+")

# Comfortably inside the 256 KB payload limit, with room for the rest of the
# case context. Far more than any genuine supplier query needs.
MAX_BODY_CHARS = 20000


def plain_text(body: str | None, limit: int = MAX_BODY_CHARS) -> str:
    """Strip HTML to readable text and cap its length."""
    if not body:
        return ""
    text = SCRIPT_AND_STYLE.sub(" ", body)
    text = WHITESPACE.sub(" ", TAGS.sub(" ", text)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " [truncated]"
