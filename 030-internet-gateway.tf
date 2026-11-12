
# =========================================================
# 030 - INTERNET GATEWAY
# =========================================================

resource "aws_internet_gateway" "psc" {
  vpc_id = aws_vpc.psc.id

  tags = {
    Name = "${var.project_name}-igw-${var.environment}"
  }
}

