
# =========================================================
# 310 - INVESTIGATION INVOCATION LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE PYTHON SOURCE
# ---------------------------------------------------------

data "archive_file" "investigation_invocation" {
  type        = "zip"
  source_dir  = "${path.module}/build/investigation_invocation"
  output_path = "${path.module}/build/investigation_invocation.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "investigation_invocation" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-${var.investigation_invocation_lambda_suffix}"

  retention_in_days = var.investigation_invocation_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.investigation_invocation_lambda_suffix}-logs"
  }
}

# ---------------------------------------------------------
# 3. AGENTCORE INVOCATION IAM POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy" "investigation_invocation_access" {
  name = "investigation-agentcore-invocation"
  role = aws_iam_role.investigation_invocation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeInvestigationAgentCore"
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]
        Resource = [
          aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn,
          "${aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn}/runtime-endpoint/*"
        ]
      },
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
# 4. INVESTIGATION INVOCATION LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "investigation_invocation" {
  function_name = "${var.project_name}-${var.environment}-${var.investigation_invocation_lambda_suffix}"

  description = "Invokes Investigation AgentCore Runtime for payment investigation"

  role = aws_iam_role.investigation_invocation.arn

  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.investigation_invocation.output_path
  source_code_hash = data.archive_file.investigation_invocation.output_base64sha256

  timeout     = var.investigation_invocation_timeout
  memory_size = var.investigation_invocation_memory_size

  # It reads and writes the case in the database, so it has to run inside the VPC.
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
      INVESTIGATION_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn

      ENABLE_INVESTIGATION_INVOCATION = tostring(var.enable_investigation_invocation)

      # Names the code reads from its environment.
      AURORA_HOST        = aws_db_proxy.psc.endpoint
      AURORA_DB          = var.email_ingestion_db_name
      AURORA_USER        = var.email_ingestion_db_user
      GRAPH_API_BASE_URL = var.graph_api_base_url
      GRAPH_SECRET_ID    = aws_secretsmanager_secret.microsoft_graph.arn
      GRAPH_MAILBOX      = var.mailbox_user
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.investigation_invocation,
    aws_iam_role_policy_attachment.investigation_invocation_baseline,
    aws_iam_role_policy.investigation_invocation_access
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.investigation_invocation_lambda_suffix}"
  }
}

# ---------------------------------------------------------
# 5. OUTPUTS
# ---------------------------------------------------------

output "investigation_invocation_lambda_arn" {
  description = "Investigation Invocation Lambda ARN"
  value       = aws_lambda_function.investigation_invocation.arn
}

output "investigation_invocation_lambda_name" {
  description = "Investigation Invocation Lambda name"
  value       = aws_lambda_function.investigation_invocation.function_name
}

