
# =========================================================
# 166 - PRE-SF1 DISPATCHER LAMBDA
#
# Flow:
# Email Ingestion Lambda (160)
# -> Pre-SF1 SQS (165)
# -> Pre-SF1 Dispatcher Lambda (166)
# -> Step Functions 1 (170)
#
# Replaces the EventBridge Pipe.
# =========================================================

locals {
  pre_sf1_dispatcher_name = "${var.project_name}-${var.environment}-pre-sf1-dispatcher"
}

# ---------------------------------------------------------
# 1. DEPLOYMENT ARCHIVE
# ---------------------------------------------------------
# Build directory must contain lambda_function.py.
# No external Python packages required.

data "archive_file" "pre_sf1_dispatcher" {
  type        = "zip"
  source_dir  = "${path.module}/build/pre_sf1_dispatcher"
  output_path = "${path.module}/build/pre_sf1_dispatcher.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "pre_sf1_dispatcher" {
  name              = "/aws/lambda/${local.pre_sf1_dispatcher_name}"
  retention_in_days = 30

  tags = {
    Name = "${local.pre_sf1_dispatcher_name}-logs"
  }
}

# ---------------------------------------------------------
# 3. IAM EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "pre_sf1_dispatcher" {
  name = "${local.pre_sf1_dispatcher_name}-role"

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
    Name = "${local.pre_sf1_dispatcher_name}-role"
  }
}

# ---------------------------------------------------------
# 4. AWS MANAGED LAMBDA BASELINE POLICY ATTACHMENT
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "pre_sf1_dispatcher_baseline" {
  role       = aws_iam_role.pre_sf1_dispatcher.name
  policy_arn = local.lambda_baseline_policy_arn
}

# ---------------------------------------------------------
# 5. LEAST-PRIVILEGE APPLICATION PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "pre_sf1_dispatcher" {
  name = "${local.pre_sf1_dispatcher_name}-policy"
  role = aws_iam_role.pre_sf1_dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "ConsumePreSF1Queue"
        Effect = "Allow"

        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:ChangeMessageVisibility",
          "sqs:GetQueueAttributes"
        ]

        Resource = aws_sqs_queue.pre_sf1.arn
      },
      {
        Sid    = "DecryptPreSF1Messages"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "StartSF1Execution"
        Effect = "Allow"

        Action = [
          "states:StartExecution"
        ]

        Resource = aws_sfn_state_machine.sf1.arn
      },
      {
        Sid    = "WriteDispatcherLogs"
        Effect = "Allow"

        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]

        Resource = "${aws_cloudwatch_log_group.pre_sf1_dispatcher.arn}:*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. PRE-SF1 DISPATCHER LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "pre_sf1_dispatcher" {
  function_name = local.pre_sf1_dispatcher_name
  description   = "Dispatches Pre-SF1 SQS messages to Step Functions 1"

  role    = aws_iam_role.pre_sf1_dispatcher.arn
  runtime = "python3.13"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.pre_sf1_dispatcher.output_path
  source_code_hash = data.archive_file.pre_sf1_dispatcher.output_base64sha256

  timeout     = 30
  memory_size = 256

  # -------------------------------------------------------
  # VPC CONFIGURATION - SAME PATTERN AS STAGE 180
  # -------------------------------------------------------

  vpc_config {
    subnet_ids = var.existing_subnet_ids

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = {
      SF1_ARN = aws_sfn_state_machine.sf1.arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.pre_sf1_dispatcher,
    aws_iam_role_policy_attachment.pre_sf1_dispatcher_baseline,
    aws_iam_role_policy.pre_sf1_dispatcher
  ]

  tags = {
    Name = local.pre_sf1_dispatcher_name
  }
}

# ---------------------------------------------------------
# 7. SQS -> LAMBDA EVENT SOURCE MAPPING
# ---------------------------------------------------------

resource "aws_lambda_event_source_mapping" "pre_sf1_dispatcher" {
  event_source_arn = aws_sqs_queue.pre_sf1.arn
  function_name    = aws_lambda_function.pre_sf1_dispatcher.arn

  enabled = true

  batch_size                         = 1
  maximum_batching_window_in_seconds = 0

  function_response_types = [
    "ReportBatchItemFailures"
  ]

  # Limit concurrent dispatcher invocations.
  scaling_config {
    maximum_concurrency = 50
  }
}

# ---------------------------------------------------------
# 8. OUTPUTS
# ---------------------------------------------------------

output "pre_sf1_dispatcher_lambda_arn" {
  description = "ARN of the Pre-SF1 Dispatcher Lambda"
  value       = aws_lambda_function.pre_sf1_dispatcher.arn
}

output "pre_sf1_dispatcher_role_arn" {
  description = "IAM role ARN of the Pre-SF1 Dispatcher Lambda"
  value       = aws_iam_role.pre_sf1_dispatcher.arn
}

output "pre_sf1_dispatcher_event_source_mapping_uuid" {
  description = "SQS event source mapping UUID"
  value       = aws_lambda_event_source_mapping.pre_sf1_dispatcher.uuid
}

# =========================================================
# END OF 166 - PRE-SF1 DISPATCHER LAMBDA
# =========================================================
