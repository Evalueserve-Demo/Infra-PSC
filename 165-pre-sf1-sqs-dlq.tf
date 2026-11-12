# =========================================================
# 165 - PRE-SF1 SQS STANDARD QUEUE AND DEAD LETTER QUEUE
#
# Flow:
# Aurora Outbox -> Email Ingestion Lambda (Outflow Dispatcher)
# -> Pre-SF1 SQS -> EventBridge Pipe -> Step Functions 1
#
# Purpose:
# Buffer admitted workflow events before SF1.
# =========================================================

# ---------------------------------------------------------
# 1. RESOURCE NAMING
# ---------------------------------------------------------

locals {
  pre_sf1_queue_name = "${var.project_name}-${var.environment}-pre-sf1"
  pre_sf1_dlq_name   = "${var.project_name}-${var.environment}-pre-sf1-dlq"
}

# ---------------------------------------------------------
# 2. PRE-SF1 DEAD LETTER QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "pre_sf1_dlq" {
  name = local.pre_sf1_dlq_name

  # Retain failed messages for 14 days.
  message_retention_seconds = 1209600

  # Reuse the existing PSC KMS key.
  kms_master_key_id                 = aws_kms_key.psc.arn
  kms_data_key_reuse_period_seconds = 300

  tags = {
    Name = local.pre_sf1_dlq_name
  }
}

# ---------------------------------------------------------
# 3. PRE-SF1 STANDARD QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "pre_sf1" {
  name = local.pre_sf1_queue_name

  # Initial settings; review against Pipe retry behavior.
  visibility_timeout_seconds = 300
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20

  # Reuse the existing PSC KMS key.
  kms_master_key_id                 = aws_kms_key.psc.arn
  kms_data_key_reuse_period_seconds = 300

  # Move repeatedly failing messages to the DLQ.
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.pre_sf1_dlq.arn
    maxReceiveCount     = 5
  })

  tags = {
    Name = local.pre_sf1_queue_name
  }
}

# ---------------------------------------------------------
# 4. DLQ REDRIVE ALLOW POLICY
# ---------------------------------------------------------

resource "aws_sqs_queue_redrive_allow_policy" "pre_sf1_dlq" {
  queue_url = aws_sqs_queue.pre_sf1_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"

    sourceQueueArns = [
      aws_sqs_queue.pre_sf1.arn
    ]
  })
}

# ---------------------------------------------------------
# 5. OUTPUTS
# ---------------------------------------------------------

output "pre_sf1_queue_url" {
  description = "Pre-SF1 queue URL for the Outflow Dispatcher"
  value       = aws_sqs_queue.pre_sf1.url
}

output "pre_sf1_queue_arn" {
  description = "Pre-SF1 queue ARN for EventBridge Pipe"
  value       = aws_sqs_queue.pre_sf1.arn
}

output "pre_sf1_dlq_arn" {
  description = "Pre-SF1 dead-letter queue ARN"
  value       = aws_sqs_queue.pre_sf1_dlq.arn
}