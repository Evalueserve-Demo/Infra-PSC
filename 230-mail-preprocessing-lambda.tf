
# =========================================================
# 230 - MAIL PREPROCESSING LAMBDA
#
# Project: Boeing Helpdesk Payment Status Check
#
# Pattern: 166 - Pre-SF1 Dispatcher Lambda
# =========================================================

locals {
  mail_preprocessing_name = "${var.project_name}-${var.environment}-${var.mail_preprocessing_name_suffix}"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------

data "archive_file" "mail_preprocessing" {
  type        = "zip"
  source_dir  = "${path.module}/build/mail_preprocessing"
  output_path = "${path.module}/build/mail_preprocessing.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "mail_preprocessing" {
  name              = "/aws/lambda/${local.mail_preprocessing_name}"
  retention_in_days = var.mail_preprocessing_log_retention_days

  tags = {
    Name = "${local.mail_preprocessing_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "mail_preprocessing" {
  name = "${local.mail_preprocessing_name}-role"

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
    Name = "${local.mail_preprocessing_name}-role"
  }
}

# ---------------------------------------------------------
# 4. AWS MANAGED LAMBDA BASELINE POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "mail_preprocessing_baseline" {
  role       = aws_iam_role.mail_preprocessing.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. BEDROCK GUARDRAIL IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "mail_preprocessing_guardrail" {
  name = "${local.mail_preprocessing_name}-guardrail-policy"
  role = aws_iam_role.mail_preprocessing.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ApplyClassificationGuardrail"
        Effect = "Allow"

        Action = [
          "bedrock:ApplyGuardrail"
        ]

        Resource = aws_bedrock_guardrail.classification.guardrail_arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. MAIL PREPROCESSING LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "mail_preprocessing" {
  function_name = local.mail_preprocessing_name

  description = "Preprocesses payment status emails and applies the classification Bedrock Guardrail"

  role    = aws_iam_role.mail_preprocessing.arn
  runtime = var.mail_preprocessing_runtime
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.mail_preprocessing.output_path
  source_code_hash = data.archive_file.mail_preprocessing.output_base64sha256

  timeout     = var.mail_preprocessing_timeout
  memory_size = var.mail_preprocessing_memory

  # -------------------------------------------------------
  # VPC CONFIGURATION - SAME PATTERN AS STAGE 166 / 180
  # -------------------------------------------------------

  vpc_config {
    subnet_ids = var.existing_subnet_ids

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  # -------------------------------------------------------
  # BEDROCK GUARDRAIL ENVIRONMENT VARIABLES
  # -------------------------------------------------------

  environment {
    variables = {
      GUARDRAIL_ID      = aws_bedrock_guardrail.classification.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail_version.classification.version
    }
  }

  # -------------------------------------------------------
  # DEPENDENCIES
  # -------------------------------------------------------

  depends_on = [
    aws_cloudwatch_log_group.mail_preprocessing,
    aws_iam_role_policy_attachment.mail_preprocessing_baseline,
    aws_iam_role_policy.mail_preprocessing_guardrail
  ]

  tags = {
    Name = local.mail_preprocessing_name
  }
}

# ---------------------------------------------------------
# 7. OUTPUTS
# ---------------------------------------------------------

output "mail_preprocessing_lambda_arn" {
  description = "ARN of the Mail Preprocessing Lambda"
  value       = aws_lambda_function.mail_preprocessing.arn
}

output "mail_preprocessing_role_arn" {
  description = "IAM role ARN of the Mail Preprocessing Lambda"
  value       = aws_iam_role.mail_preprocessing.arn
}

# =========================================================
# END OF 230 - MAIL PREPROCESSING LAMBDA
# =========================================================
