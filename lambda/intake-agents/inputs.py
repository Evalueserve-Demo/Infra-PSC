"""How an email, and the text of its attachments, is laid out for the intake agents.

Kept apart from classification.py and extraction.py, which carry the pydantic
schemas, because the intake Lambda builds these inputs too and its package has
no pydantic. One layout, used by both the Lambda and the agent runtimes.
"""

from typing import List, Optional


def _attachment_label(attachment: dict) -> str:
    return attachment.get("filename") or attachment.get("name") or "attachment"


def classification_input(subject: str, body: str, sender: str = "",
                         attachments: Optional[List[dict]] = None) -> str:
    """The email for the classifier, with a short excerpt of each attachment.

    The classifier only decides whether the email is ours, so it sees enough of
    an attachment to recognise an invoice, not the whole document.
    """
    text = f"From: {sender}\nSubject: {subject}\n\n{body}"
    if attachments:
        lines = ["", "", "ATTACHMENTS:"]
        for attachment in attachments:
            excerpt = attachment.get("excerpt")
            lines.append(f"- {_attachment_label(attachment)}" + (f":\n{excerpt}" if excerpt else ""))
        text += "\n".join(lines)
    return text


def extraction_input(subject: str, body: str, attachments: Optional[List[dict]] = None) -> str:
    """The email and the full text of each attachment, for the extractor."""
    parts = [f"EMAIL SUBJECT:\n{subject}", f"\nEMAIL BODY:\n{body}"]
    for attachment in attachments or []:
        text = attachment.get("extracted_text")
        if not text:
            continue
        parts.append(f"\nATTACHMENT ({_attachment_label(attachment)}):\n{text}")
    return "\n".join(parts)
