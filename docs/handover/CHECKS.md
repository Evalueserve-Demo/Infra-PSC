# Checking the code runs

Two levels. The first needs only Python and no AWS. The second is done in the AWS console after a deploy.

## 1. On a laptop, no AWS

Python 3.11 or 3.12, then:

    pip install pytest "psycopg[binary]" pypdf boto3 pg8000 bedrock-agentcore

Run from the repository root:

    python scripts/check_lambdas.py
    python -m pytest tests

`check_lambdas.py` compiles and loads every Lambda, tool and agent. Lambdas are loaded twice, once reading mail from Outlook (`MAIL_SOURCE` unset) and once with `MAIL_SOURCE=s3`. It prints `PASS`, `SKIP` or `FAIL` per file and exits with 1 if anything failed. A `SKIP` means a package is missing on this machine (the line names it); install it and run again. A `FAIL` naming an embedded module means a Lambda is missing a module it imports.

`pytest` runs the tests against the code inside the Lambda and agent files, so they check what is deployed. They also compare every embedded copy of a module across files and fail if two copies differ.

Expected: all tests pass, none fail. Two groups are left out on purpose:

- `test_apis.py` and `test_rules.py` need the `tools/` folder and are skipped when it is absent.
- `test_attachment_store.py` and `test_attachments.py` describe the earlier design in which the text of an attachment is read as the file is stored. attachment-processing now stores the file and reads the text in a later step (`EXTRACT_CLEAN`). They need rewriting before they are switched on.

These tests use fakes (for example a fake S3 and a fake database). They show each piece works alone. They do not show that one stage hands the right data to the next; that is level 2.

## 2. After a deploy, in the AWS console

For each Lambda: open it and check the state is Active and the last update succeeded. A broken package shows `Runtime.ImportModuleError` in its first CloudWatch log.

Investigation Lambda, Test tab. Both calls are safe and change nothing:

- `{}` with `ENABLE_INVESTIGATION_INVOCATION=false` returns the error `Investigation invocation disabled by configuration`.
- `{"Records": []}` with it set to `true` returns `{"statusCode": 200, "body": {"handled": []}}`.

Do not invoke the other Lambdas by hand with an empty event. They read the mailbox or write to the database.

AgentCore: each runtime (classification, extraction, investigation) should show status READY.

## 3. One mail through the stages

1. Set `mail_source = "s3"` (or leave `graph`) and make sure the roles can read the bucket.
2. For S3: upload a CSV with the columns `message_id, received_at, sender, subject, body` (others optional, see `samples/mail_inbox_example.csv`) to `<mail_s3_prefix>` in the attachments bucket. Attachments go to `<prefix>attachments/<message_id>/<file name>`.
3. The pre-SF1 pipe and pipes 1 and 2 are STOPPED in the Terraform; start the ones you want to test. To stop after classification and extraction, leave pipe 2 stopped.
4. Run the ingestion Lambda (or wait for the schedule). Then follow the mail:
   - Step Functions 1 execution succeeded (attachments, then email processing).
   - A case row exists in the database; the message is on SQS1.
   - Step Functions 2 execution succeeded. A payment status request goes on SQS2; any other mail ends at `NotInvestigated`, labelled with the reason.
   - The dead-letter queues are empty and the Lambda `Errors` metrics stay at zero.

| Symptom | Likely cause |
|---|---|
| `Runtime.ImportModuleError` | a module or package missing from the Lambda zip |
| `AccessDenied` on S3 | the role lacks list/get on the mail prefix |
| `KeyError` for a `GRAPH_*` or `MAIL_*` name | an environment value is not set |
| timeout calling Bedrock or the gateway | no route from the VPC: NAT or VPC endpoint missing |
| runtime not READY | the image failed its `/ping` check on port 8080 |
