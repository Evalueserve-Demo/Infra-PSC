# =========================================================
# 610 - FRONTEND ECS CLUSTER
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

resource "aws_ecs_cluster" "frontend" {
  name = "${local.frontend_name}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name = "${local.frontend_name}-cluster"
  }
}

output "frontend_ecs_cluster_name" {
  value = aws_ecs_cluster.frontend.name
}

output "frontend_ecs_cluster_arn" {
  value = aws_ecs_cluster.frontend.arn
}