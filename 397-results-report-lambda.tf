
# =========================================================
# 397 - RESULTS REPORT LAMBDA
# =========================================================
# A read-only report of what the pipeline decided for each mail.
# The export writes results/latest.csv and a copy for the day under
# results/daily/, in a private bucket.
# The code is generated from payment_query_platform.reporting.api
# by scripts/build_single_file_lambda.py. Do not edit it by hand.
#
# The schedule is DISABLED until enable_results_export_schedule is set.

# ---------------------------------------------------------
# 1. PRIVATE RESULTS BUCKET
# ---------------------------------------------------------

resource "aws_s3_bucket" "results" {
  bucket = "${var.project_name}-${var.environment}-${var.aws_account_id}-results"

  tags = {
    Name = "${var.project_name}-results-${var.environment}"
  }
}

resource "aws_s3_bucket_public_access_block" "results" {
  bucket = aws_s3_bucket.results.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "results" {
  bucket = aws_s3_bucket.results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.psc.arn
    }

    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "results_https" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.results.arn,
      "${aws_s3_bucket.results.arn}/*"
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "results" {
  bucket = aws_s3_bucket.results.id
  policy = data.aws_iam_policy_document.results_https.json

  depends_on = [
    aws_s3_bucket_public_access_block.results
  ]
}

# ---------------------------------------------------------
# 2. PACKAGE LAMBDA SOURCE
# ---------------------------------------------------------

data "archive_file" "results_report" {
  type        = "zip"
  source_dir  = "${path.module}/build/results_report"
  output_path = "${path.module}/build/results_report.zip"
}

# ---------------------------------------------------------
# 3. CLOUDWATCH LOG GROUP
# ---------------------------------------------------------

resource "aws_cloudwatch_log_group" "results_report" {
  name = "/aws/lambda/${var.project_name}-${var.environment}-results-report"

  retention_in_days = var.results_report_log_retention_days

  tags = {
    Name = "${var.project_name}-${var.environment}-results-report-logs"
  }
}

# ---------------------------------------------------------
# 4. LAMBDA IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "results_report" {
  name               = "${var.project_name}-${var.environment}-results-report-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-results-report-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "results_report_baseline" {
  role       = aws_iam_role.results_report.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}

# ---------------------------------------------------------
# 5. LAMBDA IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "results_report_access" {
  name = "results-report-access"
  role = aws_iam_role.results_report.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        # Lets the function log in to the database through RDS Proxy.
        Sid      = "DatabaseProxyIamAuth"
        Effect   = "Allow"
        Action   = ["rds-db:connect"]
        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.aws_region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      },
      {
        Sid      = "WriteResultsExport"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.results.arn}/*"
      },
      {
        Sid    = "EncryptResultsExport"
        Effect = "Allow"
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. RESULTS REPORT LAMBDA
# ---------------------------------------------------------

resource "aws_lambda_function" "results_report" {
  function_name = "${var.project_name}-${var.environment}-results-report"

  role    = aws_iam_role.results_report.arn
  runtime = "python3.12"
  handler = "lambda_function.lambda_handler"

  filename         = data.archive_file.results_report.output_path
  source_code_hash = data.archive_file.results_report.output_base64sha256

  timeout     = var.results_report_timeout
  memory_size = var.results_report_memory

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
    variables = {
      AURORA_HOST = aws_db_proxy.psc.endpoint
      AURORA_DB   = var.email_ingestion_db_name
      AURORA_USER = var.email_ingestion_db_user

      RESULTS_BUCKET = aws_s3_bucket.results.bucket
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.results_report,
    aws_iam_role_policy_attachment.results_report_baseline,
    aws_iam_role_policy.results_report_access
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-results-report"
  }
}

# ---------------------------------------------------------
# 7. SCHEDULER IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "results_report_scheduler" {
  name               = "${var.project_name}-${var.environment}-results-sched-role"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume_role.json

  tags = {
    Name = "${var.project_name}-results-report-scheduler-role-${var.environment}"
  }
}

resource "aws_iam_role_policy" "results_report_scheduler_invoke" {
  name = "invoke-results-report"
  role = aws_iam_role.results_report_scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "InvokeResultsReportLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = aws_lambda_function.results_report.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 8. EVENTBRIDGE SCHEDULE FOR THE EXPORT
# ---------------------------------------------------------

resource "aws_scheduler_schedule" "results_export" {
  name = "${var.project_name}-${var.environment}-results-export-schedule"

  description = "Periodically write the results export to S3"

  schedule_expression = var.results_export_schedule_expression

  state = var.enable_results_export_schedule ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.results_report.arn
    role_arn = aws_iam_role.results_report_scheduler.arn

    input = jsonencode({
      export = "1"
    })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 3
    }
  }

  depends_on = [
    aws_iam_role_policy.results_report_scheduler_invoke
  ]
}

# ---------------------------------------------------------
# 9. OUTPUTS
# ---------------------------------------------------------

output "results_report_lambda_arn" {
  description = "Results Report Lambda ARN"
  value       = aws_lambda_function.results_report.arn
}

output "results_bucket_name" {
  description = "Bucket the results export is written to"
  value       = aws_s3_bucket.results.bucket
}
