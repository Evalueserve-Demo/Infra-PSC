# Changes to the Infra-PSC Terraform

This branch starts from Infra-PSC `main` (commit e9a27cf) with the changes below on top.
To see them all:

    git diff main..psc-lambda-layout

`sql/migrations/001_init.sql` is not in the patch: it is the same as the one in Infra-PSC.

This repository holds the same Terraform files at its root, so the folder
structure, file names and resource names are those of Infra-PSC. Everything
else in it (`tools/`, `tests/`, `infra/`, `docs/`) is not read by Terraform.

## What changed

| Area | Change |
|---|---|
| Database | `001_init.sql` is the application's schema (`ps.messages`, `ps.graph_sync_state`, the case tables). The database user is `integ_app`: `sql/admin/setup_integ_app_role.sql` creates it before the schema runs. `variables.tf`, `dev.tfvars` and the `130` comment follow. |
| `sf1` (170) | Checks `message_id`. No Lambda creates an `ingestion_id`. |
| `sf2` (220) | Runs the application's intake: `Classify`, `IsPaymentRelated`, `ExtractIdentifiers`, `PublishToSQS2`. The intake Lambda (395) screens the mail with the guardrail, calls the agents, labels the mail in Outlook and decides whether it is investigated. `mail-preprocessing`, `identifier-validation` and `invoice-scope` stay deployed but are not called. |
| New Lambdas | `395` intake-agents, `396` reconciler, `397` results-report (his 400-530 are reserved for later stages) (with a private results bucket). Both schedules are disabled by default. |
| Agents (260, 265, 320) | The classification, extraction and investigation runtimes run the application's Strands agents. Environment is `AGENT_ROLE` and `BEDROCK_MODEL_ID` (variable `agent_model_id`). The roles gain `InvokeModelWithResponseStream`; investigation also gains the model and gateway permissions it lacked. |
| Gateway (360) | The five targets expose the application's tool names and schemas. The four target names are shortened (`spvr-target`, `payment-rules-target`, `root-cause-map-target`, `draft-builder-target`): the gateway names each tool `<target>___<tool>`, and Bedrock rejects a tool name over 64 characters. |
| Lambda code | `email-ingestion` keeps the existing switches, the `ENQUEUED` confirmation and the cutoff, with the application's bookmark logic restored (a permanently failing mail is skipped, not allowed to hold the sync state). `s4hana-tool` uses the application's mock. |
| Environment (160, 180, 190, 310) | The variable names the code reads were missing and are added: `AURORA_*`, `GRAPH_API_BASE_URL`, `GRAPH_SECRET_ID`, `SQS_QUEUE_URL`. `190` and `310` also gain `rds-db:connect`; `310` joins the VPC. |
| Cutoff | Variable `ingestion_cutoff_utc`: mail older than it is not ingested and the reconciler does not look for it. |

## Before applying

1. `terraform plan` first. The files parse and every reference resolves, but
   Terraform could not be run where they were written. One thing to confirm in
   the plan: the draft builder's `transaction_details` property is a free-form
   object, written as `type = "object"` with no nested properties.
2. Add to `dev.tfvars` (it is not in this repository):

        email_ingestion_db_user = "integ_app"
        ingestion_cutoff_utc    = "<the go-live time, ISO-8601 UTC>"

3. Build the packages. The Terraform zips `build/<name>/` for every Lambda and
   agent, and `scripts/build_packages.py` writes those folders from `lambda/`
   (it reads the names from the `source_dir` lines in the `*.tf` files):

       python scripts/build_packages.py --deps <deps> --agent-bundle <bundle.zip>

   - `--deps` holds `psycopg`, `psycopg_binary` and `pypdf` built for Lambda's
     platform (pip will not resolve these on Windows):

         pip install --target <deps> --platform manylinux2014_x86_64 \
             --python-version 3.12 --implementation cp --only-binary=:all: \
             --no-deps psycopg psycopg-binary typing_extensions tzdata pypdf

   - `--agent-bundle` is the zip of the agents' dependencies built for
     AgentCore's ARM64 Python runtime (Strands, bedrock-agentcore, pydantic,
     mcp, httpx, boto3). Only the three agents use it.
   - A Lambda that uses the database gets `--deps` beside its code; a tool
     Lambda is just its own file.
   - `intake-agents`, `reconciler`, `results-report` and the three agents are
     each one complete file with the application code inside, so they need
     nothing else from this repository.

## Going live

1. Run `sql/admin/setup_integ_app_role.sql`, then `001_init.sql`.
2. Set `ingestion_cutoff_utc` to the go-live time and `reconcile_since` to the
   same time, so mail that arrived earlier is neither ingested nor retried.
3. Switch on, in this order: `ENABLE_WORKFLOW = "true"` in
   `160-email-ingestion-lambda.tf` (a literal there, not a variable);
   `enable_investigation_invocation`; the pipes (`pre-sf1-pipe`, `pipe1`,
   `pipe2`); then the schedules (`enable_scheduler`, `enable_reconciler_schedule`,
   `enable_results_export_schedule`).
4. Send a test mail and check `results/latest.csv` in the results bucket.

## What was tested

In a copy of the stack in us-east-2, with a real mail and the pipes and
schedules off: ingestion, `sf1`, `sf2` and the investigation ran to a stored
draft and an Outlook label; the guardrail stopped a prompt-injection mail; the
reconciler reported nothing missed.

Not tested: mails with attachments (including the malware-scan step), several
invoices in one mail, the escalation paths, the reconciler retrying or
escalating a stuck mail, and the pipes and schedules running unattended.

## Cut-over from the scripted us-east-2 stack to Terraform

The us-east-2 stack was brought up to these files by `infra/apply_devops_stack_fixes.py`, not by Terraform. Three things in it carry a `-test` name because Terraform has to create its own:

| Scripted (live) | Terraform creates |
|---|---|
| Lambdas `intake-agents-test`, `reconciler-test`, `results-report-test`, each with a `*-test-role` | the same without `-test` (files 395, 396, 397) |
| Schedules `reconciler-test-schedule`, `results-test-schedule`, with `recon-sched-test-role` and `results-sched-test-role` | `reconciler-schedule`, `results-export-schedule` and their roles |
| `email-schedule` (already Terraform's) | unchanged; enabled with `enable_scheduler = true` |

A Lambda cannot be renamed, and creating the Terraform names by hand would make `terraform apply` fail with "already exists". So the order is:

1. `terraform apply` creates the new Lambdas, roles and schedules, and repoints `sf2` at the new intake Lambda.
2. Send a test mail and check `results/latest.csv`.
3. Delete the `*-test` Lambdas, roles and schedules (`python infra/apply_devops_stack_fixes.py --remove-new` for the Lambdas), so two reconcilers and two exports are not running at once.

Until step 3, both the `-test` and the Terraform schedules would run if both are enabled; set `enable_reconciler_schedule` and `enable_results_export_schedule` to `true` only after step 3.

## Deploying without Terraform

Two scripts do the whole deploy from this folder. Run them in this order:

    python scripts/build_packages.py --deps <deps> --agent-bundle <bundle.zip>
    python infra/apply_devops_stack_fixes.py              # shows what it would change
    python infra/apply_devops_stack_fixes.py --apply      # makes the changes

`--apply` saves what it is about to change first. To go back, run
`python infra/apply_devops_stack_fixes.py --restore <backup folder>`.
