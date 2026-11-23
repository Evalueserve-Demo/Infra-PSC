
# =========================================================
# 395 - INTAKE AGENTS LAMBDA
# Project: Boeing Helpdesk Payment Status Check
# Pattern: Stage 166
# =========================================================

locals {
  intake_agents_name = "${var.project_name}-${var.environment}-intake-agents"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------

data "archive_file" "intake_agents" {
  type        = "zip"
  source_dir  = "${path.module}/build/intake_agents"
  output_path = "${path.module}/build/intake_agents.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "intake_agents" {
  name              = "/aws/lambda/${local.intake_agents_name}"
  retention_in_days = var.intake_agents_log_retention_days

  tags = {
    Name = "${local.intake_agents_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE - BOEING REQUIREMENTS
# ---------------------------------------------------------

resource "aws_iam_role" "intake_agents" {
  name = "${local.intake_agents_name}-role"

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
    Name = "${local.intake_agents_name}-role"
  }
}

# ---------------------------------------------------------
# 4. AWS MANAGED LAMBDA BASELINE POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "intake_agents_baseline" {
  role       = aws_iam_role.intake_agents.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. APPLICATION IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "intake_agents_access" {
  name = "${local.intake_agents_name}-access"
  role = aws_iam_role.intake_agents.id

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
        Sid      = "DecryptApplicationData"
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
        Sid    = "InvokeIntakeAgentCore"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]
        Resource = [
          var.existing_agentcore_runtime_arn,
          "${var.existing_agentcore_runtime_arn}/runtime-endpoint/*"
        ]
      },
      {
        Sid      = "ScreenMailWithGuardrail"
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = aws_bedrock_guardrail.classification.guardrail_arn
      },
      {
        Sid      = "ReadAttachments"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.attachments.arn}/*"
      },
      {
        Sid      = "PublishMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. INTAKE AGENTS LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "intake_agents" {
  function_name = local.intake_agents_name
  description   = "Performs guardrail, classification and invoice extraction for incoming payment emails"

  role    = aws_iam_role.intake_agents.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.intake_agents.output_path
  source_code_hash = data.archive_file.intake_agents.output_base64sha256

  timeout     = var.intake_agents_timeout
  memory_size = var.intake_agents_memory

  # Boeing existing VPC subnet pattern
  vpc_config {
    subnet_ids = var.existing_subnet_ids

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = {
      AURORA_HOST = aws_db_proxy.psc.endpoint
      AURORA_DB   = var.email_ingestion_db_name
      AURORA_USER = var.email_ingestion_db_user

      GRAPH_API_BASE_URL = var.graph_api_base_url
      GRAPH_SECRET_ID    = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_MAILBOX      = var.mailbox_user

      ATTACHMENT_BUCKET = aws_s3_bucket.attachments.bucket
      MAIL_SOURCE       = var.mail_source

      CLASSIFICATION_RUNTIME_ARN = var.existing_agentcore_runtime_arn
      EXTRACTION_RUNTIME_ARN     = var.existing_agentcore_runtime_arn

      GUARDRAIL_ID      = aws_bedrock_guardrail.classification.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail_version.classification.version
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.intake_agents,
    aws_iam_role_policy_attachment.intake_agents_baseline,
    aws_iam_role_policy.intake_agents_access
  ]

  tags = {
    Name = local.intake_agents_name
  }
}

# ---------------------------------------------------------
# 7. OUTPUTS
# ---------------------------------------------------------

output "intake_agents_lambda_arn" {
  description = "Intake Agents Lambda ARN"
  value       = aws_lambda_function.intake_agents.arn
}

output "intake_agents_lambda_name" {
  description = "Intake Agents Lambda name"
  value       = aws_lambda_function.intake_agents.function_name
}

# =========================================================
# END OF 395
# =========================================================
