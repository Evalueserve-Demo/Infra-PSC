"""Marking a message in Outlook with how far the pipeline took it.

As an email moves through the pipeline it collects coloured labels, one per
milestone, so anyone looking at the mailbox sees where it got to:

    Spam              gray    blocked by the guardrail, not processed
    AI Not Picked Up  purple  classified as not payment related
    AI Picked Up      blue    classified as payment related, taken up
    In Progress       yellow  being investigated
    Draft Ready       green   a reply has been drafted, awaiting review
    Needs Attention   red     the pipeline could not finish it; a person must look

The labels stack rather than replace each other: a payment email that reaches a
draft shows blue, yellow and green together, a visible trail of its path. A
category needs to exist in the mailbox's master list before it shows a colour;
ensure_categories creates them and is safe to call repeatedly.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from s3_mail import uses_s3

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20

# The milestones, in the order an email collects them.
SPAM = "Spam"
NOT_PICKED_UP = "AI Not Picked Up"
PICKED_UP = "AI Picked Up"
IN_PROGRESS = "In Progress"
DRAFT_READY = "Draft Ready"
NEEDS_ATTENTION = "Needs Attention"

# Outlook's palette is named rather than hex: preset7 blue, preset8 purple,
# preset12 gray, preset3 yellow, preset4 green, preset0 red.
CATEGORY_COLOURS = {
    SPAM: "preset12",
    NOT_PICKED_UP: "preset8",
    PICKED_UP: "preset7",
    IN_PROGRESS: "preset3",
    DRAFT_READY: "preset4",
    NEEDS_ATTENTION: "preset0",
}


class CategoryError(RuntimeError):
    """Raised when the mailbox could not be updated."""


def labels_enabled() -> bool:
    """False when mail comes from S3: there is no Outlook mailbox to label."""
    return not uses_s3()


def category_for(classification: Dict[str, Any]) -> str:
    """The label earned at classification: blue if payment related, else purple.

    Driven by is_payment_related rather than request_type, so a new request
    type does not silently fall through to the wrong colour.
    """
    return PICKED_UP if classification.get("is_payment_related") else NOT_PICKED_UP


class MailboxCategories:
    """Reads and writes Outlook categories for one mailbox."""

    def __init__(self, base_url: str, mailbox: str, token_provider: Any, timeout: int = TIMEOUT_SECONDS):
        self.base_url = base_url.rstrip("/")
        self.mailbox = mailbox
        self.token_provider = token_provider
        self.timeout = timeout

    def _request(self, path: str, method: str = "GET", body: Optional[dict] = None):
        url = f"{self.base_url}/users/{self.mailbox}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.token_provider.token()}"}
        if data:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode() or "{}").get("error", {}).get("message", "")
            except Exception:
                pass
            raise CategoryError(f"{method} {path} returned HTTP {exc.code}. {detail}".strip()) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CategoryError(f"{method} {path} unreachable: {exc}") from exc

    def ensure_categories(self) -> List[str]:
        """Create the coloured labels if the mailbox does not have them.

        Safe to call repeatedly. Returns the names it created.
        """
        existing = {c.get("displayName") for c in self._request("/outlook/masterCategories").get("value", [])}
        created = []
        for name, colour in CATEGORY_COLOURS.items():
            if name in existing:
                continue
            self._request("/outlook/masterCategories", "POST",
                          {"displayName": name, "color": colour})
            created.append(name)
            logger.info("Created mailbox category %r (%s)", name, colour)
        return created

    def apply(self, message_id: str, category: str) -> List[str]:
        """Add a category to a message, keeping every label already on it.

        The pipeline's labels stack to show the email's path, so a new one is
        appended rather than replacing the others, and any label a person set by
        hand is kept too. PATCH replaces the whole array, so the current value is
        read first and the new label added to it. Re-applying the same label is a
        no-op.
        """
        quoted = urllib.parse.quote(message_id, safe="")
        current = self._request(f"/messages/{quoted}?$select=categories").get("categories") or []
        if category in current:
            return current
        updated = current + [category]
        self._request(f"/messages/{quoted}", "PATCH", {"categories": updated})
        return updated
