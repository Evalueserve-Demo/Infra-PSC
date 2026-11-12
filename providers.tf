
# =========================================================
# AWS PROVIDER CONFIGURATION
# Project: Boeing Helpdesk Payment Status Check
# =========================================================


# ---------------------------------------------------------
# 1. AWS PROVIDER
# ---------------------------------------------------------

provider "aws" {
  region = var.aws_region

  allowed_account_ids = [
    var.aws_account_id
  ]

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}


# ---------------------------------------------------------
# 2. CURRENT AWS ACCOUNT
# ---------------------------------------------------------

data "aws_caller_identity" "current" {}


# ---------------------------------------------------------
# 3. AWS PARTITION
# ---------------------------------------------------------

data "aws_partition" "current" {}


# ---------------------------------------------------------
# 4. CURRENT AWS REGION
# ---------------------------------------------------------

data "aws_region" "current" {}
