
# ==========================================================
# 540 - PSC CLOUDWATCH MONITORING
# Region: us-east-2
# ==========================================================

locals {
  psc_monitoring_prefix_540 = "${var.project_name}-${var.environment}"

  # Active Lambda functions (V4 scope)
  psc_lambdas_540 = {
    email_ingestion          = aws_lambda_function.email_ingestion.function_name
    attachment_processing    = aws_lambda_function.attachment_processing.function_name
    email_processing         = aws_lambda_function.email_processing.function_name
    mail_preprocessing       = aws_lambda_function.mail_preprocessing.function_name
    pre_sf1_dispatcher       = aws_lambda_function.pre_sf1_dispatcher.function_name
    sf2_dispatcher           = aws_lambda_function.sf2_dispatcher.function_name
    intake_agents            = aws_lambda_function.intake_agents.function_name
    investigation_invocation = aws_lambda_function.investigation_invocation.function_name
    s4hana_tool              = aws_lambda_function.s4hana_tool.function_name
    spvr_tool                = aws_lambda_function.spvr_tool.function_name
    payment_rules_tool       = aws_lambda_function.payment_rules_tool.function_name
    root_cause_map_tool      = aws_lambda_function.root_cause_map_tool.function_name
    draft_builder_tool       = aws_lambda_function.draft_builder_tool.function_name
  }

  # Active SQS queues (V4 scope)
  psc_queues_540 = {
    pre_sf1     = aws_sqs_queue.pre_sf1.name
    pre_sf1_dlq = aws_sqs_queue.pre_sf1_dlq.name
    sqs1        = aws_sqs_queue.sqs1.name
    sqs1_dlq    = aws_sqs_queue.sqs1_dlq.name
    sqs2        = aws_sqs_queue.sqs2.name
    sqs2_dlq    = aws_sqs_queue.sqs2_dlq.name
  }

  psc_main_queues_540 = {
    pre_sf1 = aws_sqs_queue.pre_sf1.name
    sqs1    = aws_sqs_queue.sqs1.name
    sqs2    = aws_sqs_queue.sqs2.name
  }

  psc_dlqs_540 = {
    pre_sf1_dlq = aws_sqs_queue.pre_sf1_dlq.name
    sqs1_dlq    = aws_sqs_queue.sqs1_dlq.name
    sqs2_dlq    = aws_sqs_queue.sqs2_dlq.name
  }

  psc_state_machines_540 = {
    sf1 = aws_sfn_state_machine.sf1.arn
    sf2 = aws_sfn_state_machine.sf2.arn
  }

  psc_aurora_instances_540 = {
    writer = aws_rds_cluster_instance.writer.identifier
    reader = aws_rds_cluster_instance.reader.identifier
  }
}

# ==========================================================
# 1. SNS ALERT NOTIFICATIONS
# ==========================================================

resource "aws_sns_topic" "psc_monitoring_540" {
  name = "${local.psc_monitoring_prefix_540}-monitoring-alerts"
}

resource "aws_sns_topic_subscription" "psc_monitoring_email_540" {
  topic_arn = aws_sns_topic.psc_monitoring_540.arn
  protocol  = "email"
  endpoint  = var.cloudwatch_alarm_email
}

# ==========================================================
# 2. LAMBDA ERRORS
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "lambda_errors_540" {
  for_each = local.psc_lambdas_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-errors"
  alarm_description   = "Lambda function ${each.key} reported errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
  ok_actions    = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 3. LAMBDA THROTTLING
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "lambda_throttles_540" {
  for_each = local.psc_lambdas_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-throttles"
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 4. LAMBDA DURATION
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "lambda_duration_540" {
  for_each = local.psc_lambdas_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-duration"
  namespace           = "AWS/Lambda"
  metric_name         = "Duration"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = 10000
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 5. SQS DEAD-LETTER QUEUES
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sqs_dlq_540" {
  for_each = local.psc_dlqs_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-messages"
  alarm_description   = "Messages detected in ${each.key}"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 6. SQS MESSAGE BACKLOG
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sqs_backlog_540" {
  for_each = local.psc_main_queues_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-backlog"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 100
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 7. SQS OLDEST MESSAGE AGE
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sqs_message_age_540" {
  for_each = local.psc_main_queues_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-message-age"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 900
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 8. STEP FUNCTIONS EXECUTION FAILURES
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sfn_failed_540" {
  for_each = local.psc_state_machines_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 9. STEP FUNCTIONS TIMEOUTS
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sfn_timeout_540" {
  for_each = local.psc_state_machines_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-timeout"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsTimedOut"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 10. STEP FUNCTIONS ABORTED EXECUTIONS
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "sfn_aborted_540" {
  for_each = local.psc_state_machines_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-${each.key}-aborted"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsAborted"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 11. AURORA WRITER AND READER CPU
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "aurora_cpu_540" {
  for_each = local.psc_aurora_instances_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-aurora-${each.key}-cpu"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 12. AURORA DATABASE CONNECTIONS
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "aurora_connections_540" {
  for_each = local.psc_aurora_instances_540

  alarm_name          = "${local.psc_monitoring_prefix_540}-aurora-${each.key}-connections"
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 100
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = each.value
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# ==========================================================
# 13. RDS PROXY CLIENT CONNECTIONS
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "rds_proxy_connections_540" {
  alarm_name          = "${local.psc_monitoring_prefix_540}-rds-proxy-connections"
  namespace           = "AWS/RDS"
  metric_name         = "ClientConnections"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 100
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ProxyName = aws_db_proxy.psc.name
  }

  alarm_actions = [aws_sns_topic.psc_monitoring_540.arn]
}

# V4: API Gateway and ALB monitoring deferred with stages #400-#530.

# ==========================================================
# 19. CONSOLIDATED CLOUDWATCH DASHBOARD
# ==========================================================

resource "aws_cloudwatch_dashboard" "psc_540" {
  dashboard_name = "${local.psc_monitoring_prefix_540}-monitoring"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Lambda Errors"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          metrics = [
            for name, function_name in local.psc_lambdas_540 :
            [
              "AWS/Lambda",
              "Errors",
              "FunctionName",
              function_name,
              { label = name }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "SQS Visible Messages"
          region = var.aws_region
          period = 300
          stat   = "Maximum"
          metrics = [
            for name, queue_name in local.psc_queues_540 :
            [
              "AWS/SQS",
              "ApproximateNumberOfMessagesVisible",
              "QueueName",
              queue_name,
              { label = name }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Step Functions Failures"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          metrics = [
            for name, arn in local.psc_state_machines_540 :
            [
              "AWS/States",
              "ExecutionsFailed",
              "StateMachineArn",
              arn,
              { label = name }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Aurora CPU Utilization"
          region = var.aws_region
          period = 300
          stat   = "Average"
          metrics = [
            for name, identifier in local.psc_aurora_instances_540 :
            [
              "AWS/RDS",
              "CPUUtilization",
              "DBInstanceIdentifier",
              identifier,
              { label = name }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 12
        width  = 12
        height = 6
        properties = {
          title  = "RDS Proxy Client Connections"
          region = var.aws_region
          period = 300
          stat   = "Average"
          metrics = [
            [
              "AWS/RDS",
              "ClientConnections",
              "ProxyName",
              aws_db_proxy.psc.name
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "AgentCore Runtime CPU Usage (vCPU-hours)"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          metrics = [
            [
              "AWS/Bedrock-AgentCore", "CPUUsed-vCPUHours",
              "Service", "AgentCore.Runtime",
              "Resource", aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn,
              { label = "Classification Agent" }
            ],
            [
              "AWS/Bedrock-AgentCore", "CPUUsed-vCPUHours",
              "Service", "AgentCore.Runtime",
              "Resource", aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn,
              { label = "Investigation Agent" }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 18
        width  = 12
        height = 6
        properties = {
          title  = "AgentCore Runtime Memory Usage (GB-hours)"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          metrics = [
            [
              "AWS/Bedrock-AgentCore", "MemoryUsed-GBHours",
              "Service", "AgentCore.Runtime",
              "Resource", aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn,
              { label = "Classification Agent" }
            ],
            [
              "AWS/Bedrock-AgentCore", "MemoryUsed-GBHours",
              "Service", "AgentCore.Runtime",
              "Resource", aws_bedrockagentcore_agent_runtime.investigation.agent_runtime_arn,
              { label = "Investigation Agent" }
            ]
          ]
        }
      },
      # ------------------------------------------------------
      # Aurora / RDS Proxy - additional monitoring charts
      # ------------------------------------------------------
      {
        type   = "metric"
        x      = 0
        y      = 24
        width  = 12
        height = 6
        properties = {
          title  = "Aurora Freeable Memory (MiB)"
          region = var.aws_region
          period = 300
          stat   = "Average"
          view   = "timeSeries"
          metrics = concat(
            [for name, identifier in local.psc_aurora_instances_540 :
              [{ expression = "m${name} / 1048576", label = "${name} (MiB)", id = "e${name}" }]
            ],
            [for name, identifier in local.psc_aurora_instances_540 :
              ["AWS/RDS", "FreeableMemory", "DBInstanceIdentifier", identifier,
              { id = "m${name}", stat = "Average", visible = false }]
            ]
          )
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 24
        width  = 12
        height = 6
        properties = {
          title  = "Aurora Database Deadlocks"
          region = var.aws_region
          period = 300
          stat   = "Sum"
          view   = "timeSeries"
          metrics = [
            for name, identifier in local.psc_aurora_instances_540 : [
              "AWS/RDS", "Deadlocks", "DBInstanceIdentifier", identifier,
              { label = name }
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 30
        width  = 12
        height = 6
        properties = {
          title  = "Aurora Reader Replication Lag (Milliseconds)"
          region = var.aws_region
          period = 300
          stat   = "Average"
          view   = "timeSeries"
          metrics = [[
            "AWS/RDS", "AuroraReplicaLag", "DBInstanceIdentifier",
            aws_rds_cluster_instance.reader.identifier,
            { label = "Aurora Reader" }
          ]]
          annotations = {
            horizontal = [{ label = "30 seconds", value = 30000 }]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 30
        width  = 12
        height = 6
        properties = {
          title  = "RDS Proxy Database Connections"
          region = var.aws_region
          period = 300
          stat   = "Maximum"
          view   = "timeSeries"
          metrics = [[
            "AWS/RDS", "DatabaseConnections", "ProxyName", aws_db_proxy.psc.name,
            { label = "Database Connections" }
          ]]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 36
        width  = 12
        height = 6
        properties = {
          title  = "RDS Proxy Connection Borrow Latency (Microseconds)"
          region = var.aws_region
          period = 300
          stat   = "Average"
          view   = "timeSeries"
          metrics = [[
            "AWS/RDS", "DatabaseConnectionsBorrowLatency", "ProxyName", aws_db_proxy.psc.name,
            { label = "Borrow Latency" }
          ]]
          annotations = {
            horizontal = [{ label = "1 second", value = 1000000 }]
          }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 36
        width  = 12
        height = 6
        properties = {
          title  = "RDS Proxy Connection Pool Utilization (%)"
          region = var.aws_region
          period = 300
          view   = "timeSeries"
          metrics = [
            [{ expression = "IF(allowed > 0, 100 * used / allowed, 0)", label = "Pool Utilization (%)", id = "utilization" }],
            ["AWS/RDS", "DatabaseConnections", "ProxyName", aws_db_proxy.psc.name, { id = "used", stat = "Maximum", visible = false }],
            ["AWS/RDS", "MaxDatabaseConnectionsAllowed", "ProxyName", aws_db_proxy.psc.name, { id = "allowed", stat = "Maximum", visible = false }]
          ]
          annotations = {
            horizontal = [{ label = "80%", value = 80 }]
          }
        }
      }
    ]
  })
}


# ==========================================================
# 540 - PSC AURORA MONITORING ADDITIONS
# Region: us-east-2
#
# Reuses existing resources from 540-cloudwatch-monitoring.tf:
#   local.psc_monitoring_prefix_540
#   local.psc_aurora_instances_540
#   aws_sns_topic.psc_monitoring_540
#
# No new variables required.
# ==========================================================


# ==========================================================
# 1. AURORA FREEABLE MEMORY - WRITER AND READER
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "aurora_freeable_memory_540" {
  for_each = local.psc_aurora_instances_540

  alarm_name = "${local.psc_monitoring_prefix_540}-aurora-${each.key}-low-memory"

  alarm_description = "Aurora ${each.key} freeable memory below 512 MiB"

  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 536870912
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"

  dimensions = {
    DBInstanceIdentifier = each.value
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}


# ==========================================================
# 2. AURORA DATABASE DEADLOCKS - WRITER AND READER
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "aurora_deadlocks_540" {
  for_each = local.psc_aurora_instances_540

  alarm_name = "${local.psc_monitoring_prefix_540}-aurora-${each.key}-deadlocks"

  alarm_description = "Aurora ${each.key} reported database deadlocks"

  namespace           = "AWS/RDS"
  metric_name         = "Deadlocks"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = each.value
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}


# ==========================================================
# 3. AURORA READER REPLICATION LAG
# ==========================================================

resource "aws_cloudwatch_metric_alarm" "aurora_replica_lag_540" {
  alarm_name = "${local.psc_monitoring_prefix_540}-aurora-reader-replication-lag"

  alarm_description = "Aurora reader replication lag exceeds 30 seconds for 10 minutes"

  namespace           = "AWS/RDS"
  metric_name         = "AuroraReplicaLag"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 30000
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"

  dimensions = {
    DBInstanceIdentifier = aws_rds_cluster_instance.reader.identifier
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}

# ==========================================================
# END - AURORA MONITORING ADDITIONS
# ==========================================================


# ==========================================================
# 540 - RDS PROXY MONITORING REFINEMENTS
# ==========================================================
#
# Reuses:
#   aws_db_proxy.psc
#   aws_sns_topic.psc_monitoring_540
#   local.psc_monitoring_prefix_540
#
# No additional variables required.
# ==========================================================


# ----------------------------------------------------------
# 1. RDS Proxy Database Connections
# ----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_proxy_database_connections_540" {
  alarm_name = "${local.psc_monitoring_prefix_540}-rds-proxy-database-connections"

  alarm_description = "RDS Proxy database connections exceeded the initial threshold"

  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 100
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ProxyName = aws_db_proxy.psc.name
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}


# ----------------------------------------------------------
# 2. RDS Proxy Database Connection Borrow Latency
# ----------------------------------------------------------
# AWS publishes this metric in microseconds.
# 1000000 microseconds = 1 second.
# ----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_proxy_borrow_latency_540" {
  alarm_name = "${local.psc_monitoring_prefix_540}-rds-proxy-borrow-latency"

  alarm_description = "RDS Proxy average database connection borrow latency exceeded 1 second"

  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnectionsBorrowLatency"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1000000
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ProxyName = aws_db_proxy.psc.name
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}


# ----------------------------------------------------------
# 3. RDS Proxy Database Connection Setup Failures
# ----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_proxy_connection_setup_failures_540" {
  alarm_name = "${local.psc_monitoring_prefix_540}-rds-proxy-connection-setup-failures"

  alarm_description = "RDS Proxy failed to establish database connections"

  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnectionsSetupFailed"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ProxyName = aws_db_proxy.psc.name
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}


# ----------------------------------------------------------
# 4. RDS Proxy Database Connection Pool Utilization
# ----------------------------------------------------------
# Metric math:
# DatabaseConnections / MaxDatabaseConnectionsAllowed * 100
#
# Alert when utilization reaches 80%.
# ----------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_proxy_pool_utilization_540" {
  alarm_name = "${local.psc_monitoring_prefix_540}-rds-proxy-pool-utilization"

  alarm_description = "RDS Proxy database connection pool utilization reached 80 percent"

  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 3
  threshold           = 80
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "utilization"
    expression  = "IF(allowed > 0, 100 * used / allowed, 0)"
    label       = "RDS Proxy Pool Utilization Percent"
    return_data = true
  }

  metric_query {
    id          = "used"
    return_data = false

    metric {
      namespace   = "AWS/RDS"
      metric_name = "DatabaseConnections"
      period      = 300
      stat        = "Maximum"

      dimensions = {
        ProxyName = aws_db_proxy.psc.name
      }
    }
  }

  metric_query {
    id          = "allowed"
    return_data = false

    metric {
      namespace   = "AWS/RDS"
      metric_name = "MaxDatabaseConnectionsAllowed"
      period      = 300
      stat        = "Maximum"

      dimensions = {
        ProxyName = aws_db_proxy.psc.name
      }
    }
  }

  alarm_actions = [
    aws_sns_topic.psc_monitoring_540.arn
  ]
}

# ==========================================================
# END - RDS PROXY MONITORING REFINEMENTS
# ==========================================================


# ==========================================================
# 20. OUTPUTS
# ==========================================================

output "psc_monitoring_sns_topic_arn_540" {
  value = aws_sns_topic.psc_monitoring_540.arn
}

output "psc_cloudwatch_dashboard_name_540" {
  value = aws_cloudwatch_dashboard.psc_540.dashboard_name
}

