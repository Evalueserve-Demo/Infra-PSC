
# =========================================================
# 340 - AGENTCORE POLICY ENGINE
# =========================================================

# ---------------------------------------------------------
# 1. INVESTIGATION POLICY ENGINE
# ---------------------------------------------------------

resource "aws_bedrockagentcore_policy_engine" "investigation" {
  name = var.investigation_policy_engine_name

  description = "Authorization policy engine for payment investigation gateway tools"

  encryption_key_arn = aws_kms_key.psc.arn

  tags = {
    Name        = var.investigation_policy_engine_name
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 2. OUTPUTS
# ---------------------------------------------------------

output "investigation_policy_engine_arn" {
  description = "Investigation AgentCore Policy Engine ARN"
  value       = aws_bedrockagentcore_policy_engine.investigation.policy_engine_arn
}

output "investigation_policy_engine_id" {
  description = "Investigation AgentCore Policy Engine ID"
  value       = aws_bedrockagentcore_policy_engine.investigation.policy_engine_id
}

