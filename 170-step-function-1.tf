
# =========================================================
# 170 - STEP FUNCTIONS 1
# =========================================================

locals {
  sf1_attachment_lambda_arn = aws_lambda_function.attachment_processing.arn
  sf1_email_lambda_arn      = aws_lambda_function.email_processing.arn
}

resource "aws_iam_role_policy" "sf1" {
  name = "sf1-invoke-attachment-processing"
  role = aws_iam_role.sf1.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "InvokeAttachmentProcessingLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = local.sf1_attachment_lambda_arn
      },
      {
        Sid      = "InvokeEmailProcessingLambda"
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = local.sf1_email_lambda_arn
      }
    ]
  })
}

resource "aws_sfn_state_machine" "sf1" {
  name     = "${var.project_name}-${var.environment}-${var.sf1_name_suffix}"
  role_arn = aws_iam_role.sf1.arn
  type     = var.sf1_type

  definition = jsonencode({
    Comment = "Payment Status Check - Step Functions 1: attachment and email processing"
    StartAt = "ValidateInput"

    States = {
      ValidateInput = {
        Type = "Choice"

        Choices = [
          {
            Variable  = "$.ingestion_id"
            IsPresent = true
            Next      = "ValidateIngestionIdType"
          }
        ]

        Default = "InvalidInput"
      }

      ValidateIngestionIdType = {
        Type = "Choice"

        Choices = [
          {
            Variable = "$.ingestion_id"
            IsString = true
            Next     = "ProcessAttachments"
          }
        ]

        Default = "InvalidInput"
      }

      ProcessAttachments = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = local.sf1_attachment_lambda_arn
          "Payload.$"  = "$"
        }

        ResultPath = "$.attachment_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]

            IntervalSeconds = var.sf1_retry_interval_seconds
            MaxAttempts     = var.sf1_max_retry_attempts
            BackoffRate     = var.sf1_retry_backoff_rate
          }
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "ProcessingFailed"
          }
        ]

        Next = "ProcessEmail"
      }

      ProcessEmail = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = local.sf1_email_lambda_arn
          "Payload.$"  = "$"
        }

        ResultPath = "$.email_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]

            IntervalSeconds = var.sf1_retry_interval_seconds
            MaxAttempts     = var.sf1_max_retry_attempts
            BackoffRate     = var.sf1_retry_backoff_rate
          }
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "EmailProcessingFailed"
          }
        ]

        Next = "ProcessingCompleted"
      }

      ProcessingCompleted = {
        Type = "Succeed"
      }

      InvalidInput = {
        Type  = "Fail"
        Error = "InvalidInput"
        Cause = "Required ingestion_id must be present and must be a string"
      }

      ProcessingFailed = {
        Type  = "Fail"
        Error = "AttachmentProcessingFailed"
        Cause = "Attachment Processing Lambda failed"
      }

      EmailProcessingFailed = {
        Type  = "Fail"
        Error = "EmailProcessingFailed"
        Cause = "Email Processing Lambda failed"
      }
    }
  })

  depends_on = [
    aws_iam_role_policy.sf1
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sf1_name_suffix}"
  }
}
