# =========================================================
# 392 - PAYMENT RULES TOOL LAMBDA
#
# Investigation Agent -> AgentCore Gateway -> Payment Rules
#
# The code is in lambda/payment_rules_tool/.
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "payment_rules_tool" {
  type        = "zip"
  source_dir  = "${path.module}/build/payment_rules_tool"
  output_path = "${path.module}/build/payment_rules_tool.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "payment_rules_tool" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-payment-rules-tool"
  retention_in_days = 30

  tags = {
    Name        = "${var.project_name}-${var.environment}-payment-rules-tool"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. PAYMENT RULES TOOL IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "payment_rules_tool" {
  name = "${var.project_name}-${var.environment}-payment-rules-role"

  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "${var.project_name}-${var.environment}-payment-rules-tool-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 4. BASELINE IAM POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "payment_rules_tool_baseline" {
  role       = aws_iam_role.payment_rules_tool.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 5. PAYMENT RULES TOOL LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "payment_rules_tool" {
  function_name = "${var.project_name}-${var.environment}-payment-rules-tool"

  role = aws_iam_role.payment_rules_tool.arn

  runtime       = "python3.12"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]

  filename         = data.archive_file.payment_rules_tool.output_path
  source_code_hash = data.archive_file.payment_rules_tool.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-payment-rules-tool"
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.payment_rules_tool_baseline,
    aws_cloudwatch_log_group.payment_rules_tool
  ]
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "payment_rules_tool_lambda_arn" {
  description = "Payment Rules Tool Lambda ARN"
  value       = aws_lambda_function.payment_rules_tool.arn
}

output "payment_rules_tool_lambda_name" {
  description = "Payment Rules Tool Lambda name"
  value       = aws_lambda_function.payment_rules_tool.function_name
}