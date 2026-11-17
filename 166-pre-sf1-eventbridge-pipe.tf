# =========================================================
# 166 - PRE-SF1 EVENTBRIDGE PIPE
#
# Flow:
# Email Ingestion Lambda (Outflow Dispatcher)
# -> Pre-SF1 SQS -> EventBridge Pipe -> SF1
#
# This pipe consumes admitted messages from the new SQS
# queue and starts Step Functions 1 executions.
# =========================================================

# ---------------------------------------------------------
# 1. EVENTBRIDGE PIPE EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "pre_sf1_pipe" {
  name = "${var.project_name}-${var.environment}-pre-sf1-pipe-role"

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
    Name = "${var.project_name}-${var.environment}-pre-sf1-pipe-role"
  }
}

# ---------------------------------------------------------
# 2. EVENTBRIDGE PIPE IAM POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy" "pre_sf1_pipe" {
  name = "pre-sf1-sqs-to-sf1-access"
  role = aws_iam_role.pre_sf1_pipe.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ConsumePreSF1Queue"
        Effect = "Allow"

        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes"
        ]

        Resource = aws_sqs_queue.pre_sf1.arn
      },
      {
        Sid    = "DecryptPreSF1Queue"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "StartStepFunctions1"
        Effect = "Allow"

        Action = [
          "states:StartExecution"
        ]

        Resource = aws_sfn_state_machine.sf1.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 3. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "pre_sf1_pipe" {
  name              = "/aws/vendedlogs/pipes/${var.project_name}-${var.environment}-pre-sf1"
  retention_in_days = 30

  tags = {
    Name = "${var.project_name}-${var.environment}-pre-sf1-pipe"
  }
}

# ---------------------------------------------------------
# 4. CLOUDWATCH LOGGING PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "pre_sf1_pipe_logging" {
  name = "pre-sf1-pipe-cloudwatch-logging"
  role = aws_iam_role.pre_sf1_pipe.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "WritePipeLogs"
        Effect = "Allow"

        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = "${aws_cloudwatch_log_group.pre_sf1_pipe.arn}:*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 5. EVENTBRIDGE PIPE
# ---------------------------------------------------------

resource "aws_pipes_pipe" "pre_sf1" {
  name = "${var.project_name}-${var.environment}-pre-sf1-pipe"

  role_arn = aws_iam_role.pre_sf1_pipe.arn

  source = aws_sqs_queue.pre_sf1.arn
  target = aws_sfn_state_machine.sf1.arn

  # Keep the pipe stopped until the dispatcher, IAM,
  # database slot tracking and recovery are validated.
  desired_state = "RUNNING"

  log_configuration {
    level                  = "ERROR"
    include_execution_data = []

    cloudwatch_logs_log_destination {
      log_group_arn = aws_cloudwatch_log_group.pre_sf1_pipe.arn
    }
  }

  source_parameters {
    sqs_queue_parameters {
      # One SQS message starts one SF1 execution.
      batch_size = 1

      maximum_batching_window_in_seconds = 0
    }
  }

  target_parameters {
    # Pass the JSON message body to SF1.
    # Build the JSON object the state machine expects. Passing "<$.body>" on its own
    # arrives as a map, and Step Functions rejects that.
    input_template = "{\"message_id\":\"<$.body.message_id>\",\"conversation_id\":\"<$.body.conversation_id>\",\"sender\":\"<$.body.sender>\",\"recipient\":\"<$.body.recipient>\",\"subject\":\"<$.body.subject>\",\"body\":\"<$.body.body>\",\"has_attachment\":<$.body.has_attachment>}"

    step_function_state_machine_parameters {
      invocation_type = "FIRE_AND_FORGET"
    }
  }

  depends_on = [
    aws_iam_role_policy.pre_sf1_pipe,
    aws_iam_role_policy.pre_sf1_pipe_logging
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-pre-sf1-pipe"
  }
}

# ---------------------------------------------------------
# 6. OUTPUTS
# ---------------------------------------------------------

output "pre_sf1_pipe_arn" {
  description = "EventBridge Pipe ARN for Pre-SF1 processing"
  value       = aws_pipes_pipe.pre_sf1.arn
}

output "pre_sf1_pipe_role_arn" {
  description = "IAM execution role ARN for the Pre-SF1 Pipe"
  value       = aws_iam_role.pre_sf1_pipe.arn
}