
# =========================================================
# 040 - ELASTIC IP
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "${var.project_name}-nat-eip-${var.environment}"
  }
}
