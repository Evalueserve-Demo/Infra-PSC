
# =========================================================
# 290 - SQS2 AND DEAD-LETTER QUEUE
# =========================================================

# ---------------------------------------------------------
# 1. SQS2 DEAD-LETTER QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "sqs2_dlq" {
  name = "${var.project_name}-${var.environment}-${var.sqs2_dlq_suffix}"

  message_retention_seconds = var.sqs2_dlq_retention_seconds

  kms_master_key_id = aws_kms_key.psc.arn

  kms_data_key_reuse_period_seconds = var.sqs2_kms_data_key_reuse_seconds

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sqs2_dlq_suffix}"
  }
}

# ---------------------------------------------------------
# 2. SQS2 MAIN QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "sqs2" {
  name = "${var.project_name}-${var.environment}-${var.sqs2_queue_suffix}"

  delay_seconds              = var.sqs2_delay_seconds
  max_message_size           = var.sqs2_max_message_size
  message_retention_seconds  = var.sqs2_retention_seconds
  receive_wait_time_seconds  = var.sqs2_receive_wait_time_seconds
  visibility_timeout_seconds = var.sqs2_visibility_timeout_seconds

  kms_master_key_id = aws_kms_key.psc.arn

  kms_data_key_reuse_period_seconds = var.sqs2_kms_data_key_reuse_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.sqs2_dlq.arn
    maxReceiveCount     = var.sqs2_max_receive_count
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sqs2_queue_suffix}"
  }
}

# ---------------------------------------------------------
# 3. SQS2 DLQ REDRIVE ALLOW POLICY
# ---------------------------------------------------------

resource "aws_sqs_queue_redrive_allow_policy" "sqs2_dlq" {
  queue_url = aws_sqs_queue.sqs2_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns = [
      aws_sqs_queue.sqs2.arn
    ]
  })
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "sqs2_queue_url" {
  description = "SQS2 main queue URL"
  value       = aws_sqs_queue.sqs2.id
}

output "sqs2_queue_arn" {
  description = "SQS2 main queue ARN"
  value       = aws_sqs_queue.sqs2.arn
}

output "sqs2_dlq_url" {
  description = "SQS2 dead-letter queue URL"
  value       = aws_sqs_queue.sqs2_dlq.id
}

output "sqs2_dlq_arn" {
  description = "SQS2 dead-letter queue ARN"
  value       = aws_sqs_queue.sqs2_dlq.arn
}

