"""Reads the shared mailbox from Microsoft Graph.

Requests are addressed to /users/<mailbox> and carry an application bearer
token, so no user is signed in.

Change detection uses the delta endpoint, which is what makes the flow's sync
state real: each pass ends with a deltaLink that names exactly what the mailbox
had been read up to. Storing that link and starting the next pass from it means
a message is offered once, rather than the mailbox being re-scanned every run.

Delta returns changed message stubs but not attachment metadata, so each
changed message is then fetched with $expand=attachments to get the attachment
IDs that Stage 2 needs.
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from models import EmailMessage
from text import plain_text

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
MESSAGE_FIELDS = "id,internetMessageId,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId,hasAttachments"


def cutoff_from_env() -> Optional[datetime]:
    """The time before which mail is not ingested, if INGESTION_CUTOFF_UTC is set.

    The mailbox already holds mail from before go-live. Setting this keeps it out,
    so the pipeline only handles mail that arrives from then on.
    """
    value = os.getenv("INGESTION_CUTOFF_UTC", "").strip()
    if not value:
        return None
    cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("INGESTION_CUTOFF_UTC must include a UTC offset, for example 2026-11-12T22:30:00Z")
    return cutoff.astimezone(timezone.utc)


class GraphApiError(RuntimeError):
    """Raised when the mailbox API cannot be read.

    ``status`` carries the HTTP status when there was one, so a caller can tell a
    permanent client error (4xx — retrying will not help) from a transient one
    (5xx, timeout, network — worth retrying). It is None when the call never got
    an HTTP response.
    """

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status

    @property
    def is_permanent(self) -> bool:
        """A 4xx means the request itself is wrong; retrying it will fail again."""
        return self.status is not None and 400 <= self.status < 500


class GraphApiRepository:
    """Reads one shared mailbox from Microsoft Graph."""

    def __init__(
        self,
        base_url: str,
        mailbox: str,
        token_provider: Any,
        timeout: int = TIMEOUT_SECONDS,
        page_size: int = 50,
    ):
        self.base_url = base_url.rstrip("/")
        self.mailbox = mailbox
        self.token_provider = token_provider
        self.timeout = timeout
        self.page_size = page_size

    def _url(self, path: str) -> str:
        return f"{self.base_url}/users/{self.mailbox}{path}"

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token_provider.token()}",
                          "Prefer": f"odata.maxpagesize={self.page_size}"}
        )

    def _describe(self, exc: urllib.error.HTTPError) -> str:
        try:
            return json.loads(exc.read().decode() or "{}").get("error", {}).get("code", "")
        except Exception:
            return ""

    def _get(self, url: str) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GraphApiError(f"{url} returned HTTP {exc.code} {self._describe(exc)}".strip(), status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GraphApiError(f"{url} unreachable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise GraphApiError(f"{url} returned invalid JSON") from exc

    def _get_bytes(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise GraphApiError(f"{url} returned HTTP {exc.code} {self._describe(exc)}".strip(), status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GraphApiError(f"{url} unreachable: {exc}") from exc

    def delta_url(self) -> str:
        """The URL that starts a fresh delta enumeration of the inbox."""
        # Page size goes in the Prefer header, not $top: $top caps the whole
        # enumeration, and the deltaLink that follows would skip the rest.
        params = {"$select": MESSAGE_FIELDS}
        return self._url("/mailFolders/inbox/messages/delta") + "?" + urllib.parse.urlencode(params)

    def changed_message_ids(self, sync_state: Optional[str] = None) -> Tuple[List[str], str]:
        """Walk the delta feed, returning changed message IDs and the next sync state.

        Passing the sync state from the previous run resumes from it. Passing
        None enumerates the inbox from the beginning, which is what the first
        run does. The returned state is the deltaLink to commit once the
        messages have been safely persisted.

        Graph answers 410 SyncStateNotFound once it no longer recognises a saved
        state. Retrying that state fails every time, so the run would be stuck
        until someone cleared it by hand. Graph's own guidance is to start over,
        so the inbox is enumerated from the beginning instead. That is safe:
        messages already stored are recognised and skipped downstream.
        """
        try:
            return self._walk(sync_state or self.delta_url())
        except GraphApiError as exc:
            if sync_state and exc.status == 410:
                logger.warning("Graph no longer recognises the saved sync state (%s); "
                               "reading the inbox from the beginning", exc)
                return self._walk(self.delta_url())
            raise

    def _walk(self, url: str) -> Tuple[List[str], str]:
        message_ids: List[str] = []
        # Delta still has to be read to the end to get a valid sync position, but
        # mail older than the cutoff is dropped here, so its body is never fetched.
        cutoff = cutoff_from_env()
        next_state = ""

        while url:
            payload = self._get(url)
            changes = payload.get("value")
            if not isinstance(changes, list):
                raise GraphApiError(f"{url} did not contain a 'value' array")
            for change in changes:
                # Deletions arrive as @removed stubs and carry nothing to ingest.
                if isinstance(change, dict) and change.get("id") and "@removed" not in change:
                    if cutoff is not None and self._received_before(change, cutoff):
                        continue
                    message_ids.append(change["id"])
            next_state = payload.get("@odata.deltaLink") or next_state
            url = payload.get("@odata.nextLink") or ""

        if not next_state:
            raise GraphApiError("Graph did not return a deltaLink; sync state cannot be committed")
        return message_ids, next_state

    @staticmethod
    def _received_before(change: Dict[str, Any], cutoff: datetime) -> bool:
        """Whether a changed message arrived before the cutoff.

        A message with no usable time is an error, not a skip: dropping it quietly
        could lose mail that should have been handled.
        """
        received_at = change.get("receivedDateTime")
        if not received_at:
            raise GraphApiError("A changed message came back without receivedDateTime")
        try:
            received = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GraphApiError(f"Could not read receivedDateTime {received_at!r}") from exc
        return received.astimezone(timezone.utc) < cutoff

    def message_ids_before(self, moment: datetime, since: Optional[datetime] = None) -> List[str]:
        """The ID of every inbox message received before the moment (a timezone-aware time).

        With since, only those received from then on: mail older than the ingestion cutoff is never saved.
        """
        window = f"receivedDateTime lt {moment.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        if since is not None:
            window = f"receivedDateTime ge {since.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ} and {window}"
        params = {"$select": "id", "$top": "100", "$filter": window}
        url = self._url("/mailFolders/inbox/messages") + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        ids: List[str] = []
        while url:
            payload = self._get(url)
            ids += [m["id"] for m in payload.get("value", []) if m.get("id")]
            url = payload.get("@odata.nextLink") or ""
        return ids

    def get_message(self, message_id: str) -> Dict[str, Any]:
        """Return one message with its full body and attachment metadata."""
        quoted = urllib.parse.quote(message_id, safe="=")
        params = {"$select": MESSAGE_FIELDS, "$expand": "attachments($select=id,name,contentType,size,isInline)"}
        return self._get(self._url(f"/messages/{quoted}/") + "?" + urllib.parse.urlencode(params))

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        """Download the raw content of one attachment."""
        message = urllib.parse.quote(message_id, safe="")
        attachment = urllib.parse.quote(attachment_id, safe="")
        return self._get_bytes(self._url(f"/messages/{message}/attachments/{attachment}/$value"))


def normalize(raw: Dict[str, Any]) -> EmailMessage:
    """Map a Graph message onto the internal EmailMessage shape."""
    sender = (raw.get("from") or {}).get("emailAddress") or {}
    body = raw.get("body") or {}
    raw_body = body.get("content") or raw.get("bodyPreview") or ""
    body_text = plain_text(raw_body, limit=len(raw_body) + 1)
    return EmailMessage.from_dict(
        {
            "message_id": raw.get("id") or "",
            "internet_message_id": raw.get("internetMessageId") or "",
            "in_reply_to_message_id": raw.get("inReplyTo"),
            "subject": raw.get("subject") or "",
            "sender": sender.get("address") or sender.get("name") or "",
            "recipient": ", ".join(recipients(raw)),
            "received_at": raw.get("receivedDateTime") or "",
            "body": body_text,
            "body_hash": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
        }
    )


def recipients(raw: Dict[str, Any]) -> List[str]:
    """Every To address on the message, in the order Graph returned them."""
    addresses = []
    for entry in raw.get("toRecipients") or []:
        if not isinstance(entry, dict):
            continue
        address = (entry.get("emailAddress") or {}).get("address")
        if address:
            addresses.append(address)
    return addresses


def thread_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the thread and attachment context the downstream stages need."""
    attachments = [
        {
            "attachment_id": a.get("id"),
            "name": a.get("name"),
            "content_type": a.get("contentType"),
            "size_bytes": a.get("size"),
        }
        for a in raw.get("attachments") or []
        if isinstance(a, dict) and not a.get("isInline")
    ]
    return {
        "conversation_id": raw.get("conversationId"),
        "recipients": recipients(raw),
        # Inline images set hasAttachments too, so trust the filtered list.
        "has_attachment": bool(attachments),
        "attachment_id": attachments[0]["attachment_id"] if attachments else None,
        "attachments": attachments,
    }
