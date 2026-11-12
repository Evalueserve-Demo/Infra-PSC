# =========================================================
# 360 - AGENTCORE GATEWAY
#
# Investigation Agent MCP Gateway
#
# Targets:
# 1. SAP S/4HANA
# 2. SPVR
# 3. Payment Rules
# 4. Root Cause Map
# 5. Draft Builder
#
# NOTE:
# New tool schemas are PROVISIONAL.
# Confirm with developers before deployment.
# =========================================================

# ---------------------------------------------------------
# 1. GATEWAY TRUST POLICY
# ---------------------------------------------------------

data "aws_iam_policy_document" "investigation_gateway_assume" {
  statement {
    sid     = "AllowAgentCoreAssumeRole"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["bedrock-agentcore.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

# ---------------------------------------------------------
# 2. GATEWAY EXECUTION ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "investigation_gateway" {
  name = "${var.project_name}-${var.environment}-gateway-role"

  assume_role_policy = data.aws_iam_policy_document.investigation_gateway_assume.json

  tags = {
    Name        = "${var.project_name}-${var.environment}-gateway-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

# ---------------------------------------------------------
# 3. GATEWAY IAM PERMISSIONS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "investigation_gateway" {
  name = "${var.project_name}-${var.environment}-gateway-access"
  role = aws_iam_role.investigation_gateway.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "InvokeInvestigationToolLambdas"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = [
          aws_lambda_function.s4hana_tool.arn,
          aws_lambda_function.spvr_tool.arn,
          aws_lambda_function.payment_rules_tool.arn,
          aws_lambda_function.root_cause_map_tool.arn,
          aws_lambda_function.draft_builder_tool.arn
        ]
      },
      {
        Sid    = "EvaluateInvestigationPolicy"
        Effect = "Allow"

        Action = [
          "bedrock-agentcore:GetPolicyEngine"
        ]

        Resource = aws_bedrockagentcore_policy_engine.investigation.policy_engine_arn
      },
      {
        Sid    = "AuthorizeInvestigationActions"
        Effect = "Allow"

        Action = [
          "bedrock-agentcore:AuthorizeAction",
          "bedrock-agentcore:PartiallyAuthorizeActions"
        ]

        Resource = [
          aws_bedrockagentcore_policy_engine.investigation.policy_engine_arn,
          "arn:${data.aws_partition.current.partition}:bedrock-agentcore:${var.aws_region}:${data.aws_caller_identity.current.account_id}:gateway/${var.investigation_gateway_name}-*"
        ]
      },
      {
        Sid    = "DecryptInvestigationPolicyEngine"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ---------------------------------------------------------
# 4. MCP GATEWAY
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway" "investigation" {
  name        = var.investigation_gateway_name
  description = "Investigation MCP Gateway for payment status investigation tools"

  role_arn = aws_iam_role.investigation_gateway.arn

  authorizer_type = "AWS_IAM"
  protocol_type   = "MCP"

  policy_engine_configuration {
    arn  = aws_bedrockagentcore_policy_engine.investigation.policy_engine_arn
    mode = var.investigation_gateway_policy_mode
  }

  tags = {
    Name        = var.investigation_gateway_name
    Environment = var.environment
    Project     = var.project_name
  }

  depends_on = [
    aws_iam_role_policy.investigation_gateway
  ]
}

# ---------------------------------------------------------
# 5. SAP S/4HANA MCP TARGET
# Existing schema preserved.
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "s4hana" {
  name               = var.s4hana_gateway_target_name
  gateway_identifier = aws_bedrockagentcore_gateway.investigation.gateway_id

  description = "Read-only SAP S4HANA payment status investigation tool"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.s4hana_tool.arn

        tool_schema {
          inline_payload {
            name        = "get_payment_status"
            description = "Retrieve invoice payment status from SAP S4HANA"

            input_schema {
              type = "object"

              property {
                name        = "case_id"
                type        = "string"
                description = "Payment investigation case ID"
                required    = true
              }

              property {
                name        = "message_id"
                type        = "string"
                description = "Supplier message ID"
                required    = true
              }

              property {
                name        = "invoice_id"
                type        = "string"
                description = "Invoice identifier"
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------
# 6. SPVR MCP TARGET
# PROVISIONAL INPUT SCHEMA
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "spvr" {
  name               = "${var.project_name}-${var.environment}-spvr-target"
  gateway_identifier = aws_bedrockagentcore_gateway.investigation.gateway_id

  description = "SPVR investigation tool"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.spvr_tool.arn

        tool_schema {
          inline_payload {
            name        = "get_spvr_details"
            description = "Retrieve SPVR information for payment investigation"

            input_schema {
              type = "object"

              property {
                name        = "case_id"
                type        = "string"
                description = "Payment investigation case ID"
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------
# 7. PAYMENT RULES MCP TARGET
# PROVISIONAL INPUT SCHEMA
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "payment_rules" {
  name               = "${var.project_name}-${var.environment}-payment-rules-target"
  gateway_identifier = aws_bedrockagentcore_gateway.investigation.gateway_id

  description = "Payment Rules investigation tool"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.payment_rules_tool.arn

        tool_schema {
          inline_payload {
            name        = "evaluate_payment_rules"
            description = "Evaluate payment rules for an investigation case"

            input_schema {
              type = "object"

              property {
                name        = "case_id"
                type        = "string"
                description = "Payment investigation case ID"
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------
# 8. ROOT CAUSE MAP MCP TARGET
# PROVISIONAL INPUT SCHEMA
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "root_cause_map" {
  name               = "${var.project_name}-${var.environment}-root-cause-map-target"
  gateway_identifier = aws_bedrockagentcore_gateway.investigation.gateway_id

  description = "Root Cause Map investigation tool"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.root_cause_map_tool.arn

        tool_schema {
          inline_payload {
            name        = "map_payment_root_cause"
            description = "Map potential root causes for a payment investigation"

            input_schema {
              type = "object"

              property {
                name        = "case_id"
                type        = "string"
                description = "Payment investigation case ID"
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------
# 9. DRAFT BUILDER MCP TARGET
# PROVISIONAL INPUT SCHEMA
# ---------------------------------------------------------

resource "aws_bedrockagentcore_gateway_target" "draft_builder" {
  name               = "${var.project_name}-${var.environment}-draft-builder-target"
  gateway_identifier = aws_bedrockagentcore_gateway.investigation.gateway_id

  description = "Draft Builder investigation tool"

  credential_provider_configuration {
    gateway_iam_role {}
  }

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.draft_builder_tool.arn

        tool_schema {
          inline_payload {
            name        = "build_payment_response_draft"
            description = "Build a proposed payment status response draft"

            input_schema {
              type = "object"

              property {
                name        = "case_id"
                type        = "string"
                description = "Payment investigation case ID"
                required    = true
              }
            }
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------
# 10. OUTPUTS
# ---------------------------------------------------------

output "investigation_gateway_id" {
  description = "Investigation Gateway ID"
  value       = aws_bedrockagentcore_gateway.investigation.gateway_id
}

output "investigation_gateway_url" {
  description = "Investigation MCP Gateway URL"
  value       = aws_bedrockagentcore_gateway.investigation.gateway_url
}

output "s4hana_gateway_target_id" {
  description = "SAP S4HANA Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.s4hana.target_id
}

output "spvr_gateway_target_id" {
  description = "SPVR Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.spvr.target_id
}

output "payment_rules_gateway_target_id" {
  description = "Payment Rules Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.payment_rules.target_id
}

output "root_cause_map_gateway_target_id" {
  description = "Root Cause Map Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.root_cause_map.target_id
}

output "draft_builder_gateway_target_id" {
  description = "Draft Builder Gateway Target ID"
  value       = aws_bedrockagentcore_gateway_target.draft_builder.target_id
}