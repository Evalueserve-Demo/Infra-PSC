# =========================================================
# 580 - BACKEND API LAMBDA
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

locals {
  backend_api_name = "${var.project_name}-${var.environment}-backend-api"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------

data "archive_file" "backend_api" {
  type        = "zip"
  source_dir  = "${path.module}/build/backend_api"
  output_path = "${path.module}/build/backend_api.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "backend_api" {
  name              = "/aws/lambda/${local.backend_api_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.backend_api_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "backend_api" {
  name = "${local.backend_api_name}-role"

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
    Name = "${local.backend_api_name}-role"
  }
}

# ---------------------------------------------------------
# 4. LAMBDA BASELINE POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "backend_api_baseline" {
  role       = aws_iam_role.backend_api.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. APPLICATION IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "backend_api_access" {
  name = "${local.backend_api_name}-access"
  role = aws_iam_role.backend_api.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ListSQS2Output"
        Effect = "Allow"

        Action = [
          "s3:ListBucket"
        ]

        Resource = aws_s3_bucket.attachments.arn

        Condition = {
          StringLike = {
            "s3:prefix" = [
              "${local.sqs2_export_prefix}/",
              "${local.sqs2_export_prefix}/*"
            ]
          }
        }
      },
      {
        Sid    = "ReadSQS2Output"
        Effect = "Allow"

        Action = [
          "s3:GetObject"
        ]

        Resource = "${aws_s3_bucket.attachments.arn}/${local.sqs2_export_prefix}/*"
      },
      {
        Sid    = "DecryptS3Output"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. BACKEND API LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "backend_api" {
  function_name = local.backend_api_name
  description   = "Read PSC processing output from S3 for frontend UI"

  role    = aws_iam_role.backend_api.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.backend_api.output_path
  source_code_hash = data.archive_file.backend_api.output_base64sha256

  timeout     = 30
  memory_size = 512

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
    aws_cloudwatch_log_group.backend_api,
    aws_iam_role_policy_attachment.backend_api_baseline,
    aws_iam_role_policy.backend_api_access
  ]

  tags = {
    Name = local.backend_api_name
  }
}

# ---------------------------------------------------------
# 7. OUTPUTS
# ---------------------------------------------------------

output "backend_api_lambda_arn" {
  description = "Backend API Lambda ARN"
  value       = aws_lambda_function.backend_api.arn
}

output "backend_api_lambda_name" {
  description = "Backend API Lambda name"
  value       = aws_lambda_function.backend_api.function_name
}