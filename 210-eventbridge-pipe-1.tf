v
# =========================================================
# 210 - SQS1 TO SF2 DISPATCHER LAMBDA
# =========================================================
# Architecture:
# SQS1 -> Dispatcher Lambda -> SF2
#
# Replaces EventBridge Pipe 1 completely.
# =========================================================

# ---------------------------------------------------------
# 1. DISPATCHER LAMBDA IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "sf2_dispatcher" {
  name = "${var.project_name}-${var.environment}-sf2-dispatcher-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"

        Principal = {
          Service = "lambda.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-sf2-dispatcher-role"
  }
}

# ---------------------------------------------------------
# 2. DISPATCHER IAM POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy" "sf2_dispatcher" {
  name = "sf2-dispatcher-access"
  role = aws_iam_role.sf2_dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ConsumeSQS1"
        Effect = "Allow"

        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]

        Resource = aws_sqs_queue.sqs1.arn
      },
      {
        Sid    = "DecryptSQS1"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "StartSF2"
        Effect = "Allow"

        Action = [
          "states:StartExecution"
        ]

        Resource = aws_sfn_state_machine.sf2.arn
      },
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"

        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-${var.environment}-sf2-dispatcher:*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 3. DISPATCHER CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "sf2_dispatcher" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-sf2-dispatcher"
  retention_in_days = 14

  tags = {
    Name = "${var.project_name}-${var.environment}-sf2-dispatcher"
  }
}

# ---------------------------------------------------------
# 4. DISPATCHER LAMBDA SOURCE PACKAGE
# ---------------------------------------------------------

data "archive_file" "sf2_dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/sf2-dispatcher"
  output_path = "${path.module}/sf2-dispatcher.zip"
}

# ---------------------------------------------------------
# 5. DISPATCHER LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "sf2_dispatcher" {
  function_name = "${var.project_name}-${var.environment}-sf2-dispatcher"

  role    = aws_iam_role.sf2_dispatcher.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.sf2_dispatcher.output_path
  source_code_hash = data.archive_file.sf2_dispatcher.output_base64sha256

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      SF2_STATE_MACHINE_ARN = aws_sfn_state_machine.sf2.arn
    }
  }

  depends_on = [
    aws_iam_role_policy.sf2_dispatcher,
    aws_cloudwatch_log_group.sf2_dispatcher
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-sf2-dispatcher"
  }
}

# ---------------------------------------------------------
# 6. SQS1 EVENT SOURCE MAPPING
# ---------------------------------------------------------

resource "aws_lambda_event_source_mapping" "sf2_dispatcher" {
  event_source_arn = aws_sqs_queue.sqs1.arn
  function_name    = aws_lambda_function.sf2_dispatcher.arn

  batch_size = 1
  enabled    = true

  function_response_types = [
    "ReportBatchItemFailures"
  ]

  depends_on = [
    aws_iam_role_policy.sf2_dispatcher
  ]
}
