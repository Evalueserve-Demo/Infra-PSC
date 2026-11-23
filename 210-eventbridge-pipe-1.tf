
# =========================================================
# 210 - SQS1 TO SF2 DISPATCHER LAMBDA
#
# Architecture:
# SQS1 -> Dispatcher Lambda -> SF2
#
# Replaces EventBridge Pipe 1 completely.
# Boeing GovCloud deployment configuration.
# =========================================================

locals {
  sf2_dispatcher_name = "${var.project_name}-${var.environment}-sf2-dispatcher"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------
# Build directory must contain lambda_function.py.
# No external packages required if Python uses only
# standard library and runtime-provided boto3/botocore.

data "archive_file" "sf2_dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/build/sf2_dispatcher"
  output_path = "${path.module}/build/sf2_dispatcher.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "sf2_dispatcher" {
  name              = "/aws/lambda/${local.sf2_dispatcher_name}"
  retention_in_days = 14

  tags = {
    Name = "${local.sf2_dispatcher_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "sf2_dispatcher" {
  name = "${local.sf2_dispatcher_name}-role"

  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

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
    Name = "${local.sf2_dispatcher_name}-role"
  }
}

# ---------------------------------------------------------
# 4. AWS MANAGED LAMBDA BASELINE POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "sf2_dispatcher_baseline" {
  role       = aws_iam_role.sf2_dispatcher.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. LEAST-PRIVILEGE APPLICATION PERMISSIONS
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
          "sqs:ChangeMessageVisibility",
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

        Resource = "${aws_cloudwatch_log_group.sf2_dispatcher.arn}:*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. SQS1 TO SF2 DISPATCHER LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "sf2_dispatcher" {
  function_name = local.sf2_dispatcher_name
  description   = "Dispatches SQS1 messages to Step Functions 2"

  role    = aws_iam_role.sf2_dispatcher.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.sf2_dispatcher.output_path
  source_code_hash = data.archive_file.sf2_dispatcher.output_base64sha256

  timeout     = 60
  memory_size = 256

  # -------------------------------------------------------
  # VPC CONFIGURATION - SAME AS SUCCESSFUL 166
  # -------------------------------------------------------

  vpc_config {
    subnet_ids = var.existing_subnet_ids

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = {
      SF2_STATE_MACHINE_ARN = aws_sfn_state_machine.sf2.arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.sf2_dispatcher,
    aws_iam_role_policy_attachment.sf2_dispatcher_baseline,
    aws_iam_role_policy.sf2_dispatcher
  ]

  tags = {
    Name = local.sf2_dispatcher_name
  }
}

# ---------------------------------------------------------
# 7. SQS1 -> LAMBDA EVENT SOURCE MAPPING
# ---------------------------------------------------------

resource "aws_lambda_event_source_mapping" "sf2_dispatcher" {
  event_source_arn = aws_sqs_queue.sqs1.arn
  function_name    = aws_lambda_function.sf2_dispatcher.arn

  enabled = true

  batch_size                         = 1
  maximum_batching_window_in_seconds = 0

  function_response_types = [
    "ReportBatchItemFailures"
  ]

  scaling_config {
    maximum_concurrency = 50
  }

  depends_on = [
    aws_iam_role_policy.sf2_dispatcher
  ]
}

# ---------------------------------------------------------
# 8. OUTPUTS
# ---------------------------------------------------------

output "sf2_dispatcher_lambda_arn" {
  description = "ARN of the SQS1 to SF2 Dispatcher Lambda"
  value       = aws_lambda_function.sf2_dispatcher.arn
}

output "sf2_dispatcher_role_arn" {
  description = "IAM role ARN of the SF2 Dispatcher Lambda"
  value       = aws_iam_role.sf2_dispatcher.arn
}

output "sf2_dispatcher_event_source_mapping_uuid" {
  description = "SQS1 event source mapping UUID"
  value       = aws_lambda_event_source_mapping.sf2_dispatcher.uuid
}

# =========================================================
# END OF 210 - SQS1 TO SF2 DISPATCHER LAMBDA
# =========================================================
