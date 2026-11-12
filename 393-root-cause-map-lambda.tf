# =========================================================
# 393 - ROOT CAUSE MAP TOOL LAMBDA
#
# Investigation Agent -> AgentCore Gateway -> Root Cause Map
#
# Business logic will be supplied by developers.
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "root_cause_map_tool" {
  type        = "zip"
  source_dir  = "${path.module}/build/root_cause_map_tool"
  output_path = "${path.module}/build/root_cause_map_tool.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "root_cause_map_tool" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-root-cause-map-tool"
  retention_in_days = 30

  tags = {
    Name        = "${var.project_name}-${var.environment}-root-cause-map-tool"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. ROOT CAUSE MAP TOOL IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "root_cause_map_tool" {
  name = "${var.project_name}-${var.environment}-root-cause-map-role"

  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "${var.project_name}-${var.environment}-root-cause-map-tool-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 4. BASELINE IAM POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "root_cause_map_tool_baseline" {
  role       = aws_iam_role.root_cause_map_tool.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 5. ROOT CAUSE MAP TOOL LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "root_cause_map_tool" {
  function_name = "${var.project_name}-${var.environment}-root-cause-map-tool"

  role = aws_iam_role.root_cause_map_tool.arn

  runtime       = "python3.12"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]

  filename         = data.archive_file.root_cause_map_tool.output_path
  source_code_hash = data.archive_file.root_cause_map_tool.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-root-cause-map-tool"
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.root_cause_map_tool_baseline,
    aws_cloudwatch_log_group.root_cause_map_tool
  ]
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "root_cause_map_tool_lambda_arn" {
  description = "Root Cause Map Tool Lambda ARN"
  value       = aws_lambda_function.root_cause_map_tool.arn
}

output "root_cause_map_tool_lambda_name" {
  description = "Root Cause Map Tool Lambda name"
  value       = aws_lambda_function.root_cause_map_tool.function_name
}