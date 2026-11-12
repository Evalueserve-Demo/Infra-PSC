
# =========================================================
# 020 - SUBNETS
# Project: Boeing Helpdesk Payment Status Check
# Environment and Region: Configured through Terraform
# =========================================================


# ---------------------------------------------------------
# 1. PUBLIC SUBNET A
# ---------------------------------------------------------

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.public_subnet_a_cidr
  availability_zone       = var.availability_zone_a
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-public-a-${var.environment}"
  }
}


# ---------------------------------------------------------
# 2. PUBLIC SUBNET B
# ---------------------------------------------------------

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.public_subnet_b_cidr
  availability_zone       = var.availability_zone_b
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-public-b-${var.environment}"
  }
}


# ---------------------------------------------------------
# 3. PRIVATE APPLICATION SUBNET A
# ---------------------------------------------------------

resource "aws_subnet" "private_a" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.private_subnet_a_cidr
  availability_zone       = var.availability_zone_a
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-private-a-${var.environment}"
  }
}


# ---------------------------------------------------------
# 4. PRIVATE APPLICATION SUBNET B
# ---------------------------------------------------------

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.private_subnet_b_cidr
  availability_zone       = var.availability_zone_b
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-private-b-${var.environment}"
  }
}


# ---------------------------------------------------------
# 5. PRIVATE DATABASE SUBNET A
# ---------------------------------------------------------

resource "aws_subnet" "database_a" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.database_subnet_a_cidr
  availability_zone       = var.availability_zone_a
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-database-a-${var.environment}"
  }
}


# ---------------------------------------------------------
# 6. PRIVATE DATABASE SUBNET B
# ---------------------------------------------------------

resource "aws_subnet" "database_b" {
  vpc_id                  = aws_vpc.psc.id
  cidr_block              = var.database_subnet_b_cidr
  availability_zone       = var.availability_zone_b
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.project_name}-database-b-${var.environment}"
  }
}
