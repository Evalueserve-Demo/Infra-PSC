
# =========================================================
# 160 - EMAIL INGESTION LAMBDA
# =========================================================
# Stage 1 uses the DBA-approved ps schema.
# Table-level operations are implemented in the Lambda code;
# PostgreSQL role grants must be initialized separately.
#
# SQS publishing occurs only when ENABLE_WORKFLOW=true.
# No separate dispatcher Lambda is created.

# ---------------------------------------------------------
# 1. PACKAGE EMAIL INGESTION LAMBDA
# ---------------------------------------------------------
# Build directory must contain lambda_function.py and all
# runtime dependencies (including psycopg) before Terraform plan.
data "archive_file" "email_ingestion" {
  type        = "zip"
  source_dir  = "${path.module}/build/email_ingestion"
  output_path = "${path.module}/build/email_ingestion.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "email_ingestion" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-email-ingestion"
  retention_in_days = var.email_ingestion_log_retention_days

  tags = {
    Name = "${var.project_name}-email-ingestion-logs-${var.environment}"
  }
}

# ---------------------------------------------------------
# 3. EMAIL INGESTION IAM ACCESS
# ---------------------------------------------------------
resource "aws_iam_role_policy" "email_ingestion_access" {
  name = "email-ingestion-access"
  role = aws_iam_role.email_ingestion.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "ReadGraphSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.microsoft_graph.arn
      },
      {
        Sid      = "DecryptApplicationSecrets"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.psc.arn
      },
      {
        # IAM authentication from Lambda to RDS Proxy.
        Sid      = "DatabaseProxyIamAuth"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      },
      {
        Sid      = "StartWorkflowOne"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.sf1.arn
      },
      {
        Sid      = "DescribeWorkflowOneExecutions"
        Effect   = "Allow"
        Action   = ["states:DescribeExecution"]
        Resource = "${replace(aws_sfn_state_machine.sf1.arn, ":stateMachine:", ":execution:")}:psc-ing-*"
      },
      {
        # NEW: Publish admitted workflow messages to Pre-SF1 SQS.
        Sid    = "PublishPreSF1Messages"
        Effect = "Allow"

        Action = [
          "sqs:SendMessage",
          "sqs:SendMessageBatch",
          "sqs:GetQueueAttributes"
        ]

        Resource = aws_sqs_queue.pre_sf1.arn
      },
      {
        # NEW: Required for sending messages to the
        # customer-managed KMS encrypted SQS queue.
        Sid    = "EncryptPreSF1Messages"
        Effect = "Allow"

        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 4. EMAIL INGESTION LAMBDA
# ---------------------------------------------------------
resource "aws_lambda_function" "email_ingestion" {
  function_name = "${var.project_name}-${var.environment}-email-ingestion"

  role    = aws_iam_role.email_ingestion.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.email_ingestion.output_path
  source_code_hash = data.archive_file.email_ingestion.output_base64sha256

  timeout     = var.email_ingestion_timeout
  memory_size = var.email_ingestion_memory

  vpc_config {
    subnet_ids = [
      aws_subnet.private_a.id,
      aws_subnet.private_b.id
    ]

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = {
      # Microsoft Graph authentication
      GRAPH_SECRET_ARN   = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_SECRET_ID    = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_MAILBOX      = var.mailbox_user
      GRAPH_API_BASE_URL = "https://graph.microsoft.com/v1.0"

      # Existing database configuration
      DB_PROXY_HOST = aws_db_proxy.psc.endpoint
      DB_NAME       = var.email_ingestion_db_name
      DB_USER       = var.email_ingestion_db_user
      DB_PORT       = "5432"

      # Environment variables expected by developer Lambda code
      # IAM authentication continues through RDS Proxy.
      AURORA_HOST = aws_db_proxy.psc.endpoint
      AURORA_DB   = var.email_ingestion_db_name
      AURORA_USER = var.email_ingestion_db_user

      # Queue used by the Outflow Dispatcher.
      SQS_QUEUE_URL = aws_sqs_queue.pre_sf1.url

      # Keep workflow dispatch disabled until integration testing.
      ENABLE_INGESTION     = "true"
      ENABLE_WORKFLOW      = "true"
      INGESTION_CUTOFF_UTC = "2026-11-12T22:30:00Z"

      START_INTERVAL_SECONDS = tostring(var.start_interval_seconds)
      SF1_ARN                = aws_sfn_state_machine.sf1.arn
      PRE_SF1_QUEUE_URL      = aws_sqs_queue.pre_sf1.url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.email_ingestion,
    aws_iam_role_policy_attachment.email_ingestion_baseline,
    aws_iam_role_policy.email_ingestion_access
  ]

  tags = {
    Name = "${var.project_name}-email-ingestion-${var.environment}"
  }
}
