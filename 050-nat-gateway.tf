
# =========================================================
# 050 - NAT GATEWAY
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

resource "aws_nat_gateway" "psc" {
  allocation_id = aws_eip.nat.id

  subnet_id = aws_subnet.public_a.id

  connectivity_type = "public"

  depends_on = [
    aws_internet_gateway.psc
  ]

  tags = {
    Name = "${var.project_name}-nat-${var.environment}"
  }
}
