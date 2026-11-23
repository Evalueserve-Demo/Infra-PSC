
# =========================================================
# 550 - EC2 SSM POSTGRESQL CLIENT
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

# ---------------------------------------------------------
# 1. AMAZON LINUX 2023 AMI
# ---------------------------------------------------------

data "aws_ssm_parameter" "ec2_al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# ---------------------------------------------------------
# 2. EC2 IAM TRUST POLICY
# ---------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------
# 3. EC2 IAM ROLE - BOEING REQUIREMENTS
# ---------------------------------------------------------

resource "aws_iam_role" "ec2_ssm" {
  name = "${var.project_name}-ec2-ssm-role-${var.environment}"

  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json

  tags = {
    Name = "${var.project_name}-ec2-ssm-role-${var.environment}"
  }
}

# ---------------------------------------------------------
# 4. SSM MANAGED INSTANCE POLICY
# ---------------------------------------------------------

resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2_ssm.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ---------------------------------------------------------
# 5. EC2 INSTANCE PROFILE
# ---------------------------------------------------------

resource "aws_iam_instance_profile" "ec2_ssm" {
  name = "${var.project_name}-ec2-ssm-profile-${var.environment}"
  role = aws_iam_role.ec2_ssm.name
}

# ---------------------------------------------------------
# 6. EC2 SECURITY GROUP - EXISTING BOEING VPC
# ---------------------------------------------------------

resource "aws_security_group" "ec2_db_client" {
  name        = "${var.project_name}-ec2-db-client-sg-${var.environment}"
  description = "EC2 PostgreSQL administration client"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-ec2-db-client-sg-${var.environment}"
  }
}

# ---------------------------------------------------------
# 7. OUTBOUND HTTPS
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_https" {
  security_group_id = aws_security_group.ec2_db_client.id
  description       = "Outbound HTTPS for SSM and package repositories"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------------------------------------------------------
# 8. EC2 TO RDS PROXY
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_to_rds_proxy" {
  security_group_id            = aws_security_group.ec2_db_client.id
  description                  = "PostgreSQL to RDS Proxy"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds_proxy.id
}

resource "aws_vpc_security_group_ingress_rule" "rds_proxy_from_ec2" {
  security_group_id            = aws_security_group.rds_proxy.id
  description                  = "PostgreSQL from EC2 administration client"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ec2_db_client.id
}

# ---------------------------------------------------------
# 9. EC2 IAM AUTHENTICATION TO RDS PROXY
# ---------------------------------------------------------

resource "aws_iam_role_policy" "ec2_rds_proxy_connect" {
  name = "${var.project_name}-ec2-rds-proxy-connect-${var.environment}"
  role = aws_iam_role.ec2_ssm.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "AllowRdsProxyIamAuthentication"
        Effect = "Allow"

        Action = [
          "rds-db:connect"
        ]

        Resource = "arn:${data.aws_partition.current.partition}:rds-db:${var.region}:${data.aws_caller_identity.current.account_id}:dbuser:${element(split(":", aws_db_proxy.psc.arn), 6)}/${var.email_ingestion_db_user}"
      }
    ]
  })
}

# ---------------------------------------------------------
# 10. TEMPORARY DIRECT AURORA ACCESS
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_to_aurora_admin" {
  security_group_id            = aws_security_group.ec2_db_client.id
  description                  = "Temporary PostgreSQL administration access to Aurora"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.aurora.id
}

resource "aws_vpc_security_group_ingress_rule" "aurora_from_ec2_admin" {
  security_group_id            = aws_security_group.aurora.id
  description                  = "Temporary PostgreSQL administration access from EC2"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ec2_db_client.id
}

# ---------------------------------------------------------
# 11. TEMPORARY AURORA MASTER SECRET ACCESS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "ec2_aurora_master_secret_access" {
  name = "${var.project_name}-ec2-aurora-master-secret-${var.environment}"
  role = aws_iam_role.ec2_ssm.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "AllowAuroraMasterSecretRead"
        Effect = "Allow"

        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue"
        ]

        Resource = aws_rds_cluster.psc.master_user_secret[0].secret_arn
      },
      {
        Sid    = "AllowAuroraMasterSecretDecrypt"
        Effect = "Allow"

        Action = [
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn

        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.region}.${data.aws_partition.current.dns_suffix}"
          }
        }
      }
    ]
  })
}

# ---------------------------------------------------------
# 12. PRIVATE EC2 INSTANCE
# ---------------------------------------------------------

resource "aws_instance" "postgres_client" {
  ami           = data.aws_ssm_parameter.ec2_al2023_ami.value
  instance_type = var.ec2_postgres_instance_type

  subnet_id = var.existing_subnet_ids[0]

  vpc_security_group_ids = [
    aws_security_group.ec2_db_client.id
  ]

  iam_instance_profile        = aws_iam_instance_profile.ec2_ssm.name
  associate_public_ip_address = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  user_data = <<-EOF
    #!/bin/bash
    set -euxo pipefail
    dnf install -y postgresql15
  EOF

  tags = {
    Name = "${var.project_name}-postgres-client-${var.environment}"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ec2_ssm_core,
    aws_iam_role_policy.ec2_rds_proxy_connect,
    aws_iam_role_policy.ec2_aurora_master_secret_access,
    aws_vpc_security_group_egress_rule.ec2_https,
    aws_vpc_security_group_egress_rule.ec2_to_rds_proxy,
    aws_vpc_security_group_ingress_rule.rds_proxy_from_ec2,
    aws_vpc_security_group_egress_rule.ec2_to_aurora_admin,
    aws_vpc_security_group_ingress_rule.aurora_from_ec2_admin
  ]
}

# =========================================================
# END OF 550 - EC2 SSM POSTGRESQL CLIENT
# =========================================================
