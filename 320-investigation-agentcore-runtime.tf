
# =========================================================
# 320 - INVESTIGATION AGENTCORE RUNTIME
# =========================================================

# ---------------------------------------------------------
# 1. PACKAGE AGENT CODE
# ---------------------------------------------------------

data "archive_file" "investigation_agent" {
  type        = "zip"
  source_dir  = "${path.module}/build/investigation_agent"
  output_path = "${path.module}/build/investigation_agent.zip"
}

# ---------------------------------------------------------
# 2. AGENT ARTIFACT BUCKET
# ---------------------------------------------------------

resource "aws_s3_bucket" "investigation_agent_artifacts" {
  bucket = "${var.project_name}-${var.environment}-${var.aws_account_id}-${var.investigation_artifact_bucket_suffix}"

  tags = {
    Name = "${var.project_name}-${var.environment}-investigation-artifacts"
  }
}

# ---------------------------------------------------------
# 3. BLOCK PUBLIC ACCESS
# ---------------------------------------------------------

resource "aws_s3_bucket_public_access_block" "investigation_agent_artifacts" {
  bucket = aws_s3_bucket.investigation_agent_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------
# 4. BUCKET ENCRYPTION
# ---------------------------------------------------------

resource "aws_s3_bucket_server_side_encryption_configuration" "investigation_agent_artifacts" {
  bucket = aws_s3_bucket.investigation_agent_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.psc.arn
    }
  }
}

# ---------------------------------------------------------
# 5. UPLOAD DEPLOYMENT ARTIFACT
# ---------------------------------------------------------

resource "aws_s3_object" "investigation_agent_zip" {
  bucket = aws_s3_bucket.investigation_agent_artifacts.id
  key    = var.investigation_agent_artifact_key

  source      = data.archive_file.investigation_agent.output_path
  source_hash = data.archive_file.investigation_agent.output_base64sha256

  server_side_encryption = "aws:kms"
  kms_key_id             = aws_kms_key.psc.arn

  depends_on = [
    aws_s3_bucket_server_side_encryption_configuration.investigation_agent_artifacts,
    aws_s3_bucket_public_access_block.investigation_agent_artifacts
  ]
}

# ---------------------------------------------------------
# 6. AGENTCORE EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "investigation_agentcore" {
  name = "${var.project_name}-${var.environment}-${var.investigation_agent_role_suffix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "bedrock-agentcore.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project_name}-${var.environment}-investigation-agentcore"
  }
}

# ---------------------------------------------------------
# 7. AGENTCORE EXECUTION PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "investigation_agentcore" {
  name = "investigation-agentcore-execution"
  role = aws_iam_role.investigation_agentcore.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadInvestigationArtifact"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = [
          "${aws_s3_bucket.investigation_agent_artifacts.arn}/*"
        ]
      },
      {
        Sid    = "DecryptArtifact"
        Effect = "Allow"
        Action = [
          "kms:Decrypt"
        ]
        Resource = aws_kms_key.psc.arn
      },
      {
        Sid    = "CloudWatchLogging"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
          "logs:DescribeLogStreams"
        ]
        Resource = "*"
      },
      {
        Sid    = "InvokeInvestigationModel"
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
        Sid      = "CallToolsGateway"
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:InvokeGateway"]
        Resource = "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.aws_region}:${data.aws_caller_identity.current.account_id}:gateway/${aws_bedrockagentcore_gateway.investigation.gateway_id}"
      }
    ]
  })
}

# ---------------------------------------------------------
# 8. INVESTIGATION AGENTCORE RUNTIME
# ---------------------------------------------------------

resource "aws_bedrockagentcore_agent_runtime" "investigation" {
  agent_runtime_name = var.investigation_agent_runtime_name

  description = "Payment investigation runtime - enterprise integration pending"

  role_arn = aws_iam_role.investigation_agentcore.arn

  agent_runtime_artifact {
    code_configuration {
      entry_point = ["main.py"]
      runtime     = "PYTHON_3_12"

      code {
        s3 {
          bucket     = aws_s3_bucket.investigation_agent_artifacts.bucket
          prefix     = aws_s3_object.investigation_agent_zip.key
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

  # Runs agents/investigation/main.py (a Strands agent).
  # It calls the tools through the Gateway.
  environment_variables = {
    AWS_REGION       = var.aws_region
    BEDROCK_MODEL_ID = var.agent_model_id
    GATEWAY_URL      = aws_bedrockagentcore_gateway.investigation.gateway_url
  }

  depends_on = [
    aws_iam_role_policy.investigation_agentcore,
    aws_s3_object.investigation_agent_zip
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-investigation-agentcore"
  }
}

# ---------------------------------------------------------
# 9. OUTPUTS
# ---------------------------------------------------------

output "investigation_agent_runtime_arn" {
  description = "Investigation AgentCore Runtime ARN"
  value       = aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn
}

output "investigation_agent_runtime_id" {
  description = "Investigation AgentCore Runtime ID"
  value       = aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_id
}

