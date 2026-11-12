
# =========================================================
# 100 - AWS SECRETS MANAGER
# =========================================================

# ---------------------------------------------------------
# 1. MICROSOFT GRAPH CREDENTIALS SECRET
# ---------------------------------------------------------

resource "aws_secretsmanager_secret" "microsoft_graph" {
  name        = "${var.project_name}/${var.environment}/microsoft-graph"
  description = "Microsoft Graph credentials for ${var.project_name} ${var.environment}"

  kms_key_id = aws_kms_key.psc.arn

  recovery_window_in_days = var.secrets_recovery_window_days

  tags = {
    Name = "${var.project_name}-microsoft-graph-${var.environment}"
  }
}

# ---------------------------------------------------------
# 2. DEDICATED APPLICATION DATABASE CREDENTIALS SECRET
# ---------------------------------------------------------

resource "aws_secretsmanager_secret" "psc_app_db" {
  name                    = "${var.project_name}-${var.environment}-app-db-credentials"
  description             = "Dedicated PostgreSQL application credentials for PSC RDS Proxy"
  kms_key_id              = aws_kms_key.psc.arn
  recovery_window_in_days = 7

  tags = {
    Name = "${var.project_name}-app-db-credentials-${var.environment}"
  }
}

