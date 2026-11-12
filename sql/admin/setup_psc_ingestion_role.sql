-- =========================================================
-- PSC STAGE 1 - POSTGRESQL APPLICATION ROLE AND GRANTS
-- File: sql/admin/setup_psc_ingestion_role.sql
-- Database: paymentstatuscheck
--
-- PREREQUISITES:
-- 1. Aurora PostgreSQL is deployed.
-- 2. 001_init.sql has been reviewed, approved and executed.
-- 3. Connect as an authorized database administrator.
--
-- SECURITY:
-- Password is intentionally NOT stored in this file.
-- Set the LOGIN password separately using a secure method.
-- The RDS Proxy secret must use the same username/password.
-- =========================================================

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'psc_ingestion'
    ) THEN
        CREATE ROLE psc_ingestion LOGIN;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE paymentstatuscheck
TO psc_ingestion;

GRANT USAGE ON SCHEMA ps
TO psc_ingestion;

-- Email Ingestion Lambda
GRANT SELECT, INSERT, UPDATE
ON TABLE
    ps.ingestion_run,
    ps.ingestion_checkpoint,
    ps.ingestion_message,
    ps.workflow_outbox
TO psc_ingestion;

-- Attachment Processing and Malware Scan Handler
GRANT SELECT, INSERT, UPDATE
ON TABLE ps.ingestion_attachment
TO psc_ingestion;

COMMIT;
