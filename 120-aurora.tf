
# =========================================================
# 120 - AURORA POSTGRESQL SERVERLESS V2
# =========================================================

# ---------------------------------------------------------
# 1. DB SUBNET GROUP
# ---------------------------------------------------------

resource "aws_db_subnet_group" "psc" {
  name = "${var.project_name}-${var.environment}-db-subnet-group"

  subnet_ids = [
    aws_subnet.database_a.id,
    aws_subnet.database_b.id
  ]

  tags = {
    Name = "${var.project_name}-db-subnet-group-${var.environment}"
  }
}

# ---------------------------------------------------------
# 2. DB CLUSTER PARAMETER GROUP
# ---------------------------------------------------------

resource "aws_rds_cluster_parameter_group" "psc" {
  name   = "${var.project_name}-${var.environment}-aurora-pg-parameters"
  family = var.aurora_parameter_group_family

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = {
    Name = "${var.project_name}-aurora-parameters-${var.environment}"
  }
}

# ---------------------------------------------------------
# 3. AURORA SERVERLESS V2 CLUSTER
# ---------------------------------------------------------

resource "aws_rds_cluster" "psc" {
  cluster_identifier = "${var.project_name}-${var.environment}-aurora"

  engine         = "aurora-postgresql"
  engine_version = var.aurora_engine_version
  engine_mode    = "provisioned"

  database_name   = var.database_name
  master_username = var.database_username

  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.psc.arn

  db_subnet_group_name   = aws_db_subnet_group.psc.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.psc.name

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_serverless_min_acu
    max_capacity = var.aurora_serverless_max_acu
  }

  storage_encrypted = true
  kms_key_id        = aws_kms_key.psc.arn

  backup_retention_period = var.aurora_backup_retention_days
  preferred_backup_window = var.aurora_backup_window

  preferred_maintenance_window = var.aurora_maintenance_window

  deletion_protection       = var.aurora_deletion_protection
  skip_final_snapshot       = var.aurora_skip_final_snapshot
  final_snapshot_identifier = var.aurora_final_snapshot_identifier

  copy_tags_to_snapshot = true

  enabled_cloudwatch_logs_exports = [
    "postgresql"
  ]

  tags = {
    Name = "${var.project_name}-aurora-${var.environment}"
  }
}

# ---------------------------------------------------------
# 4. SERVERLESS V2 WRITER
# ---------------------------------------------------------

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${var.project_name}-${var.environment}-aurora-writer"
  cluster_identifier = aws_rds_cluster.psc.id

  instance_class = "db.serverless"

  engine         = aws_rds_cluster.psc.engine
  engine_version = aws_rds_cluster.psc.engine_version

  availability_zone = var.availability_zone_a

  promotion_tier = 0

  publicly_accessible  = false
  db_subnet_group_name = aws_db_subnet_group.psc.name

  auto_minor_version_upgrade = var.aurora_auto_minor_version_upgrade

  performance_insights_enabled = var.aurora_performance_insights_enabled

  tags = {
    Name = "${var.project_name}-aurora-writer-${var.environment}"
  }
}

# ---------------------------------------------------------
# 5. SERVERLESS V2 READER
# ---------------------------------------------------------

resource "aws_rds_cluster_instance" "reader" {
  identifier         = "${var.project_name}-${var.environment}-aurora-reader"
  cluster_identifier = aws_rds_cluster.psc.id

  instance_class = "db.serverless"

  engine         = aws_rds_cluster.psc.engine
  engine_version = aws_rds_cluster.psc.engine_version

  availability_zone = var.availability_zone_b

  promotion_tier = 1

  publicly_accessible  = false
  db_subnet_group_name = aws_db_subnet_group.psc.name

  auto_minor_version_upgrade = var.aurora_auto_minor_version_upgrade

  performance_insights_enabled = var.aurora_performance_insights_enabled

  depends_on = [
    aws_rds_cluster_instance.writer
  ]

  tags = {
    Name = "${var.project_name}-aurora-reader-${var.environment}"
  }
}

