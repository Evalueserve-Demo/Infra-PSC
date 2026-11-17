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
# The tool names and schemas follow the tools in the application code.
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
            name        = "s4_get_invoice_reconciliation"
            description = "Check whether the invoice is in S/4HANA and whether it is reconciled. Returns the vendor code, company code and purchasing organisation, which the SPVR check needs later. Needs both the purchase order ID and the supplier invoice ID. If found is false the invoice is not in S/4HANA, so stop and treat it as not found."

            input_schema {
              type = "object"

              property {
                name        = "po_id"
                type        = "string"
                description = "The purchase order ID, for example 4500987654."
                required    = true
              }

              property {
                name        = "supplier_invoice_id"
                type        = "string"
                description = "The invoice ID exactly as the supplier wrote it, for example INV-55678."
                required    = true
              }
            }
          }

          inline_payload {
            name        = "s4_get_payment_status"
            description = "Read the payment status of an invoice in S/4HANA. Branch on what it returns: found false means the invoice is not in S/4HANA at all; payment_status is Paid, Scheduled, Sending or Rejected. When Paid it also returns the payment transaction ID and its details, to be shared with the supplier. It returns due_date_passed and days_overdue, computed against today's date, which is given as as_of. Use due_date_passed rather than comparing dates yourself; null means the due date was missing or unreadable."

            input_schema {
              type = "object"

              property {
                name        = "po_id"
                type        = "string"
                description = "The purchase order ID, for example 4500987654."
                required    = true
              }

              property {
                name        = "supplier_invoice_id"
                type        = "string"
                description = "The invoice ID exactly as the supplier wrote it, for example INV-55678."
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
  # Kept short on purpose. The gateway names each tool "<target>___<tool>"
  # and Bedrock rejects tool names over 64 characters.
  name               = "spvr-target"
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
            name        = "spvr_get_supplier_block"
            description = "Check the supplier in the SPVR supplier register. This is the supplier check, and it runs only when an invoice is overdue and unpaid. Pass the vendor, company code and purchasing organisation exactly as the S/4HANA reconciliation lookup returned them; the best code is worked out for you. If in_spvr is false the supplier is not registered, so stop the supplier checks and draft a helpdesk mail saying so. If blocked is true the supplier is on hold: report the entries in active_blocks as the reason the invoice has not been paid. If blocked is false the overdue invoice has no supported automated explanation, so draft a helpdesk mail."

            input_schema {
              type = "object"

              property {
                name        = "vendor"
                type        = "string"
                description = "The vendor code from s4_get_invoice_reconciliation, for example 001234."
                required    = true
              }

              property {
                name        = "company_code"
                type        = "string"
                description = "The company code from s4_get_invoice_reconciliation."
                required    = true
              }

              property {
                name        = "purchase_organisation"
                type        = "string"
                description = "The purchasing organisation from s4_get_invoice_reconciliation."
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
  name               = "payment-rules-target"
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
            name        = "validate_invoice_id"
            description = "Check whether a supplier invoice ID follows the Boeing naming convention before any system lookup. Always call this first. If it returns valid=false, stop the investigation and draft a helpdesk mail stating the invoice ID convention is incorrect."

            input_schema {
              type = "object"

              property {
                name        = "supplier_invoice_id"
                type        = "string"
                description = "The supplier invoice reference taken from the query, for example INV-55678."
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
  name               = "root-cause-map-target"
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
            name        = "route_helpdesk"
            description = "Decide which helpdesk a drafted reply should go to. ITAR controlled items route to the US Helpdesk, everything else to TCS. Call this before concluding, passing the ITAR flag exactly as the query gave it, and state clearly when the ITAR flag is unknown."

            input_schema {
              type = "object"

              property {
                name        = "itar_flag"
                type        = "string"
                description = "The ITAR flag from the query. Use Yes or No. Omit when the query did not give one."
                required    = false
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
  name               = "draft-builder-target"
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
            name        = "build_draft_reply"
            description = "Compose the supplier reply from the confirmed findings. Use this rather than writing the reply yourself, so that export-control wording is applied consistently. Pass itar_controlled exactly as the query gave it. For a paid invoice, also pass the transaction ID and transaction details from s4_get_payment_status so the supplier receives them."

            input_schema {
              type = "object"

              property {
                name        = "query_summary"
                type        = "string"
                description = "One line describing what the supplier asked, e.g. invoice INV-55678."
                required    = true
              }

              property {
                name        = "root_cause"
                type        = "string"
                description = "The confirmed finding in plain language."
                required    = true
              }

              property {
                name        = "next_action"
                type        = "string"
                description = "What happens next or what is needed."
                required    = true
              }

              property {
                name        = "itar_controlled"
                type        = "string"
                description = "Yes or No, as the query gave it. Omit when it gave none."
                required    = false
              }

              property {
                name        = "transaction_id"
                type        = "string"
                description = "The payment transaction ID, for a paid invoice."
                required    = false
              }

              property {
                name        = "transaction_details"
                type        = "object"
                description = "The transaction details exactly as s4_get_payment_status returned them, for a paid invoice."
                required    = false
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