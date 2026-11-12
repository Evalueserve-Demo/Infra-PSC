
# =========================================================
# 090 - AWS KMS
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

# ---------------------------------------------------------
# 1. CUSTOMER-MANAGED KMS KEY
# ---------------------------------------------------------

resource "aws_kms_key" "psc" {
  description = "${var.project_name} encryption key for ${var.environment}"

  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"

  enable_key_rotation     = var.kms_enable_key_rotation
  deletion_window_in_days = var.kms_deletion_window_days

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "EnableAccountIAMPermissions"
        Effect = "Allow"

        Principal = {
          AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
        }

        Action   = "kms:*"
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-kms-${var.environment}"
  }
}


# ---------------------------------------------------------
# 2. KMS ALIAS
# ---------------------------------------------------------

resource "aws_kms_alias" "psc" {
  name          = "alias/${var.project_name}-${var.environment}"
  target_key_id = aws_kms_key.psc.key_id
}

