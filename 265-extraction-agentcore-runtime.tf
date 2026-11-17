
# =========================================================
# 265 - EXTRACTION AGENTCORE RUNTIME
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

# ---------------------------------------------------------
# 1. EXTRACTION CONFIGURATION VARIABLES
# ---------------------------------------------------------

variable "extraction_agent_runtime_name" {
  description = "Extraction AgentCore Runtime name"
  type        = string
  default     = "psc_dev_extraction"
}

variable "extraction_agent_role_suffix" {
  description = "Extraction AgentCore IAM role suffix"
  type        = string
  default     = "extraction-agentcore-role"
}

variable "extraction_artifact_bucket_suffix" {
  description = "Dedicated Extraction artifact bucket suffix"
  type        = string
  default     = "extraction"
}

variable "extraction_agent_artifact_key" {
  description = "S3 key for Extraction Agent ZIP"
  type        = string
  default     = "extraction/extraction_agent.zip"
}

# Initially use the approved Classification model configuration.
# Extraction can have independent model settings later.

locals {
  extraction_model_config = {
    model_id          = local.classification_model_config.model_id
    temperature       = local.classification_model_config.temperature
    top_p             = local.classification_model_config.top_p
    max_output_tokens = local.classification_model_config.max_output_tokens
  }
}

# ---------------------------------------------------------
# 2. PACKAGE EXTRACTION AGENT
# ---------------------------------------------------------

data "archive_file" "extraction_agent" {
  type        = "zip"
  source_dir  = "${path.module}/build/extraction_agent"
  output_path = "${path.module}/build/extraction_agent.zip"
}

# ---------------------------------------------------------
# 3. DEDICATED EXTRACTION ARTIFACT BUCKET
# ---------------------------------------------------------

resource "aws_s3_bucket" "extraction_agent_artifacts" {
  bucket = "${var.project_name}-${var.environment}-${var.aws_account_id}-${var.extraction_artifact_bucket_suffix}"

  force_destroy = false

  tags = {
    Name = "${var.project_name}-${var.environment}-extraction-artifacts"
  }
}

resource "aws_s3_bucket_public_access_block" "extraction_agent_artifacts" {
  bucket = aws_s3_bucket.extraction_agent_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "extraction_agent_artifacts" {
  bucket = aws_s3_bucket.extraction_agent_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.psc.arn
    }

    bucket_key_enabled = true
  }
}

# ---------------------------------------------------------
# 4. UPLOAD EXTRACTION ZIP
# ---------------------------------------------------------

resource "aws_s3_object" "extraction_agent_zip" {
  bucket = aws_s3_bucket.extraction_agent_artifacts.id
  key    = var.extraction_agent_artifact_key

  source      = data.archive_file.extraction_agent.output_path
  source_hash = data.archive_file.extraction_agent.output_base64sha256

  server_side_encryption = "aws:kms"
  kms_key_id             = aws_kms_key.psc.arn

  depends_on = [
    aws_s3_bucket_server_side_encryption_configuration.extraction_agent_artifacts,
    aws_s3_bucket_public_access_block.extraction_agent_artifacts
  ]
}

# ---------------------------------------------------------
# 5. AGENTCORE EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "extraction_agentcore" {
  name = "${var.project_name}-${var.environment}-${var.extraction_agent_role_suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowAgentCoreAssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "bedrock-agentcore.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-extraction-agentcore"
  }
}

# ---------------------------------------------------------
# 6. AGENTCORE EXECUTION PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "extraction_agentcore" {
  name = "extraction-agentcore-access"
  role = aws_iam_role.extraction_agentcore.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeExtractionModel"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:bedrock:*::foundation-model/*",
          "arn:${data.aws_partition.current.partition}:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/${var.agent_model_id}"
        ]
      },
      {
        Sid    = "ApplyExtractionGuardrail"
        Effect = "Allow"
        Action = [
          "bedrock:ApplyGuardrail"
        ]
        Resource = [
          aws_bedrock_guardrail.classification.guardrail_arn
        ]
      },
      {
        Sid    = "ReadExtractionDeploymentPackage"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.extraction_agent_artifacts.arn}/${var.extraction_agent_artifact_key}"
        ]
      },
      {
        Sid    = "DecryptExtractionDeploymentPackage"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = [
          aws_kms_key.psc.arn
        ]
      },
      {
        Sid    = "WriteExtractionAgentLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"
        ]
      }
    ]
  })
}

# ---------------------------------------------------------
# 7. EXTRACTION AGENTCORE RUNTIME
# ---------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "extraction" {
  agent_runtime_name = var.extraction_agent_runtime_name

  description = "Payment email invoice and identifier extraction agent"

  role_arn = aws_iam_role.extraction_agentcore.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket     = aws_s3_bucket.extraction_agent_artifacts.bucket
          prefix     = aws_s3_object.extraction_agent_zip.key
          version_id = null
        }
      }
    }
  }

  network_configuration {
    network_mode = "PUBLIC"
  }

  protocol_configuration {
    server_protocol = "HTTP"
  }

  # Same code as the classification runtime. AGENT_ROLE picks which job it does.
  environment_variables = {
    AWS_REGION       = var.aws_region
    AGENT_ROLE       = "extraction"
    BEDROCK_MODEL_ID = var.agent_model_id
  }

  depends_on = [
    aws_s3_object.extraction_agent_zip,
    aws_iam_role_policy.extraction_agentcore
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-extraction-agentcore"
  }
}

# ---------------------------------------------------------
# 8. OUTPUTS
# ---------------------------------------------------------

output "extraction_agentcore_runtime_arn" {
  description = "Extraction AgentCore Runtime ARN"
  value       = aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn
}

output "extraction_agentcore_runtime_id" {
  description = "Extraction AgentCore Runtime ID"
  value       = aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_id
}
