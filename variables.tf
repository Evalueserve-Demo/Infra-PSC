
# =========================================================
# VARIABLES
# Project: Boeing Helpdesk Payment Status Check
# =========================================================


# ---------------------------------------------------------
# 1. AWS ACCOUNT AND REGION
# ---------------------------------------------------------

variable "aws_account_id" {
  description = "AWS account ID authorized for deployment."
  type        = string
}

variable "aws_region" {
  description = "AWS Region for deployment."
  type        = string
}

variable "environment" {
  description = "Deployment environment such as dev, test or prod."
  type        = string
}

variable "project_name" {
  description = "Project name used for resource naming and tagging."
  type        = string
}


# ---------------------------------------------------------
# 2. VPC
# ---------------------------------------------------------

variable "vpc_cidr" {
  description = "CIDR block assigned to the VPC."
  type        = string
}


# ---------------------------------------------------------
# 3. AVAILABILITY ZONES
# ---------------------------------------------------------

variable "availability_zone_a" {
  description = "First Availability Zone for the deployment."
  type        = string
}

variable "availability_zone_b" {
  description = "Second Availability Zone for the deployment."
  type        = string
}


# ---------------------------------------------------------
# 4. SUBNET CIDR BLOCKS
# ---------------------------------------------------------

variable "public_subnet_a_cidr" {
  description = "CIDR block for Public Subnet A."
  type        = string
}

variable "public_subnet_b_cidr" {
  description = "CIDR block for Public Subnet B."
  type        = string
}

variable "private_subnet_a_cidr" {
  description = "CIDR block for Private Application Subnet A."
  type        = string
}

variable "private_subnet_b_cidr" {
  description = "CIDR block for Private Application Subnet B."
  type        = string
}

variable "database_subnet_a_cidr" {
  description = "CIDR block for Database Subnet A."
  type        = string
}

variable "database_subnet_b_cidr" {
  description = "CIDR block for Database Subnet B."
  type        = string
}


# ---------------------------------------------------------
# 5. MICROSOFT 365 EMAIL INGESTION
# ---------------------------------------------------------

variable "mailbox_user" {
  description = "Microsoft 365 supplier mailbox UPN."
  type        = string
  default     = ""
}

variable "enable_scheduler" {
  description = "Enable email ingestion schedule."
  type        = bool
  default     = false
}


# ---------------------------------------------------------
# 6. AURORA POSTGRESQL
# ---------------------------------------------------------

variable "database_name" {
  description = "Aurora PostgreSQL database name."
  type        = string
  default     = "paymentstatuscheck"
}

variable "database_username" {
  description = "Aurora PostgreSQL master username."
  type        = string
  default     = "pscadmin"
}


# ---------------------------------------------------------
# 7. AMAZON BEDROCK
# ---------------------------------------------------------

variable "bedrock_model_id" {
  description = "Approved Bedrock model identifier."
  type        = string
  default     = ""
}


# ---------------------------------------------------------
# 8. HUMAN REVIEW AUTHENTICATION
# ---------------------------------------------------------

variable "human_review_jwt_issuer" {
  description = "JWT issuer for human review."
  type        = string
  default     = ""
}

variable "human_review_jwt_audience" {
  description = "JWT audience for human review."
  type        = string
  default     = ""
}


# ---------------------------------------------------------
# 9. EXTERNAL ENTERPRISE APIs
# ---------------------------------------------------------

variable "sap_ariba_api_url" {
  description = "Approved SAP Ariba API base URL."
  type        = string
  default     = ""
}

variable "sap_s4_api_url" {
  description = "Approved SAP S/4HANA API base URL."
  type        = string
  default     = ""
}

variable "pr_itar_api_url" {
  description = "Approved PR/ITAR API base URL."
  type        = string
  default     = ""
}

variable "external_api_token_secret_arn" {
  description = "Secrets Manager ARN for external API credentials."
  type        = string
  default     = ""
}


# ---------------------------------------------------------
# 10. AGENTCORE RUNTIMES
# ---------------------------------------------------------

variable "agentcore_runtime_arn_classification" {
  description = "Classification AgentCore Runtime ARN."
  type        = string
  default     = ""
}

variable "agentcore_runtime_arn_investigation" {
  description = "Investigation AgentCore Runtime ARN."
  type        = string
  default     = ""
}

# ---------------------------------------------------------
# 11. AWS KMS
# ---------------------------------------------------------

variable "kms_enable_key_rotation" {
  description = "Enable automatic rotation for the customer-managed KMS key."
  type        = bool
}

variable "kms_deletion_window_days" {
  description = "KMS key deletion waiting period in days."
  type        = number

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "KMS deletion window must be between 7 and 30 days."
  }
}

# ---------------------------------------------------------
# 12. AWS SECRETS MANAGER
# ---------------------------------------------------------

variable "secrets_recovery_window_days" {
  description = "Recovery window for deleted Secrets Manager secrets."
  type        = number

  validation {
    condition     = var.secrets_recovery_window_days >= 7 && var.secrets_recovery_window_days <= 30
    error_message = "Secrets Manager recovery window must be between 7 and 30 days."
  }
}

# ---------------------------------------------------------
# 13. S3 ATTACHMENTS
# ---------------------------------------------------------

variable "attachments_versioning_status" {
  description = "Versioning status for the attachments bucket."
  type        = string

  validation {
    condition     = contains(["Enabled", "Suspended"], var.attachments_versioning_status)
    error_message = "Versioning status must be Enabled or Suspended."
  }
}

variable "attachments_abort_multipart_days" {
  description = "Days before incomplete multipart uploads are removed."
  type        = number

  validation {
    condition     = var.attachments_abort_multipart_days >= 1
    error_message = "Multipart cleanup days must be at least 1."
  }
}

variable "attachments_malware_object_tagging_status" {
  description = "Enable or disable GuardDuty malware scan result object tagging."
  type        = string

  validation {
    condition     = contains(["ENABLED", "DISABLED"], var.attachments_malware_object_tagging_status)
    error_message = "Tagging status must be ENABLED or DISABLED."
  }
}

# ---------------------------------------------------------
# 14. AURORA POSTGRESQL SERVERLESS V2
# ---------------------------------------------------------

variable "aurora_engine_version" {
  description = "Aurora PostgreSQL engine version."
  type        = string
}

variable "aurora_parameter_group_family" {
  description = "Aurora PostgreSQL parameter group family."
  type        = string
}

variable "aurora_serverless_min_acu" {
  description = "Minimum Aurora Serverless v2 ACUs."
  type        = number
}

variable "aurora_serverless_max_acu" {
  description = "Maximum Aurora Serverless v2 ACUs."
  type        = number

  validation {
    condition     = var.aurora_serverless_max_acu >= 1
    error_message = "Maximum ACU must be at least 1."
  }
}

variable "aurora_backup_retention_days" {
  description = "Automated backup retention in days."
  type        = number
}

variable "aurora_backup_window" {
  description = "Preferred backup window in UTC."
  type        = string
}

variable "aurora_maintenance_window" {
  description = "Preferred maintenance window in UTC."
  type        = string
}

variable "aurora_deletion_protection" {
  description = "Enable Aurora cluster deletion protection."
  type        = bool
}

variable "aurora_skip_final_snapshot" {
  description = "Whether to skip the final snapshot."
  type        = bool
}

variable "aurora_final_snapshot_identifier" {
  description = "Final snapshot identifier when deleting the Aurora cluster."
  type        = string
}

variable "aurora_auto_minor_version_upgrade" {
  description = "Enable automatic minor engine version upgrades."
  type        = bool
}

variable "aurora_performance_insights_enabled" {
  description = "Enable Performance Insights."
  type        = bool
}

# ---------------------------------------------------------
# 15. RDS PROXY
# ---------------------------------------------------------

variable "rds_proxy_idle_client_timeout" {
  description = "Seconds before an idle client connection closes."
  type        = number
}

variable "rds_proxy_debug_logging" {
  description = "Enable RDS Proxy debug logging."
  type        = bool
}

variable "rds_proxy_max_connections_percent" {
  description = "Maximum database connections as a percentage of the DB limit."
  type        = number
}

variable "rds_proxy_max_idle_connections_percent" {
  description = "Maximum idle database connections percentage."
  type        = number
}

variable "rds_proxy_connection_borrow_timeout" {
  description = "Seconds to wait for an available database connection."
  type        = number
}


# =========================================================
# 150 - EVENTBRIDGE SCHEDULER VARIABLES
# =========================================================

variable "email_scheduler_expression" {
  description = "How frequently the Email Ingestion Lambda is triggered"
  type        = string
  default     = "rate(5 minutes)"
}

variable "email_scheduler_timezone" {
  description = "Timezone used by EventBridge Scheduler"
  type        = string
  default     = "UTC"
}


# =========================================================
# 160 - EMAIL INGESTION LAMBDA VARIABLES
# =========================================================

variable "email_ingestion_timeout" {
  type    = number
  default = 120
}

variable "email_ingestion_memory" {
  type    = number
  default = 256
}

variable "email_ingestion_log_retention_days" {
  type    = number
  default = 30
}

variable "email_ingestion_db_name" {
  type    = string
  default = "postgres"
}

variable "email_ingestion_db_user" {
  type    = string
  default = "psc_ingestion"
}



# =========================================================
# 170 - STEP FUNCTIONS 1 VARIABLES
# =========================================================

variable "sf1_name_suffix" {
  type    = string
  default = "sf1"
}

variable "sf1_type" {
  type    = string
  default = "STANDARD"

  validation {
    condition     = contains(["STANDARD", "EXPRESS"], var.sf1_type)
    error_message = "Use STANDARD or EXPRESS."
  }
}

variable "sf1_attachment_lambda_suffix" {
  type    = string
  default = "attachment-processing"
}

variable "sf1_retry_interval_seconds" {
  type    = number
  default = 2
}

variable "sf1_max_retry_attempts" {
  type    = number
  default = 3
}

variable "sf1_retry_backoff_rate" {
  type    = number
  default = 2.0
}


# =========================================================
# 180 - ATTACHMENT PROCESSING LAMBDA VARIABLES
# =========================================================

variable "attachment_processing_name_suffix" {
  type    = string
  default = "attachment-processing"
}

variable "attachment_processing_runtime" {
  type    = string
  default = "python3.12"
}

variable "attachment_processing_timeout" {
  type    = number
  default = 120
}

variable "attachment_processing_memory" {
  type    = number
  default = 256
}

variable "attachment_processing_log_retention_days" {
  type    = number
  default = 30
}

variable "attachment_processing_s3_prefix" {
  type    = string
  default = "email-attachments"
}

variable "attachment_processing_max_bytes" {
  description = "Maximum attachment size accepted by this Lambda"
  type        = number
  default     = 10485760
}


# =========================================================
# 190 - EMAIL PROCESSING LAMBDA VARIABLES
# =========================================================

variable "email_processing_name_suffix" {
  type    = string
  default = "email-processing"
}

variable "email_processing_runtime" {
  type    = string
  default = "python3.12"
}

variable "email_processing_timeout" {
  type    = number
  default = 60
}

variable "email_processing_memory" {
  type    = number
  default = 256
}

variable "email_processing_log_retention_days" {
  type    = number
  default = 30
}


# =========================================================
# 200 - SQS1 VARIABLES
# =========================================================

variable "sqs1_name_suffix" {
  type    = string
  default = "sqs1"
}

variable "sqs1_dlq_name_suffix" {
  type    = string
  default = "sqs1-dlq"
}

variable "sqs1_visibility_timeout_seconds" {
  type    = number
  default = 180
}

variable "sqs1_retention_seconds" {
  type    = number
  default = 345600
}

variable "sqs1_dlq_retention_seconds" {
  type    = number
  default = 1209600
}

variable "sqs1_receive_wait_time_seconds" {
  type    = number
  default = 20
}

variable "sqs1_max_receive_count" {
  type    = number
  default = 5
}

variable "sqs1_kms_data_key_reuse_seconds" {
  type    = number
  default = 300
}


# =========================================================
# 210 - EVENTBRIDGE PIPE 1 VARIABLES
# =========================================================

variable "pipe1_name_suffix" {
  type    = string
  default = "pipe1"
}

variable "pipe1_role_name_suffix" {
  type    = string
  default = "pipe1-execution-role"
}

variable "pipe1_desired_state" {
  type    = string
  default = "STOPPED"

  validation {
    condition = contains(
      ["RUNNING", "STOPPED"],
      var.pipe1_desired_state
    )
    error_message = "Use RUNNING or STOPPED."
  }
}

variable "pipe1_batch_size" {
  type    = number
  default = 1

  validation {
    condition     = var.pipe1_batch_size == 1
    error_message = "Pipe1 must use batch size 1 for the current SF2 message contract."
  }
}

variable "pipe1_batching_window_seconds" {
  type    = number
  default = 0
}


# =========================================================
# 220 - STEP FUNCTIONS 2 VARIABLES
# =========================================================

variable "sf2_name_suffix" {
  type    = string
  default = "sf2"
}

variable "sf2_type" {
  type    = string
  default = "STANDARD"

  validation {
    condition     = contains(["STANDARD", "EXPRESS"], var.sf2_type)
    error_message = "Use STANDARD or EXPRESS."
  }
}

variable "sf2_retry_interval_seconds" {
  type    = number
  default = 2
}

variable "sf2_max_retry_attempts" {
  type    = number
  default = 3
}

variable "sf2_retry_backoff_rate" {
  type    = number
  default = 2.0
}


# =========================================================
# 230 - MAIL PREPROCESSING VARIABLES
# =========================================================

variable "mail_preprocessing_name_suffix" {
  type    = string
  default = "mail-preprocessing"
}

variable "mail_preprocessing_runtime" {
  type    = string
  default = "python3.12"
}

variable "mail_preprocessing_timeout" {
  type    = number
  default = 60
}

variable "mail_preprocessing_memory" {
  type    = number
  default = 256
}

variable "mail_preprocessing_log_retention_days" {
  type    = number
  default = 30
}


# =========================================================
# 240 - BEDROCK GUARDRAIL VARIABLES
# =========================================================

variable "classification_guardrail_name_suffix" {
  type    = string
  default = "classification-guardrail"
}

variable "classification_guardrail_blocked_input_message" {
  type    = string
  default = "The request could not be processed because it did not meet the content safety policy."
}

variable "classification_guardrail_blocked_output_message" {
  type    = string
  default = "The generated response could not be returned because it did not meet the content safety policy."
}

variable "classification_guardrail_content_filters" {
  description = "Content filters applied to classification inputs and outputs"

  type = list(object({
    type            = string
    input_strength  = string
    output_strength = string
  }))

  default = [
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
}

variable "classification_guardrail_pii_entities" {
  description = "PII types blocked by the classification guardrail"

  type = list(object({
    type   = string
    action = string
  }))

  default = [
    {
      type   = "US_SOCIAL_SECURITY_NUMBER"
      action = "BLOCK"
    },
    {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }
  ]
}

# =========================================================
# 250 - BEDROCK MODEL VARIABLES
# =========================================================

variable "classification_model_id" {
  description = "Bedrock model ID or inference profile ID"
  type        = string
}

variable "classification_model_temperature" {
  description = "Sampling temperature for classification"
  type        = number
  default     = 0

  validation {
    condition     = var.classification_model_temperature >= 0 && var.classification_model_temperature <= 1
    error_message = "Temperature must be between 0 and 1."
  }
}

variable "classification_model_max_output_tokens" {
  description = "Maximum generated tokens for classification"
  type        = number
  default     = 1024

  validation {
    condition     = var.classification_model_max_output_tokens > 0
    error_message = "Maximum output tokens must be greater than zero."
  }
}

variable "classification_model_top_p" {
  description = "Nucleus sampling parameter"
  type        = number
  default     = 1

  validation {
    condition     = var.classification_model_top_p > 0 && var.classification_model_top_p <= 1
    error_message = "top_p must be greater than 0 and at most 1."
  }
}


# =========================================================
# 260 - CLASSIFICATION AGENTCORE VARIABLES
# =========================================================

variable "classification_agent_runtime_name" {
  description = "AgentCore classification runtime name"
  type        = string
  default     = "psc_dev_classification"
}

variable "classification_agent_role_suffix" {
  description = "IAM execution role suffix"
  type        = string
  default     = "classification-agentcore-role"
}

variable "classification_artifact_bucket_suffix" {
  description = "S3 artifact bucket suffix"
  type        = string
  default     = "agent-artifacts"
}

variable "classification_agent_artifact_key" {
  description = "S3 object key for the classification ZIP"
  type        = string
  default     = "classification/classification_agent.zip"
}


# =========================================================
# 270 - IDENTIFIER VALIDATION VARIABLES
# =========================================================

variable "identifier_validation_lambda_suffix" {
  description = "Identifier Validation Lambda name suffix"
  type        = string
  default     = "identifier-validation"
}

variable "identifier_validation_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "identifier_validation_memory_size" {
  description = "Lambda memory in MB"
  type        = number
  default     = 256
}

variable "identifier_validation_log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}

variable "identifier_validation_max_invoices" {
  description = "Maximum invoice identifiers per request"
  type        = number
  default     = 25

  validation {
    condition = (
      var.identifier_validation_max_invoices >= 1 &&
      var.identifier_validation_max_invoices <= 100
    )
    error_message = "Maximum invoices must be between 1 and 100."
  }
}

variable "identifier_validation_max_identifier_length" {
  description = "Maximum length of an identifier"
  type        = number
  default     = 100

  validation {
    condition = (
      var.identifier_validation_max_identifier_length >= 20 &&
      var.identifier_validation_max_identifier_length <= 500
    )
    error_message = "Identifier length must be between 20 and 500."
  }
}


# =========================================================
# 280 - INVOICE SCOPE VARIABLES
# =========================================================

variable "invoice_scope_lambda_suffix" {
  description = "Invoice Scope Lambda name suffix"
  type        = string
  default     = "invoice-scope"
}

variable "invoice_scope_timeout" {
  description = "Invoice Scope Lambda timeout"
  type        = number
  default     = 60
}

variable "invoice_scope_memory_size" {
  description = "Invoice Scope Lambda memory in MB"
  type        = number
  default     = 256
}

variable "invoice_scope_log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}

variable "invoice_scope_mode" {
  description = "DISABLED, DRY_RUN or LIVE"
  type        = string
  default     = "DISABLED"

  validation {
    condition = contains(
      ["DISABLED", "DRY_RUN", "LIVE"],
      var.invoice_scope_mode
    )
    error_message = "Use DISABLED, DRY_RUN or LIVE."
  }
}

variable "invoice_scope_max_invoices" {
  description = "Maximum invoices in one request"
  type        = number
  default     = 25

  validation {
    condition = (
      var.invoice_scope_max_invoices >= 1 &&
      var.invoice_scope_max_invoices <= 100
    )
    error_message = "Maximum invoices must be between 1 and 100."
  }
}


# =========================================================
# 290 - SQS2 VARIABLES
# =========================================================

variable "sqs2_queue_suffix" {
  description = "SQS2 main queue name suffix"
  type        = string
  default     = "sqs2"
}

variable "sqs2_dlq_suffix" {
  description = "SQS2 dead-letter queue name suffix"
  type        = string
  default     = "sqs2-dlq"
}

variable "sqs2_delay_seconds" {
  description = "SQS2 delivery delay in seconds"
  type        = number
  default     = 0
}

variable "sqs2_max_message_size" {
  description = "Maximum SQS2 message size in bytes"
  type        = number
  default     = 262144
}

variable "sqs2_retention_seconds" {
  description = "SQS2 message retention in seconds"
  type        = number
  default     = 345600
}

variable "sqs2_dlq_retention_seconds" {
  description = "SQS2 DLQ retention in seconds"
  type        = number
  default     = 1209600
}

variable "sqs2_receive_wait_time_seconds" {
  description = "SQS2 long-polling wait time"
  type        = number
  default     = 20
}

variable "sqs2_visibility_timeout_seconds" {
  description = "SQS2 visibility timeout in seconds"
  type        = number
  default     = 180
}

variable "sqs2_max_receive_count" {
  description = "Maximum receives before moving to DLQ"
  type        = number
  default     = 5

  validation {
    condition     = var.sqs2_max_receive_count >= 1 && var.sqs2_max_receive_count <= 1000
    error_message = "SQS2 max receive count must be between 1 and 1000."
  }
}

variable "sqs2_kms_data_key_reuse_seconds" {
  description = "KMS data key reuse period in seconds"
  type        = number
  default     = 300
}



# =========================================================
# 300 - EVENTBRIDGE PIPE 2 VARIABLES
# =========================================================

variable "pipe2_name_suffix" {
  description = "EventBridge Pipe 2 name suffix"
  type        = string
  default     = "pipe2"
}

variable "pipe2_role_suffix" {
  description = "EventBridge Pipe 2 IAM role suffix"
  type        = string
  default     = "pipe2-role"
}

variable "pipe2_desired_state" {
  description = "Initial state of EventBridge Pipe 2"
  type        = string
  default     = "STOPPED"

  validation {
    condition = contains(
      ["STOPPED", "RUNNING"],
      var.pipe2_desired_state
    )
    error_message = "Pipe 2 state must be STOPPED or RUNNING."
  }
}

variable "pipe2_batch_size" {
  description = "Number of SQS2 messages processed per Pipe batch"
  type        = number
  default     = 1

  validation {
    condition = (
      var.pipe2_batch_size >= 1 &&
      var.pipe2_batch_size <= 10
    )
    error_message = "Pipe 2 batch size must be between 1 and 10."
  }
}

# =========================================================
# 310 - INVESTIGATION INVOCATION VARIABLES
# =========================================================

variable "investigation_invocation_lambda_suffix" {
  description = "Investigation Invocation Lambda suffix"
  type        = string
  default     = "investigation-invocation"
}

variable "investigation_invocation_timeout" {
  description = "Investigation Invocation Lambda timeout in seconds"
  type        = number
  default     = 180
}

variable "investigation_invocation_memory_size" {
  description = "Investigation Invocation Lambda memory in MB"
  type        = number
  default     = 256
}

variable "investigation_invocation_log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 14
}

variable "enable_investigation_invocation" {
  description = "Enable actual AgentCore Runtime invocation"
  type        = bool
  default     = false
}


# =========================================================
# 320 - INVESTIGATION AGENTCORE VARIABLES
# =========================================================

variable "investigation_agent_runtime_name" {
  description = "Investigation AgentCore Runtime name"
  type        = string
  default     = "psc_dev_investigation"
}

variable "investigation_agent_role_suffix" {
  description = "Investigation AgentCore IAM role suffix"
  type        = string
  default     = "investigation-role"
}

variable "investigation_artifact_bucket_suffix" {
  description = "Investigation deployment artifact bucket suffix"
  type        = string
  default     = "investigation"
}

variable "investigation_agent_artifact_key" {
  description = "S3 object key for investigation agent ZIP"
  type        = string
  default     = "investigation/investigation_agent.zip"
}



# =========================================================
# 340 - AGENTCORE POLICY VARIABLES
# =========================================================

variable "investigation_policy_engine_name" {
  description = "Name of the investigation AgentCore Policy Engine"
  type        = string
  default     = "psc_dev_investigation_policy"

  validation {
    condition = (
      length(var.investigation_policy_engine_name) <= 48 &&
      can(regex("^[A-Za-z][A-Za-z0-9_]*$", var.investigation_policy_engine_name))
    )
    error_message = "Policy Engine name must begin with a letter, contain only letters, numbers and underscores, and be at most 48 characters."
  }
}


# =========================================================
# 350 - AGENTCORE MEMORY VARIABLES
# =========================================================

variable "investigation_memory_name" {
  description = "Name of the Investigation AgentCore Memory"
  type        = string
  default     = "psc_dev_investigation_memory"

  validation {
    condition = (
      length(var.investigation_memory_name) <= 48 &&
      can(regex("^[A-Za-z][A-Za-z0-9_]*$", var.investigation_memory_name))
    )
    error_message = "Memory name must begin with a letter, use only letters, numbers and underscores, and be at most 48 characters."
  }
}

variable "investigation_memory_retention_days" {
  description = "Number of days AgentCore Memory events are retained"
  type        = number
  default     = 30

  validation {
    condition = (
      var.investigation_memory_retention_days >= 7 &&
      var.investigation_memory_retention_days <= 365
    )
    error_message = "AgentCore Memory retention must be between 7 and 365 days."
  }
}


# =========================================================
# 360 - AGENTCORE GATEWAY VARIABLES
# =========================================================

variable "investigation_gateway_name" {
  description = "Investigation AgentCore Gateway name"
  type        = string
  default     = "psc-dev-investigation-gateway"
}

variable "investigation_gateway_policy_mode" {
  description = "Policy Engine evaluation mode"
  type        = string
  default     = "LOG_ONLY"

  validation {
    condition = contains(
      ["LOG_ONLY", "ENFORCE"],
      var.investigation_gateway_policy_mode
    )
    error_message = "Policy mode must be LOG_ONLY or ENFORCE."
  }
}

variable "s4hana_gateway_target_name" {
  description = "SAP S4HANA Gateway target name"
  type        = string
  default     = "s4hana-payment-status"
}


# =========================================================
# 390 - SAP S/4HANA TOOL VARIABLES
# =========================================================

variable "s4hana_integration_mode" {
  description = "SAP S/4HANA integration mode"
  type        = string
  default     = "DISABLED"

  validation {
    condition = contains(
      ["DISABLED", "LIVE"],
      var.s4hana_integration_mode
    )
    error_message = "Use DISABLED or LIVE."
  }
}

variable "s4hana_tool_timeout" {
  description = "SAP S/4HANA Lambda timeout in seconds"
  type        = number
  default     = 30
}

variable "s4hana_tool_memory_size" {
  description = "SAP S/4HANA Lambda memory in MB"
  type        = number
  default     = 256
}

variable "s4hana_tool_log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}



# =========================================================
# 400 - AMAZON ECR VARIABLES
# =========================================================

variable "human_review_ecr_suffix" {
  description = "Repository name suffix for Human Review UI"
  type        = string
  default     = "human-review-ui"
}

variable "human_review_ecr_image_retention_count" {
  description = "Number of version-tagged ECR images to retain"
  type        = number
  default     = 10

  validation {
    condition = (
      var.human_review_ecr_image_retention_count >= 1 &&
      var.human_review_ecr_image_retention_count <= 1000
    )

    error_message = "ECR image retention count must be between 1 and 1000."
  }
}


# =========================================================
# 410 - ECS CLUSTER VARIABLES
# =========================================================

variable "human_review_ecs_cluster_suffix" {
  description = "Name suffix for Human Review ECS Cluster"
  type        = string
  default     = "human-review-ecs"
}


# =========================================================
# 420 - ECS IAM ROLES AND LOGGING VARIABLES
# =========================================================

variable "human_review_ecs_execution_role_suffix" {
  description = "IAM role suffix for ECS task execution"
  type        = string
  default     = "human-review-ecs-execution-role"
}

variable "human_review_ecs_task_role_suffix" {
  description = "IAM role suffix for the Next.js ECS application task"
  type        = string
  default     = "human-review-ecs-task-role"
}

variable "human_review_ecs_log_retention_days" {
  description = "CloudWatch log retention period for Human Review ECS"
  type        = number
  default     = 30

  validation {
    condition = contains(
      [1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653],
      var.human_review_ecs_log_retention_days
    )

    error_message = "Specify a CloudWatch Logs supported retention period."
  }
}


# ==========================================================
# 430 - ROUTE 53 DOMAIN
# ==========================================================

variable "domain_name" {
  description = "Registered domain name for PSC"
  type        = string
}

# =========================================================
# 450 - ALB AND ECS SECURITY GROUP VARIABLES
# =========================================================

variable "human_review_alb_allowed_cidr" {
  description = "IPv4 CIDR permitted to access Human Review ALB HTTPS"
  type        = string
  default     = "0.0.0.0/0"

  validation {
    condition     = can(cidrhost(var.human_review_alb_allowed_cidr, 0))
    error_message = "Provide a valid IPv4 CIDR."
  }
}

variable "human_review_container_port" {
  description = "Port on which the Next.js container listens"
  type        = number
  default     = 3000

  validation {
    condition = (
      var.human_review_container_port >= 1 &&
      var.human_review_container_port <= 65535
    )

    error_message = "Container port must be between 1 and 65535."
  }
}


# =========================================================
# 490 - REPLY SQS VARIABLES
# =========================================================

variable "reply_queue_suffix" {
  description = "Name suffix for the approved-reply SQS queue"
  type        = string
  default     = "reply-sqs"
}

variable "reply_dlq_suffix" {
  description = "Name suffix for the Reply dead-letter queue"
  type        = string
  default     = "reply-dlq"
}

variable "reply_visibility_timeout_seconds" {
  description = "Visibility timeout for Reply SQS messages"
  type        = number
  default     = 300

  validation {
    condition = (
      var.reply_visibility_timeout_seconds >= 0 &&
      var.reply_visibility_timeout_seconds <= 43200
    )
    error_message = "SQS visibility timeout must be between 0 and 43200 seconds."
  }
}

variable "reply_message_retention_seconds" {
  description = "Message retention period for Reply SQS"
  type        = number
  default     = 345600
}

variable "reply_dlq_retention_seconds" {
  description = "Message retention period for Reply DLQ"
  type        = number
  default     = 1209600
}

variable "reply_receive_wait_time_seconds" {
  description = "Long-polling wait time for Reply SQS"
  type        = number
  default     = 20
}

variable "reply_max_receive_count" {
  description = "Maximum receives before moving a message to Reply DLQ"
  type        = number
  default     = 5
}


# =========================================================
# 500 - HUMAN REVIEW BACKEND VARIABLES
# =========================================================

variable "human_review_backend_timeout" {
  type    = number
  default = 30
}

variable "human_review_backend_memory" {
  type    = number
  default = 256
}

variable "human_review_outbox_timeout" {
  type    = number
  default = 60
}

variable "human_review_outbox_memory" {
  type    = number
  default = 256
}

variable "human_review_lambda_log_retention_days" {
  type    = number
  default = 30
}

variable "human_review_outbox_batch_size" {
  type    = number
  default = 10
}

variable "human_review_outbox_max_attempts" {
  type    = number
  default = 20
}

variable "human_review_outbox_schedule_enabled" {
  type    = bool
  default = false
}


# =========================================================
# 510 - REPLY PROCESSING VARIABLES
# =========================================================

variable "reply_graph_secret_arn" {
  description = "Existing Microsoft Graph credentials secret ARN"
  type        = string
  default     = null
}

variable "reply_graph_mailbox" {
  description = "Microsoft 365 mailbox used for supplier replies"
  type        = string
}

variable "reply_send_enabled" {
  description = "Allow actual Microsoft Graph email sending"
  type        = bool
  default     = false
}

variable "cloudwatch_alarm_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}


variable "ec2_postgres_instance_type" {
  description = "EC2 instance type for the SSM PostgreSQL administration client"
  type        = string
}
