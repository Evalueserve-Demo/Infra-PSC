
# =========================================================
# 350 - AGENTCORE MEMORY
# =========================================================

# ---------------------------------------------------------
# 1. INVESTIGATION AGENTCORE MEMORY
# ---------------------------------------------------------

resource "aws_bedrockagentcore_memory" "investigation" {
  name = var.investigation_memory_name

  description = "Session memory for payment investigation agent"

  event_expiry_duration = var.investigation_memory_retention_days

  encryption_key_arn = aws_kms_key.psc.arn

  tags = {
    Name        = var.investigation_memory_name
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 2. OUTPUTS
# ---------------------------------------------------------

output "investigation_memory_arn" {
  description = "Investigation AgentCore Memory ARN"
  value       = aws_bedrockagentcore_memory.investigation.arn
}

output "investigation_memory_id" {
  description = "Investigation AgentCore Memory ID"
  value       = aws_bedrockagentcore_memory.investigation.id
}

