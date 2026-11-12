
# =========================================================
# 250 - BEDROCK MODEL CONFIGURATION
# =========================================================
# Configuration only.
# No managed AWS resources are created in this stage.
# Consumed by #260 Classification AgentCore Runtime.

locals {
  classification_model_config = {
    model_id          = var.classification_model_id
    region            = var.aws_region
    temperature       = var.classification_model_temperature
    max_output_tokens = var.classification_model_max_output_tokens
    top_p             = var.classification_model_top_p
  }
}

output "classification_model_id" {
  description = "Bedrock model or inference profile identifier for classification"
  value       = local.classification_model_config.model_id
}

