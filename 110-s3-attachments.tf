
# =========================================================
# 110 - S3 ATTACHMENTS
# =========================================================

# ---------------------------------------------------------
# 1. PRIVATE ATTACHMENTS BUCKET
# ---------------------------------------------------------

resource "aws_s3_bucket" "attachments" {
  bucket = "${var.project_name}-${var.environment}-${var.aws_account_id}-attachments"

  tags = {
    Name = "${var.project_name}-attachments-${var.environment}"
  }
}

# ---------------------------------------------------------
# 2. BLOCK PUBLIC ACCESS
# ---------------------------------------------------------

resource "aws_s3_bucket_public_access_block" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------
# 3. BUCKET OWNERSHIP CONTROLS
# ---------------------------------------------------------

resource "aws_s3_bucket_ownership_controls" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# ---------------------------------------------------------
# 4. VERSIONING
# ---------------------------------------------------------

resource "aws_s3_bucket_versioning" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  versioning_configuration {
    status = var.attachments_versioning_status
  }
}

# ---------------------------------------------------------
# 5. DEFAULT KMS ENCRYPTION
# ---------------------------------------------------------

resource "aws_s3_bucket_server_side_encryption_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.psc.arn
    }

    bucket_key_enabled = true
  }
}

# ---------------------------------------------------------
# 6. ENFORCE HTTPS
# ---------------------------------------------------------

data "aws_iam_policy_document" "attachments_https" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.attachments.arn,
      "${aws_s3_bucket.attachments.arn}/*"
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "attachments" {
  bucket = aws_s3_bucket.attachments.id
  policy = data.aws_iam_policy_document.attachments_https.json

  depends_on = [
    aws_s3_bucket_public_access_block.attachments
  ]
}

# ---------------------------------------------------------
# 7. CLEAN UP INCOMPLETE MULTIPART UPLOADS
# ---------------------------------------------------------

resource "aws_s3_bucket_lifecycle_configuration" "attachments" {
  bucket = aws_s3_bucket.attachments.id

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    abort_incomplete_multipart_upload {
      days_after_initiation = var.attachments_abort_multipart_days
    }
  }
}
