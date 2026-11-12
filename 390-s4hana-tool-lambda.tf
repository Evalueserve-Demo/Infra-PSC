
# =========================================================
# 390 - SAP S/4HANA TOOL LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "s4hana_tool" {
  type        = "zip"
  source_dir  = "${path.module}/build/s4hana_tool"
  output_path = "${path.module}/build/s4hana_tool.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "s4hana_tool" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-s4hana-tool"

  retention_in_days = var.s4hana_tool_log_retention_days

  tags = {
    Name        = "${var.project_name}-${var.environment}-s4hana-tool"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. SAP S/4HANA TOOL LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "s4hana_tool" {
  function_name = "${var.project_name}-${var.environment}-s4hana-tool"

  role = aws_iam_role.s4hana_tool.arn

  runtime       = "python3.12"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]

  filename         = data.archive_file.s4hana_tool.output_path
  source_code_hash = data.archive_file.s4hana_tool.output_base64sha256

  timeout     = var.s4hana_tool_timeout
  memory_size = var.s4hana_tool_memory_size

  environment {
    variables = {
      S4HANA_INTEGRATION_MODE = var.s4hana_integration_mode
      ENVIRONMENT             = var.environment
    }
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-s4hana-tool"
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.s4hana_tool_baseline,
    aws_cloudwatch_log_group.s4hana_tool
  ]
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "s4hana_tool_lambda_arn" {
  description = "SAP S/4HANA Tool Lambda ARN"
  value       = aws_lambda_function.s4hana_tool.arn
}

output "s4hana_tool_lambda_name" {
  description = "SAP S/4HANA Tool Lambda name"
  value       = aws_lambda_function.s4hana_tool.function_name
}
