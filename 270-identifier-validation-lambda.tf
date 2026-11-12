
# =========================================================
# 270 - IDENTIFIER VALIDATION LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE PYTHON CODE
# ---------------------------------------------------------

data "archive_file" "identifier_validation" {
  type        = "zip"
  source_dir  = "${path.module}/build/identifier_validation"
  output_path = "${path.module}/build/identifier_validation.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "identifier_validation" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-${var.identifier_validation_lambda_suffix}"

  retention_in_days = var.identifier_validation_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.identifier_validation_lambda_suffix}-logs"
  }
}

# ---------------------------------------------------------
# 3. IDENTIFIER VALIDATION LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "identifier_validation" {
  function_name = "${var.project_name}-${var.environment}-${var.identifier_validation_lambda_suffix}"

  description = "Validates payment case, message, and invoice identifiers"

  role = aws_iam_role.identifier_validation.arn

  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.identifier_validation.output_path
  source_code_hash = data.archive_file.identifier_validation.output_base64sha256

  timeout     = var.identifier_validation_timeout
  memory_size = var.identifier_validation_memory_size

  environment {
    variables = {
      MAX_INVOICES          = tostring(var.identifier_validation_max_invoices)
      MAX_IDENTIFIER_LENGTH = tostring(var.identifier_validation_max_identifier_length)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.identifier_validation,
    aws_iam_role_policy_attachment.identifier_validation_baseline
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.identifier_validation_lambda_suffix}"
  }
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "identifier_validation_lambda_arn" {
  description = "Identifier Validation Lambda ARN"
  value       = aws_lambda_function.identifier_validation.arn
}

output "identifier_validation_lambda_name" {
  description = "Identifier Validation Lambda name"
  value       = aws_lambda_function.identifier_validation.function_name
}

