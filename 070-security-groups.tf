
# =========================================================
# 070 - SECURITY GROUPS
# Project: Boeing Helpdesk Payment Status Check
# Environment: DEV
# =========================================================


# ---------------------------------------------------------
# 1. LAMBDA SECURITY GROUP
# ---------------------------------------------------------

resource "aws_security_group" "lambda" {
  name        = "${var.project_name}-lambda-sg-${var.environment}"
  description = "Security group for VPC Lambda functions"
  vpc_id      = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-lambda-sg-${var.environment}"
  }
}


# Lambda outbound HTTPS - Port 443

resource "aws_vpc_security_group_egress_rule" "lambda_https" {
  security_group_id = aws_security_group.lambda.id

  description = "Allow outbound HTTPS"
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"
}


# Lambda outbound PostgreSQL - Port 5432

resource "aws_vpc_security_group_egress_rule" "lambda_postgres" {
  security_group_id = aws_security_group.lambda.id

  description                  = "Allow PostgreSQL to RDS Proxy"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds_proxy.id
}


# ---------------------------------------------------------
# 2. RDS PROXY SECURITY GROUP
# ---------------------------------------------------------

resource "aws_security_group" "rds_proxy" {
  name        = "${var.project_name}-rds-proxy-sg-${var.environment}"
  description = "Security group for RDS Proxy"
  vpc_id      = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-rds-proxy-sg-${var.environment}"
  }
}


# RDS Proxy inbound PostgreSQL from Lambda

resource "aws_vpc_security_group_ingress_rule" "rds_proxy_from_lambda" {
  security_group_id = aws_security_group.rds_proxy.id

  description                  = "Allow PostgreSQL from Lambda"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.lambda.id
}


# RDS Proxy outbound PostgreSQL to Aurora

resource "aws_vpc_security_group_egress_rule" "rds_proxy_to_aurora" {
  security_group_id = aws_security_group.rds_proxy.id

  description                  = "Allow PostgreSQL to Aurora"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.aurora.id
}


# ---------------------------------------------------------
# 3. AURORA SECURITY GROUP
# ---------------------------------------------------------

resource "aws_security_group" "aurora" {
  name        = "${var.project_name}-aurora-sg-${var.environment}"
  description = "Security group for Aurora PostgreSQL"
  vpc_id      = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-aurora-sg-${var.environment}"
  }
}


# Aurora inbound PostgreSQL from RDS Proxy

resource "aws_vpc_security_group_ingress_rule" "aurora_from_rds_proxy" {
  security_group_id = aws_security_group.aurora.id

  description                  = "Allow PostgreSQL from RDS Proxy"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds_proxy.id
}


# ---------------------------------------------------------
# 4. VPC ENDPOINT SECURITY GROUP
# ---------------------------------------------------------

resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.project_name}-vpce-sg-${var.environment}"
  description = "Security group for VPC Interface Endpoints"
  vpc_id      = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-vpce-sg-${var.environment}"
  }
}


# Allow inbound HTTPS from Lambda Security Group

resource "aws_vpc_security_group_ingress_rule" "vpce_from_lambda" {
  security_group_id = aws_security_group.vpc_endpoints.id

  description                  = "Allow HTTPS from Lambda"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.lambda.id
}

# ---------------------------------------------------------
# Allow PostgreSQL Client EC2 to access VPC Interface Endpoints
# ---------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "vpce_from_ec2_db_client" {
  security_group_id = aws_security_group.vpc_endpoints.id

  description                  = "Allow HTTPS from PostgreSQL client EC2"
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  referenced_security_group_id = aws_security_group.ec2_db_client.id
}