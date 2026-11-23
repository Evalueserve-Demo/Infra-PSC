
# =========================================================
# 140 - IAM ROLES AND BASELINE POLICIES
# PROJECT: BOEING HELPDESK PAYMENT STATUS CHECK
# =========================================================
#
# 8 Lambda execution roles
# 8 Lambda baseline policy attachments
#  1 Shared Lambda baseline IAM policy
#  2 Step Functions execution roles
#  1 RDS Proxy IAM role
#  1 RDS Proxy IAM inline policy
#
# TOTAL: 21 TERRAFORM RESOURCES
#
# Service-specific IAM permissions will be added
# in the corresponding infrastructure stages.
# =========================================================


# =========================================================
# 1. IAM TRUST POLICIES
# =========================================================

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "step_functions_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}


# =========================================================
# 2. COMMON LAMBDA BASELINE POLICY
# =========================================================

data "aws_iam_policy_document" "lambda_baseline" {

  statement {
    sid = "WriteCloudWatchLogs"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${var.aws_account_id}:*"
    ]
  }

  statement {
    sid = "ManageLambdaVpcNetworkInterfaces"

    actions = [
      "ec2:CreateNetworkInterface",
      "ec2:DescribeNetworkInterfaces",
      "ec2:DeleteNetworkInterface",
      "ec2:AssignPrivateIpAddresses",
      "ec2:UnassignPrivateIpAddresses"
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "lambda_baseline" {
  name   = "${var.project_name}-${var.environment}-lambda-base"
  policy = data.aws_iam_policy_document.lambda_baseline.json

  tags = {
    Name = "${var.project_name}-lambda-baseline-${var.environment}"
  }
}


# =========================================================
# 3. EMAIL INGESTION LAMBDA - STAGE 160
# =========================================================

resource "aws_iam_role" "email_ingestion" {
  name               = "${var.project_name}-${var.environment}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-email-ingestion-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "email_ingestion_baseline" {
  role       = aws_iam_role.email_ingestion.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 4. ATTACHMENT PROCESSING LAMBDA - STAGE 180
# =========================================================

resource "aws_iam_role" "attachment_processing" {
  name               = "${var.project_name}-${var.environment}-attach-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-attachment-processing-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "attachment_processing_baseline" {
  role       = aws_iam_role.attachment_processing.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 5. MAIL PROCESSING LAMBDA - STAGE 190
# =========================================================

resource "aws_iam_role" "email_processing" {
  name               = "${var.project_name}-${var.environment}-mail-process-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-mail-processing-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "email_processing_baseline" {
  role       = aws_iam_role.email_processing.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 6. MAIL PREPROCESSING LAMBDA - STAGE 230
# =========================================================

resource "aws_iam_role" "mail_preprocessing" {
  name               = "${var.project_name}-${var.environment}-mail-prep-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-mail-preprocessing-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "mail_preprocessing_baseline" {
  role       = aws_iam_role.mail_preprocessing.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 7. INVESTIGATION INVOCATION LAMBDA - STAGE 310
# =========================================================

resource "aws_iam_role" "investigation_invocation" {
  name               = "${var.project_name}-${var.environment}-investigate-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-investigation-invocation-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "investigation_invocation_baseline" {
  role       = aws_iam_role.investigation_invocation.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 8. SAP S/4HANA TOOL LAMBDA - STAGE 390
# =========================================================

resource "aws_iam_role" "s4hana_tool" {
  name               = "${var.project_name}-${var.environment}-s4hana-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-s4hana-tool-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "s4hana_tool_baseline" {
  role       = aws_iam_role.s4hana_tool.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 9. HUMAN REVIEW BACKEND LAMBDA - STAGE 410
# =========================================================

resource "aws_iam_role" "human_review_backend" {
  name               = "${var.project_name}-${var.environment}-review-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-human-review-backend-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "human_review_backend_baseline" {
  role       = aws_iam_role.human_review_backend.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 10. REPLY PROCESSING LAMBDA - STAGE 430
# =========================================================

resource "aws_iam_role" "reply_processing" {
  name               = "${var.project_name}-${var.environment}-reply-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = {
    Name = "${var.project_name}-reply-processing-role-${var.environment}"
  }
}

resource "aws_iam_role_policy_attachment" "reply_processing_baseline" {
  role       = aws_iam_role.reply_processing.name
  policy_arn = aws_iam_policy.lambda_baseline.arn
}


# =========================================================
# 11. STEP FUNCTIONS 1 EXECUTION ROLE - STAGE 170
# =========================================================

resource "aws_iam_role" "sf1" {
  name               = "${var.project_name}-${var.environment}-sf1-role"
  assume_role_policy = data.aws_iam_policy_document.step_functions_assume_role.json

  tags = {
    Name = "${var.project_name}-sf1-role-${var.environment}"
  }
}


# =========================================================
# 12. STEP FUNCTIONS 2 EXECUTION ROLE - STAGE 220
# =========================================================

resource "aws_iam_role" "sf2" {
  name               = "${var.project_name}-${var.environment}-sf2-role"
  assume_role_policy = data.aws_iam_policy_document.step_functions_assume_role.json

  tags = {
    Name = "${var.project_name}-sf2-role-${var.environment}"
  }
}


# =========================================================
# 13. RDS PROXY IAM ROLE
# =========================================================

resource "aws_iam_role" "rds_proxy" {
  name = "${var.project_name}-${var.environment}-rds-proxy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "rds.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-rds-proxy-role-${var.environment}"
  }
}


# =========================================================
# 14. RDS PROXY IAM POLICY
# =========================================================

resource "aws_iam_role_policy" "rds_proxy" {
  name = "${var.project_name}-${var.environment}-rds-proxy-policy"
  role = aws_iam_role.rds_proxy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadApplicationDatabaseSecret"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = aws_secretsmanager_secret.psc_app_db.arn
      },
      {
        Sid    = "DecryptApplicationDatabaseSecret"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.psc.arn
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${data.aws_region.current.region}.${data.aws_partition.current.dns_suffix}"
          }
        }
      }
    ]
  })
}


# =========================================================
# END OF 140 - IAM FOUNDATION
# =========================================================
