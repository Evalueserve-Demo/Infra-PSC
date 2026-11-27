# =========================================================
# 630 - FRONTEND ECS SERVICE
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

# Frontend Security Group

resource "aws_security_group" "frontend" {
  name        = "${local.frontend_name}-sg"
  description = "Security group for PSC frontend ECS"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${local.frontend_name}-sg"
  }
}

# Frontend HTTPS Outbound Rule

resource "aws_vpc_security_group_egress_rule" "frontend_https" {
  security_group_id = aws_security_group.frontend.id

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
  cidr_ipv4   = "0.0.0.0/0"
}

# Frontend ECS Service

resource "aws_ecs_service" "frontend" {
  name            = "${local.frontend_name}-service"
  cluster         = aws_ecs_cluster.frontend.id
  task_definition = aws_ecs_task_definition.frontend.arn

  launch_type   = "FARGATE"
  desired_count = 0

  network_configuration {
    subnets          = var.existing_subnet_ids
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = false
  }

  tags = {
    Name = "${local.frontend_name}-service"
  }
}

# Outputs

output "frontend_ecs_service_name" {
  value = aws_ecs_service.frontend.name
}

output "frontend_security_group_id" {
  value = aws_security_group.frontend.id
}