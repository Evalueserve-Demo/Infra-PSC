
-- =========================================================
-- 500 - HUMAN REVIEW DATABASE SCHEMA
-- AURORA POSTGRESQL
-- =========================================================

-- ---------------------------------------------------------
-- 1. HUMAN REVIEW CASES
-- ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS human_review_cases (
    case_id VARCHAR(100) PRIMARY KEY,
    message_id VARCHAR(255) NOT NULL,
    conversation_id VARCHAR(255),
    invoice_id VARCHAR(100),

    supplier_email VARCHAR(320) NOT NULL,
    email_subject TEXT,

    investigation_status VARCHAR(50) NOT NULL
        DEFAULT 'PENDING_ENTERPRISE_INTEGRATION',

    investigation_result JSONB NOT NULL
        DEFAULT '{}'::jsonb,

    draft_reply TEXT,

    processing_status VARCHAR(50) NOT NULL
        DEFAULT 'PENDING_REVIEW',

    approved_reply TEXT,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT human_review_cases_status_check
        CHECK (
            processing_status IN (
                'PENDING_REVIEW',
                'APPROVED',
                'REJECTED',
                'REPLY_QUEUED',
                'REPLY_SENDING',
                'REPLY_SENT',
                'REPLY_FAILED'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_human_review_cases_status
ON human_review_cases (processing_status, created_at);

-- ---------------------------------------------------------
-- 2. HUMAN REVIEW DECISIONS
-- ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS human_review_decisions (
    approval_id UUID PRIMARY KEY,

    case_id VARCHAR(100) NOT NULL
        REFERENCES human_review_cases(case_id),

    reviewer_id VARCHAR(255) NOT NULL,

    decision VARCHAR(50) NOT NULL,

    original_draft TEXT,
    final_reply TEXT,

    reviewed_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT human_review_decision_check
        CHECK (
            decision IN (
                'APPROVED',
                'EDITED_AND_APPROVED',
                'REJECTED'
            )
        ),

    CONSTRAINT one_review_decision_per_case
        UNIQUE (case_id)
);

-- ---------------------------------------------------------
-- 3. HUMAN REVIEW OUTBOX
-- ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS human_review_outbox (
    event_id UUID PRIMARY KEY,

    approval_id UUID NOT NULL UNIQUE
        REFERENCES human_review_decisions(approval_id),

    case_id VARCHAR(100) NOT NULL
        REFERENCES human_review_cases(case_id),

    event_type VARCHAR(100) NOT NULL,

    event_payload JSONB NOT NULL,

    publish_status VARCHAR(30) NOT NULL
        DEFAULT 'PENDING',

    attempts INTEGER NOT NULL DEFAULT 0,

    last_error TEXT,

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    published_at TIMESTAMPTZ,

    CONSTRAINT human_review_outbox_status_check
        CHECK (
            publish_status IN (
                'PENDING',
                'PUBLISHING',
                'PUBLISHED',
                'FAILED'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_human_review_outbox_pending
ON human_review_outbox (created_at)
WHERE publish_status = 'PENDING';


-- =========================================================
-- 510 - REPLY DELIVERY ATTEMPTS
-- =========================================================
-- A durable record prevents automatic duplicate Graph sends.
-- UNCERTAIN cases require operational reconciliation.

CREATE TABLE IF NOT EXISTS human_review_delivery_attempts (
    event_id UUID PRIMARY KEY,
    approval_id UUID NOT NULL UNIQUE
        REFERENCES human_review_decisions(approval_id),
    case_id VARCHAR(100) NOT NULL
        REFERENCES human_review_cases(case_id),

    delivery_status VARCHAR(30) NOT NULL
        DEFAULT 'CLAIMED',

    created_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMPTZ NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT human_review_delivery_status_check
        CHECK (
            delivery_status IN (
                'CLAIMED',
                'ACCEPTED',
                'UNCERTAIN'
            )
        )
);

CREATE INDEX IF NOT EXISTS
    idx_human_review_delivery_status
ON human_review_delivery_attempts (
    delivery_status,
    created_at
);
