
# =========================================================
# 060 - ROUTE TABLES
# Project: Boeing Helpdesk Payment Status Check
# =========================================================


# ---------------------------------------------------------
# 1. PUBLIC ROUTE TABLE
# ---------------------------------------------------------

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-public-rt-${var.environment}"
  }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.psc.id
}

resource "aws_route_table_association" "public_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "public_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}


# ---------------------------------------------------------
# 2. PRIVATE ROUTE TABLE A
# ---------------------------------------------------------

resource "aws_route_table" "private_a" {
  vpc_id = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-private-a-rt-${var.environment}"
  }
}

resource "aws_route" "private_a_internet" {
  route_table_id         = aws_route_table.private_a.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.psc.id
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private_a.id
}


# ---------------------------------------------------------
# 3. PRIVATE ROUTE TABLE B
# ---------------------------------------------------------

resource "aws_route_table" "private_b" {
  vpc_id = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-private-b-rt-${var.environment}"
  }
}

resource "aws_route" "private_b_internet" {
  route_table_id         = aws_route_table.private_b.id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.psc.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private_b.id
}


# ---------------------------------------------------------
# 4. DATABASE ROUTE TABLE
# ---------------------------------------------------------

resource "aws_route_table" "database" {
  vpc_id = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-database-rt-${var.environment}"
  }
}

resource "aws_route_table_association" "database_a" {
  subnet_id      = aws_subnet.database_a.id
  route_table_id = aws_route_table.database.id
}

resource "aws_route_table_association" "database_b" {
  subnet_id      = aws_subnet.database_b.id
  route_table_id = aws_route_table.database.id
}
