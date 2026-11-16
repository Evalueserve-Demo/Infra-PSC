# =========================================================
# 391 - SPVR TOOL LAMBDA
#
# Investigation Agent -> AgentCore Gateway -> SPVR Tool
#
# The code is in lambda/spvr_tool/.
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "spvr_tool" {
  type        = "zip"
  source_dir  = "${path.module}/build/spvr_tool"
  output_path = "${path.module}/build/spvr_tool.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "spvr_tool" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-spvr-tool"

  retention_in_days = 30

  tags = {
    Name        = "${var.project_name}-${var.environment}-spvr-tool"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. SPVR TOOL IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "spvr_tool" {
  name = "${var.project_name}-${var.environment}-spvr-role"

  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "${var.project_name}-${var.environment}-spvr-tool-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 4. BASELINE IAM POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "spvr_tool_baseline" {
  role       = aws_iam_role.spvr_tool.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 5. SPVR TOOL LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "spvr_tool" {
  function_name = "${var.project_name}-${var.environment}-spvr-tool"

  role = aws_iam_role.spvr_tool.arn

  runtime       = "python3.12"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]

  filename         = data.archive_file.spvr_tool.output_path
  source_code_hash = data.archive_file.spvr_tool.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-spvr-tool"
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.spvr_tool_baseline,
    aws_cloudwatch_log_group.spvr_tool
  ]
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "spvr_tool_lambda_arn" {
  description = "SPVR Tool Lambda ARN"
  value       = aws_lambda_function.spvr_tool.arn
}

output "spvr_tool_lambda_name" {
  description = "SPVR Tool Lambda name"
  value       = aws_lambda_function.spvr_tool.function_name
}