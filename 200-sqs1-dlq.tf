
# =========================================================
# 200 - SQS1 AND DEAD LETTER QUEUE
# =========================================================

# ---------------------------------------------------------
# 1. DEAD LETTER QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "sqs1_dlq" {
  name = "${var.project_name}-${var.environment}-${var.sqs1_dlq_name_suffix}"

  message_retention_seconds = var.sqs1_dlq_retention_seconds

  kms_master_key_id                 = aws_kms_key.psc.arn
  kms_data_key_reuse_period_seconds = var.sqs1_kms_data_key_reuse_seconds

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sqs1_dlq_name_suffix}"
  }
}

# ---------------------------------------------------------
# 2. MAIN SQS QUEUE
# ---------------------------------------------------------

resource "aws_sqs_queue" "sqs1" {
  name = "${var.project_name}-${var.environment}-${var.sqs1_name_suffix}"

  visibility_timeout_seconds = var.sqs1_visibility_timeout_seconds
  message_retention_seconds  = var.sqs1_retention_seconds
  receive_wait_time_seconds  = var.sqs1_receive_wait_time_seconds

  kms_master_key_id                 = aws_kms_key.psc.arn
  kms_data_key_reuse_period_seconds = var.sqs1_kms_data_key_reuse_seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.sqs1_dlq.arn
    maxReceiveCount     = var.sqs1_max_receive_count
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sqs1_name_suffix}"
  }
}

# ---------------------------------------------------------
# 3. DLQ REDRIVE ALLOW POLICY
# ---------------------------------------------------------

resource "aws_sqs_queue_redrive_allow_policy" "sqs1_dlq" {
  queue_url = aws_sqs_queue.sqs1_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns = [
      aws_sqs_queue.sqs1.arn
    ]
  })
}

# ---------------------------------------------------------
# 4. OUTPUTS
# ---------------------------------------------------------

output "sqs1_queue_url" {
  description = "SQS1 queue URL"
  value       = aws_sqs_queue.sqs1.url
}

output "sqs1_queue_arn" {
  description = "SQS1 queue ARN"
  value       = aws_sqs_queue.sqs1.arn
}

output "sqs1_dlq_arn" {
  description = "SQS1 dead letter queue ARN"
  value       = aws_sqs_queue.sqs1_dlq.arn
}

