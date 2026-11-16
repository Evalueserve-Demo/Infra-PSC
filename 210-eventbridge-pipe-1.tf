# =========================================================
# 210 - EVENTBRIDGE PIPE 1
# SQS1 -> STEP FUNCTIONS 2
# =========================================================

# ---------------------------------------------------------
# 1. EVENTBRIDGE PIPE EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "pipe1" {
  name = "${var.project_name}-${var.environment}-${var.pipe1_role_name_suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "AllowEventBridgePipesAssumeRole"
        Effect = "Allow"

        Principal = {
          Service = "pipes.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.pipe1_role_name_suffix}"
  }
}

# ---------------------------------------------------------
# 2. EVENTBRIDGE PIPE IAM POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy" "pipe1" {
  name = "pipe1-sqs1-to-sf2-access"
  role = aws_iam_role.pipe1.id

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
        Sid    = "DecryptSQS1Messages"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "StartStepFunctions2"
        Effect = "Allow"

        Action = [
          "states:StartExecution"
        ]

        Resource = aws_sfn_state_machine.sf2.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 3. EVENTBRIDGE PIPE 1
# ---------------------------------------------------------

resource "aws_pipes_pipe" "pipe1" {
  name          = "${var.project_name}-${var.environment}-${var.pipe1_name_suffix}"
  role_arn      = aws_iam_role.pipe1.arn
  source        = aws_sqs_queue.sqs1.arn
  target        = aws_sfn_state_machine.sf2.arn
  desired_state = var.pipe1_desired_state

  log_configuration {
    level                  = "ERROR"
    include_execution_data = []

    cloudwatch_logs_log_destination {
      log_group_arn = aws_cloudwatch_log_group.pipe1_540.arn
    }
  }

  source_parameters {
    sqs_queue_parameters {
      batch_size                         = var.pipe1_batch_size
      maximum_batching_window_in_seconds = var.pipe1_batching_window_seconds
    }
  }

  target_parameters {
    # Build the JSON object the state machine expects. Passing "<$.body>" on its own
    # arrives as a map, and Step Functions rejects that.
    input_template = "{\"case_id\":\"<$.body.case_id>\",\"message_id\":\"<$.body.message_id>\",\"conversation_id\":\"<$.body.conversation_id>\",\"sender\":\"<$.body.sender>\",\"recipient\":\"<$.body.recipient>\",\"subject\":\"<$.body.subject>\",\"body\":\"<$.body.body>\",\"has_attachment\":<$.body.has_attachment>,\"attachment_id\":<$.body.attachment_id>,\"attachment_s3_reference\":<$.body.attachment_s3_reference>,\"attachments\":<$.body.attachments>}"

    step_function_state_machine_parameters {
      invocation_type = "FIRE_AND_FORGET"
    }
  }

  depends_on = [
    aws_iam_role_policy.pipe1,
    aws_iam_role_policy.pipe1_logging_540
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.pipe1_name_suffix}"
  }
}
