# =========================================================
# 300 - EVENTBRIDGE PIPE 2
# SOURCE: SQS2
# TARGET: INVESTIGATION INVOCATION LAMBDA
# =========================================================

# ---------------------------------------------------------
# 1. EVENTBRIDGE PIPE 2 IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "pipe2" {
  name = "${var.project_name}-${var.environment}-${var.pipe2_role_suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "AllowEventBridgePipes"
        Effect = "Allow"

        Principal = {
          Service = "pipes.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.pipe2_role_suffix}"
  }
}

# ---------------------------------------------------------
# 2. EVENTBRIDGE PIPE 2 IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "pipe2" {
  name = "pipe2-sqs-lambda-access"
  role = aws_iam_role.pipe2.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ReadAndAcknowledgeSQS2"
        Effect = "Allow"

        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]

        Resource = aws_sqs_queue.sqs2.arn
      },
      {
        Sid    = "DecryptSQS2Messages"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "InvokeInvestigationLambda"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = aws_lambda_function.investigation_invocation.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 3. EVENTBRIDGE PIPE 2
# ---------------------------------------------------------

resource "aws_pipes_pipe" "pipe2" {
  name = "${var.project_name}-${var.environment}-${var.pipe2_name_suffix}"

  description = "Routes SQS2 messages to Investigation Invocation Lambda"

  role_arn = aws_iam_role.pipe2.arn

  source = aws_sqs_queue.sqs2.arn

  target = aws_lambda_function.investigation_invocation.arn

  desired_state = var.pipe2_desired_state

  log_configuration {
    level                  = "ERROR"
    include_execution_data = []

    cloudwatch_logs_log_destination {
      log_group_arn = aws_cloudwatch_log_group.pipe2_540.arn
    }
  }

  source_parameters {
    sqs_queue_parameters {
      batch_size = var.pipe2_batch_size
    }
  }

  target_parameters {
    lambda_function_parameters {
      invocation_type = "REQUEST_RESPONSE"
    }
  }

  depends_on = [
    aws_iam_role_policy.pipe2,
    aws_iam_role_policy.pipe2_logging_540
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.pipe2_name_suffix}"
  }
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "pipe2_arn" {
  description = "EventBridge Pipe 2 ARN"
  value       = aws_pipes_pipe.pipe2.arn
}

output "pipe2_name" {
  description = "EventBridge Pipe 2 name"
  value       = aws_pipes_pipe.pipe2.name
}
