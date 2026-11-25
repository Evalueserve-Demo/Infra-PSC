# =========================================================
# 560 - EC2 SSH POSTGRESQL CLIENT
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

# ---------------------------------------------------------
# 1. BOEING APPROVED RHEL 9 AMI
# ---------------------------------------------------------

data "aws_ami" "ssh_ecs_baseline" {
  most_recent = true
  owners      = ["304766873718"]

  filter {
    name   = "name"
    values = ["ecs-baseline-linux_rhel-9*"]
  }
}

# ---------------------------------------------------------
# 2. SSH KEY PAIR
# ---------------------------------------------------------

resource "aws_key_pair" "postgres_ssh" {
  key_name   = "${var.project_name}-postgres-ssh-key-${var.environment}"
  public_key = var.ec2_ssh_public_key

  tags = {
    Name = "${var.project_name}-postgres-ssh-key-${var.environment}"
  }
}

# ---------------------------------------------------------
# 3. EC2 IAM TRUST POLICY
# ---------------------------------------------------------

data "aws_iam_policy_document" "ec2_ssh_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------
# 4. EC2 IAM ROLE
# ---------------------------------------------------------

resource "aws_iam_role" "ec2_ssh" {
  name = "${var.project_name}-ec2-ssh-role-${var.environment}"
  path = "/tenant_iac_bound/"

  permissions_boundary = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:policy/TenantIaCDeployerBoundaryPolicy"

  assume_role_policy = data.aws_iam_policy_document.ec2_ssh_assume_role.json

  tags = {
    Name = "${var.project_name}-ec2-ssh-role-${var.environment}"
  }
}

# ---------------------------------------------------------
# 5. EC2 INSTANCE PROFILE
# ---------------------------------------------------------

resource "aws_iam_instance_profile" "ec2_ssh" {
  name = "${var.project_name}-ec2-ssh-profile-${var.environment}"
  path = "/tenant_iac_bound/"
  role = aws_iam_role.ec2_ssh.name
}

# ---------------------------------------------------------
# 6. EC2 SECURITY GROUP
# ---------------------------------------------------------

resource "aws_security_group" "ec2_ssh_db_client" {
  name        = "${var.project_name}-ec2-ssh-db-client-sg-${var.environment}"
  description = "Private EC2 SSH PostgreSQL administration client"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-ec2-ssh-db-client-sg-${var.environment}"
  }
}

# ---------------------------------------------------------
# 7. SSH INBOUND ACCESS
# ---------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "ec2_ssh_ingress" {
  security_group_id = aws_security_group.ec2_ssh_db_client.id
  description       = "SSH from approved Boeing corporate network"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.ec2_ssh_source_cidr
}

# ---------------------------------------------------------
# 8. HTTPS OUTBOUND ACCESS
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_ssh_https" {
  security_group_id = aws_security_group.ec2_ssh_db_client.id
  description       = "HTTPS for AWS APIs and package repositories"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# ---------------------------------------------------------
# 9. EC2 TO RDS PROXY
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_ssh_to_rds_proxy" {
  security_group_id            = aws_security_group.ec2_ssh_db_client.id
  description                  = "PostgreSQL to RDS Proxy"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.rds_proxy.id
}

resource "aws_vpc_security_group_ingress_rule" "rds_proxy_from_ec2_ssh" {
  security_group_id            = aws_security_group.rds_proxy.id
  description                  = "PostgreSQL from SSH administration EC2"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ec2_ssh_db_client.id
}

# ---------------------------------------------------------
# 10. RDS PROXY IAM AUTHENTICATION
# ---------------------------------------------------------

resource "aws_iam_role_policy" "ec2_ssh_rds_proxy_connect" {
  name = "${var.project_name}-ec2-ssh-rds-proxy-connect-${var.environment}"
  role = aws_iam_role.ec2_ssh.id

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
# 11. DIRECT AURORA ADMINISTRATION ACCESS
# ---------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "ec2_ssh_to_aurora_admin" {
  security_group_id            = aws_security_group.ec2_ssh_db_client.id
  description                  = "Temporary PostgreSQL administration access"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.aurora.id
}

resource "aws_vpc_security_group_ingress_rule" "aurora_from_ec2_ssh_admin" {
  security_group_id            = aws_security_group.aurora.id
  description                  = "Temporary PostgreSQL access from SSH EC2"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ec2_ssh_db_client.id
}

# ---------------------------------------------------------
# 12. AURORA MASTER SECRET ACCESS
# ---------------------------------------------------------

resource "aws_iam_role_policy" "ec2_ssh_aurora_master_secret_access" {
  name = "${var.project_name}-ec2-ssh-aurora-master-secret-${var.environment}"
  role = aws_iam_role.ec2_ssh.id

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
# 13. PRIVATE EC2 SSH POSTGRESQL CLIENT
# ---------------------------------------------------------

resource "aws_instance" "postgres_ssh_client" {
  ami           = data.aws_ami.ssh_ecs_baseline.id
  instance_type = var.ec2_postgres_instance_type
  subnet_id     = var.existing_subnet_ids[0]

  key_name = aws_key_pair.postgres_ssh.key_name

  vpc_security_group_ids = [
    aws_security_group.ec2_ssh_db_client.id
  ]

  iam_instance_profile        = aws_iam_instance_profile.ec2_ssh.name
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
    dnf install -y postgresql
  EOF

  tags = {
    Name = "${var.project_name}-postgres-ssh-client-${var.environment}"
  }

  depends_on = [
    aws_iam_role_policy.ec2_ssh_rds_proxy_connect,
    aws_iam_role_policy.ec2_ssh_aurora_master_secret_access,
    aws_vpc_security_group_ingress_rule.ec2_ssh_ingress,
    aws_vpc_security_group_egress_rule.ec2_ssh_https,
    aws_vpc_security_group_egress_rule.ec2_ssh_to_rds_proxy,
    aws_vpc_security_group_ingress_rule.rds_proxy_from_ec2_ssh,
    aws_vpc_security_group_egress_rule.ec2_ssh_to_aurora_admin,
    aws_vpc_security_group_ingress_rule.aurora_from_ec2_ssh_admin
  ]
}

# ---------------------------------------------------------
# 14. EC2 OUTPUTS
# ---------------------------------------------------------

output "postgres_ssh_client_instance_id" {
  description = "SSH PostgreSQL client EC2 instance ID"
  value       = aws_instance.postgres_ssh_client.id
}

output "postgres_ssh_client_private_ip" {
  description = "SSH PostgreSQL client private IP"
  value       = aws_instance.postgres_ssh_client.private_ip
}



variable "ec2_ssh_public_key" {
  description = "OpenSSH public key for PostgreSQL client"
  type        = string
}

variable "ec2_ssh_source_cidr" {
  description = "Approved Boeing corporate SSH source CIDR"
  type        = string
}


ec2_ssh_public_key  = "PASTE_YOUR_COMPLETE_SSH_RSA_PUBLIC_KEY"
ec2_ssh_source_cidr = "10.37.5.156/32"