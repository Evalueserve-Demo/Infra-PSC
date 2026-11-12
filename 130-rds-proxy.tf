
# =========================================================
# 130 - AMAZON RDS PROXY
# =========================================================
# Deployment prerequisite:
# Populate the application secret with a JSON object containing
# "username" and "password", and create the matching PostgreSQL
# LOGIN role/password and least-privilege grants before creating the
# RDS Proxy. Terraform does NOT create the PostgreSQL role.
# Example secret value (do not commit credentials):
# {"username":"psc_ingestion","password":"<secure-password>"}

# ---------------------------------------------------------
# 1. RDS PROXY
# ---------------------------------------------------------

resource "aws_db_proxy" "psc" {
  name = "${var.project_name}-${var.environment}-rds-proxy"

  engine_family = "POSTGRESQL"

  role_arn = aws_iam_role.rds_proxy.arn

  vpc_subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id
  ]

  vpc_security_group_ids = [
    aws_security_group.rds_proxy.id
  ]

  require_tls = true

  idle_client_timeout = var.rds_proxy_idle_client_timeout

  debug_logging = var.rds_proxy_debug_logging

  auth {
    auth_scheme = "SECRETS"
    iam_auth    = "REQUIRED"

    secret_arn = aws_secretsmanager_secret.psc_app_db.arn
  }

  depends_on = [
    aws_iam_role_policy.rds_proxy
  ]

  tags = {
    Name = "${var.project_name}-rds-proxy-${var.environment}"
  }
}

# ---------------------------------------------------------
# 2. DEFAULT TARGET GROUP
# ---------------------------------------------------------

resource "aws_db_proxy_default_target_group" "psc" {
  db_proxy_name = aws_db_proxy.psc.name

  connection_pool_config {
    max_connections_percent      = var.rds_proxy_max_connections_percent
    max_idle_connections_percent = var.rds_proxy_max_idle_connections_percent
    connection_borrow_timeout    = var.rds_proxy_connection_borrow_timeout
  }
}

# ---------------------------------------------------------
# 3. ATTACH AURORA CLUSTER TO PROXY
# ---------------------------------------------------------

resource "aws_db_proxy_target" "psc" {
  db_proxy_name         = aws_db_proxy.psc.name
  target_group_name     = aws_db_proxy_default_target_group.psc.name
  db_cluster_identifier = aws_rds_cluster.psc.cluster_identifier

  depends_on = [
    aws_rds_cluster_instance.writer,
    aws_rds_cluster_instance.reader
  ]
}

# ---------------------------------------------------------
# 4. READ-ONLY PROXY ENDPOINT
# ---------------------------------------------------------

resource "aws_db_proxy_endpoint" "reader" {
  db_proxy_name = aws_db_proxy.psc.name

  db_proxy_endpoint_name = "${var.project_name}-${var.environment}-reader-proxy"

  vpc_subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id
  ]

  vpc_security_group_ids = [
    aws_security_group.rds_proxy.id
  ]

  target_role = "READ_ONLY"

  tags = {
    Name = "${var.project_name}-reader-proxy-${var.environment}"
  }
}
