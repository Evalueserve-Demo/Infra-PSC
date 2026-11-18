"""PSC #180 Attachment Processing Lambda.

Downloads the mail's attachments from Graph and stores them in S3. After the
malware scan, the EXTRACT_CLEAN action reads the text out of the clean files.
The helper modules are embedded below so this stays one file.

Needs boto3 (provided by Lambda) and pypdf (in the package).
"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional
import re
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import boto3
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from typing import Optional
import io
from dataclasses import dataclass
from typing import Any, Dict

# ======================================================================
# --- models.py ---
# ======================================================================
"""The message shape carried through every stage of the workflow.

Field names match the production flow specification, so what Stage 1 stores is
what Stage 7 reads. Recipient is part of that contract: it travels with the case
all the way to the payment investigation agent.
"""


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

# ======================================================================
# --- text.py ---
# ======================================================================
"""Reducing an email body to something safe to carry between stages.

Outlook sends HTML, and a marketing or phishing message can run to hundreds of
kilobytes of it. Step Functions and SQS both cap a payload at 256 KB, so the
raw body cannot travel through the pipeline: an oversized one failed
StartExecution outright and orphaned the message.

The full body is kept in Aurora. What travels is this: tags stripped,
whitespace collapsed, and a hard cap well inside the limit. Everything
downstream reads the text a person would have read.
"""


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

# ======================================================================
# --- mailbox/graph_auth.py ---
# ======================================================================
"""Acquires Microsoft Graph access tokens for the mailbox application.

Uses the OAuth2 client-credentials flow, so the app reads the mailbox without a
signed-in user. Credentials live in AWS Secrets Manager and are never held in
environment variables, where get-function-configuration would expose them.

Tokens last about an hour and are cached in memory, which a warm Lambda reuses
across invocations.
"""



TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SCOPE = "https://graph.microsoft.com/.default"
# Refresh early so a token cannot expire mid-request.
EXPIRY_MARGIN_SECONDS = 120


class GraphAuthError(RuntimeError):
    """Raised when a Graph access token cannot be obtained."""


def load_credentials(secret_id: Optional[str] = None) -> Dict[str, Any]:
    """Read the Azure AD application credentials from Secrets Manager."""
    secret_id = secret_id or os.environ["GRAPH_SECRET_ID"]
    client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1"))
    try:
        payload = client.get_secret_value(SecretId=secret_id)["SecretString"]
    except Exception as exc:
        raise GraphAuthError(f"Could not read secret {secret_id}: {exc}") from exc
    credentials = json.loads(payload)
    for field in ("tenant_id", "client_id", "client_secret"):
        if not credentials.get(field):
            raise GraphAuthError(f"Secret {secret_id} is missing {field}")
    return credentials


class GraphTokenProvider:
    """Fetches and caches an application token for Microsoft Graph."""

    def __init__(self, credentials: Optional[Dict[str, Any]] = None, timeout: int = 30):
        self._credentials = credentials
        self._timeout = timeout
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    @property
    def credentials(self) -> Dict[str, Any]:
        if self._credentials is None:
            self._credentials = load_credentials()
        return self._credentials

    def token(self) -> str:
        if self._token and time.time() < self._expires_at:
            return self._token

        credentials = self.credentials
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": credentials["client_id"],
                "client_secret": credentials["client_secret"],
                "scope": SCOPE,
            }
        ).encode()

        url = TOKEN_URL.format(tenant=credentials["tenant_id"])
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=self._timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read().decode() or "{}").get("error", "unknown")
            raise GraphAuthError(f"Token request rejected: {detail}") from exc
        except Exception as exc:
            raise GraphAuthError(f"Token request failed: {exc}") from exc

        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_SECONDS
        return self._token

# ======================================================================
# --- mailbox/graph_client.py ---
# ======================================================================
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



logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
MESSAGE_FIELDS = "id,internetMessageId,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId,hasAttachments"


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
        next_state = ""

        while url:
            payload = self._get(url)
            changes = payload.get("value")
            if not isinstance(changes, list):
                raise GraphApiError(f"{url} did not contain a 'value' array")
            for change in changes:
                # Deletions arrive as @removed stubs and carry nothing to ingest.
                if isinstance(change, dict) and change.get("id") and "@removed" not in change:
                    message_ids.append(change["id"])
            next_state = payload.get("@odata.deltaLink") or next_state
            url = payload.get("@odata.nextLink") or ""

        if not next_state:
            raise GraphApiError("Graph did not return a deltaLink; sync state cannot be committed")
        return message_ids, next_state

    def message_ids_before(self, moment: datetime) -> List[str]:
        """The ID of every inbox message received before the moment (a timezone-aware time)."""
        params = {"$select": "id", "$top": "100",
                  "$filter": f"receivedDateTime lt {moment.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"}
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

# ======================================================================
# --- storage/object_store.py ---
# ======================================================================
"""Stores attachment content in S3.

The flow is explicit that the file itself goes to S3 and only the reference
goes to Aurora, so this module is the only place attachment bytes are handled.

Keys are laid out by message rather than by case, because attachments are
stored in Stage 2 step 15 and the case is not opened until step 18. The case
learns the reference afterwards; the object also carries the message ID in its
metadata, so a file found in the bucket can always be traced back.

The text read out of an attachment is kept beside the files, under text/ and
keyed by the sha256 of the file it came from. The same invoice re-attached to a
reply is the same file, so its text has one key however often it arrives.
"""



UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: Optional[str], fallback: str) -> str:
    """Reduce a supplier-supplied filename to something safe for an S3 key."""
    cleaned = UNSAFE_CHARACTERS.sub("_", (name or "").strip()).strip("._-")
    return cleaned[:120] or fallback


class AttachmentStore:
    def __init__(self, bucket: Optional[str] = None, region: Optional[str] = None):
        self.bucket = bucket or os.environ["ATTACHMENT_BUCKET"]
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def put(
        self,
        message_id: str,
        attachment_id: str,
        filename: Optional[str],
        content: bytes,
        content_type: Optional[str] = None,
    ) -> str:
        """Store one attachment and return its s3:// reference."""
        # Graph message IDs are long and contain characters that make poor key
        # segments, so the prefix is a digest of the ID rather than the ID.
        prefix = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:32]
        key = f"messages/{prefix}/{safe_filename(filename, attachment_id)}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type or "application/octet-stream",
            # Tracing a file back to its mail without opening the database.
            Metadata={"message-id": message_id[:1024], "attachment-id": attachment_id[:1024]},
        )
        return f"s3://{self.bucket}/{key}"

    def put_text(self, sha256: str, text: str) -> str:
        """Store the text read out of an attachment and return its s3:// reference."""
        key = f"text/{sha256}.txt"
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
        )
        return f"s3://{self.bucket}/{key}"

# ======================================================================
# --- attachment_text.py ---
# ======================================================================
"""Reading the text out of an attachment.

The supplier's numbers are often only on the attached invoice, so the agents
need its text, not just a pointer to the file. This turns the bytes of one
attachment into plain text, and says plainly when it cannot.

It is a pure function of the bytes: no S3, no Graph, no database. Stage 2 calls
it right after storing the file, and whatever happens here never fails the
stage. A file that cannot be read is recorded as FAILED or SKIPPED, and the
email carries on with its body alone.

What is read:
    text/*            decoded as UTF-8
    PDF               the text layer, page by page
Everything else (images, Office files, archives) is SKIPPED. A PDF with no
text layer is a scan and is SKIPPED too; reading those needs OCR.

The statuses are the ones ps.case_attachment.extraction_status allows.
"""


logger = logging.getLogger(__name__)

EXTRACTED = "EXTRACTED"
SKIPPED = "SKIPPED"
FAILED = "FAILED"

# A supplier invoice is a few pages and well under a megabyte. These keep a
# hostile or accidental giant from tying up the Lambda.
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 50

# The same cap the email body gets, so one attachment cannot crowd out the rest
# of an agent's input.
MAX_TEXT_CHARS = 20000


@dataclass
class ExtractedText:
    status: str
    text: str = ""
    reason: Optional[str] = None


def _is_pdf(content_type: str, filename: str) -> bool:
    # Mail clients often send application/octet-stream, so the name counts too.
    return content_type == "application/pdf" or filename.endswith(".pdf")


def _is_text(content_type: str, filename: str) -> bool:
    return content_type.startswith("text/") or filename.endswith(".txt")


def _cap(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_TEXT_CHARS:
        return text
    return text[:MAX_TEXT_CHARS].rstrip() + " [truncated]"


def _pdf_text(content: bytes) -> ExtractedText:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        return ExtractedText(SKIPPED, reason="PDF is password protected")
    pages = reader.pages[:MAX_PDF_PAGES]
    text = "\n\n".join(page.extract_text() or "" for page in pages)
    if not text.strip():
        return ExtractedText(SKIPPED, reason="PDF has no text layer; it needs OCR")
    reason = None
    if len(reader.pages) > MAX_PDF_PAGES:
        reason = f"only the first {MAX_PDF_PAGES} of {len(reader.pages)} pages read"
    return ExtractedText(EXTRACTED, _cap(text), reason)


def extract_text(content: bytes, content_type: Optional[str], filename: Optional[str]) -> ExtractedText:
    """The text of one attachment, or the reason there is none."""
    content_type = (content_type or "").lower()
    filename = (filename or "").lower()

    if len(content) > MAX_FILE_BYTES:
        return ExtractedText(SKIPPED, reason=f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")

    try:
        if _is_pdf(content_type, filename):
            return _pdf_text(content)
        if _is_text(content_type, filename):
            text = content.decode("utf-8", errors="replace")
            if not text.strip():
                return ExtractedText(SKIPPED, reason="empty text file")
            return ExtractedText(EXTRACTED, _cap(text))
    except Exception as exc:  # a malformed file must not fail the stage
        logger.warning("Could not read %s (%s): %s", filename, content_type, exc)
        return ExtractedText(FAILED, reason=f"{type(exc).__name__}: {exc}"[:200])

    return ExtractedText(SKIPPED, reason=f"unsupported type {content_type or 'unknown'}")

# ======================================================================
# --- handler ---
# ======================================================================
"""STAGE 2, part one — attachment processing.

Invoked by Step Function 1 only when the message has an attachment, and follows
the flow specification:

    12. Get the Graph credentials from Secrets Manager
    13. Request the attachment using the message ID and attachment ID
    14. Graph returns the file
    15. Store the actual file in S3

and then reads the text out of each stored file, which the C4 container
diagram gives to this service ("extracts safe text/metadata"), so the agents
downstream can see what is on the attached invoice and not only the email.

The file and its text go to S3, never to Aurora, and never into the workflow
payload, which Step Functions and SQS cap at 256 KB. What travels onward is the
reference.

Input is the Stage 1 message context. Output is that same context with these
added to each attachment, so nothing from Stage 1 is lost:

    s3_reference         where the file is
    size_bytes           its actual size
    sha256               of the content; ps.case_attachment requires it and uses
                         it to record a file sent twice in one thread only once
    extraction_status    EXTRACTED, SKIPPED or FAILED
    extracted_text_uri   where the text is, when there is any
    extraction_note      why a file was skipped, failed or cut short

A file that cannot be read never fails the stage; it is recorded and the email
carries on with its body.

Environment:
    GRAPH_API_BASE_URL   Graph base, e.g. https://graph.microsoft.com/v1.0
    GRAPH_SECRET_ID      Secrets Manager id holding the Azure AD credentials
    GRAPH_MAILBOX        mailbox to read; falls back to the secret's value
    ATTACHMENT_BUCKET    S3 bucket for attachment content
"""



logger = logging.getLogger()
logger.setLevel(logging.INFO)

_store: AttachmentStore | None = None


def _get_store() -> AttachmentStore:
    global _store
    if _store is None:
        _store = AttachmentStore()
    return _store


class S3Attachments:
    """The attachments of mail read from S3 (MAIL_SOURCE=s3): <prefix>attachments/<message_id>/<file name>."""

    def __init__(self, bucket: str, prefix: str):
        self.bucket = bucket
        self.prefix = prefix if prefix.endswith("/") else prefix + "/"

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        key = f"{self.prefix}attachments/{message_id}/{attachment_id}"
        try:
            return boto3.client("s3").get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # a missing file is a 404, as it is from Graph
            raise GraphApiError(f"No attachment at {key}: {exc}", status=404) from exc


def _build_mailbox() -> GraphApiRepository | S3Attachments:
    """Step 12 — S3 when MAIL_SOURCE=s3, otherwise Graph with credentials from Secrets Manager."""
    source = os.getenv("MAIL_SOURCE", "graph").strip().lower()
    if source not in ("graph", "s3"):
        raise ValueError(f"MAIL_SOURCE must be graph or s3, not {source!r}")
    if source == "s3":
        return S3Attachments(os.environ["MAIL_S3_BUCKET"], os.getenv("MAIL_S3_PREFIX", "inbox/"))
    credentials = load_credentials(os.environ["GRAPH_SECRET_ID"])
    mailbox = os.getenv("GRAPH_MAILBOX") or credentials.get("mailbox")
    if not mailbox:
        raise GraphAuthError("No mailbox configured; set GRAPH_MAILBOX or add it to the secret")
    return GraphApiRepository(
        os.environ["GRAPH_API_BASE_URL"], mailbox=mailbox, token_provider=GraphTokenProvider(credentials)
    )


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    # The scanner is the authority for CLEAN; never infer CLEAN from an S3 upload.
    if event.get("action") == "EXTRACT_CLEAN":
        return _extract_clean(event)

    message_id = event["message_id"]
    attachments = event.get("attachments") or []
    if not attachments:
        logger.info("No attachments on %s; nothing to store", message_id)
        return event

    mailbox = _build_mailbox()
    store = _get_store()
    stored = []

    for attachment in attachments:
        attachment_id = attachment["attachment_id"]
        # Steps 13 and 14 — fetch the file from Graph.
        content = mailbox.get_attachment_bytes(message_id, attachment_id)
        if not content:
            # Nothing to store or read, and the case record rejects a zero-byte file: the mail goes on without it.
            logger.warning("Skipping %s on %s: the attachment is empty", attachment.get("name"), message_id)
            continue
        # Step 15 — the file itself goes to S3.
        reference = store.put(
            message_id=message_id,
            attachment_id=attachment_id,
            filename=attachment.get("name"),
            content=content,
            content_type=attachment.get("content_type"),
        )
        logger.info("Stored %s (%d bytes) at %s", attachment.get("name"), len(content), reference)
        sha256 = hashlib.sha256(content).hexdigest()

        # Do not extract unscanned bytes. Malware scanning is a separate SF1 step.
        # DBA ps.case_attachment defaults both statuses to PENDING.
        extracted_status = "PENDING"
        text_reference = None

        stored.append({
            **attachment,
            "s3_reference": reference,
            "size_bytes": len(content),
            "sha256": sha256,
            "extraction_status": extracted_status,
            "extracted_text_uri": text_reference,
            "extraction_note": None,
            "scan_status": "PENDING",
        })

    return {
        **event,
        "attachments": stored,
        "attachment_s3_reference": stored[0]["s3_reference"] if stored else None,
    }


def _extract_clean(event: Dict[str, Any]) -> Dict[str, Any]:
    """SF1 post-scan branch; only scanner-confirmed CLEAN attachments are extracted.

    The orchestrator must provide scanner result from the trusted malware-scan step.
    This function does not update the DBA tables: the case writer must persist the
    returned metadata after a case_header/case_message exists (FK requirement).
    """
    if event.get("scan_status") != "CLEAN":
        raise ValueError("EXTRACT_CLEAN requires scanner-confirmed CLEAN status")
    attachments = event.get("attachments") or []
    s3 = boto3.client("s3")
    store = _get_store()
    output = []
    for attachment in attachments:
        item = dict(attachment)
        if item.get("scan_status") != "CLEAN":
            item["extraction_status"] = "SKIPPED"
            item["extraction_note"] = "Attachment not marked CLEAN by malware scanner"
            output.append(item)
            continue
        uri = item.get("s3_reference")
        if not isinstance(uri, str) or not uri.startswith("s3://"):
            item["extraction_status"] = "FAILED"
            item["extraction_note"] = "Missing S3 attachment reference"
            output.append(item)
            continue
        location = urllib.parse.urlsplit(uri)
        bucket, key = location.netloc, urllib.parse.unquote(location.path.lstrip("/"))
        if not bucket or not key:
            raise ValueError("Invalid attachment S3 URI")
        try:
            # Read one byte over the limit so an oversized file is noticed.
            # MAX_FILE_BYTES is defined in attachment_text above.
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read(MAX_FILE_BYTES + 1)
            result = extract_text(raw, item.get("content_type") or "", item.get("name") or "")
            item["extraction_status"] = result.status
            item["extracted_text_uri"] = store.put_text(item["sha256"], result.text) if result.status == EXTRACTED else None
            item["extraction_note"] = result.reason
        except Exception as exc:
            logger.exception("Attachment extraction failed for %s", item.get("attachment_id"))
            item["extraction_status"] = "FAILED"
            item["extracted_text_uri"] = None
            item["extraction_note"] = str(exc)[:200]
        output.append(item)
    return {**event, "attachments": output}
