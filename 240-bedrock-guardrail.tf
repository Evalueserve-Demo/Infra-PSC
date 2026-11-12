
# =========================================================
# 240 - BEDROCK GUARDRAIL
# =========================================================

# ---------------------------------------------------------
# 1. BEDROCK GUARDRAIL
# ---------------------------------------------------------

resource "aws_bedrock_guardrail" "classification" {
  name = "${var.project_name}-${var.environment}-${var.classification_guardrail_name_suffix}"

  description = "Guardrail for payment email classification"

  blocked_input_messaging   = var.classification_guardrail_blocked_input_message
  blocked_outputs_messaging = var.classification_guardrail_blocked_output_message

  # -------------------------------------------------------
  # CONTENT FILTERS
  # -------------------------------------------------------

  content_policy_config {
    dynamic "filters_config" {
      for_each = var.classification_guardrail_content_filters

      content {
        type            = filters_config.value.type
        input_strength  = filters_config.value.input_strength
        output_strength = filters_config.value.output_strength
      }
    }
  }

  # -------------------------------------------------------
  # SENSITIVE INFORMATION FILTERS
  # -------------------------------------------------------

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = var.classification_guardrail_pii_entities

      content {
        type   = pii_entities_config.value.type
        action = pii_entities_config.value.action
      }
    }
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.classification_guardrail_name_suffix}"
  }
}

# ---------------------------------------------------------
# 2. NUMBERED GUARDRAIL VERSION
# ---------------------------------------------------------

resource "aws_bedrock_guardrail_version" "classification" {
  guardrail_arn = aws_bedrock_guardrail.classification.guardrail_arn

  description = "Payment email classification guardrail version"

  skip_destroy = false
}

# ---------------------------------------------------------
# 3. OUTPUTS FOR FUTURE INTEGRATION
# ---------------------------------------------------------

output "classification_guardrail_arn" {
  description = "ARN of classification guardrail"
  value       = aws_bedrock_guardrail.classification.guardrail_arn
}

output "classification_guardrail_version" {
  description = "Numbered classification guardrail version"
  value       = aws_bedrock_guardrail_version.classification.version
}

