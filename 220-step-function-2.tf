
# ==========================================================
# 220 - STEP FUNCTION 2
# PSC - PAYMENT STATUS CHECK
# ==========================================================
# Classifies the mail and pulls out the invoice number.
# The intake Lambda (395) does the work: it runs the guardrail, calls the
# two agents and colours the mail in Outlook.
# A payment status request goes on to SQS2. Any other mail stops here,
# with its label and the reason saved on the case.

# ----------------------------------------------------------
# 1. SF2 EXECUTION IAM POLICY
# Existing role is defined in 140-iam.tf
# ----------------------------------------------------------

resource "aws_iam_role_policy" "sf2" {
  name = "sf2-processing-workflow-access"
  role = aws_iam_role.sf2.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "InvokeIntakeAgentsLambda"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = [
          aws_lambda_function.intake_agents.arn
        ]
      },
      {
        Sid    = "PublishToSQS2"
        Effect = "Allow"

        Action = [
          "sqs:SendMessage"
        ]

        Resource = aws_sqs_queue.sqs2.arn
      },
      {
        Sid    = "EncryptSQS2Messages"
        Effect = "Allow"

        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt"
        ]

        Resource = aws_kms_key.psc.arn
      }
    ]
  })
}

# ----------------------------------------------------------
# 2. STEP FUNCTION 2
# ----------------------------------------------------------

resource "aws_sfn_state_machine" "sf2" {
  name     = "${var.project_name}-${var.environment}-${var.sf2_name_suffix}"
  role_arn = aws_iam_role.sf2.arn
  type     = var.sf2_type

  definition = jsonencode({
    Comment = "PSC - SF2 Classification and Identifier Extraction"

    StartAt = "UnwrapInput"

    States = {

      # The pipe sends the SQS batch as a list, even for one mail. A manual start
      # sends the object itself. If it is a list, take the first item.
      UnwrapInput = {
        Type = "Choice"

        Choices = [
          {
            Variable  = "$[0]"
            IsPresent = true
            Next      = "TakeFirstItem"
          }
        ]

        Default = "ValidateInput"
      }

      TakeFirstItem = {
        Type      = "Pass"
        InputPath = "$[0]"
        Next      = "ValidateInput"
      }

      # ----------------------------------------------------
      # VALIDATE SQS1 INPUT
      # The pipe passes the case in as the execution input.
      # ----------------------------------------------------

      ValidateInput = {
        Type = "Choice"

        Choices = [
          {
            And = [
              {
                Variable  = "$.case_id"
                IsPresent = true
              },
              {
                Variable  = "$.message_id"
                IsPresent = true
              }
            ]

            Next = "Classify"
          }
        ]

        Default = "InvalidInput"
      }

      # ----------------------------------------------------
      # CLASSIFY
      # Runs the guardrail, then asks the classification agent what the
      # supplier wants. The Lambda colours the mail in Outlook and decides
      # (rules in escalation.py) whether it gets investigated.
      # RuntimeClientError is retried, because an agent runtime that has
      # been idle can fail its first call.
      # ----------------------------------------------------

      Classify = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.intake_agents.arn

          Payload = {
            role    = "classification"
            "case.$" = "$"
          }
        }

        ResultSelector = {
          "case.$" = "$.Payload"
        }

        OutputPath = "$.case"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException",
              "RuntimeClientError"
            ]

            IntervalSeconds = 3
            MaxAttempts     = 6
            BackoffRate     = 2
          }
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "ProcessingFailed"
          }
        ]

        Next = "IsPaymentRelated"
      }

      # ----------------------------------------------------
      # ONLY A CONFIDENT PAYMENT STATUS REQUEST CONTINUES
      # The Lambda sets the investigate flag; only true carries on.
      # ----------------------------------------------------

      IsPaymentRelated = {
        Type = "Choice"

        Choices = [
          {
            Variable      = "$.investigate"
            BooleanEquals = true
            Next          = "ExtractIdentifiers"
          }
        ]

        Default = "NotInvestigated"
      }

      # ----------------------------------------------------
      # EXTRACT IDENTIFIERS
      # Finds which invoice the mail is about. If the invoice is missing or
      # badly formed the flow carries on, and the reply asks the supplier for it.
      # ----------------------------------------------------

      ExtractIdentifiers = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.intake_agents.arn

          Payload = {
            role     = "extraction"
            "case.$" = "$"
          }
        }

        ResultSelector = {
          "case.$" = "$.Payload"
        }

        OutputPath = "$.case"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException",
              "RuntimeClientError"
            ]

            IntervalSeconds = 3
            MaxAttempts     = 6
            BackoffRate     = 2
          }
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "ProcessingFailed"
          }
        ]

        Next = "PublishToSQS2"
      }

      # ----------------------------------------------------
      # 290 - SQS2
      # Sends the full case, in the shape the investigation reads.
      # ----------------------------------------------------

      PublishToSQS2 = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::sqs:sendMessage"

        Parameters = {
          QueueUrl = aws_sqs_queue.sqs2.id

          "MessageBody.$" = "$"
        }

        ResultPath = null

        Retry = [
          {
            ErrorEquals     = ["States.ALL"]
            IntervalSeconds = var.sf2_retry_interval_seconds
            MaxAttempts     = var.sf2_max_retry_attempts
            BackoffRate     = var.sf2_retry_backoff_rate
          }
        ]

        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "ProcessingFailed"
          }
        ]

        Next = "ProcessingCompleted"
      }

      # ----------------------------------------------------
      # SUCCESS
      # ----------------------------------------------------

      ProcessingCompleted = {
        Type = "Succeed"
      }

      # Not a failure. The mail is either not about payment, or a person should
      # handle it. It is labelled in Outlook and the reason is saved on the case.
      NotInvestigated = {
        Type = "Succeed"
      }

      # ----------------------------------------------------
      # FAILURE STATES
      # ----------------------------------------------------

      InvalidInput = {
        Type  = "Fail"
        Error = "InvalidInput"
        Cause = "Missing required SQS1 message fields"
      }

      ProcessingFailed = {
        Type  = "Fail"
        Error = "ProcessingFailed"
        Cause = "SF2 task invocation or processing failed"
      }
    }
  })

  depends_on = [
    aws_iam_role_policy.sf2
  ]

  tags = {
    Name = "${var.project_name}-${var.environment}-${var.sf2_name_suffix}"
  }
}
