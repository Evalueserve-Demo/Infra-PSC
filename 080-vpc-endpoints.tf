
# =========================================================
# 080 - VPC ENDPOINTS
# Production-Ready Regional Configuration
# =========================================================



# ---------------------------------------------------------
# 1. S3 GATEWAY ENDPOINT
# ---------------------------------------------------------

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.psc.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [
    aws_route_table.private_a.id,
    aws_route_table.private_b.id
  ]

  tags = {
    Name = "${var.project_name}-s3-vpce-${var.environment}"
  }
}


# ---------------------------------------------------------
# 2. SECRETS MANAGER INTERFACE ENDPOINT
# ---------------------------------------------------------

resource "aws_vpc_endpoint" "secrets_manager" {
  vpc_id              = aws_vpc.psc.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id
  ]

  security_group_ids = [
    aws_security_group.vpc_endpoints.id
  ]

  tags = {
    Name = "${var.project_name}-secretsmanager-vpce-${var.environment}"
  }
}


# ---------------------------------------------------------
# 3. KMS INTERFACE ENDPOINT
# ---------------------------------------------------------

resource "aws_vpc_endpoint" "kms" {
  vpc_id              = aws_vpc.psc.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.kms"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id
  ]

  security_group_ids = [
    aws_security_group.vpc_endpoints.id
  ]

  tags = {
    Name = "${var.project_name}-kms-vpce-${var.environment}"
  }
}


# ---------------------------------------------------------
# 4. STEP FUNCTIONS INTERFACE ENDPOINT
# ---------------------------------------------------------

resource "aws_vpc_endpoint" "step_functions" {
  vpc_id              = aws_vpc.psc.id
  service_name        = "com.amazonaws.${data.aws_region.current.region}.states"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true

  subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id
  ]

  security_group_ids = [
    aws_security_group.vpc_endpoints.id
  ]

  tags = {
    Name = "${var.project_name}-states-vpce-${var.environment}"
  }
}
