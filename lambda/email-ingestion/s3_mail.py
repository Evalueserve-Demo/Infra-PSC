"""Reads mail from CSV files in S3, for when the mailbox itself cannot be reached.

It answers the same four questions as the Graph reader (GraphApiRepository), so
ingestion, attachment storage and the reconciler use it without knowing which
one they have. MAIL_SOURCE=s3 selects it; the default is Graph.

Mail arrives as CSV files placed under MAIL_S3_PREFIX, one row per mail:

    message_id        unique and stable; a mail is recognised by it
    received_at       ISO time, for example 2026-11-17T09:30:00Z
    sender            the From address
    subject
    body              plain text
    recipients        optional, addresses separated by ; or ,
    conversation_id   optional; mails sharing one become one case, default is the mail's own id
    attachment_names  optional, file names separated by ;
    internet_message_id  optional; the message_id is used when it is left out

Each attachment is a file at <prefix>attachments/<message_id>/<file name>.

Graph reports "what changed since last time". Here that is the CSV files that are
newer than the position kept from the last run, so a file is read once. Putting a
new file for each batch, and not editing old ones, keeps every run small. Reading a
mail twice is still harmless: the database refuses a message_id it already holds.
"""

import csv
import io
import logging
import mimetypes
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from payment_query_platform.mailbox.graph_client import GraphApiError, cutoff_from_env

logger = logging.getLogger(__name__)

REQUIRED = ("message_id", "received_at", "sender", "subject", "body")
EPOCH = datetime.fromtimestamp(0, timezone.utc)


def uses_s3() -> bool:
    """Whether mail comes from S3 (MAIL_SOURCE=s3) and not from Outlook through Graph."""
    source = os.getenv("MAIL_SOURCE", "graph").strip().lower()
    if source not in ("graph", "s3"):
        raise ValueError(f"MAIL_SOURCE must be graph or s3, not {source!r}")
    return source == "s3"


def received(raw: Dict[str, Any]) -> datetime:
    """When the mail arrived, as a timezone-aware time; a time with no zone is taken as UTC."""
    moment = datetime.fromisoformat(raw["receivedDateTime"].replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def to_graph(row: Dict[str, str]) -> Dict[str, Any]:
    """One CSV row in the shape Graph returns, so normalize() and thread_context() read it as they are."""
    names = [name.strip() for name in (row.get("attachment_names") or "").split(";") if name.strip()]
    addresses = (row.get("recipients") or "").replace(",", ";").split(";")
    message_id = row["message_id"].strip()
    return {
        "id": message_id,
        "internetMessageId": (row.get("internet_message_id") or "").strip() or message_id,
        "subject": row["subject"],
        "from": {"emailAddress": {"address": row["sender"].strip()}},
        "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in addresses if a.strip()],
        "receivedDateTime": row["received_at"].strip(),
        "body": {"contentType": "text", "content": row["body"]},
        "conversationId": (row.get("conversation_id") or "").strip() or message_id,
        "hasAttachments": bool(names),
        "attachments": [
            {"id": name, "name": name, "contentType": mimetypes.guess_type(name)[0] or "application/octet-stream",
             "size": None, "isInline": False}
            for name in names
        ],
    }


class S3MailRepository:
    """Reads mail from the CSV files under one S3 prefix."""

    def __init__(self, bucket: str, prefix: str = "inbox/", client: Any = None):
        self.bucket = bucket
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"
        # Keys the sync state and the reconciler's read-time lookup, as the mailbox address does for Graph.
        self.mailbox = f"s3://{bucket}/{self.prefix}"
        self._client = client
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._loaded: set = set()

    @classmethod
    def from_env(cls) -> "S3MailRepository":
        return cls(os.environ["MAIL_S3_BUCKET"], os.getenv("MAIL_S3_PREFIX", "inbox/"))

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3  # only needed when mail comes from S3

            self._client = boto3.client("s3")
        return self._client

    # ---------------------------------------------------------------- files

    def _files(self) -> List[Tuple[datetime, str]]:
        """Every CSV directly under the prefix, oldest first: (when it was put there, its key)."""
        found = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=self.prefix, Delimiter="/"):
            for item in page.get("Contents", []):
                if item["Key"].lower().endswith(".csv"):
                    found.append((item["LastModified"], item["Key"]))
        return sorted(found)

    def _read(self, key: str) -> List[Dict[str, Any]]:
        """The mails in one file, in Graph's shape. A row that cannot be used is logged and left out."""
        if key in self._loaded:
            return [raw for raw in self._rows.values() if raw["_file"] == key]
        try:
            text = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8-sig")
        except Exception as exc:
            raise GraphApiError(f"Could not read {key}: {exc}") from exc
        mails = []
        for number, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
            missing = [column for column in REQUIRED if not (row.get(column) or "").strip()]
            try:
                if missing:
                    raise ValueError("missing " + ", ".join(missing))
                raw = to_graph(row)
                received(raw)
            except ValueError as exc:
                logger.error("Skipping row %d of %s: %s", number, key, exc)
                continue
            raw["_file"] = key
            self._rows[raw["id"]] = raw
            mails.append(raw)
        self._loaded.add(key)
        return mails

    def _all(self) -> Dict[str, Dict[str, Any]]:
        for _, key in self._files():
            self._read(key)
        return self._rows

    @staticmethod
    def _position(sync_state: Optional[str]) -> Tuple[datetime, str]:
        if not sync_state:
            return EPOCH, ""
        when, _, key = sync_state.partition("|")
        return datetime.fromisoformat(when), key

    # ------------------------------------------------- the Graph reader's methods

    def changed_message_ids(self, sync_state: Optional[str] = None) -> Tuple[List[str], str]:
        """The mails in files newer than the saved position, and the position to save once they are stored."""
        position = self._position(sync_state)
        new = [entry for entry in self._files() if entry > position]
        cutoff = cutoff_from_env()
        ids = []
        for _, key in new:
            for raw in self._read(key):
                if cutoff is None or received(raw) >= cutoff:
                    ids.append(raw["id"])
        if not new:
            return ids, sync_state or f"{EPOCH.isoformat()}|"
        return ids, f"{new[-1][0].isoformat()}|{new[-1][1]}"

    def get_message(self, message_id: str) -> Dict[str, Any]:
        raw = self._rows.get(message_id) or self._all().get(message_id)
        if raw is None:
            raise GraphApiError(f"No mail {message_id} in {self.mailbox}", status=404)
        return {k: v for k, v in raw.items() if k != "_file"}

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        key = f"{self.prefix}attachments/{message_id}/{attachment_id}"
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise GraphApiError(f"No attachment at {key}", status=404) from exc

    def message_ids_before(self, moment: datetime, since: Optional[datetime] = None) -> List[str]:
        """The ID of every mail received before the moment, and from since on when that is given."""
        return [
            message_id for message_id, raw in self._all().items()
            if received(raw) < moment and (since is None or received(raw) >= since)
        ]
