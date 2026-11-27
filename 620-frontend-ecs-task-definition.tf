# =========================================================
# 620 - FRONTEND ECS TASK DEFINITION
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/${local.frontend_name}"
  retention_in_days = 30
}

# ECS Task Assume Role Policy

data "aws_iam_policy_document" "frontend_ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ECS Execution Role

resource "aws_iam_role" "frontend_execution" {
  name = "${local.frontend_name}-execution-role"
  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

  assume_role_policy = data.aws_iam_policy_document.frontend_ecs_assume.json
}

# ECS Execution Role Permissions

resource "aws_iam_role_policy" "frontend_execution" {
  name = "${local.frontend_name}-execution-policy"
  role = aws_iam_role.frontend_execution.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage"
        ]
        Resource = aws_ecr_repository.frontend.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.frontend.arn}:*"
      }
    ]
  })
}

# ECS Application Task Role

resource "aws_iam_role" "frontend_task" {
  name = "${local.frontend_name}-task-role"
  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

  assume_role_policy = data.aws_iam_policy_document.frontend_ecs_assume.json
}

# ECS Fargate Task Definition

resource "aws_ecs_task_definition" "frontend" {
  family                   = local.frontend_name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]

  cpu    = "512"
  memory = "1024"

  execution_role_arn = aws_iam_role.frontend_execution.arn
  task_role_arn      = aws_iam_role.frontend_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "frontend"
      image     = "${aws_ecr_repository.frontend.repository_url}:v1"
      essential = true

      portMappings = [
        {
          containerPort = 3000
          hostPort      = 3000
          protocol      = "tcp"
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"

        options = {
          awslogs-group         = aws_cloudwatch_log_group.frontend.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "frontend"
        }
      }
    }
  ])

  depends_on = [
    aws_iam_role_policy.frontend_execution
  ]

  tags = {
    Name = local.frontend_name
  }
}

# Outputs

output "frontend_task_definition_arn" {
  value = aws_ecs_task_definition.frontend.arn
}

output "frontend_execution_role_arn" {
  value = aws_iam_role.frontend_execution.arn
}

output "frontend_task_role_arn" {
  value = aws_iam_role.frontend_task.arn
}