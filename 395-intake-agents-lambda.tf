
# =========================================================
# 395 - INTAKE AGENTS LAMBDA
# =========================================================
# Does the intake work for each mail: guardrail, classification,
# the reason for sending it to a person, the Outlook label and
# the invoice extraction. Step Function 2 calls it.
# The code is lambda/intake-agents/lambda_function.py and the files beside it, plus the
# modules it borrows from other Lambdas' folders; the build copies them into build/.
# Edit those, not build/.

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
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
  name = "/aws/lambda/${var.project_name}-${var.environment}-intake-agents"

  retention_in_days = var.intake_agents_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-intake-agents-logs"
  }
}

# ---------------------------------------------------------
# 3. LAMBDA IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "intake_agents" {
  name               = "${var.project_name}-${var.environment}-intake-agents-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-intake-agents-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "intake_agents_baseline" {
  role       = aws_iam_role.intake_agents.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 4. LAMBDA IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "intake_agents_access" {
  name = "intake-agents-access"
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
        Sid    = "DecryptApplicationData"
        Effect = "Allow"
        Action = [
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
      },
      {
        Sid    = "InvokeIntakeAgentCore"
        Effect = "Allow"
        Action = ["bedrock-agentcore:InvokeAgentRuntime"]
        Resource = [
          aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn,
          "${aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn}/runtime-endpoint/*",
          aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn,
          "${aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn}/runtime-endpoint/*"
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
        # PutMetricData cannot be limited to one resource, so this one uses "*".
        Sid      = "PublishMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 5. INTAKE AGENTS LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "intake_agents" {
  function_name = "${var.project_name}-${var.environment}-intake-agents"

  role    = aws_iam_role.intake_agents.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.intake_agents.output_path
  source_code_hash = data.archive_file.intake_agents.output_base64sha256

  timeout     = var.intake_agents_timeout
  memory_size = var.intake_agents_memory

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
      AURORA_HOST = aws_db_proxy.psc.endpoint
      AURORA_DB   = var.email_ingestion_db_name
      AURORA_USER = var.email_ingestion_db_user

      GRAPH_API_BASE_URL = var.graph_api_base_url
      GRAPH_SECRET_ID    = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_MAILBOX      = var.mailbox_user

      ATTACHMENT_BUCKET = aws_s3_bucket.attachments.bucket

      # "s3" means there is no Outlook mailbox, so mails are not labelled.
      MAIL_SOURCE = var.mail_source

      CLASSIFICATION_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn
      EXTRACTION_RUNTIME_ARN     = aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn

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
    Name = "${var.project_name}-${var.environment}-intake-agents"
  }
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "intake_agents_lambda_arn" {
  description = "Intake Agents Lambda ARN"
  value       = aws_lambda_function.intake_agents.arn
}

output "intake_agents_lambda_name" {
  description = "Intake Agents Lambda name"
  value       = aws_lambda_function.intake_agents.function_name
}
