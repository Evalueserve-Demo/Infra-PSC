
# =========================================================
# 150 - EVENTBRIDGE SCHEDULER
# =========================================================
# Purpose:
# Trigger Email Ingestion Lambda at a configured interval.
#
# Lambda is created in Stage 160.
# Scheduler remains DISABLED until explicitly enabled.
# Failed Scheduler deliveries are sent to an SQS DLQ.
# =========================================================

# ---------------------------------------------------------
# 1. SCHEDULER ASSUME ROLE POLICY
# ---------------------------------------------------------

data "aws_iam_policy_document" "scheduler_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------
# 2. SCHEDULER IAM EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "email_scheduler" {
  name               = "${var.project_name}-${var.environment}-sched-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json

  tags = {
    Name = "${var.project_name}-email-scheduler-role-${var.environment}"
  }
}

# ---------------------------------------------------------
# 3. SCHEDULER DEAD-LETTER QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "email_scheduler_dlq" {
  name = "${var.project_name}-${var.environment}-email-scheduler-dlq"

  message_retention_seconds = 1209600

  sqs_managed_sse_enabled = true

  tags = {
    Name = "${var.project_name}-email-scheduler-dlq-${var.environment}"
  }
}

# ---------------------------------------------------------
# 4. SCHEDULER DLQ RESOURCE POLICY
# ---------------------------------------------------------

data "aws_iam_policy_document" "email_scheduler_dlq" {
  statement {
    sid    = "AllowSchedulerSendMessage"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.email_scheduler.arn]
    }

    actions = [
      "sqs:SendMessage"
    ]

    resources = [
      aws_sqs_queue.email_scheduler_dlq.arn
    ]
  }
}

resource "aws_sqs_queue_policy" "email_scheduler_dlq" {
  queue_url = aws_sqs_queue.email_scheduler_dlq.id
  policy    = data.aws_iam_policy_document.email_scheduler_dlq.json
}

# ---------------------------------------------------------
# 5. PERMISSION TO INVOKE EMAIL INGESTION LAMBDA
#    AND SEND FAILED DELIVERIES TO DLQ
# ---------------------------------------------------------

resource "aws_iam_role_policy" "email_scheduler_invoke" {
  name = "invoke-email-ingestion"
  role = aws_iam_role.email_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "InvokeEmailIngestionLambda"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = aws_lambda_function.email_ingestion.arn
      },
      {
        Sid    = "SendFailedDeliveriesToDLQ"
        Effect = "Allow"

        Action = [
          "sqs:SendMessage"
        ]

        Resource = aws_sqs_queue.email_scheduler_dlq.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. EVENTBRIDGE SCHEDULE
# ---------------------------------------------------------

resource "aws_scheduler_schedule" "email_ingestion" {
  name = "${var.project_name}-${var.environment}-email-schedule"

  description = "Periodically invoke the Email Ingestion Lambda"

  schedule_expression          = var.email_scheduler_expression
  schedule_expression_timezone = var.email_scheduler_timezone

  state = var.enable_scheduler ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.email_ingestion.arn
    role_arn = aws_iam_role.email_scheduler.arn

    input = jsonencode({
      source      = "eventbridge-scheduler"
      workflow    = "payment-status-check"
      environment = var.environment
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }

    dead_letter_config {
      arn = aws_sqs_queue.email_scheduler_dlq.arn
    }
  }

  depends_on = [
    aws_iam_role_policy.email_scheduler_invoke,
    aws_sqs_queue_policy.email_scheduler_dlq
  ]
}

# =========================================================
# END OF STAGE 150
# =========================================================
