# Boeing Helpdesk PSC — V2 source bundle

## Safety and status
This is a **source-code draft, NOT a complete working deployment**. The stages are numbered #010–#440 and include Terraform infrastructure, Lambda source files, SQL schema, and agent contracts. Several enterprise and AgentCore stages are explicitly blocked because API contracts and provider integration details are unknown. The project must not be applied as-is.

## Exact sequence
010 VPC; 020 Subnets; 030 IGW; 040 EIP; 050 NAT; 060 Routes; 070 SG; 080 VPC endpoints; 090 KMS; 100 Secrets; 110 S3; 120 Aurora; 130 Proxy; 140 IAM; 150 Scheduler; 160 Ingestion Lambda; 170 SF1; 180 Attachment; 190 Email Processing; 200 SQS1; 210 Pipe1; 220 SF2; 230 Preprocessing; 240 Guardrail; 250 Model; 260 Classification Runtime; 270 Identifier Validation; 280 Invoice Scope; 290 SQS2; 300 Pipe2; 310 Investigation Invocation; 320 Investigation Runtime; 330 Identity; 340 Policy; 350 Memory; 360 Gateway; 370 PR/ITAR; 380 Ariba; 390 S4; 400 Auth; 410 Review Backend; 420 API; 430 Reply; 440 Observability.

## Current blockers
- Microsoft Graph delta/checkpoint ingestion, attachment download and reply are not implemented.
- RDS schema is provided but not automatically applied; DB access libraries and credentials must be integrated.
- Classification is not connected to SF2. Investigation AgentCore runtime and Gateway are not provisioned.
- Enterprise tools fail closed pending approved API contracts.
- Human review API returns 503 until persistence and transaction-safe review actions are implemented.
- IAM currently grants broad shared permissions; least privilege per Lambda is required before deployment.
- No `terraform plan` or `terraform apply` has been executed.

## Source structure
`lambda/*/handler.py`, `agents/*/main.py`, `sql/migrations/001_init.sql`, `docs/AGENTCORE-INTEGRATIONS.md`. Terraform creates ZIPs from Python single files; package dependencies separately if adding third-party imports. Build folder must exist (`mkdir -p build`).

## Validation
Terraform CLI is not installed in the generation environment; the Terraform configuration has not been validated. Validate and resolve all errors before planning or deployment. Keep your original PSC-Demo project unchanged.
