# =========================================================
# 600 - FRONTEND ECR REPOSITORY
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

locals {
  frontend_name = "${var.project_name}-${var.environment}-frontend"
}

resource "aws_ecr_repository" "frontend" {
  name                 = local.frontend_name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.psc.arn
  }

  tags = {
    Name = local.frontend_name
  }
}

output "frontend_ecr_repository_url" {
  value = aws_ecr_repository.frontend.repository_url
}