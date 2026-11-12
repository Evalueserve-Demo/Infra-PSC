
# =========================================================
# 010 - VPC
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

resource "aws_vpc" "psc" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.project_name}-vpc-${var.environment}"
  }
}
