# Database migrations

`001_init.sql` is the single schema for the platform: the case tables and the
mailbox staging tables (`ps.messages`, `ps.graph_sync_state`). It is the file
from the application repository, `infra/schema_ps.sql`, copied unchanged.

Fresh database, in this order:

1. `sql/admin/setup_integ_app_role.sql` creates the `integ_app` role.
2. `sql/migrations/001_init.sql` creates the schema and grants to `integ_app`.

Do not run `001_init.sql` against a database that already has the tables.
Do not assume tables exist before enabling ingestion.
