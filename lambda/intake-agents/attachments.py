"""The text of an email's attachments, ready for the intake agents.

Stage 2 read the text out of each attachment and left it in S3; the case
carries only references. Here that text is read back, screened by the
guardrail file by file, and fitted into what the agents accept.

Nothing here fails the email. A text that cannot be read, or that the
guardrail withholds, is noted and left out; the email carries on with its body
and whatever else could be read. Failing instead would lose the email: the
Pipe has already taken it off SQS1 by the time this runs.

Environment:
    ATTACHMENT_BUCKET   the only bucket attachment text is read from
"""

import logging
import os
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

import boto3

from guardrail import AttachmentScreen, screen_attachment
from inputs import classification_input, extraction_input

logger = logging.getLogger(__name__)

# The agent runtimes cut their input at this many characters
# (MAX_INPUT_CHARS in intake/agent_runtime.py).
MAX_AGENT_INPUT_CHARS = 20000

# Room for subject, labels and headings around the body and attachments.
LAYOUT_ALLOWANCE = 1000

# The attachments always get at least this much of the input, so a long email
# body cannot push them out of it.
MIN_ATTACHMENT_SHARE = 10000

# How much of each attachment the classifier sees. Enough to recognise an
# invoice or a statement; deciding "is this ours?" needs no more.
EXCERPT_CHARS = 1000

_s3 = None


def read_text(uri: str) -> str:
    """Read one attachment text from S3."""
    global _s3
    parsed = urllib.parse.urlparse(uri)
    bucket, key = parsed.netloc, parsed.path.lstrip("/")
    expected = os.getenv("ATTACHMENT_BUCKET")
    if parsed.scheme != "s3" or (expected and bucket != expected):
        raise ValueError(f"Attachment text is not in the attachment bucket: {uri}")
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    return _s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def screened(
    attachments: List[Dict[str, Any]],
    read: Optional[Callable[[str], str]] = None,
    screen: Optional[Callable[[str, str], AttachmentScreen]] = None,
) -> List[Dict[str, Any]]:
    """Every attachment with the text an agent may read (None if none), and why."""
    read = read or read_text
    screen = screen or screen_attachment
    result = []
    for attachment in attachments or []:
        name = attachment.get("name") or "attachment"
        uri = attachment.get("extracted_text_uri")
        if attachment.get("extraction_status") != "EXTRACTED" or not uri:
            result.append({"name": name, "text": None,
                           "note": f"no text ({attachment.get('extraction_status') or 'not processed'})"})
            continue
        try:
            text = read(uri)
        except Exception as exc:
            logger.warning("Could not read the text of %s at %s: %s", name, uri, exc)
            result.append({"name": name, "text": None, "note": "text could not be read"})
            continue
        check = screen(name, text)
        result.append({"name": name, "text": check.text, "note": check.reason})
    return result


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:max(limit, 0)].rstrip() + " [truncated]"


def fit(body: str, texts: List[str], limit: int = MAX_AGENT_INPUT_CHARS) -> Tuple[str, List[str]]:
    """Share the agent's input between the body and the attachment texts.

    The attachments get what the body leaves, and never less than
    MIN_ATTACHMENT_SHARE; that share is split evenly between them. The body
    gets the rest.
    """
    if not texts:
        return body, texts
    available = limit - LAYOUT_ALLOWANCE
    share = max(available - len(body), min(MIN_ATTACHMENT_SHARE, available))
    each = share // len(texts)
    fitted = [_cut(text, each) for text in texts]
    return _cut(body, available - sum(len(text) for text in fitted)), fitted


def summary(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What became of each attachment, for the case record. No text."""
    return [{"name": item["name"], "used": item["text"] is not None, "note": item["note"]} for item in items]


def classification_text(subject: str, body: str, sender: str, items: List[Dict[str, Any]]) -> str:
    """The classifier's input: the email, every attachment's name, and an excerpt of each readable one."""
    readable = [item for item in items if item["text"]]
    body, excerpts = fit(body, [_cut(item["text"], EXCERPT_CHARS) for item in readable])
    excerpt_of = {id(item): excerpt for item, excerpt in zip(readable, excerpts)}
    attachments = [{"name": item["name"], "excerpt": excerpt_of.get(id(item))} for item in items]
    return classification_input(subject, body, sender, attachments)


def extraction_text(subject: str, body: str, items: List[Dict[str, Any]]) -> str:
    """The extractor's input: the email and the full text of each readable attachment."""
    readable = [item for item in items if item["text"]]
    body, texts = fit(body, [item["text"] for item in readable])
    attachments = [{"name": item["name"], "extracted_text": text} for item, text in zip(readable, texts)]
    return extraction_input(subject, body, attachments)
