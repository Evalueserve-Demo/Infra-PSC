
# =========================================================
# 280 - INVOICE SCOPE DETERMINATION LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE PYTHON SOURCE
# ---------------------------------------------------------

data "archive_file" "invoice_scope" {
  type        = "zip"
  source_dir  = "${path.module}/build/invoice_scope"
  output_path = "${path.module}/build/invoice_scope.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "invoice_scope" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-${var.invoice_scope_lambda_suffix}"

  retention_in_days = var.invoice_scope_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.invoice_scope_lambda_suffix}-logs"
  }
}

# ---------------------------------------------------------
# 3. INVOICE SCOPE LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "invoice_scope" {
  function_name = "${var.project_name}-${var.environment}-${var.invoice_scope_lambda_suffix}"

  description = "Invoice scope determination for validated payment requests"

  role = aws_iam_role.invoice_scope.arn

  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.invoice_scope.output_path
  source_code_hash = data.archive_file.invoice_scope.output_base64sha256

  timeout     = var.invoice_scope_timeout
  memory_size = var.invoice_scope_memory_size

  environment {
    variables = {
      INVOICE_SCOPE_MODE = var.invoice_scope_mode
      MAX_INVOICES       = tostring(var.invoice_scope_max_invoices)
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.invoice_scope,
    aws_iam_role_policy_attachment.invoice_scope_baseline
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.invoice_scope_lambda_suffix}"
  }
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "invoice_scope_lambda_arn" {
  description = "Invoice Scope Lambda ARN"
  value       = aws_lambda_function.invoice_scope.arn
}

output "invoice_scope_lambda_name" {
  description = "Invoice Scope Lambda name"
  value       = aws_lambda_function.invoice_scope.function_name
}

