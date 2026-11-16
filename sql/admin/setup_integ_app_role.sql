-- =========================================================
-- PSC POSTGRESQL APPLICATION ROLE
-- File: sql/admin/setup_integ_app_role.sql
-- Database: paymentstatuscheck
--
-- Run this once, as a database administrator, before
-- sql/migrations/001_init.sql. That file grants table access to
-- integ_app, so the role has to exist first.
--
-- This file only creates the role and lets it connect. The table
-- grants are in 001_init.sql.
--
-- No password is stored here. Set one separately, and keep it the
-- same as the one in the RDS Proxy secret (130-rds-proxy.tf).
-- If the proxy logs in with IAM instead of the secret, also run:
--     GRANT rds_iam TO integ_app;
-- =========================================================

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'integ_app'
    ) THEN
        CREATE ROLE integ_app LOGIN;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE paymentstatuscheck
TO integ_app;

COMMIT;
