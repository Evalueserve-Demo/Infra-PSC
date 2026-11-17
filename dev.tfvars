
# =========================================================
# DEV ENVIRONMENT CONFIGURATION
# Project: Boeing Helpdesk Payment Status Check
# =========================================================


# ---------------------------------------------------------
# 1. AWS ACCOUNT AND REGION
# ---------------------------------------------------------

aws_account_id = "684485044617"
aws_region     = "us-east-2"


# ---------------------------------------------------------
# 2. PROJECT DETAILS
# ---------------------------------------------------------

environment  = "dev"
project_name = "boeing-helpdesk-paymentstatuscheck"


# ---------------------------------------------------------
# 3. VPC CONFIGURATION
# ---------------------------------------------------------

vpc_cidr = "10.0.0.0/16"


# ---------------------------------------------------------
# 4. AVAILABILITY ZONES
# ---------------------------------------------------------

availability_zone_a = "us-east-2a"
availability_zone_b = "us-east-2b"


# ---------------------------------------------------------
# 5. PUBLIC SUBNETS
# ---------------------------------------------------------

public_subnet_a_cidr = "10.0.1.0/24"
public_subnet_b_cidr = "10.0.2.0/24"


# ---------------------------------------------------------
# 6. PRIVATE APPLICATION SUBNETS
# ---------------------------------------------------------

private_subnet_a_cidr = "10.0.11.0/24"
private_subnet_b_cidr = "10.0.12.0/24"


# ---------------------------------------------------------
# 7. DATABASE SUBNETS
# ---------------------------------------------------------

database_subnet_a_cidr = "10.0.21.0/24"
database_subnet_b_cidr = "10.0.22.0/24"


# ---------------------------------------------------------
# 8. EMAIL INGESTION
# ---------------------------------------------------------

mailbox_user     = "Boeing.ISC@Evalueserve.com"
enable_scheduler = true


# ---------------------------------------------------------
# 9. AURORA POSTGRESQL
# ---------------------------------------------------------

database_name     = "paymentstatuscheck"
database_username = "pscadmin"


# ---------------------------------------------------------
# 10. AMAZON BEDROCK
# ---------------------------------------------------------

bedrock_model_id = ""


# ---------------------------------------------------------
# 11. HUMAN REVIEW AUTHENTICATION
# ---------------------------------------------------------

human_review_jwt_issuer   = ""
human_review_jwt_audience = ""


# ---------------------------------------------------------
# 12. ENTERPRISE INTEGRATIONS
# ---------------------------------------------------------

sap_ariba_api_url             = ""
sap_s4_api_url                = ""
pr_itar_api_url               = ""
external_api_token_secret_arn = ""


# ---------------------------------------------------------
# 13. AGENTCORE RUNTIMES
# ---------------------------------------------------------

agentcore_runtime_arn_classification = ""
agentcore_runtime_arn_investigation  = ""

# ---------------------------------------------------------
# AWS KMS
# ---------------------------------------------------------

kms_enable_key_rotation  = true
kms_deletion_window_days = 30

# ---------------------------------------------------------
# AWS SECRETS MANAGER
# ---------------------------------------------------------

secrets_recovery_window_days = 30

# ---------------------------------------------------------
# S3 ATTACHMENTS
# ---------------------------------------------------------

attachments_versioning_status             = "Enabled"
attachments_abort_multipart_days          = 7
attachments_malware_object_tagging_status = "ENABLED"

# ---------------------------------------------------------
# AURORA POSTGRESQL SERVERLESS V2 - DEV
# ---------------------------------------------------------

aurora_engine_version         = "16.10"
aurora_parameter_group_family = "aurora-postgresql16"

aurora_serverless_min_acu = 0.5
aurora_serverless_max_acu = 4

aurora_backup_retention_days = 7
aurora_backup_window         = "03:00-04:00"
aurora_maintenance_window    = "sun:04:00-sun:05:00"

aurora_deletion_protection       = false
aurora_skip_final_snapshot       = true
aurora_final_snapshot_identifier = "boeing-helpdesk-paymentstatuscheck-dev-aurora-final"

aurora_auto_minor_version_upgrade   = true
aurora_performance_insights_enabled = false

# ---------------------------------------------------------
# RDS PROXY - DEV
# ---------------------------------------------------------

rds_proxy_idle_client_timeout = 1800
rds_proxy_debug_logging       = false

rds_proxy_max_connections_percent      = 80
rds_proxy_max_idle_connections_percent = 50
rds_proxy_connection_borrow_timeout    = 120

# =========================================================
# 150 - EVENTBRIDGE SCHEDULER
# =========================================================

email_scheduler_expression = "rate(5 minutes)"
email_scheduler_timezone   = "UTC"


# =========================================================
# 160 - EMAIL INGESTION LAMBDA
# =========================================================

email_ingestion_timeout = 900
email_ingestion_memory             = 512
email_ingestion_log_retention_days = 30
email_ingestion_db_name            = "paymentstatuscheck"
email_ingestion_db_user            = "integ_app"



# =========================================================
# 170 - STEP FUNCTIONS 1
# =========================================================

sf1_name_suffix              = "sf1"
sf1_type                     = "STANDARD"
sf1_attachment_lambda_suffix = "attachment-processing"

sf1_retry_interval_seconds = 2
sf1_max_retry_attempts     = 3
sf1_retry_backoff_rate     = 2.0


# =========================================================
# 180 - ATTACHMENT PROCESSING LAMBDA
# =========================================================

attachment_processing_name_suffix        = "attachment-processing"
attachment_processing_runtime            = "python3.12"
attachment_processing_timeout            = 120
attachment_processing_memory             = 256
attachment_processing_log_retention_days = 30

attachment_processing_s3_prefix = "email-attachments"
attachment_processing_max_bytes = 10485760


# =========================================================
# 190 - EMAIL PROCESSING LAMBDA
# =========================================================

email_processing_name_suffix        = "email-processing"
email_processing_runtime            = "python3.12"
email_processing_timeout            = 60
email_processing_memory             = 256
email_processing_log_retention_days = 30


# =========================================================
# 200 - SQS1 CONFIGURATION
# =========================================================

sqs1_name_suffix                = "sqs1"
sqs1_dlq_name_suffix            = "sqs1-dlq"
sqs1_visibility_timeout_seconds = 180
sqs1_retention_seconds          = 345600
sqs1_dlq_retention_seconds      = 1209600
sqs1_receive_wait_time_seconds  = 20
sqs1_max_receive_count          = 5
sqs1_kms_data_key_reuse_seconds = 300


# =========================================================
# 210 - EVENTBRIDGE PIPE 1 CONFIGURATION
# =========================================================

pipe1_name_suffix             = "pipe1"
pipe1_role_name_suffix        = "pipe1-execution-role"
pipe1_desired_state           = "RUNNING"
pipe1_batch_size              = 1
pipe1_batching_window_seconds = 0

# =========================================================
# 220 - STEP FUNCTIONS 2 CONFIGURATION
# =========================================================

sf2_name_suffix            = "sf2"
sf2_type                   = "STANDARD"
sf2_retry_interval_seconds = 2
sf2_max_retry_attempts     = 3
sf2_retry_backoff_rate     = 2.0

# =========================================================
# 230 - MAIL PREPROCESSING CONFIGURATION
# =========================================================

mail_preprocessing_name_suffix        = "mail-preprocessing"
mail_preprocessing_runtime            = "python3.12"
mail_preprocessing_timeout            = 60
mail_preprocessing_memory             = 256
mail_preprocessing_log_retention_days = 30


# =========================================================
# 240 - BEDROCK GUARDRAIL CONFIGURATION
# =========================================================

classification_guardrail_name_suffix = "guardrail"

classification_guardrail_blocked_input_message  = "The request could not be processed because it did not meet the content safety policy."
classification_guardrail_blocked_output_message = "The generated response could not be returned because it did not meet the content safety policy."

classification_guardrail_content_filters = [
  {
    type            = "HATE"
    input_strength  = "MEDIUM"
    output_strength = "MEDIUM"
  },
  {
    type            = "VIOLENCE"
    input_strength  = "MEDIUM"
    output_strength = "MEDIUM"
  },
  {
    type            = "SEXUAL"
    input_strength  = "MEDIUM"
    output_strength = "MEDIUM"
  },
  {
    type            = "INSULTS"
    input_strength  = "MEDIUM"
    output_strength = "MEDIUM"
  },
  {
    type            = "MISCONDUCT"
    input_strength  = "MEDIUM"
    output_strength = "MEDIUM"
  },
  {
    type            = "PROMPT_ATTACK"
    input_strength  = "HIGH"
    output_strength = "NONE"
  }
]

classification_guardrail_pii_entities = [
  {
    type   = "US_SOCIAL_SECURITY_NUMBER"
    action = "BLOCK"
  },
  {
    type   = "CREDIT_DEBIT_CARD_NUMBER"
    action = "BLOCK"
  }
]

# =========================================================
# 250 - BEDROCK MODEL CONFIGURATION
# =========================================================

classification_model_id                = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
classification_model_temperature       = 0
classification_model_max_output_tokens = 1024
classification_model_top_p             = 1



# =========================================================
# 260 - CLASSIFICATION AGENTCORE
# =========================================================

classification_agent_runtime_name     = "psc_dev_classification"
classification_agent_role_suffix      = "agentcore-role"
classification_artifact_bucket_suffix = "agents"
classification_agent_artifact_key     = "classification/classification_agent.zip"


# =========================================================
# 270 - IDENTIFIER VALIDATION
# =========================================================

identifier_validation_lambda_suffix         = "identifier-validation"
identifier_validation_timeout               = 30
identifier_validation_memory_size           = 256
identifier_validation_log_retention_days    = 14
identifier_validation_max_invoices          = 25
identifier_validation_max_identifier_length = 100


# =========================================================
# 280 - INVOICE SCOPE DETERMINATION
# =========================================================

invoice_scope_lambda_suffix      = "invoice-scope"
invoice_scope_timeout            = 60
invoice_scope_memory_size        = 256
invoice_scope_log_retention_days = 14
invoice_scope_mode               = "DISABLED"
invoice_scope_max_invoices       = 25



# =========================================================
# 290 - SQS2 AND DLQ
# =========================================================

sqs2_queue_suffix = "sqs2"
sqs2_dlq_suffix   = "sqs2-dlq"

sqs2_delay_seconds    = 0
sqs2_max_message_size = 262144

sqs2_retention_seconds     = 345600
sqs2_dlq_retention_seconds = 1209600

sqs2_receive_wait_time_seconds  = 20
sqs2_visibility_timeout_seconds = 180

sqs2_max_receive_count          = 5
sqs2_kms_data_key_reuse_seconds = 300


# =========================================================
# 300 - EVENTBRIDGE PIPE 2
# =========================================================

pipe2_name_suffix   = "pipe2"
pipe2_role_suffix   = "pipe2-role"
pipe2_desired_state = "RUNNING"
pipe2_batch_size    = 1


# =========================================================
# 310 - INVESTIGATION INVOCATION LAMBDA
# =========================================================

investigation_invocation_lambda_suffix      = "investigation-invocation"
investigation_invocation_timeout            = 180
investigation_invocation_memory_size        = 256
investigation_invocation_log_retention_days = 14

enable_investigation_invocation = true


# =========================================================
# 320 - INVESTIGATION AGENTCORE RUNTIME
# =========================================================

investigation_agent_runtime_name     = "psc_dev_investigation"
investigation_agent_role_suffix      = "investigation-role"
investigation_artifact_bucket_suffix = "inv"
investigation_agent_artifact_key     = "investigation/investigation_agent.zip"


# =========================================================
# 340 - AGENTCORE POLICY
# =========================================================

investigation_policy_engine_name = "psc_dev_investigation_policy"


# =========================================================
# 350 - AGENTCORE MEMORY
# =========================================================

investigation_memory_name           = "psc_dev_investigation_memory"
investigation_memory_retention_days = 30


# =========================================================
# 360 - AGENTCORE GATEWAY
# =========================================================

investigation_gateway_name        = "psc-dev-investigation-gateway"
investigation_gateway_policy_mode = "LOG_ONLY"
s4hana_gateway_target_name        = "s4hana-payment-status"


# =========================================================
# 390 - SAP S/4HANA TOOL
# =========================================================

s4hana_integration_mode        = "DISABLED"
s4hana_tool_timeout            = 30
s4hana_tool_memory_size        = 256
s4hana_tool_log_retention_days = 30


# =========================================================
# 400 - AMAZON ECR
# =========================================================

human_review_ecr_suffix                = "human-review-ui"
human_review_ecr_image_retention_count = 10


# =========================================================
# 410 - ECS CLUSTER
# =========================================================

human_review_ecs_cluster_suffix = "human-review-ecs"


# =========================================================
# 420 - ECS IAM ROLES AND CLOUDWATCH LOGS
# =========================================================

human_review_ecs_execution_role_suffix = "ecs-execution-role"
human_review_ecs_task_role_suffix      = "ecs-task-role"
human_review_ecs_log_retention_days    = 30

# 430 - Route 53 Domain
domain_name = "paymentqueryhub.shop"

# =========================================================
# 450 - ALB AND ECS SECURITY GROUPS
# =========================================================

human_review_alb_allowed_cidr = "0.0.0.0/0"
human_review_container_port   = 3000


# =========================================================
# 490 - REPLY SQS AND DEAD-LETTER QUEUE
# =========================================================

reply_queue_suffix = "reply-sqs"
reply_dlq_suffix   = "reply-dlq"

reply_visibility_timeout_seconds = 300
reply_message_retention_seconds  = 345600
reply_dlq_retention_seconds      = 1209600

reply_receive_wait_time_seconds = 20
reply_max_receive_count         = 5


# =========================================================
# 500 - HUMAN REVIEW BACKEND
# =========================================================

human_review_backend_timeout           = 30
human_review_backend_memory            = 256
human_review_outbox_timeout            = 60
human_review_outbox_memory             = 256
human_review_lambda_log_retention_days = 30
human_review_outbox_batch_size         = 10
human_review_outbox_max_attempts       = 20
human_review_outbox_schedule_enabled   = false


# 510 - Reply Processing Lambda

reply_graph_mailbox = "your-approved-mailbox@company.com"
reply_send_enabled  = false

cloudwatch_alarm_email = "niranjankp2026@gmail.com"


ec2_postgres_instance_type = "t3.small"


# =========================================================
# 395-397 - INTAKE AGENTS, RECONCILER, RESULTS REPORT
# =========================================================
ingestion_cutoff_utc               = ""
intake_agents_timeout              = 300
intake_agents_memory               = 512
intake_agents_log_retention_days   = 30

reconciler_timeout                 = 300
reconciler_memory                  = 512
reconciler_log_retention_days      = 30
reconciler_schedule_expression     = "rate(15 minutes)"
enable_reconciler_schedule = true
reconcile_since                    = ""

results_report_timeout             = 60
results_report_memory              = 256
results_report_log_retention_days  = 30
results_export_schedule_expression = "rate(15 minutes)"
enable_results_export_schedule = true
