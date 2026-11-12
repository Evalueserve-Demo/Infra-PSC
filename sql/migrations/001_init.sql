-- PSC STAGE 1 / BUSINESS SCHEMA — 001_init.sql
-- STATUS: PREPARED FOR REVIEW; NOT YET EXECUTED ON POSTGRESQL.
-- SOURCE: DBA ASCENT Payment Status v1.1 (13 tables) plus 5 proposed
-- operational tables for ingestion, checkpointing and workflow handoff.
-- Fresh database only; no existing data migration is included.
-- Before execution: DBA approval, PostgreSQL 15+ execution test,
-- application DB user/proxy grants, and alignment with Lambda/Step Functions.
-- Do not run this file against an existing populated database.

-- ASCENT Payment Status (Epic 3) schema, v1.1. Aurora PostgreSQL 15+. Generated 2026-10-12. Review before applying.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS ps;
SET search_path TO ps;

CREATE SEQUENCE IF NOT EXISTS seq_case_number;

CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END; $$ LANGUAGE plpgsql;

-- status_ref: Single lookup table for controlled vocabularies: case statuses, outcomes, root causes, mismatch types.
CREATE TABLE status_ref (
    code                       VARCHAR(40)      NOT NULL PRIMARY KEY,
    domain                     VARCHAR(20)      NOT NULL,
    description                VARCHAR(255)     NOT NULL,
    is_terminal                BOOLEAN          DEFAULT false NOT NULL,
    sort_order                 SMALLINT        ,
    is_active                  BOOLEAN          DEFAULT true NOT NULL,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT ck_ref_domain CHECK (domain IN ('CASE_STATUS','OUTCOME','ROOT_CAUSE'))
);
CREATE TRIGGER status_ref_upd BEFORE UPDATE ON status_ref FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- source_status_map: Allowed raw status values per source system and object type, and the outcome each one maps to.
CREATE TABLE source_status_map (
    map_id                     SMALLINT         GENERATED ALWAYS AS IDENTITY NOT NULL PRIMARY KEY,
    source_system              VARCHAR(20)      NOT NULL,
    object_type                VARCHAR(20)      NOT NULL,
    source_status              VARCHAR(40)      NOT NULL,
    outcome_code               VARCHAR(40)     ,
    description                VARCHAR(255)     NOT NULL,
    is_exception               BOOLEAN          DEFAULT false NOT NULL,
    is_active                  BOOLEAN          DEFAULT true NOT NULL,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_source_status_map_outcome_code FOREIGN KEY (outcome_code) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT ck_map_system CHECK (source_system IN ('ARIBA','S4HANA')),
    CONSTRAINT ck_map_object CHECK (object_type IN ('INVOICE','IR','RECEIPT','PAYMENT')),
    CONSTRAINT uq_status_map UNIQUE (source_system, object_type, source_status)
);
CREATE TRIGGER source_status_map_upd BEFORE UPDATE ON source_status_map FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- case_header: One row per email thread in the Group Mailbox that is a payment status query.
CREATE TABLE case_header (
    case_id                    UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_number                VARCHAR(20)      DEFAULT 'PSQ-' || to_char(now(),'YYYY') || '-' || lpad(nextval('seq_case_number')::text, 6, '0') NOT NULL,
    correlation_id             UUID             DEFAULT gen_random_uuid() NOT NULL,
    mailbox_id                 VARCHAR(320)     NOT NULL,
    conversation_id            VARCHAR(255)     NOT NULL,
    case_status                VARCHAR(40)      DEFAULT 'NEW' NOT NULL,
    outcome_code               VARCHAR(40)     ,
    linked_case_id             UUID            ,
    is_itar                    BOOLEAN          DEFAULT false NOT NULL,
    assigned_queue             VARCHAR(30)     ,
    escalation_reason          VARCHAR(255)    ,
    summary                    TEXT            ,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    closed_at                  TIMESTAMPTZ     ,
    reopen_count               SMALLINT         DEFAULT 0 NOT NULL,
    last_reopened_at           TIMESTAMPTZ     ,
    CONSTRAINT uq_case_header_case_number UNIQUE (case_number),
    CONSTRAINT uq_case_header_correlation_id UNIQUE (correlation_id),
    CONSTRAINT uq_case_header_mailbox_conversation UNIQUE (mailbox_id, conversation_id),
    CONSTRAINT fk_case_header_case_status FOREIGN KEY (case_status) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT fk_case_header_outcome_code FOREIGN KEY (outcome_code) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT fk_case_header_linked_case_id FOREIGN KEY (linked_case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT ck_case_queue CHECK (assigned_queue IS NULL OR assigned_queue IN ('TCS_HELPDESK','US_HELPDESK')),
    CONSTRAINT ck_case_escalation CHECK (case_status <> 'ESCALATED' OR escalation_reason IS NOT NULL),
    CONSTRAINT ck_case_closed CHECK (closed_at IS NULL OR closed_at >= created_at),
    CONSTRAINT ck_case_reopen CHECK (reopen_count >= 0)
);
CREATE TRIGGER case_header_upd BEFORE UPDATE ON case_header FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- case_message: One row per email in or out of the Group Mailbox that belongs to a case.
CREATE TABLE case_message (
    message_id                 UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    direction                  VARCHAR(8)       NOT NULL,
    internet_message_id        VARCHAR(512)    ,
    graph_message_id           VARCHAR(512)    ,
    in_reply_to_message_id     VARCHAR(255)    ,
    sender_address             VARCHAR(320)     NOT NULL,
    recipient_addresses        TEXT            ,
    subject                    VARCHAR(500)    ,
    body_text                  TEXT            ,
    body_uri                   VARCHAR(1024)   ,
    body_hash                  CHAR(64)         NOT NULL,
    received_at                TIMESTAMPTZ      NOT NULL,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_message_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT ck_msg_direction CHECK (direction IN ('INBOUND','OUTBOUND')),
    CONSTRAINT ck_msg_body CHECK (body_text IS NOT NULL OR body_uri IS NOT NULL)
);

-- case_attachment: Metadata for every attachment received on an email or ticket. Binary stays in S3.
CREATE TABLE case_attachment (
    attachment_id              UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    message_id                 UUID             NOT NULL,
    file_name                  VARCHAR(255)     NOT NULL,
    mime_type                  VARCHAR(255)     NOT NULL,
    size_bytes                 BIGINT           NOT NULL,
    sha256                     CHAR(64)         NOT NULL,
    storage_uri                VARCHAR(1024)    NOT NULL,
    scan_status                VARCHAR(20)      DEFAULT 'PENDING' NOT NULL,
    extraction_status          VARCHAR(20)      DEFAULT 'PENDING' NOT NULL,
    extracted_text_uri         VARCHAR(1024)   ,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_attachment_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT fk_case_attachment_message_id FOREIGN KEY (message_id) REFERENCES case_message(message_id) ON DELETE RESTRICT,
    CONSTRAINT ck_att_size CHECK (size_bytes > 0),
    CONSTRAINT ck_att_scan CHECK (scan_status IN ('PENDING','CLEAN','THREAT','FAILED','UNSUPPORTED')),
    CONSTRAINT ck_att_extract CHECK (extraction_status IN ('PENDING','EXTRACTED','FAILED','SKIPPED')),
    CONSTRAINT uq_att_message_storage UNIQUE (message_id, storage_uri)
);
CREATE TRIGGER case_attachment_upd BEFORE UPDATE ON case_attachment FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- case_identifier: Every invoice, PO, supplier or contract identifier found in the request, with where it came from and whether it validated.
CREATE TABLE case_identifier (
    identifier_id              UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    identifier_type            VARCHAR(20)      NOT NULL,
    supplied_value             VARCHAR(100)     NOT NULL,
    canonical_value            VARCHAR(16)     ,
    source                     VARCHAR(20)      NOT NULL,
    confidence                 NUMERIC(4,3)    ,
    validation_status          VARCHAR(20)      DEFAULT 'PENDING' NOT NULL,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_identifier_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT ck_ident_type CHECK (identifier_type IN ('INVOICE','PO','SUPPLIER','CONTRACT','RECEIPT')),
    CONSTRAINT ck_ident_canonical CHECK (canonical_value IS NULL OR canonical_value ~ '^[A-Za-z0-9_-]{1,16}$'),
    CONSTRAINT ck_ident_source CHECK (source IN ('EMAIL_BODY','SUBJECT','ATTACHMENT','HUMAN')),
    CONSTRAINT ck_ident_conf CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_ident_valid CHECK (validation_status IN ('PENDING','VALID','INVALID_FORMAT','NOT_FOUND')),
    CONSTRAINT uq_ident UNIQUE (case_id, identifier_type, canonical_value)
);
CREATE TRIGGER case_identifier_upd BEFORE UPDATE ON case_identifier FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- case_investigation: One row per invoice-PO pair investigated under a case. Invoice and PO are in the same row; an invoice spanning two POs is two rows. Owns per-pair status, scope and recheck schedule.
CREATE TABLE case_investigation (
    investigation_id           UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    correlation_id             UUID             NOT NULL,
    identifier_id              UUID            ,
    invoice_number             VARCHAR(16)      NOT NULL,
    po_number                  VARCHAR(16)     ,
    pair_seq                   SMALLINT         DEFAULT 1 NOT NULL,
    is_primary_po              BOOLEAN          DEFAULT true NOT NULL,
    pairing_source             VARCHAR(12)      DEFAULT 'SUPPLIED' NOT NULL,
    po_line_numbers            JSONB           ,
    scope_class                VARCHAR(10)     ,
    status                     VARCHAR(20)      DEFAULT 'PENDING' NOT NULL,
    attempt_no                 SMALLINT         DEFAULT 1 NOT NULL,
    next_recheck_at            TIMESTAMPTZ     ,
    latest_finding_id          UUID            ,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_investigation_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT fk_case_investigation_identifier_id FOREIGN KEY (identifier_id) REFERENCES case_identifier(identifier_id) ON DELETE RESTRICT,
    CONSTRAINT ck_inv_scope CHECK (scope_class IS NULL OR scope_class IN ('ITAR','PO_BASED','NON_PO')),
    CONSTRAINT ck_inv_status CHECK (status IN ('PENDING','IN_PROGRESS','COMPLETE','RECHECK_SCHEDULED','FAILED','ESCALATED')),
    CONSTRAINT ck_inv_attempt CHECK (attempt_no >= 1),
    CONSTRAINT uq_investigation UNIQUE NULLS NOT DISTINCT (case_id, invoice_number, po_number),
    CONSTRAINT ck_inv_pair_seq CHECK (pair_seq >= 1),
    CONSTRAINT ck_inv_pairing CHECK (pairing_source IN ('SUPPLIED','API','HUMAN'))
);
CREATE TRIGGER case_investigation_upd BEFORE UPDATE ON case_investigation FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- invoice_finding: What the source systems said about one invoice at one point in time, and the outcome the agent derived from it.
CREATE TABLE invoice_finding (
    finding_id                 UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    investigation_id           UUID             NOT NULL,
    correlation_id             UUID             NOT NULL,
    invoice_number             VARCHAR(16)      NOT NULL,
    po_number                  VARCHAR(16)     ,
    supplier_id                VARCHAR(50)     ,
    invoice_amount             NUMERIC(15,2)   ,
    currency                   CHAR(3)         ,
    invoice_date               DATE            ,
    due_date                   DATE            ,
    ariba_invoice_status       VARCHAR(40)     ,
    ariba_ir_status            VARCHAR(40)     ,
    ariba_receipt_status       VARCHAR(40)     ,
    s4_payment_status          VARCHAR(40)     ,
    s4_payment_block           VARCHAR(40)     ,
    scheduled_payment_date     DATE            ,
    paid_on                    DATE            ,
    payment_reference_masked   VARCHAR(50)     ,
    outcome_code               VARCHAR(40)      NOT NULL,
    root_cause_code            VARCHAR(40)     ,
    evidence                   JSONB            DEFAULT '{}' NOT NULL,
    source_captured_at         TIMESTAMPTZ      NOT NULL,
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_invoice_finding_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT fk_invoice_finding_investigation_id FOREIGN KEY (investigation_id) REFERENCES case_investigation(investigation_id) ON DELETE RESTRICT,
    CONSTRAINT fk_invoice_finding_outcome_code FOREIGN KEY (outcome_code) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT fk_invoice_finding_root_cause_code FOREIGN KEY (root_cause_code) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT ck_inv_amount CHECK (invoice_amount IS NULL OR invoice_amount >= 0),
    CONSTRAINT ck_inv_currency CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_inv_rootcause CHECK (outcome_code NOT IN ('OVERDUE','REJECTED','MISMATCH_FOUND') OR root_cause_code IS NOT NULL)
);
ALTER TABLE case_investigation ADD CONSTRAINT fk_case_investigation_latest_finding FOREIGN KEY (latest_finding_id) REFERENCES invoice_finding(finding_id) ON DELETE RESTRICT;

-- response_template: Approved, versioned reply templates per outcome, channel and audience, with the placeholders each one needs.
CREATE TABLE response_template (
    template_id                UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    template_code              VARCHAR(50)      NOT NULL,
    version                    SMALLINT         DEFAULT 1 NOT NULL,
    outcome_code               VARCHAR(40)      NOT NULL,
    audience                   VARCHAR(20)      NOT NULL,
    subject_template           VARCHAR(500),
    body_template              TEXT             NOT NULL,
    required_placeholders      JSONB            DEFAULT '[]' NOT NULL,
    is_active                  BOOLEAN          DEFAULT true NOT NULL,
    approved_by                VARCHAR(100),
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_response_template_outcome_code FOREIGN KEY (outcome_code) REFERENCES status_ref(code) ON DELETE RESTRICT,
    CONSTRAINT ck_tpl_version CHECK (version >= 1),
    CONSTRAINT ck_tpl_audience CHECK (audience IN ('SUPPLIER','HELPDESK','PA_BP','REQUESTOR')),
    CONSTRAINT uq_tpl_code_ver UNIQUE (template_code, version)
);
CREATE TRIGGER response_template_upd BEFORE UPDATE ON response_template FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- case_response: Each draft produced for a case, the values filled in, the human review decision, and the send result.
CREATE TABLE case_response (
    response_id                UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    template_id                UUID             NOT NULL,
    finding_id                 UUID,
    draft_version              SMALLINT         DEFAULT 1 NOT NULL,
    draft_subject              VARCHAR(500),
    draft_body                 TEXT             NOT NULL,
    placeholder_values         JSONB            DEFAULT '{}' NOT NULL,
    review_status              VARCHAR(20)      DEFAULT 'QUEUED' NOT NULL,
    assigned_to                VARCHAR(100),
    assigned_at                TIMESTAMPTZ,
    claim_expires_at           TIMESTAMPTZ,
    reviewer_id                VARCHAR(100),
    reviewed_at                TIMESTAMPTZ,
    review_comment             VARCHAR(1000),
    final_body                 TEXT,
    sent_at                    TIMESTAMPTZ,
    sent_external_ref          VARCHAR(255),
    created_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_response_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT fk_case_response_template_id FOREIGN KEY (template_id) REFERENCES response_template(template_id) ON DELETE RESTRICT,
    CONSTRAINT fk_case_response_finding_id FOREIGN KEY (finding_id) REFERENCES invoice_finding(finding_id) ON DELETE RESTRICT,
    CONSTRAINT ck_resp_status CHECK (review_status IN ('QUEUED','IN_REVIEW','APPROVED','EDITED_APPROVED','REJECTED','SEND_QUEUED','SENT','SEND_FAILED','WITHDRAWN')),
    CONSTRAINT ck_resp_claim CHECK (review_status <> 'IN_REVIEW' OR assigned_to IS NOT NULL),
    CONSTRAINT ck_resp_reviewer CHECK (review_status IN ('QUEUED','IN_REVIEW','WITHDRAWN') OR reviewer_id IS NOT NULL),
    CONSTRAINT ck_resp_sent CHECK (review_status <> 'SENT' OR sent_at IS NOT NULL),
    CONSTRAINT uq_resp_version UNIQUE (case_id, draft_version)
);
CREATE TRIGGER case_response_upd BEFORE UPDATE ON case_response FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- outbound_dispatch: One row per attempt to deliver an approved reply: frontend approve -> SQS -> Graph reply in thread.
CREATE TABLE outbound_dispatch (
    dispatch_id                UUID             DEFAULT gen_random_uuid() NOT NULL PRIMARY KEY,
    response_id                UUID             NOT NULL,
    case_id                    UUID             NOT NULL,
    correlation_id             UUID             NOT NULL,
    attempt_no                 SMALLINT         DEFAULT 1 NOT NULL,
    status                     VARCHAR(20)      DEFAULT 'QUEUED' NOT NULL,
    sqs_message_id             VARCHAR(100),
    payload_hash               CHAR(64)         NOT NULL,
    external_ref               VARCHAR(255),
    error_code                 VARCHAR(50),
    error_message              VARCHAR(1000),
    queued_at                  TIMESTAMPTZ      DEFAULT now() NOT NULL,
    delivered_at               TIMESTAMPTZ,
    updated_at                 TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_outbound_dispatch_response_id FOREIGN KEY (response_id) REFERENCES case_response(response_id) ON DELETE RESTRICT,
    CONSTRAINT fk_outbound_dispatch_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT ck_disp_status CHECK (status IN ('QUEUED','IN_FLIGHT','DELIVERED','FAILED','DEAD_LETTER','CANCELLED')),
    CONSTRAINT ck_disp_attempt CHECK (attempt_no >= 1),
    CONSTRAINT ck_disp_delivered CHECK (status <> 'DELIVERED' OR delivered_at IS NOT NULL),
    CONSTRAINT uq_disp_attempt UNIQUE (response_id, attempt_no)
);
CREATE TRIGGER outbound_dispatch_upd BEFORE UPDATE ON outbound_dispatch FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- integration_call_log: One row per outbound call: Ariba, S/4, Graph, FootPrints, Bedrock model invocations. Carries the correlation id.
CREATE TABLE integration_call_log (
    call_id                    BIGINT           GENERATED ALWAYS AS IDENTITY NOT NULL PRIMARY KEY,
    correlation_id             UUID             NOT NULL,
    request_id                 UUID             DEFAULT gen_random_uuid() NOT NULL,
    parent_request_id          UUID,
    case_id                    UUID,
    component                  VARCHAR(50)      NOT NULL,
    invoked_by                 VARCHAR(100),
    target_system              VARCHAR(30)      NOT NULL,
    operation                  VARCHAR(100)     NOT NULL,
    attempt_no                 SMALLINT         DEFAULT 1 NOT NULL,
    http_status                SMALLINT,
    outcome                    VARCHAR(10)      NOT NULL,
    latency_ms                 INTEGER,
    request_hash               CHAR(64),
    response_uri               VARCHAR(1024),
    error_code                 VARCHAR(50),
    error_message              VARCHAR(1000),
    model_id                   VARCHAR(100),
    input_tokens               INTEGER,
    output_tokens              INTEGER,
    guardrail_action           VARCHAR(12),
    guardrail_ref              VARCHAR(100),
    run_at                     TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT uq_integration_call_log_request_id UNIQUE (request_id),
    CONSTRAINT fk_integration_call_log_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE SET NULL,
    CONSTRAINT ck_log_target CHECK (target_system IN ('ARIBA','S4HANA','GRAPH','BEDROCK','BEDW','INTERNAL')),
    CONSTRAINT ck_log_outcome CHECK (outcome IN ('SUCCESS','FAILURE','TIMEOUT','REJECTED')),
    CONSTRAINT ck_log_attempt CHECK (attempt_no >= 1),
    CONSTRAINT ck_log_latency CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT ck_log_guardrail CHECK (guardrail_action IS NULL OR guardrail_action IN ('NONE','INTERVENED','BLOCKED'))
);

-- case_event: Append-only business audit trail: every status change, decision and send, with actor.
CREATE TABLE case_event (
    event_id                   BIGINT           GENERATED ALWAYS AS IDENTITY NOT NULL PRIMARY KEY,
    case_id                    UUID             NOT NULL,
    correlation_id             UUID             NOT NULL,
    event_type                 VARCHAR(40)      NOT NULL,
    from_status                VARCHAR(40),
    to_status                  VARCHAR(40),
    actor_type                 VARCHAR(10)      NOT NULL,
    actor_id                   VARCHAR(100)     NOT NULL,
    details                    JSONB,
    occurred_at                TIMESTAMPTZ      DEFAULT now() NOT NULL,
    CONSTRAINT fk_case_event_case_id FOREIGN KEY (case_id) REFERENCES case_header(case_id) ON DELETE RESTRICT,
    CONSTRAINT ck_evt_actor CHECK (actor_type IN ('AGENT','HUMAN','SYSTEM'))
);

-- Indexes
CREATE INDEX ix_case_status ON case_header (case_status, updated_at);
CREATE INDEX ix_case_created ON case_header (created_at);
CREATE INDEX ix_case_linked ON case_header (linked_case_id);
CREATE INDEX ix_msg_case ON case_message (case_id, received_at);
CREATE INDEX ix_att_pending ON case_attachment (scan_status) WHERE scan_status = 'PENDING';
CREATE INDEX ix_ident_canonical ON case_identifier (canonical_value);
CREATE INDEX ix_invest_recheck ON case_investigation (status, next_recheck_at);
CREATE INDEX ix_invest_invoice ON case_investigation (invoice_number);
CREATE INDEX ix_invest_po ON case_investigation (po_number);
CREATE INDEX ix_find_case ON invoice_finding (case_id, created_at DESC);
CREATE INDEX ix_find_investigation ON invoice_finding (investigation_id, created_at DESC);
CREATE INDEX ix_find_invoice ON invoice_finding (invoice_number);
CREATE INDEX ix_find_po ON invoice_finding (po_number);
CREATE INDEX ix_find_outcome ON invoice_finding (outcome_code);
CREATE UNIQUE INDEX uq_invest_primary ON case_investigation (case_id, invoice_number) WHERE is_primary_po;
CREATE INDEX ix_tpl_lookup ON response_template (outcome_code, audience, is_active);
CREATE UNIQUE INDEX uq_tpl_one_active ON response_template (template_code) WHERE is_active;
CREATE INDEX ix_resp_queue ON case_response (review_status, created_at) WHERE review_status IN ('QUEUED','IN_REVIEW');
CREATE INDEX ix_resp_assigned ON case_response (assigned_to) WHERE review_status = 'IN_REVIEW';
CREATE UNIQUE INDEX uq_resp_sent_ref ON case_response (sent_external_ref) WHERE sent_external_ref IS NOT NULL;
CREATE INDEX ix_disp_open ON outbound_dispatch (status, queued_at) WHERE status IN ('QUEUED','IN_FLIGHT');
CREATE UNIQUE INDEX uq_disp_external ON outbound_dispatch (external_ref) WHERE external_ref IS NOT NULL;
CREATE INDEX ix_log_corr ON integration_call_log (correlation_id, run_at);
CREATE INDEX ix_log_case ON integration_call_log (case_id);
CREATE INDEX ix_log_target ON integration_call_log (target_system, outcome, run_at);
CREATE INDEX ix_evt_case ON case_event (case_id, occurred_at);
CREATE INDEX ix_evt_corr ON case_event (correlation_id);
CREATE INDEX ix_ref_domain ON status_ref (domain, sort_order);

-- Domain guard: each FK column may only use codes from its own status_ref domain
CREATE OR REPLACE FUNCTION trg_check_status_domain() RETURNS trigger AS $$
DECLARE d TEXT; BEGIN
  EXECUTE format('SELECT domain FROM ps.status_ref WHERE code = ($1).%I', TG_ARGV[0]) INTO d USING NEW;
  IF d IS NOT NULL AND d <> TG_ARGV[1] THEN
    RAISE EXCEPTION 'column % must use status_ref domain %, got %', TG_ARGV[0], TG_ARGV[1], d;
  END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER case_header_status_dom BEFORE INSERT OR UPDATE ON case_header FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('case_status','CASE_STATUS');
CREATE TRIGGER case_header_outcome_dom BEFORE INSERT OR UPDATE ON case_header FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('outcome_code','OUTCOME');
CREATE TRIGGER invoice_finding_outcome_dom BEFORE INSERT ON invoice_finding FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('outcome_code','OUTCOME');
CREATE TRIGGER invoice_finding_rc_dom BEFORE INSERT ON invoice_finding FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('root_cause_code','ROOT_CAUSE');
CREATE TRIGGER status_map_outcome_dom BEFORE INSERT OR UPDATE ON source_status_map FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('outcome_code','OUTCOME');
CREATE TRIGGER response_template_outcome_dom BEFORE INSERT OR UPDATE ON response_template FOR EACH ROW EXECUTE FUNCTION trg_check_status_domain('outcome_code','OUTCOME');

-- Append-only tables: application role gets INSERT and SELECT only
-- REVOKE UPDATE, DELETE ON case_message, invoice_finding, integration_call_log, case_event FROM ps_app;

-- Seed status_ref
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('NEW', 'CASE_STATUS', 'Case created from a new thread or ticket; nothing processed yet', false, 10);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('AWAITING_IDENTIFIER', 'CASE_STATUS', 'No usable PO/invoice id; information-request draft raised (FR10)', false, 20);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('INVESTIGATING', 'CASE_STATUS', 'Agent is querying Ariba / S4 / FootPrints context', false, 30);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('PENDING_REVIEW', 'CASE_STATUS', 'Draft produced, waiting for human reviewer', false, 40);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('APPROVED', 'CASE_STATUS', 'Reviewer approved; send in progress', false, 50);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('RESPONDED', 'CASE_STATUS', 'Reply sent or comment posted; may be rechecked (FR15)', false, 60);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('ESCALATED', 'CASE_STATUS', 'Routed to human queue; agent stops (FR25, FR40)', false, 70);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('OUT_OF_SCOPE', 'CASE_STATUS', 'Not a payment query / unsupported ticket type; redirected', true, 80);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('CLOSED', 'CASE_STATUS', 'Case complete', true, 90);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('PAID', 'OUTCOME', 'Invoice paid; payment date and masked reference available', false, 10);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('SCHEDULED', 'OUTCOME', 'Reconciled, in payment run, scheduled date known', false, 20);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('NOT_YET_DUE', 'OUTCOME', 'Reconciled, due date in the future', false, 30);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('OVERDUE', 'OUTCOME', 'Reconciled, unpaid, past due; root cause required', false, 40);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('REJECTED', 'OUTCOME', 'Invoice rejected in Ariba; root cause required', false, 50);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('RECONCILING', 'OUTCOME', 'Submitted, not yet 3-way matched', false, 60);

INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('RECEIPT_PENDING', 'OUTCOME', 'Waiting on receipt creation or approval (PO >= USD 5,000)', false, 70);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('MISMATCH_FOUND', 'OUTCOME', 'PO vs invoice vs receipt mismatch detected; see po_mismatch_finding', false, 80);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('NOT_FOUND', 'OUTCOME', 'Identifier valid in format but not found in source systems', false, 90);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('DATA_INCOMPLETE', 'OUTCOME', 'Source returned incomplete data; review, never assume delay', false, 100);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('INVALID_ID_FORMAT', 'ROOT_CAUSE', 'Invoice id breaks Ariba rule (spaces, special chars, > 16 chars)', false, 10);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('SUPPLIER_REQUESTED_REJECTION', 'ROOT_CAUSE', 'Explicit rejection comment present', false, 20);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('DUPLICATE_INVOICE', 'ROOT_CAUSE', 'Invoice number already used', false, 30);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('PO_CLOSED', 'ROOT_CAUSE', 'Matched PO is closed', false, 40);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('UNKNOWN_SYSTEM_REJECTION', 'ROOT_CAUSE', 'Rejected with no rule match; manual review, do not invent a reason', false, 50);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('SUPPLIER_BLOCKED', 'ROOT_CAUSE', 'Payment block on supplier (SPVR / S4)', false, 60);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('BANK_CHANGE_PENDING', 'ROOT_CAUSE', 'Recent banking change under verification', false, 70);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('SUPPLIER_NOT_IN_NETWORK', 'ROOT_CAUSE', 'Supplier absent from Ariba Network / SPVR; escalate to verify Exostar/EDI', false, 80);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('RECEIPT_NOT_APPROVED', 'ROOT_CAUSE', 'Receipt not uploaded or not approved by requestor/BP', false, 90);
INSERT INTO status_ref (code, domain, description, is_terminal, sort_order) VALUES ('PO_MISMATCH', 'ROOT_CAUSE', 'IR exception caused by PO/invoice/receipt difference', false, 100);

-- Seed source_status_map
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Unprocessed', NULL, 'Invoice received, not yet processed', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Rejecting', 'REJECTED', 'Rejection in progress', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Rejected', 'REJECTED', 'Invoice rejected; supplier resubmits or Boeing corrects', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Paying', 'SCHEDULED', 'In payment run', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Paid', 'PAID', 'Payment issued', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'FailedRejecting', 'DATA_INCOMPLETE', 'System failure during rejection; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'FailedPaying', 'DATA_INCOMPLETE', 'System failure during payment; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Canceled', 'REJECTED', 'Invoice cancelled', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'Invalid', 'REJECTED', 'Invoice invalid (format or content)', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'AwaitingExternalResolution', 'DATA_INCOMPLETE', 'Waiting on an external system; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'INVOICE', 'FailedExternalPush', 'DATA_INCOMPLETE', 'Push to ERP failed; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Reconciling', NULL, 'Submitted, not yet matched; outcome depends on receipt and exceptions', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Approving', NULL, 'Reconciled, in approval flow', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Paying', 'SCHEDULED', 'Approved, in payment queue', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Paid', 'PAID', 'Paid', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Rejected', 'REJECTED', 'Reconciliation rejected; exceptions are the evidence', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'FailedPaying', 'DATA_INCOMPLETE', 'Payment failure; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'IR', 'Canceled', 'REJECTED', 'Reconciliation cancelled', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Composing', 'RECEIPT_PENDING', 'Receipt being created by requestor', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Submitted', 'RECEIPT_PENDING', 'Receipt submitted, awaiting approval', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Approved', NULL, 'Receipt approved; proceed to invoice leg', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Denied', 'RECEIPT_PENDING', 'Receipt denied by approver; BP/requestor action', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Processed', NULL, 'Receipt processed', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'FailedProcessing', 'DATA_INCOMPLETE', 'Receipt processing failed; manual review', true);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Invoicing', NULL, 'Invoice being created against receipt', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'InvoiceCreated', NULL, 'Invoice created against receipt', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('ARIBA', 'RECEIPT', 'Invoiced', NULL, 'Fully invoiced', false);
INSERT INTO source_status_map (source_system, object_type, source_status, outcome_code, description, is_exception) VALUES ('S4HANA', 'PAYMENT', 'TBC', NULL, 'Placeholder: S/4 payment API and status list not confirmed in any project document (open item). Replace once ESTS confirms.', false);


-- =========================================================
-- PSC operational ingestion and workflow tables (proposed)
-- =========================================================
CREATE TABLE ps.ingestion_run (
 run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 mailbox_id VARCHAR(320) NOT NULL,
 folder_id VARCHAR(512) NOT NULL,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 status VARCHAR(20) NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','COMPLETED','PARTIAL','FAILED')),
 fetched_count INTEGER NOT NULL DEFAULT 0 CHECK(fetched_count >= 0),
 inserted_count INTEGER NOT NULL DEFAULT 0 CHECK(inserted_count >= 0),
 duplicate_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_count >= 0),
 failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
 error_summary TEXT
);
CREATE INDEX ix_ingestion_run_started ON ps.ingestion_run(mailbox_id, started_at DESC);

CREATE TABLE ps.ingestion_message (
 ingestion_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID REFERENCES ps.ingestion_run(run_id) ON DELETE SET NULL,
 mailbox_id VARCHAR(320) NOT NULL,
 graph_message_id VARCHAR(512) NOT NULL,
 internet_message_id VARCHAR(512),
 conversation_id VARCHAR(512),
 sender_address VARCHAR(320),
 recipient_addresses TEXT,
 subject TEXT,
 received_at TIMESTAMPTZ,
 body_text TEXT,
 body_uri TEXT,
 body_hash CHAR(64),
 has_attachments BOOLEAN,
 status VARCHAR(24) NOT NULL DEFAULT 'RECEIVED'
   CHECK(status IN ('RECEIVED','STORED','ATTACHMENTS_PENDING','READY','HANDED_OFF','CLASSIFIED','IGNORED','FAILED')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
 last_error TEXT,
 case_id UUID REFERENCES ps.case_header(case_id) ON DELETE RESTRICT,
 case_message_id UUID UNIQUE REFERENCES ps.case_message(message_id) ON DELETE RESTRICT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_ingestion_mailbox_graph UNIQUE(mailbox_id, graph_message_id),
 CONSTRAINT ck_ingestion_body CHECK(body_text IS NULL OR body_hash IS NOT NULL)
);
CREATE INDEX ix_ingestion_message_status ON ps.ingestion_message(status, created_at);
CREATE INDEX ix_ingestion_message_conversation ON ps.ingestion_message(mailbox_id, conversation_id);

CREATE TABLE ps.ingestion_attachment (
 ingestion_attachment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 ingestion_id UUID NOT NULL REFERENCES ps.ingestion_message(ingestion_id) ON DELETE RESTRICT,
 graph_attachment_id VARCHAR(512) NOT NULL,
 attachment_type VARCHAR(100),
 filename TEXT,
 content_type VARCHAR(255),
 size_bytes BIGINT CHECK(size_bytes >= 0),
 sha256 CHAR(64),
 storage_uri TEXT,
 s3_version_id TEXT,
 upload_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
   CHECK(upload_status IN ('PENDING','STORED','UNSUPPORTED','FAILED')),
 scan_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
   CHECK(scan_status IN ('PENDING','CLEAN','THREAT','FAILED','UNSUPPORTED')),
 scan_completed_at TIMESTAMPTZ,
 last_error TEXT,
 case_attachment_id UUID UNIQUE REFERENCES ps.case_attachment(attachment_id) ON DELETE RESTRICT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_ingestion_attachment UNIQUE(ingestion_id, graph_attachment_id)
);
CREATE INDEX ix_ingestion_attachment_scan ON ps.ingestion_attachment(scan_status, created_at);

CREATE TABLE ps.ingestion_checkpoint (
 mailbox_id VARCHAR(320) NOT NULL,
 folder_id VARCHAR(512) NOT NULL,
 cursor_url TEXT,
 cursor_kind VARCHAR(10) NOT NULL DEFAULT 'INITIAL'
   CHECK(cursor_kind IN ('INITIAL','NEXT','DELTA')),
 lease_owner UUID,
 lease_expires_at TIMESTAMPTZ,
 version BIGINT NOT NULL DEFAULT 0 CHECK(version >= 0),
 last_success_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(mailbox_id, folder_id)
);

CREATE TABLE ps.workflow_outbox (
 outbox_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 ingestion_id UUID NOT NULL REFERENCES ps.ingestion_message(ingestion_id) ON DELETE RESTRICT,
 event_type VARCHAR(64) NOT NULL,
 payload JSONB NOT NULL,
 status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
   CHECK(status IN ('PENDING','IN_FLIGHT','DELIVERED','FAILED')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_owner UUID,
 lease_expires_at TIMESTAMPTZ,
 execution_arn TEXT,
 delivered_at TIMESTAMPTZ,
 last_error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_outbox_ingestion_event UNIQUE(ingestion_id,event_type)
);
CREATE INDEX ix_workflow_outbox_due ON ps.workflow_outbox(status,next_attempt_at)
 WHERE status IN ('PENDING','FAILED');

-- =========================================================
-- STAGE 1: OPERATIONAL INTEGRITY AND QUERY SUPPORT
-- =========================================================
CREATE TRIGGER ingestion_message_updated_at
BEFORE UPDATE ON ps.ingestion_message
FOR EACH ROW EXECUTE FUNCTION ps.trg_set_updated_at();

CREATE TRIGGER ingestion_attachment_updated_at
BEFORE UPDATE ON ps.ingestion_attachment
FOR EACH ROW EXECUTE FUNCTION ps.trg_set_updated_at();

CREATE TRIGGER ingestion_checkpoint_updated_at
BEFORE UPDATE ON ps.ingestion_checkpoint
FOR EACH ROW EXECUTE FUNCTION ps.trg_set_updated_at();

CREATE TRIGGER workflow_outbox_updated_at
BEFORE UPDATE ON ps.workflow_outbox
FOR EACH ROW EXECUTE FUNCTION ps.trg_set_updated_at();

CREATE INDEX ix_ingestion_checkpoint_lease
ON ps.ingestion_checkpoint(lease_expires_at)
WHERE lease_owner IS NOT NULL;

CREATE INDEX ix_ingestion_attachment_message
ON ps.ingestion_attachment(ingestion_id);

CREATE INDEX ix_workflow_outbox_lease
ON ps.workflow_outbox(lease_expires_at)
WHERE status = 'IN_FLIGHT';

ALTER TABLE ps.workflow_outbox
ADD CONSTRAINT ck_workflow_event_type
CHECK (length(trim(event_type)) > 0);

ALTER TABLE ps.ingestion_run
ADD CONSTRAINT ck_ingestion_run_completed
CHECK (status <> 'COMPLETED' OR finished_at IS NOT NULL);

ALTER TABLE ps.ingestion_attachment
ADD CONSTRAINT ck_ingestion_attachment_storage
CHECK (upload_status <> 'STORED' OR storage_uri IS NOT NULL);

ALTER TABLE ps.ingestion_attachment
ADD CONSTRAINT ck_ingestion_attachment_clean
CHECK (scan_status <> 'CLEAN' OR (upload_status = 'STORED' AND storage_uri IS NOT NULL));

-- DBA review: use least-privilege roles; do not grant the app master-user access.
-- This file is a proposed fresh-install schema; NOT approved for execution.
COMMIT;