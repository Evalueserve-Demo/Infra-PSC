
# =========================================================
# 570 - SQS2 TO S3 EXPORT LAMBDA
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

locals {
  sqs2_export_name   = "${var.project_name}-${var.environment}-sqs2-s3-export"
  sqs2_export_prefix = "sqs2-output"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------

data "archive_file" "sqs2_export" {
  type        = "zip"
  source_dir  = "${path.module}/build/sqs2_to_s3_export"
  output_path = "${path.module}/build/sqs2_to_s3_export.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "sqs2_export" {
  name              = "/aws/lambda/${local.sqs2_export_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.sqs2_export_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE - BOEING REQUIREMENTS
# ---------------------------------------------------------

resource "aws_iam_role" "sqs2_export" {
  name = "${local.sqs2_export_name}-role"

  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"

        Principal = {
          Service = "lambda.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${local.sqs2_export_name}-role"
  }
}

# ---------------------------------------------------------
# 4. AWS MANAGED LAMBDA BASELINE POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "sqs2_export_baseline" {
  role       = aws_iam_role.sqs2_export.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. APPLICATION IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "sqs2_export_access" {
  name = "${local.sqs2_export_name}-access"
  role = aws_iam_role.sqs2_export.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ConsumeSQS2Messages"
        Effect = "Allow"

        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]

        Resource = aws_sqs_queue.sqs2.arn
      },
      {
        Sid    = "WriteSQS2OutputToS3"
        Effect = "Allow"

        Action = [
          "s3:PutObject"
        ]

        Resource = "${aws_s3_bucket.attachments.arn}/${local.sqs2_export_prefix}/*"
      },
      {
        Sid    = "EncryptDecryptApplicationData"
        Effect = "Allow"

        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]

        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. SQS2 TO S3 EXPORT LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "sqs2_export" {
  function_name = local.sqs2_export_name
  description   = "Exports SQS2 payment processing output to S3 as JSON"

  role    = aws_iam_role.sqs2_export.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.sqs2_export.output_path
  source_code_hash = data.archive_file.sqs2_export.output_base64sha256

  timeout     = 30
  memory_size = 256

  # Boeing existing VPC subnet pattern

  vpc_config {
    subnet_ids = var.existing_subnet_ids

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = {
      OUTPUT_BUCKET = aws_s3_bucket.attachments.bucket
      OUTPUT_PREFIX = local.sqs2_export_prefix
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.sqs2_export,
    aws_iam_role_policy_attachment.sqs2_export_baseline,
    aws_iam_role_policy.sqs2_export_access
  ]

  tags = {
    Name = local.sqs2_export_name
  }
}

# ---------------------------------------------------------
# 7. SQS2 EVENT SOURCE MAPPING
# ---------------------------------------------------------

resource "aws_lambda_event_source_mapping" "sqs2_export" {
  event_source_arn = aws_sqs_queue.sqs2.arn
  function_name    = aws_lambda_function.sqs2_export.arn

  enabled    = var.enable_sqs2_s3_export
  batch_size = 5

  maximum_batching_window_in_seconds = 0

  function_response_types = [
    "ReportBatchItemFailures"
  ]

  depends_on = [
    aws_iam_role_policy.sqs2_export_access
  ]
}

# ---------------------------------------------------------
# 8. OUTPUTS
# ---------------------------------------------------------

output "sqs2_export_lambda_arn" {
  description = "SQS2 to S3 Export Lambda ARN"
  value       = aws_lambda_function.sqs2_export.arn
}

output "sqs2_export_lambda_name" {
  description = "SQS2 to S3 Export Lambda name"
  value       = aws_lambda_function.sqs2_export.function_name
}

output "sqs2_export_s3_path" {
  description = "S3 location for SQS2 JSON output"
  value       = "s3://${aws_s3_bucket.attachments.bucket}/${local.sqs2_export_prefix}/"
}

# =========================================================
# END OF 570
# =========================================================
