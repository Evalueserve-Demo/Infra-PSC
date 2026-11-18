
# =========================================================
# 396 - RECONCILER LAMBDA
# =========================================================
# Catches mails that got stuck. It sends each one back to the step where
# it stopped, hands it to a person if it keeps failing, and re-reads the
# mailbox when a mail was never saved.
# The code is generated from payment_query_platform.reconciliation.reconcile
# by scripts/build_single_file_lambda.py. Do not edit it by hand.
#
# The schedule is DISABLED until enable_reconciler_schedule is set.

# ---------------------------------------------------------
# 1. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "reconciler" {
  type        = "zip"
  source_dir  = "${path.module}/build/reconciler"
  output_path = "${path.module}/build/reconciler.zip"
}

# ---------------------------------------------------------
# 2. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "reconciler" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-reconciler"

  retention_in_days = var.reconciler_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-reconciler-logs"
  }
}

# ---------------------------------------------------------
# 3. LAMBDA IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "reconciler" {
  name               = "${var.project_name}-${var.environment}-reconciler-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-reconciler-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "reconciler_baseline" {
  role       = aws_iam_role.reconciler.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 4. LAMBDA IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "reconciler_access" {
  name = "reconciler-access"
  role = aws_iam_role.reconciler.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "ReadMailFiles"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.attachments.arn}/${var.mail_s3_prefix}*"
      },
      {
        Sid       = "ListMailFiles"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.attachments.arn
        Condition = { StringLike = { "s3:prefix" = ["${var.mail_s3_prefix}*"] } }
      },
      {
        Sid      = "ReadGraphSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.microsoft_graph.arn
      },
      {
        Sid    = "EncryptAndDecryptApplicationData"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.psc.arn
      },
      {
        # Lets the function log in to the database through RDS Proxy.
        Sid      = "DatabaseProxyIamAuth"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      },
      {
        Sid      = "StartWorkflowOne"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = aws_sfn_state_machine.sf1.arn
      },
      {
        Sid      = "RequeueForWorkflowTwo"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.sqs1.arn
      },
      {
        Sid      = "RerunIngestionForFullRead"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.email_ingestion.arn
      },
      {
        # PutMetricData cannot be limited to one resource, so this one uses "*".
        Sid      = "PublishMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
      }
    ]
  })
}

# ---------------------------------------------------------
# 5. RECONCILER LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "reconciler" {
  function_name = "${var.project_name}-${var.environment}-reconciler"

  role    = aws_iam_role.reconciler.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.reconciler.output_path
  source_code_hash = data.archive_file.reconciler.output_base64sha256

  timeout     = var.reconciler_timeout
  memory_size = var.reconciler_memory

  vpc_config {
    subnet_ids = [
      aws_subnet.private_a.id,
      aws_subnet.private_b.id
    ]

    security_group_ids = [
      aws_security_group.lambda.id
    ]
  }

  environment {
    variables = merge(
      {
        AURORA_HOST = aws_db_proxy.psc.endpoint
        AURORA_DB   = var.email_ingestion_db_name
        AURORA_USER = var.email_ingestion_db_user

        GRAPH_API_BASE_URL = var.graph_api_base_url
        GRAPH_SECRET_ID    = aws_secretsmanager_secret.microsoft_graph.arn
        GRAPH_MAILBOX      = var.mailbox_user

        STATE_MACHINE_ARN  = aws_sfn_state_machine.sf1.arn
        SQS1_QUEUE_URL     = aws_sqs_queue.sqs1.url
        INGESTION_FUNCTION = aws_lambda_function.email_ingestion.function_name

        INGESTION_CUTOFF_UTC = var.ingestion_cutoff_utc

        # Where mail is read from: "graph" is Outlook, "s3" is CSV files under MAIL_S3_PREFIX
        # in the attachments bucket.
        MAIL_SOURCE    = var.mail_source
        MAIL_S3_BUCKET = aws_s3_bucket.attachments.bucket
        MAIL_S3_PREFIX = var.mail_s3_prefix
      },
      var.reconcile_since == "" ? {} : { RECONCILE_SINCE = var.reconcile_since }
    )
  }

  depends_on = [
    aws_cloudwatch_log_group.reconciler,
    aws_iam_role_policy_attachment.reconciler_baseline,
    aws_iam_role_policy.reconciler_access
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-reconciler"
  }
}

# ---------------------------------------------------------
# 6. SCHEDULER IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "reconciler_scheduler" {
  name               = "${var.project_name}-${var.environment}-recon-sched-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json

  tags = {
    Name = "${var.project_name}-reconciler-scheduler-role-${var.environment}"
  }
}

resource "aws_iam_role_policy" "reconciler_scheduler_invoke" {
  name = "invoke-reconciler"
  role = aws_iam_role.reconciler_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "InvokeReconcilerLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.reconciler.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 7. EVENTBRIDGE SCHEDULE
# ---------------------------------------------------------

resource "aws_scheduler_schedule" "reconciler" {
  name = "${var.project_name}-${var.environment}-reconciler-schedule"

  description = "Periodically invoke the Reconciler Lambda"

  schedule_expression = var.reconciler_schedule_expression

  state = var.enable_reconciler_schedule ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.reconciler.arn
    role_arn = aws_iam_role.reconciler_scheduler.arn

    input = jsonencode({})

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }

  depends_on = [
    aws_iam_role_policy.reconciler_scheduler_invoke
  ]
}

# ---------------------------------------------------------
# 8. OUTPUTS
# ---------------------------------------------------------

output "reconciler_lambda_arn" {
  description = "Reconciler Lambda ARN"
  value       = aws_lambda_function.reconciler.arn
}

output "reconciler_lambda_name" {
  description = "Reconciler Lambda name"
  value       = aws_lambda_function.reconciler.function_name
}
