"""The message shape carried through every stage of the workflow.

Field names match the production flow specification, so what Stage 1 stores is
what Stage 7 reads. Recipient is part of that contract: it travels with the case
all the way to the payment investigation agent.
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

REQUIRED_FIELDS = (
    "message_id",
    "internet_message_id",
    "subject",
    "sender",
    "recipient",
    "received_at",
    "body",
    "body_hash",
)


@dataclass(frozen=True)
class EmailMessage:
    message_id: str
    subject: str
    sender: str
    recipient: str
    received_at: str
    body: str
    internet_message_id: str
    body_hash: str
    in_reply_to_message_id: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "EmailMessage":
        missing = [field for field in REQUIRED_FIELDS if not isinstance(value.get(field), str)]
        if missing:
            raise ValueError(
                "Email message fields must be strings; missing or invalid: " + ", ".join(missing)
            )
        if not value["message_id"]:
            raise ValueError("message_id must not be empty")
        if not value["internet_message_id"]:
            raise ValueError("internet_message_id must not be empty")
        fields = {field: value[field] for field in REQUIRED_FIELDS}
        fields["in_reply_to_message_id"] = value.get("in_reply_to_message_id")
        return cls(**fields)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
