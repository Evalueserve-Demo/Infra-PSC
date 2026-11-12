# =========================================================
# 394 - DRAFT BUILDER TOOL LAMBDA
#
# Investigation Agent -> AgentCore Gateway -> Draft Builder
#
# Business logic will be supplied by developers.
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "draft_builder_tool" {
  type        = "zip"
  source_dir  = "${path.module}/build/draft_builder_tool"
  output_path = "${path.module}/build/draft_builder_tool.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "draft_builder_tool" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-draft-builder-tool"
  retention_in_days = 30

  tags = {
    Name        = "${var.project_name}-${var.environment}-draft-builder-tool"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. DRAFT BUILDER TOOL IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "draft_builder_tool" {
  name = "${var.project_name}-${var.environment}-draft-builder-role"

  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name        = "${var.project_name}-${var.environment}-draft-builder-tool-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 4. BASELINE IAM POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "draft_builder_tool_baseline" {
  role       = aws_iam_role.draft_builder_tool.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 5. DRAFT BUILDER TOOL LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "draft_builder_tool" {
  function_name = "${var.project_name}-${var.environment}-draft-builder-tool"

  role = aws_iam_role.draft_builder_tool.arn

  runtime       = "python3.12"
  handler       = "lambda_function.lambda_handler"
  architectures = ["x86_64"]

  filename         = data.archive_file.draft_builder_tool.output_path
  source_code_hash = data.archive_file.draft_builder_tool.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      ENVIRONMENT = var.environment
    }
  }

  tags = {
    Name        = "${var.project_name}-${var.environment}-draft-builder-tool"
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy_attachment.draft_builder_tool_baseline,
    aws_cloudwatch_log_group.draft_builder_tool
  ]
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "draft_builder_tool_lambda_arn" {
  description = "Draft Builder Tool Lambda ARN"
  value       = aws_lambda_function.draft_builder_tool.arn
}

output "draft_builder_tool_lambda_name" {
  description = "Draft Builder Tool Lambda name"
  value       = aws_lambda_function.draft_builder_tool.function_name
}