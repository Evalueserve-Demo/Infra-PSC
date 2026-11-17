# =========================================================
# 180 - ATTACHMENT PROCESSING LAMBDA
# =========================================================
# Receives ingestion_id from SF1, reads ps.ingestion_message and
# persists attachment metadata to ps.ingestion_attachment.
# Attachment uploads do not imply malware scan clearance.

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------
# Build directory must contain lambda_function.py and installed pg8000.
data "archive_file" "attachment_processing" {
  type        = "zip"
  source_dir  = "${path.module}/build/attachment_processing"
  output_path = "${path.module}/build/attachment_processing.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------
resource "aws_cloudwatch_log_group" "attachment_processing" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-${var.attachment_processing_name_suffix}"
  retention_in_days = var.attachment_processing_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-attachment-processing-logs"
  }
}

# ---------------------------------------------------------
# 3. LEAST-PRIVILEGE APPLICATION PERMISSIONS
# ---------------------------------------------------------
resource "aws_iam_role_policy" "attachment_processing_access" {
  name = "attachment-processing-access"
  role = aws_iam_role.attachment_processing.id

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
        Sid      = "DecryptGraphSecret"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = aws_kms_key.psc.arn
      },
      {
        Sid      = "DatabaseProxyIamAuth"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      },
      {
        Sid      = "WriteAttachments"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.attachments.arn}/*"
      },
      {
        Sid    = "EncryptS3Objects"
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
# 4. ATTACHMENT PROCESSING LAMBDA
# ---------------------------------------------------------
resource "aws_lambda_function" "attachment_processing" {
  function_name = "${var.project_name}-${var.environment}-${var.attachment_processing_name_suffix}"

  role    = aws_iam_role.attachment_processing.arn
  runtime = var.attachment_processing_runtime
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.attachment_processing.output_path
  source_code_hash = data.archive_file.attachment_processing.output_base64sha256

  timeout     = var.attachment_processing_timeout
  memory_size = var.attachment_processing_memory

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
      GRAPH_SECRET_ARN    = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_MAILBOX       = var.mailbox_user
      ATTACHMENT_BUCKET   = aws_s3_bucket.attachments.bucket
      ATTACHMENT_PREFIX   = var.attachment_processing_s3_prefix
      GRAPH_API_BASE_URL  = var.graph_api_base_url
      GRAPH_SECRET_ID     = aws_secretsmanager_secret.microsoft_graph.arn
      ATTACHMENT_MAX_SIZE = tostring(var.attachment_processing_max_bytes)
      KMS_KEY_ARN         = aws_kms_key.psc.arn

      DB_PROXY_HOST = aws_db_proxy.psc.endpoint
      DB_NAME       = var.email_ingestion_db_name
      DB_USER       = var.email_ingestion_db_user
      DB_PORT       = "5432"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.attachment_processing,
    aws_iam_role_policy_attachment.attachment_processing_baseline,
    aws_iam_role_policy.attachment_processing_access
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-attachment-processing"
  }
}
