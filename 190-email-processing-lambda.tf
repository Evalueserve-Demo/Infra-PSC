
# =========================================================
# 190 - EMAIL PROCESSING LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "email_processing" {
  type        = "zip"
  source_dir  = "${path.module}/build/email_processing"
  output_path = "${path.module}/build/email_processing.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "email_processing" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-${var.email_processing_name_suffix}"

  retention_in_days = var.email_processing_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-email-processing-logs"
  }
}

# ---------------------------------------------------------
# 3. LAMBDA IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "email_processing_access" {
  name = "email-processing-access"
  role = aws_iam_role.email_processing.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "SendMessagesToSQS1"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.sqs1.arn
      },
      {
        Sid    = "GenerateSQSDataKeys"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.psc.arn
      },
      {
        # Lets the function log in to the database through RDS Proxy.
        Sid      = "DatabaseProxyIamAuth"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      }
    ]
  })
}

# ---------------------------------------------------------
# 4. EMAIL PROCESSING LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "email_processing" {
  function_name = "${var.project_name}-${var.environment}-${var.email_processing_name_suffix}"

  role    = aws_iam_role.email_processing.arn
  runtime = var.email_processing_runtime
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.email_processing.output_path
  source_code_hash = data.archive_file.email_processing.output_base64sha256

  timeout     = var.email_processing_timeout
  memory_size = var.email_processing_memory

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
      SQS1_QUEUE_URL     = aws_sqs_queue.sqs1.url
      ENABLE_SQS_PUBLISH = "false"

      # Names the code reads from its environment.
      AURORA_HOST = aws_db_proxy.psc.endpoint
      AURORA_DB   = var.email_ingestion_db_name
      AURORA_USER = var.email_ingestion_db_user
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.email_processing,
    aws_iam_role_policy_attachment.email_processing_baseline,
    aws_iam_role_policy.email_processing_access
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-email-processing"
  }
}

