# PSC #160: single-file packaging of the previously supplied developer-adapted handler.
# Embedded supporting modules are unchanged from the supplied package.
# Lambda handler: lambda_function.lambda_handler
# Scheduled recovery: invoke this same handler with {"action":"RECOVER_UNPUBLISHED"}.
# Requires boto3 and psycopg in Lambda deployment environment.
import importlib.abc as _psc_abc
import importlib.util as _psc_util
import sys as _psc_sys
_PSC_MODULES = {
    'payment_query_platform': '',
    'payment_query_platform.storage': '',
    'payment_query_platform.storage.database': '"""Aurora persistence for the ingestion and processing stages.\r\n\r\nConnects with IAM database authentication, so no password is stored anywhere.\r\nThe Lambda role needs rds-db:connect, and the integ_app database user must have\r\nbeen granted rds_iam.\r\n\r\nTwo pipeline tables in the ps schema, created by infra/schema_ps.sql beside the\r\ncase tables:\r\n\r\n    graph_sync_state   where the mailbox has been read up to\r\n    messages           one row per Graph message, with how far it got\r\n\r\nThe unique constraint on ps.case_header.conversation_id is what guarantees a\r\nthread can only ever have one case.\r\n"""\r\n\r\nimport hashlib\r\nimport json\r\nimport logging\r\nimport os\r\nimport re\r\nfrom typing import Any, Dict, List, Optional\r\nfrom uuid import UUID\r\n\r\nimport boto3\r\nimport psycopg\r\nfrom psycopg.types.json import Jsonb\r\n\r\nfrom payment_query_platform.text import plain_text\r\n\r\nlogger = logging.getLogger(__name__)\r\n\r\n# Message lifecycle, matching the flow specification.\r\nSTATUS_RECEIVED = "RECEIVED"\r\nSTATUS_READY = "READY_FOR_DOWNSTREAM"\r\n\r\nCASE_STATUS_OPEN = "OPEN"\r\nCASE_ID_FORMAT = "CASE-%06d"\r\n\r\n\r\nclass Database:\r\n    """One IAM-authenticated connection, reused across warm invocations."""\r\n\r\n    def __init__(\r\n        self,\r\n        host: Optional[str] = None,\r\n        port: int = 5432,\r\n        dbname: Optional[str] = None,\r\n        user: Optional[str] = None,\r\n        region: Optional[str] = None,\r\n    ):\r\n        self.host = host or os.environ["AURORA_HOST"]\r\n        self.port = port\r\n        self.dbname = dbname or os.getenv("AURORA_DB", "appdb")\r\n        self.user = user or os.getenv("AURORA_USER", "integ_app")\r\n        self.region = region or os.getenv("AWS_REGION", "us-east-1")\r\n        self._connection: Optional[psycopg.Connection] = None\r\n\r\n    def _auth_token(self) -> str:\r\n        return boto3.client("rds", region_name=self.region).generate_db_auth_token(\r\n            DBHostname=self.host, Port=self.port, DBUsername=self.user, Region=self.region\r\n        )\r\n\r\n    def connection(self) -> psycopg.Connection:\r\n        if self._connection is not None and not self._connection.closed:\r\n            return self._connection\r\n        self._connection = psycopg.connect(\r\n            host=self.host,\r\n            port=self.port,\r\n            dbname=self.dbname,\r\n            user=self.user,\r\n            password=self._auth_token(),\r\n            sslmode="require",\r\n            connect_timeout=15,\r\n            autocommit=True,\r\n        )\r\n        return self._connection\r\n\r\n    def cursor(self):\r\n        return self.connection().cursor()\r\n\r\n    def close(self) -> None:\r\n        if self._connection is not None and not self._connection.closed:\r\n            self._connection.close()\r\n\r\n\r\nclass MessageRepository:\r\n    """One row per Graph message."""\r\n\r\n    INSERT = """\r\n    insert into ps.messages\r\n           (message_id, internet_message_id, in_reply_to_message_id,\r\n            conversation_id, sender, recipient, subject, body, body_hash,\r\n            has_attachment, status, received_at)\r\n        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)\r\n    on conflict (message_id) do nothing\r\n    returning message_id\r\n    """\r\n\r\n    SELECT = "select status from ps.messages where message_id = %s"\r\n\r\n    STAGED_MESSAGE = """\r\n        select internet_message_id, in_reply_to_message_id, conversation_id,\r\n            sender, recipient, subject, body, body_hash, has_attachment, received_at\r\n    from ps.messages\r\n    where message_id = %s\r\n    """\r\n\r\n    def __init__(self, database: Database):\r\n        self._database = database\r\n\r\n    def save_if_absent(self, message_id: str, detail: Dict[str, Any], event_time: Optional[str]) -> bool:\r\n        """Insert one message as RECEIVED. False when it was already stored."""\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(\r\n                self.INSERT,\r\n                (\r\n                    message_id,\r\n                    detail.get("internet_message_id"),\r\n                    detail.get("in_reply_to_message_id"),\r\n                    detail.get("conversation_id"),\r\n                    detail.get("sender") or "",\r\n                    detail.get("recipient") or "",\r\n                    detail.get("subject"),\r\n                    detail.get("body"),\r\n                    detail.get("body_hash"),\r\n                    bool(detail.get("has_attachment")),\r\n                    STATUS_RECEIVED,\r\n                    event_time,\r\n                ),\r\n            )\r\n            return cursor.fetchone() is not None\r\n\r\n    def get(self, message_id: str) -> Optional[Dict[str, Any]]:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.SELECT, (message_id,))\r\n            row = cursor.fetchone()\r\n            return {"status": row[0]} if row else None\r\n\r\n    def get_staged_message(self, message_id: str) -> Optional[Dict[str, Any]]:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.STAGED_MESSAGE, (message_id,))\r\n            row = cursor.fetchone()\r\n            if row is None:\r\n                return None\r\n\r\n            raw_body = row[6] or ""\r\n            body = plain_text(raw_body, limit=len(raw_body) + 1)\r\n\r\n            if len(row) >= 10:\r\n                body_hash = row[7] or hashlib.sha256(body.encode("utf-8")).hexdigest()\r\n                has_attachment = row[8]\r\n                received_at = row[9]\r\n            else:\r\n                body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()\r\n                has_attachment = row[7]\r\n                received_at = row[8]\r\n\r\n            return {\r\n                "message_id": message_id,\r\n                "internet_message_id": row[0],\r\n                "in_reply_to_message_id": row[1],\r\n                "conversation_id": row[2],\r\n                "sender": row[3],\r\n                "recipient": row[4],\r\n                "subject": row[5],\r\n                "body": body,\r\n                "body_hash": body_hash,\r\n                "has_attachment": has_attachment,\r\n                "received_at": received_at.isoformat() if hasattr(received_at, "isoformat") else received_at,\r\n            }\r\n\r\n    def mark_ready(self, message_id: str) -> None:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(\r\n                "update ps.messages set status = %s, processed_at = now() where message_id = %s",\r\n                (STATUS_READY, message_id),\r\n            )\r\n\r\n\r\nclass SyncStateRepository:\r\n    """Holds the committed Graph delta link, one row per mailbox.\r\n\r\n    The link is only ever written after the messages it covers are persisted,\r\n    which is what makes a crash mid-run safe: the next run re-reads from the\r\n    last committed point and the message insert deduplicates the overlap.\r\n    """\r\n\r\n    SELECT = "select delta_link from ps.graph_sync_state where mailbox = %s"\r\n\r\n    UPSERT = """\r\n    insert into ps.graph_sync_state (mailbox, delta_link)\r\n    values (%s, %s)\r\n    on conflict (mailbox) do update\r\n        set delta_link = excluded.delta_link, committed_at = now()\r\n    """\r\n\r\n    def __init__(self, database: Database, mailbox: str):\r\n        self._database = database\r\n        self._mailbox = mailbox\r\n\r\n    def get(self) -> Optional[str]:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.SELECT, (self._mailbox,))\r\n            row = cursor.fetchone()\r\n            return row[0] if row else None\r\n\r\n    def commit(self, delta_link: str) -> None:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.UPSERT, (self._mailbox, delta_link))\r\n\r\n\r\nclass CaseRepository:\r\n    """Persist case records in the case schema."""\r\n\r\n    FIND = "select case_id from ps.case_header where conversation_id = %s"\r\n\r\n    INSERT = """\r\n    insert into ps.case_header (conversation_id)\r\n    values (%s)\r\n    on conflict (conversation_id) do nothing\r\n    returning case_id\r\n    """\r\n\r\n    INSERT_MESSAGE = """\r\n    insert into ps.case_message\r\n        (case_id, direction, internet_message_id, graph_message_id,\r\n         in_reply_to_message_id, sender_address, recipient_addresses, subject,\r\n         body_text, body_hash, received_at)\r\n    values (%s, \'INBOUND\', %s, %s, %s, %s, %s, %s, %s, %s, %s)\r\n    on conflict (internet_message_id) do nothing\r\n    returning message_id\r\n    """\r\n\r\n    FIND_MESSAGE = """\r\n    select message_id, case_id from ps.case_message where internet_message_id = %s\r\n    """\r\n\r\n    ADD_ATTACHMENT = """\r\n    insert into ps.case_attachment\r\n        (case_id, message_id, file_name, mime_type, size_bytes, sha256, storage_uri,\r\n         extraction_status, extracted_text_uri)\r\n    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)\r\n    on conflict (case_id, sha256) do nothing\r\n    returning attachment_id\r\n    """\r\n\r\n    ATTACHMENTS_OF_MESSAGE = """\r\n    select a.file_name, a.mime_type, a.size_bytes, a.sha256, a.storage_uri,\r\n           a.extraction_status, a.extracted_text_uri\r\n    from ps.case_attachment a join ps.case_message m using (message_id)\r\n    where m.graph_message_id = %s\r\n    order by a.created_at\r\n    """\r\n\r\n    def __init__(self, database: Database):\r\n        self._database = database\r\n\r\n    def find_by_conversation(self, conversation_id: str) -> Optional[str]:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.FIND, (conversation_id,))\r\n            row = cursor.fetchone()\r\n            return str(row[0]) if row else None\r\n\r\n    def open_case(self, conversation_id: str) -> str:\r\n        """Create or reuse the UUID case for one conversation."""\r\n        for _ in range(10):\r\n            with self._database.cursor() as cursor:\r\n                cursor.execute(self.INSERT, (conversation_id,))\r\n                row = cursor.fetchone()\r\n                if row is not None:\r\n                    return str(row[0])\r\n            existing = self.find_by_conversation(conversation_id)\r\n            if existing:\r\n                return existing\r\n        raise RuntimeError("Could not allocate a case ID after repeated contention")\r\n\r\n    def mark_out_of_scope(self, case_id: str) -> None:\r\n        """The desk has nothing to answer, so the case is finished. One already further along keeps its status."""\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute("update ps.case_header set case_status = \'OUT_OF_SCOPE\' "\r\n                           "where case_id = %s and case_status = \'NEW\'", (UUID(case_id),))\r\n\r\n    def add_message(self, case_id: str, message: Dict[str, Any]) -> str:\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(\r\n                self.INSERT_MESSAGE,\r\n                (\r\n                    UUID(case_id),\r\n                    message["internet_message_id"],\r\n                    message.get("message_id"),\r\n                    message.get("in_reply_to_message_id"),\r\n                    message.get("sender") or "",\r\n                    message.get("recipient") or "",\r\n                    message.get("subject"),\r\n                    message.get("body"),\r\n                    message["body_hash"],\r\n                    message["received_at"],\r\n                ),\r\n            )\r\n            row = cursor.fetchone()\r\n            if row is not None:\r\n                return str(row[0])\r\n\r\n            cursor.execute(self.FIND_MESSAGE, (message["internet_message_id"],))\r\n            existing = cursor.fetchone()\r\n            if existing is None or str(existing[1]) != case_id:\r\n                raise RuntimeError("Internet message ID already belongs to another payment case")\r\n            return str(existing[0])\r\n\r\n    def add_attachments(\r\n        self, case_id: str, message_id: str, attachments: List[Dict[str, Any]]\r\n    ) -> int:\r\n        """Record where each attachment and its text were stored. Both are in S3.\r\n\r\n        An attachment stored before text extraction existed carries no status,\r\n        and is recorded as PENDING, the column\'s own default.\r\n        """\r\n        stored = 0\r\n        with self._database.cursor() as cursor:\r\n            for attachment in attachments:\r\n                if not attachment.get("s3_reference"):\r\n                    continue\r\n                size_bytes = attachment.get("size_bytes")\r\n                if not size_bytes or size_bytes < 1:\r\n                    raise ValueError("Attachments must have a positive size")\r\n                cursor.execute(\r\n                    self.ADD_ATTACHMENT,\r\n                    (\r\n                        UUID(case_id),\r\n                        UUID(message_id),\r\n                        attachment.get("name") or "unnamed",\r\n                        attachment.get("content_type") or "application/octet-stream",\r\n                        size_bytes,\r\n                        attachment["sha256"],\r\n                        attachment["s3_reference"],\r\n                        attachment.get("extraction_status") or "PENDING",\r\n                        attachment.get("extracted_text_uri"),\r\n                    ),\r\n                )\r\n                stored += cursor.fetchone() is not None\r\n        return stored\r\n\r\n    def attachments_of(self, graph_message_id: str) -> List[Dict[str, Any]]:\r\n        """The attachments recorded for one message, as Stage 2 hands them on.\r\n\r\n        A file the thread already had is recorded once, under the message that\r\n        first brought it, so it is not listed again for a later one.\r\n        """\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(self.ATTACHMENTS_OF_MESSAGE, (graph_message_id,))\r\n            return [\r\n                {"name": name, "content_type": mime, "size_bytes": size, "sha256": sha256,\r\n                 "s3_reference": uri, "extraction_status": status, "extracted_text_uri": text_uri}\r\n                for name, mime, size, sha256, uri, status, text_uri in cursor.fetchall()\r\n            ]\r\n\r\n\r\nclass IdentifierRepository:\r\n    """Persist identifiers extracted from a case message."""\r\n\r\n    INSERT = """\r\n    insert into ps.case_identifier\r\n        (case_id, identifier_type, supplied_value, canonical_value, source, validation_status)\r\n    values (%s, %s, %s, %s, %s, %s)\r\n    on conflict do nothing\r\n    returning identifier_id\r\n    """\r\n\r\n    SOURCE_MAP = {\r\n        "EMAIL_SUBJECT": "SUBJECT",\r\n        "EMAIL_BODY": "EMAIL_BODY",\r\n        "ATTACHMENT": "ATTACHMENT",\r\n    }\r\n\r\n    def __init__(self, database: Database):\r\n        self._database = database\r\n\r\n    def save_candidates(self, case_id: str, candidates: List[Dict[str, Any]]) -> int:\r\n        stored = 0\r\n        with self._database.cursor() as cursor:\r\n            for candidate in candidates:\r\n                source = self.SOURCE_MAP.get(candidate.get("found_in"))\r\n                if source is None:\r\n                    raise ValueError("Extracted identifiers require a recognized found_in source")\r\n                for identifier_type, field in (("INVOICE", "invoice_number"), ("PO", "po_number")):\r\n                    supplied = candidate.get(field)\r\n                    if not supplied:\r\n                        continue\r\n                    supplied = str(supplied).strip()\r\n                    if not supplied or len(supplied) > 100:\r\n                        raise ValueError(f"{identifier_type} identifier must contain 1 to 100 characters")\r\n                    canonical = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,16}", supplied) else None\r\n                    validation_status = "PENDING"\r\n                    if identifier_type == "INVOICE":\r\n                        validation_status = (\r\n                            "VALID" if re.fullmatch(r"INV-\\d{4,10}", supplied) else "INVALID_FORMAT"\r\n                        )\r\n                    cursor.execute(\r\n                        self.INSERT,\r\n                        (UUID(case_id), identifier_type, supplied, canonical, source, validation_status),\r\n                    )\r\n                    stored += cursor.fetchone() is not None\r\n        return stored\r\n\r\n\r\nclass AgentCallLogRepository:\r\n    """Append an auditable record for each model-runtime invocation."""\r\n\r\n    INSERT = """\r\n    insert into ps.integration_call_log\r\n        (correlation_id, case_id, component, invoked_by, target_system, operation,\r\n         outcome, latency_ms, request_hash, prompt_hash, request_payload,\r\n         response_payload, model_id, error_code, error_message)\r\n    select c.correlation_id, c.case_id, %s, %s, \'BEDROCK\', %s,\r\n           %s, %s, %s, %s, %s, %s, %s, %s, %s\r\n    from ps.case_header c\r\n    where c.case_id = %s\r\n    returning call_id\r\n    """\r\n\r\n    def __init__(self, database: Database):\r\n        self._database = database\r\n\r\n    def record(\r\n        self,\r\n        case_id: str,\r\n        component: str,\r\n        invoked_by: str,\r\n        operation: str,\r\n        model_id: str,\r\n        system_prompt: str,\r\n        user_input: Any,\r\n        structured_output_instruction: str,\r\n        output_schema: Any,\r\n        outcome: str,\r\n        latency_ms: int,\r\n        response_payload: Optional[Dict[str, Any]] = None,\r\n        error: Optional[Exception] = None,\r\n    ) -> int:\r\n        request_payload: Dict[str, Any] = {\r\n            "system_prompt": system_prompt,\r\n            "user_input": user_input,\r\n            "structured_output_instruction": structured_output_instruction,\r\n            "output_schema": output_schema,\r\n        }\r\n        encoded_request = json.dumps(request_payload, sort_keys=True, separators=(",", ":"))\r\n        prompt_material = "\\n".join(\r\n            (\r\n                system_prompt,\r\n                structured_output_instruction,\r\n                json.dumps(output_schema, sort_keys=True, separators=(",", ":")),\r\n            )\r\n        )\r\n        prompt_hash = hashlib.sha256(prompt_material.encode("utf-8")).hexdigest()\r\n        request_hash = hashlib.sha256(encoded_request.encode("utf-8")).hexdigest()\r\n        error_code = type(error).__name__ if error else None\r\n        error_message = str(error)[:1000] if error else None\r\n\r\n        with self._database.cursor() as cursor:\r\n            cursor.execute(\r\n                self.INSERT,\r\n                (\r\n                    component,\r\n                    invoked_by,\r\n                    operation,\r\n                    outcome,\r\n                    latency_ms,\r\n                    request_hash,\r\n                    prompt_hash,\r\n                    Jsonb(request_payload),\r\n                    Jsonb(response_payload) if response_payload is not None else None,\r\n                    model_id,\r\n                    error_code,\r\n                    error_message,\r\n                    UUID(case_id),\r\n                ),\r\n            )\r\n            row = cursor.fetchone()\r\n            if row is None:\r\n                raise ValueError(f"Cannot audit agent call for unknown case {case_id}")\r\n            return row[0]\r\n\r\n    def try_record(self, **call) -> None:\r\n        """record(), but a failure is logged and never raised: the audit must not fail the call it describes."""\r\n        try:\r\n            self.record(**call)\r\n        except Exception:\r\n            logger.exception("Could not record the agent call for case %s", call.get("case_id"))\r\n',
    'payment_query_platform.mailbox': '',
    'payment_query_platform.mailbox.graph_client': '"""Reads the shared mailbox from Microsoft Graph.\n\nRequests are addressed to /users/<mailbox> and carry an application bearer\ntoken, so no user is signed in.\n\nChange detection uses the delta endpoint, which is what makes the flow\'s sync\nstate real: each pass ends with a deltaLink that names exactly what the mailbox\nhad been read up to. Storing that link and starting the next pass from it means\na message is offered once, rather than the mailbox being re-scanned every run.\n\nDelta returns changed message stubs but not attachment metadata, so each\nchanged message is then fetched with $expand=attachments to get the attachment\nIDs that Stage 2 needs.\n"""\n\nimport hashlib\nimport os\nimport json\nimport logging\nimport urllib.error\nimport urllib.parse\nimport urllib.request\nfrom datetime import datetime, timezone\nfrom typing import Any, Dict, List, Optional, Tuple\n\nfrom payment_query_platform.models import EmailMessage\nfrom payment_query_platform.text import plain_text\n\nlogger = logging.getLogger(__name__)\n\nTIMEOUT_SECONDS = 20\nMESSAGE_FIELDS = "id,internetMessageId,subject,from,toRecipients,receivedDateTime,body,bodyPreview,conversationId,hasAttachments"\n\n\nclass GraphApiError(RuntimeError):\n    """Raised when the mailbox API cannot be read.\n\n    ``status`` carries the HTTP status when there was one, so a caller can tell a\n    permanent client error (4xx — retrying will not help) from a transient one\n    (5xx, timeout, network — worth retrying). It is None when the call never got\n    an HTTP response.\n    """\n\n    def __init__(self, message: str, status: Optional[int] = None):\n        super().__init__(message)\n        self.status = status\n\n    @property\n    def is_permanent(self) -> bool:\n        """A 4xx means the request itself is wrong; retrying it will fail again."""\n        return self.status is not None and 400 <= self.status < 500\n\n\nclass GraphApiRepository:\n    """Reads one shared mailbox from Microsoft Graph."""\n\n    def __init__(\n        self,\n        base_url: str,\n        mailbox: str,\n        token_provider: Any,\n        timeout: int = TIMEOUT_SECONDS,\n        page_size: int = 50,\n    ):\n        self.base_url = base_url.rstrip("/")\n        self.mailbox = mailbox\n        self.token_provider = token_provider\n        self.timeout = timeout\n        self.page_size = page_size\n\n    def _url(self, path: str) -> str:\n        return f"{self.base_url}/users/{self.mailbox}{path}"\n\n    def _request(self, url: str) -> urllib.request.Request:\n        return urllib.request.Request(\n            url, headers={"Authorization": f"Bearer {self.token_provider.token()}",\n                          "Prefer": f"odata.maxpagesize={self.page_size}"}\n        )\n\n    def _describe(self, exc: urllib.error.HTTPError) -> str:\n        try:\n            return json.loads(exc.read().decode() or "{}").get("error", {}).get("code", "")\n        except Exception:\n            return ""\n\n    def _get(self, url: str) -> Dict[str, Any]:\n        try:\n            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as response:\n                return json.loads(response.read().decode("utf-8"))\n        except urllib.error.HTTPError as exc:\n            raise GraphApiError(f"{url} returned HTTP {exc.code} {self._describe(exc)}".strip(), status=exc.code) from exc\n        except (urllib.error.URLError, TimeoutError) as exc:\n            raise GraphApiError(f"{url} unreachable: {exc}") from exc\n        except json.JSONDecodeError as exc:\n            raise GraphApiError(f"{url} returned invalid JSON") from exc\n\n    def _get_bytes(self, url: str) -> bytes:\n        try:\n            with urllib.request.urlopen(self._request(url), timeout=self.timeout) as response:\n                return response.read()\n        except urllib.error.HTTPError as exc:\n            raise GraphApiError(f"{url} returned HTTP {exc.code} {self._describe(exc)}".strip(), status=exc.code) from exc\n        except (urllib.error.URLError, TimeoutError) as exc:\n            raise GraphApiError(f"{url} unreachable: {exc}") from exc\n\n    def delta_url(self) -> str:\n        """The URL that starts a fresh delta enumeration of the inbox."""\n        # Page size goes in the Prefer header, not $top: $top caps the whole\n        # enumeration, and the deltaLink that follows would skip the rest.\n        params = {"$select": MESSAGE_FIELDS}\n        return self._url("/mailFolders/inbox/messages/delta") + "?" + urllib.parse.urlencode(params)\n\n    def changed_message_ids(self, sync_state: Optional[str] = None) -> Tuple[List[str], str]:\n        """Walk the delta feed, returning changed message IDs and the next sync state.\n\n        Passing the sync state from the previous run resumes from it. Passing\n        None enumerates the inbox from the beginning, which is what the first\n        run does. The returned state is the deltaLink to commit once the\n        messages have been safely persisted.\n\n        Graph answers 410 SyncStateNotFound once it no longer recognises a saved\n        state. Retrying that state fails every time, so the run would be stuck\n        until someone cleared it by hand. Graph\'s own guidance is to start over,\n        so the inbox is enumerated from the beginning instead. That is safe:\n        messages already stored are recognised and skipped downstream.\n        """\n        try:\n            return self._walk(sync_state or self.delta_url())\n        except GraphApiError as exc:\n            if sync_state and exc.status == 410:\n                logger.warning("Graph no longer recognises the saved sync state (%s); "\n                               "reading the inbox from the beginning", exc)\n                return self._walk(self.delta_url())\n            raise\n\n    def _walk(self, url: str) -> Tuple[List[str], str]:\n        message_ids: List[str] = []\n        # Optional inclusive cutoff, ISO-8601 UTC, e.g. 2026-11-12T22:30:00Z.\n        # This avoids fetching full historical message bodies, but Graph delta\n        # pages must still be traversed to obtain a valid checkpoint.\n        cutoff_raw = os.getenv("INGESTION_CUTOFF_UTC", "").strip()\n        cutoff = None\n        if cutoff_raw:\n            cutoff = datetime.fromisoformat(cutoff_raw.replace("Z", "+00:00"))\n            if cutoff.tzinfo is None:\n                raise ValueError("INGESTION_CUTOFF_UTC must include a UTC offset")\n            cutoff = cutoff.astimezone(timezone.utc)\n        next_state = ""\n\n        while url:\n            payload = self._get(url)\n            changes = payload.get("value")\n            if not isinstance(changes, list):\n                raise GraphApiError(f"{url} did not contain a \'value\' array")\n            for change in changes:\n                # Deletions arrive as @removed stubs and carry nothing to ingest.\n                if isinstance(change, dict) and change.get("id") and "@removed" not in change:\n                    if cutoff is not None:\n                        received_at = change.get("receivedDateTime")\n                        # Fail closed on missing/unparseable timestamps rather than\n                        # silently skipping a potentially eligible email.\n                        if not received_at:\n                            raise GraphApiError("Delta message missing receivedDateTime")\n                        try:\n                            received_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))\n                        except ValueError as exc:\n                            raise GraphApiError("Invalid receivedDateTime in delta response") from exc\n                        if received_dt.astimezone(timezone.utc) < cutoff:\n                            continue\n                    message_ids.append(change["id"])\n            next_state = payload.get("@odata.deltaLink") or next_state\n            url = payload.get("@odata.nextLink") or ""\n\n        if not next_state:\n            raise GraphApiError("Graph did not return a deltaLink; sync state cannot be committed")\n        return message_ids, next_state\n\n    def message_ids_before(self, moment: datetime) -> List[str]:\n        """The ID of every inbox message received before the moment (a timezone-aware time)."""\n        params = {"$select": "id", "$top": "100",\n                  "$filter": f"receivedDateTime lt {moment.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"}\n        url = self._url("/mailFolders/inbox/messages") + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)\n        ids: List[str] = []\n        while url:\n            payload = self._get(url)\n            ids += [m["id"] for m in payload.get("value", []) if m.get("id")]\n            url = payload.get("@odata.nextLink") or ""\n        return ids\n\n    def get_message(self, message_id: str) -> Dict[str, Any]:\n        """Return one message with its full body and attachment metadata."""\n        quoted = urllib.parse.quote(message_id, safe="=")\n        params = {"$select": MESSAGE_FIELDS, "$expand": "attachments($select=id,name,contentType,size,isInline)"}\n        return self._get(self._url(f"/messages/{quoted}/") + "?" + urllib.parse.urlencode(params))\n\n    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:\n        """Download the raw content of one attachment."""\n        message = urllib.parse.quote(message_id, safe="")\n        attachment = urllib.parse.quote(attachment_id, safe="")\n        return self._get_bytes(self._url(f"/messages/{message}/attachments/{attachment}/$value"))\n\n\ndef normalize(raw: Dict[str, Any]) -> EmailMessage:\n    """Map a Graph message onto the internal EmailMessage shape."""\n    sender = (raw.get("from") or {}).get("emailAddress") or {}\n    body = raw.get("body") or {}\n    raw_body = body.get("content") or raw.get("bodyPreview") or ""\n    body_text = plain_text(raw_body, limit=len(raw_body) + 1)\n    return EmailMessage.from_dict(\n        {\n            "message_id": raw.get("id") or "",\n            "internet_message_id": raw.get("internetMessageId") or "",\n            "in_reply_to_message_id": raw.get("inReplyTo"),\n            "subject": raw.get("subject") or "",\n            "sender": sender.get("address") or sender.get("name") or "",\n            "recipient": ", ".join(recipients(raw)),\n            "received_at": raw.get("receivedDateTime") or "",\n            "body": body_text,\n            "body_hash": hashlib.sha256(body_text.encode("utf-8")).hexdigest(),\n        }\n    )\n\n\ndef recipients(raw: Dict[str, Any]) -> List[str]:\n    """Every To address on the message, in the order Graph returned them."""\n    addresses = []\n    for entry in raw.get("toRecipients") or []:\n        if not isinstance(entry, dict):\n            continue\n        address = (entry.get("emailAddress") or {}).get("address")\n        if address:\n            addresses.append(address)\n    return addresses\n\n\ndef thread_context(raw: Dict[str, Any]) -> Dict[str, Any]:\n    """Extract the thread and attachment context the downstream stages need."""\n    attachments = [\n        {\n            "attachment_id": a.get("id"),\n            "name": a.get("name"),\n            "content_type": a.get("contentType"),\n            "size_bytes": a.get("size"),\n        }\n        for a in raw.get("attachments") or []\n        if isinstance(a, dict) and not a.get("isInline")\n    ]\n    return {\n        "conversation_id": raw.get("conversationId"),\n        "recipients": recipients(raw),\n        # Inline images set hasAttachments too, so trust the filtered list.\n        "has_attachment": bool(attachments),\n        "attachment_id": attachments[0]["attachment_id"] if attachments else None,\n        "attachments": attachments,\n    }\n',
    'payment_query_platform.mailbox.graph_auth': '"""Acquires Microsoft Graph access tokens for the mailbox application.\r\n\r\nUses the OAuth2 client-credentials flow, so the app reads the mailbox without a\r\nsigned-in user. Credentials live in AWS Secrets Manager and are never held in\r\nenvironment variables, where get-function-configuration would expose them.\r\n\r\nTokens last about an hour and are cached in memory, which a warm Lambda reuses\r\nacross invocations.\r\n"""\r\n\r\nimport json\r\nimport os\r\nimport time\r\nimport urllib.error\r\nimport urllib.parse\r\nimport urllib.request\r\nfrom typing import Any, Dict, Optional\r\n\r\nimport boto3\r\n\r\nTOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"\r\nSCOPE = "https://graph.microsoft.com/.default"\r\n# Refresh early so a token cannot expire mid-request.\r\nEXPIRY_MARGIN_SECONDS = 120\r\n\r\n\r\nclass GraphAuthError(RuntimeError):\r\n    """Raised when a Graph access token cannot be obtained."""\r\n\r\n\r\ndef load_credentials(secret_id: Optional[str] = None) -> Dict[str, Any]:\r\n    """Read the Azure AD application credentials from Secrets Manager."""\r\n    secret_id = secret_id or os.environ["GRAPH_SECRET_ID"]\r\n    client = boto3.client("secretsmanager", region_name=os.getenv("AWS_REGION", "us-east-1"))\r\n    try:\r\n        payload = client.get_secret_value(SecretId=secret_id)["SecretString"]\r\n    except Exception as exc:\r\n        raise GraphAuthError(f"Could not read secret {secret_id}: {exc}") from exc\r\n    credentials = json.loads(payload)\r\n    for field in ("tenant_id", "client_id", "client_secret"):\r\n        if not credentials.get(field):\r\n            raise GraphAuthError(f"Secret {secret_id} is missing {field}")\r\n    return credentials\r\n\r\n\r\nclass GraphTokenProvider:\r\n    """Fetches and caches an application token for Microsoft Graph."""\r\n\r\n    def __init__(self, credentials: Optional[Dict[str, Any]] = None, timeout: int = 30):\r\n        self._credentials = credentials\r\n        self._timeout = timeout\r\n        self._token: Optional[str] = None\r\n        self._expires_at: float = 0.0\r\n\r\n    @property\r\n    def credentials(self) -> Dict[str, Any]:\r\n        if self._credentials is None:\r\n            self._credentials = load_credentials()\r\n        return self._credentials\r\n\r\n    def token(self) -> str:\r\n        if self._token and time.time() < self._expires_at:\r\n            return self._token\r\n\r\n        credentials = self.credentials\r\n        body = urllib.parse.urlencode(\r\n            {\r\n                "grant_type": "client_credentials",\r\n                "client_id": credentials["client_id"],\r\n                "client_secret": credentials["client_secret"],\r\n                "scope": SCOPE,\r\n            }\r\n        ).encode()\r\n\r\n        url = TOKEN_URL.format(tenant=credentials["tenant_id"])\r\n        try:\r\n            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=self._timeout) as response:\r\n                payload = json.load(response)\r\n        except urllib.error.HTTPError as exc:\r\n            detail = json.loads(exc.read().decode() or "{}").get("error", "unknown")\r\n            raise GraphAuthError(f"Token request rejected: {detail}") from exc\r\n        except Exception as exc:\r\n            raise GraphAuthError(f"Token request failed: {exc}") from exc\r\n\r\n        self._token = payload["access_token"]\r\n        self._expires_at = time.time() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_SECONDS\r\n        return self._token\r\n',
    'payment_query_platform.text': '"""Reducing an email body to something safe to carry between stages.\r\n\r\nOutlook sends HTML, and a marketing or phishing message can run to hundreds of\r\nkilobytes of it. Step Functions and SQS both cap a payload at 256 KB, so the\r\nraw body cannot travel through the pipeline: an oversized one failed\r\nStartExecution outright and orphaned the message.\r\n\r\nThe full body is kept in Aurora. What travels is this: tags stripped,\r\nwhitespace collapsed, and a hard cap well inside the limit. Everything\r\ndownstream reads the text a person would have read.\r\n"""\r\n\r\nimport re\r\n\r\nTAGS = re.compile(r"<[^>]+>")\r\nSCRIPT_AND_STYLE = re.compile(r"<(script|style)\\b.*?</\\1>", re.I | re.S)\r\nWHITESPACE = re.compile(r"\\s+")\r\n\r\n# Comfortably inside the 256 KB payload limit, with room for the rest of the\r\n# case context. Far more than any genuine supplier query needs.\r\nMAX_BODY_CHARS = 20000\r\n\r\n\r\ndef plain_text(body: str | None, limit: int = MAX_BODY_CHARS) -> str:\r\n    """Strip HTML to readable text and cap its length."""\r\n    if not body:\r\n        return ""\r\n    text = SCRIPT_AND_STYLE.sub(" ", body)\r\n    text = WHITESPACE.sub(" ", TAGS.sub(" ", text)).strip()\r\n    if len(text) <= limit:\r\n        return text\r\n    return text[:limit].rstrip() + " [truncated]"\r\n',
    'payment_query_platform.models': '"""The message shape carried through every stage of the workflow.\r\n\r\nField names match the production flow specification, so what Stage 1 stores is\r\nwhat Stage 7 reads. Recipient is part of that contract: it travels with the case\r\nall the way to the payment investigation agent.\r\n"""\r\n\r\nfrom dataclasses import asdict, dataclass\r\nfrom typing import Any, Dict, Optional\r\n\r\nREQUIRED_FIELDS = (\r\n    "message_id",\r\n    "internet_message_id",\r\n    "subject",\r\n    "sender",\r\n    "recipient",\r\n    "received_at",\r\n    "body",\r\n    "body_hash",\r\n)\r\n\r\n\r\n@dataclass(frozen=True)\r\nclass EmailMessage:\r\n    message_id: str\r\n    subject: str\r\n    sender: str\r\n    recipient: str\r\n    received_at: str\r\n    body: str\r\n    internet_message_id: str\r\n    body_hash: str\r\n    in_reply_to_message_id: Optional[str] = None\r\n\r\n    @classmethod\r\n    def from_dict(cls, value: Dict[str, Any]) -> "EmailMessage":\r\n        missing = [field for field in REQUIRED_FIELDS if not isinstance(value.get(field), str)]\r\n        if missing:\r\n            raise ValueError(\r\n                "Email message fields must be strings; missing or invalid: " + ", ".join(missing)\r\n            )\r\n        if not value["message_id"]:\r\n            raise ValueError("message_id must not be empty")\r\n        if not value["internet_message_id"]:\r\n            raise ValueError("internet_message_id must not be empty")\r\n        fields = {field: value[field] for field in REQUIRED_FIELDS}\r\n        fields["in_reply_to_message_id"] = value.get("in_reply_to_message_id")\r\n        return cls(**fields)\r\n\r\n    def to_dict(self) -> Dict[str, Any]:\r\n        return asdict(self)\r\n',
}
class _PSCEmbeddedLoader(_psc_abc.MetaPathFinder, _psc_abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _PSC_MODULES: return None
        return _psc_util.spec_from_loader(fullname, self, is_package=fullname in ("payment_query_platform", "payment_query_platform.storage", "payment_query_platform.mailbox"))
    def create_module(self, spec): return None
    def exec_module(self, module):
        exec(compile(_PSC_MODULES[module.__name__], "<embedded:" + module.__name__ + ">", "exec"), module.__dict__)
_psc_sys.meta_path.insert(0, _PSCEmbeddedLoader())

# Main Lambda implementation (unchanged):
"""STAGE 1 — Email ingestion.

Invoked on a schedule by EventBridge, and follows the flow specification step
for step:

    2. Read the previous Graph sync state from Aurora
    3. Get the Graph credentials from Secrets Manager
    4. Call Microsoft Graph
    5. Receive the changed messages and the next sync state
    6. Idempotency check, then persist each message as RECEIVED
    7. Confirm the messages are safely persisted
    8. Commit the new sync state
    9. Publish each newly received message to pre-SF1 SQS (EventBridge Pipe starts SF1)

The ordering of 7 and 8 matters. The sync state advances only after the
messages it covers are in the database, so a crash anywhere in the run leaves
the state pointing at the last safe position. Re-reading that overlap is
harmless because the insert deduplicates on the Graph message ID.

Environment:
    GRAPH_API_BASE_URL   Graph base, e.g. https://graph.microsoft.com/v1.0
    GRAPH_SECRET_ID      Secrets Manager id holding the Azure AD credentials
    GRAPH_MAILBOX        mailbox to read; falls back to the secret's value
    AURORA_HOST          cluster endpoint
    AURORA_DB            database name, defaults to appdb
    AURORA_USER          database user, defaults to integ_app
    SQS_QUEUE_URL       Pre-SF1 SQS queue URL
"""

import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3

from payment_query_platform.storage.database import Database, MessageRepository, SyncStateRepository
from payment_query_platform.mailbox.graph_client import (
    GraphApiError,
    GraphApiRepository,
    normalize,
    thread_context,
)
from payment_query_platform.mailbox.graph_auth import GraphAuthError, GraphTokenProvider, load_credentials
from payment_query_platform.text import plain_text

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Reused across warm invocations.
_database: Database | None = None


def _get_database() -> Database:
    global _database
    if _database is None:
        _database = Database()
    return _database


def build_mailbox(base_url: str) -> GraphApiRepository:
    """Step 3 — credentials from Secrets Manager, never from the environment."""
    credentials = load_credentials(os.environ["GRAPH_SECRET_ID"])
    mailbox = os.getenv("GRAPH_MAILBOX") or credentials.get("mailbox")
    if not mailbox:
        raise GraphAuthError("No mailbox configured; set GRAPH_MAILBOX or add it to the secret")
    return GraphApiRepository(base_url, mailbox=mailbox, token_provider=GraphTokenProvider(credentials))


def workflow_input(detail: Dict[str, Any]) -> Dict[str, Any]:
    """The case as the workflow receives it.

    The raw body does not travel: Outlook sends HTML, and both Step Functions
    and SQS cap a payload at 256 KB. The full body stays in Aurora; what goes
    downstream is the readable text, capped. Everything after this reads
    "body", so the key is kept and only its content changes.
    """
    return {**detail, "body": plain_text(detail.get("body"))}


def publish_to_sqs(queue_url: str, detail: Dict[str, Any]) -> None:
    """Publish a bounded message payload; Pipe, not this Lambda, starts SF1."""
    payload = workflow_input(detail)
    body = json.dumps(payload, default=str)
    if len(body.encode("utf-8")) > 256 * 1024:
        # Message body and metadata remain in Aurora. SF1 can load by message_id.
        body = json.dumps({"message_id": detail["message_id"]})
    args = {"QueueUrl": queue_url, "MessageBody": body}
    if queue_url.endswith(".fifo"):
        import hashlib
        args["MessageGroupId"] = "psc-email-ingestion"
        args["MessageDeduplicationId"] = hashlib.sha256(
            str(detail["message_id"]).encode("utf-8")
        ).hexdigest()
    boto3.client("sqs").send_message(**args)


def mark_enqueued(message_id: str) -> None:
    """Record successful SQS acceptance using columns in DBA schema_ps.sql."""
    with _get_database().cursor() as cursor:
        cursor.execute(
            "UPDATE ps.messages SET status = 'ENQUEUED', processed_at = now() "
            "WHERE message_id = %s AND status = 'RECEIVED' "
            "RETURNING message_id",
            (message_id,),
        )
        if cursor.fetchone() is None:
            raise RuntimeError(
                f"SQS accepted message {message_id}, but Aurora did not confirm its ENQUEUED status"
            )


def recover_unpublished(queue_url: str, context: Any = None, limit: int = 100) -> Dict[str, Any]:
    """Retry persisted RECEIVED messages not yet acknowledged by SQS.

    At-least-once only: a crash between SQS send and DB update can duplicate.
    Downstream SF1 must deduplicate by message_id.
    """
    repo = MessageRepository(_get_database())
    with _get_database().cursor() as cursor:
        cursor.execute(
            "SELECT message_id FROM ps.messages WHERE status = 'RECEIVED' "
            "AND ingested_at < now() - interval '2 minutes' "
            "ORDER BY ingested_at LIMIT %s",
            (limit,),
        )
        ids = [r[0] for r in cursor.fetchall()]
    succeeded, failed = 0, []
    for message_id in ids:
        if context and context.get_remaining_time_in_millis() < 30000:
            break
        try:
            detail = repo.get_staged_message(message_id)
            if detail is None:
                continue
            publish_to_sqs(queue_url, detail)
            mark_enqueued(message_id)
            succeeded += 1
        except Exception as exc:
            logger.exception("Recovery failed for %s", message_id)
            failed.append({"message_id": message_id, "error": str(exc)[:200]})
    return {"candidates": len(ids), "enqueued": succeeded, "failures": failed}


def publish_messages(queue_url: str, details: List[Dict[str, Any]], context: Any = None):
    published, failures = 0, []
    for detail in details:
        if context and context.get_remaining_time_in_millis() < 30_000:
            logger.warning("Nearly out of time; remaining persisted messages require reconciliation")
            break
        try:
            publish_to_sqs(queue_url, detail)
            mark_enqueued(detail["message_id"])
            published += 1
        except Exception as exc:
            logger.exception("Could not enqueue %s", detail["message_id"])
            failures.append({"message_id": detail["message_id"], "error": f"SQS: {exc}"})
    return published, failures


def lambda_handler(event: Dict[str, Any] | None = None, context: Any = None) -> Dict[str, Any]:
    base_url = os.environ["GRAPH_API_BASE_URL"]
    workflow_enabled = os.getenv("ENABLE_WORKFLOW", "false").strip().lower() == "true"
    ingestion_enabled = os.getenv("ENABLE_INGESTION", "false").strip().lower() == "true"
    if not ingestion_enabled:
        return {"statusCode": 200, "body": {"status": "INGESTION_DISABLED"}}
    queue_url = os.environ.get("SQS_QUEUE_URL", "") if workflow_enabled else ""
    if (event or {}).get("action") == "RECOVER_UNPUBLISHED" and not workflow_enabled:
        return {"statusCode": 200, "body": {"status": "WORKFLOW_DISABLED", "messages_enqueued": 0}}
    if (event or {}).get("action") == "RECOVER_UNPUBLISHED":
        return {"statusCode": 200, "body": recover_unpublished(
            queue_url, context, int(os.getenv("RECOVERY_LIMIT", "100"))
        )}

    try:
        mailbox = build_mailbox(base_url)
    except GraphAuthError as exc:
        logger.error("Graph authentication failed: %s", exc)
        return {"statusCode": 500, "body": {"error": str(exc)}}

    database = _get_database()
    messages = MessageRepository(database)
    sync_state = SyncStateRepository(database, mailbox.mailbox)

    # Step 2 — previous sync state, STATE-A.
    # The reconciler asks for a full re-read when it finds a mail that was never saved.
    previous_state = None if (event or {}).get("resync") else sync_state.get()
    logger.info("Resuming from a committed sync state: %s", bool(previous_state))

    # Steps 4 and 5 — changed messages and the next sync state, STATE-B.
    try:
        message_ids, next_state = mailbox.changed_message_ids(previous_state)
    except GraphApiError as exc:
        logger.error("Could not read the mailbox: %s", exc)
        return {"statusCode": 502, "body": {"error": str(exc)}}

    received: List[Dict[str, Any]] = []
    already_stored = 0
    failures: List[Dict[str, str]] = []

    for message_id in message_ids:
        try:
            raw = mailbox.get_message(message_id)
            message = normalize(raw)
            detail = {**message.to_dict(), **thread_context(raw)}

            # Steps 6 and 7 — persist, and note whether this message is new.
            if messages.save_if_absent(message.message_id, detail, message.received_at):
                received.append(detail)
            else:
                already_stored += 1
        except Exception as exc:  # one bad message must not stop the batch
            permanent = isinstance(exc, GraphApiError) and exc.is_permanent
            logger.exception("Failed on message %s (%s)", message_id,
                             "permanent" if permanent else "transient")
            failures.append({"message_id": message_id, "error": f"{type(exc).__name__}: {exc}",
                             "permanent": permanent})

    # Step 8 — commit the delta link only when every changed message was
    # successfully staged (or was already staged). A permanent Graph error
    # must not silently discard a supplier email. Such failures require
    # investigation; subsequent scheduled runs will retry from the last
    # committed checkpoint.
    committed = False
    if failures:
        logger.error(
            "Holding Graph sync state due to %d message ingestion failure(s)",
            len(failures),
        )
    else:
        sync_state.commit(next_state)
        committed = True

    # Step 9 — publish to SQS. EventBridge Pipe is responsible for starting SF1.
    published = 0
    recovery = {"enqueued": 0, "failures": []}
    if workflow_enabled:
        published, failed = publish_messages(queue_url, received, context)
        failures += failed
        # Also retry earlier persisted records that were not successfully enqueued.
        recovery = recover_unpublished(queue_url, context, int(os.getenv("RECOVERY_LIMIT", "100")))
        failures += recovery["failures"]
    else:
        logger.info("ENABLE_WORKFLOW=false: SQS publishing and recovery skipped")

    body = {
        "changed": len(message_ids),
        "received": len(received),
        "already_stored": already_stored,
        "messages_enqueued": published,
        "recovered_enqueued": recovery["enqueued"],
        "sync_state_committed": committed,
        "workflow_enabled": workflow_enabled,
        "cutoff_utc": os.getenv("INGESTION_CUTOFF_UTC", ""),
    }
    if failures:
        body["failures"] = failures
    logger.info("Stage 1 complete: %s", body)
    return {"statusCode": 200, "body": body}
