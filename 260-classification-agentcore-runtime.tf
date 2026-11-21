
# =========================================================
# 260 - CLASSIFICATION AGENTCORE RUNTIME
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE CLASSIFICATION AGENT
# ---------------------------------------------------------

data "archive_file" "classification_agent" {
  type        = "zip"
  source_dir  = "${path.module}/build/classification_agent"
  output_path = "${path.module}/build/classification_agent.zip"
}

# ---------------------------------------------------------
# 2. DEDICATED AGENT ARTIFACT BUCKET
# ---------------------------------------------------------

resource "aws_s3_bucket" "classification_agent_artifacts" {
  bucket = "${var.project_name}-${var.environment}-${var.aws_account_id}-${var.classification_artifact_bucket_suffix}"

  force_destroy = false

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.classification_artifact_bucket_suffix}"
  }
}

resource "aws_s3_bucket_public_access_block" "classification_agent_artifacts" {
  bucket = aws_s3_bucket.classification_agent_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "classification_agent_artifacts" {
  bucket = aws_s3_bucket.classification_agent_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.psc.arn
    }

    bucket_key_enabled = true
  }
}

# ---------------------------------------------------------
# 3. UPLOAD CLASSIFICATION ZIP
# ---------------------------------------------------------

resource "aws_s3_object" "classification_agent_zip" {
  bucket = aws_s3_bucket.classification_agent_artifacts.id
  key    = var.classification_agent_artifact_key

  source      = data.archive_file.classification_agent.output_path
  source_hash = data.archive_file.classification_agent.output_base64sha256

  server_side_encryption = "aws:kms"
  kms_key_id             = aws_kms_key.psc.arn

  depends_on = [
    aws_s3_bucket_server_side_encryption_configuration.classification_agent_artifacts
  ]
}

# ---------------------------------------------------------
# 4. AGENTCORE EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "classification_agentcore" {
  name = "${var.project_name}-${var.environment}-${var.classification_agent_role_suffix}"

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
    Name = "${var.project_name}-${var.environment}-${var.classification_agent_role_suffix}"
  }
}

# ---------------------------------------------------------
# 5. AGENTCORE EXECUTION PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "classification_agentcore" {
  name = "classification-agentcore-access"
  role = aws_iam_role.classification_agentcore.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeClassificationModel"
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
        Sid    = "ApplyClassificationGuardrail"
        Effect = "Allow"
        Action = [
          "bedrock:ApplyGuardrail"
        ]
        Resource = [
          aws_bedrock_guardrail.classification.guardrail_arn
        ]
      },
      {
        Sid    = "ReadAgentDeploymentPackage"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.classification_agent_artifacts.arn}/${var.classification_agent_artifact_key}"
        ]
      },
      {
        Sid    = "DecryptAgentDeploymentPackage"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = [
          aws_kms_key.psc.arn
        ]
      },
      {
        Sid    = "WriteAgentLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = [
          "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/bedrock-agentcore/*"
        ]
      }
    ]
  })
}

# ---------------------------------------------------------
# 6. CLASSIFICATION AGENTCORE RUNTIME
# ---------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "classification" {
  agent_runtime_name = var.classification_agent_runtime_name

  description = "Payment status email classification agent"

  role_arn = aws_iam_role.classification_agentcore.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket     = aws_s3_bucket.classification_agent_artifacts.bucket
          prefix     = aws_s3_object.classification_agent_zip.key
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

  # Runs agents/classification/main.py (a Strands agent).
  # AGENT_ROLE picks which job it does. The guardrail has already checked
  # the mail in the intake Lambda before this is called.
  environment_variables = {
    AWS_REGION       = var.aws_region
    AGENT_ROLE       = "classification"
    BEDROCK_MODEL_ID = var.agent_model_id
  }

  depends_on = [
    aws_s3_object.classification_agent_zip,
    aws_iam_role_policy.classification_agentcore
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.classification_agent_runtime_name}"
  }
}

# ---------------------------------------------------------
# 7. OUTPUTS
# ---------------------------------------------------------

output "classification_agentcore_runtime_arn" {
  description = "Classification AgentCore Runtime ARN"
  value       = aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn
}

output "classification_agentcore_runtime_id" {
  description = "Classification AgentCore Runtime ID"
  value       = aws_bedrockagentcore_agent_runtime.classification.agent_runtime_id
}

