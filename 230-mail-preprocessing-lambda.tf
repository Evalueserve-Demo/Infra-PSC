
# =========================================================
# 230 - MAIL PREPROCESSING LAMBDA
# =========================================================

data "archive_file" "mail_preprocessing" {
  type        = "zip"
  source_dir  = "${path.module}/build/mail_preprocessing"
  output_path = "${path.module}/build/mail_preprocessing.zip"
}

resource "aws_cloudwatch_log_group" "mail_preprocessing" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-${var.mail_preprocessing_name_suffix}"
  retention_in_days = var.mail_preprocessing_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-mail-preprocessing-logs"
  }
}

resource "aws_lambda_function" "mail_preprocessing" {
  function_name = "${var.project_name}-${var.environment}-${var.mail_preprocessing_name_suffix}"

  role    = aws_iam_role.mail_preprocessing.arn
  runtime = var.mail_preprocessing_runtime
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.mail_preprocessing.output_path
  source_code_hash = data.archive_file.mail_preprocessing.output_base64sha256

  timeout     = var.mail_preprocessing_timeout
  memory_size = var.mail_preprocessing_memory

  # Existing Bedrock Guardrail from 240-bedrock-guardrail.tf.
  environment {
    variables = {
      GUARDRAIL_ID      = aws_bedrock_guardrail.classification.guardrail_id
      GUARDRAIL_VERSION = aws_bedrock_guardrail_version.classification.version
    }
  }

  vpc_config {
    subnet_ids = [
      aws_subnet.private_a.id,
      aws_subnet.private_b.id
    ]

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  depends_on = [
    aws_cloudwatch_log_group.mail_preprocessing,
    aws_iam_role_policy_attachment.mail_preprocessing_baseline,
    aws_iam_role_policy.mail_preprocessing_guardrail
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-mail-preprocessing"
  }
}

# Allow only the existing #230 Lambda role to apply the existing guardrail.
resource "aws_iam_role_policy" "mail_preprocessing_guardrail" {
  name = "${var.project_name}-${var.environment}-mail-prep-guardrail"
  role = aws_iam_role.mail_preprocessing.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ApplyClassificationGuardrail"
        Effect   = "Allow"
        Action   = ["bedrock:ApplyGuardrail"]
        Resource = aws_bedrock_guardrail.classification.guardrail_arn
      }
    ]
  })
}
