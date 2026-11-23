
# ==========================================================
# 220 - STEP FUNCTION 2
# PSC - PAYMENT STATUS CHECK
# ==========================================================
# 230 Preprocessing + 240 Guardrail
# 395 Classification and Extraction
# 270 Identifier Validation
# 280 Invoice Scope Determination
# SQS2 handoff
#
# Uses existing AgentCore Runtime through Lambda 395.
# ==========================================================

# ----------------------------------------------------------
# 1. SF2 EXECUTION IAM POLICY
# ----------------------------------------------------------

resource "aws_iam_role_policy" "sf2" {
  name = "sf2-processing-workflow-access"
  role = aws_iam_role.sf2.id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "InvokeProcessingLambdas"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = [
          aws_lambda_function.mail_preprocessing.arn,
          aws_lambda_function.intake_agents.arn,
          aws_lambda_function.identifier_validation.arn,
          aws_lambda_function.invoice_scope.arn
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
    Comment = "PSC SF2 - Preprocess, Classify, Extract, Validate, Scope and Handoff"

    StartAt = "UnwrapInput"

    States = {

      # Accept either a single case object or an array.
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

            Next = "PreprocessMail"
          }
        ]

        Default = "InvalidInput"
      }

      # ----------------------------------------------------
      # 230 - MAIL PREPROCESSING + 240 GUARDRAIL
      # ----------------------------------------------------

      PreprocessMail = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.mail_preprocessing.arn
          "Payload.$"  = "$"
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
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 3
            MaxAttempts     = 3
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

        Next = "GuardrailDecision"
      }

      GuardrailDecision = {
        Type = "Choice"

        Choices = [
          {
            Variable     = "$.guardrail_status"
            StringEquals = "ALLOWED"
            Next         = "PrepareIntakeCase"
          }
        ]

        Default = "NotInvestigated"
      }

      # ----------------------------------------------------
      # MAP 230 OUTPUT TO 395 INPUT
      # ----------------------------------------------------

      PrepareIntakeCase = {
        Type = "Pass"

        Parameters = {
          "case_id.$"           = "$.case_id"
          "message_id.$"        = "$.message_id"
          "subject.$"           = "$.subject"
          "body.$"              = "$.preprocessed_body"
          "attachments.$"       = "$.attachments"
          "preprocessed_body.$" = "$.preprocessed_body"
          "guardrail_status.$"  = "$.guardrail_status"
          "processing_status.$" = "$.processing_status"
        }

        Next = "Classify"
      }

      # ----------------------------------------------------
      # 395 - CLASSIFICATION
      # ----------------------------------------------------

      Classify = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.intake_agents.arn

          Payload = {
            role     = "classification"
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
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 3
            MaxAttempts     = 3
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
      # 395 - EXTRACTION
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
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 3
            MaxAttempts     = 3
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

        Next = "PrepareIdentifierValidation"
      }

      # ----------------------------------------------------
      # MAP 395 OUTPUT TO 270 INPUT
      # ----------------------------------------------------

      PrepareIdentifierValidation = {
        Type = "Pass"

        Parameters = {
          "case_id.$"      = "$.case_id"
          "message_id.$"   = "$.message_id"
          "payment_info.$" = "$.identifiers.candidates"
          "case.$"         = "$"
        }

        Next = "ValidateIdentifiers"
      }

      # ----------------------------------------------------
      # 270 - IDENTIFIER VALIDATION
      # ----------------------------------------------------

      ValidateIdentifiers = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.identifier_validation.arn
          "Payload.$"  = "$"
        }

        ResultSelector = {
          "validation.$" = "$.Payload"
        }

        ResultPath = "$.identifier_validation"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 3
            MaxAttempts     = 3
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

        Next = "ValidationDecision"
      }

      ValidationDecision = {
        Type = "Choice"

        Choices = [
          {
            Variable     = "$.identifier_validation.validation.validation_status"
            StringEquals = "VALIDATED"
            Next         = "DetermineInvoiceScope"
          }
        ]

        Default = "NeedsManualReview"
      }

      # ----------------------------------------------------
      # 280 - INVOICE SCOPE DETERMINATION
      # ----------------------------------------------------

      DetermineInvoiceScope = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.invoice_scope.arn

          Payload = {
            "case_id.$"     = "$.case_id"
            "message_id.$"  = "$.message_id"
            "invoice_ids.$" = "$.identifier_validation.validation.invoice_ids"

            identifier_validation = {
              "valid_invoice_ids.$" = "$.identifier_validation.validation.invoice_ids"
              "validation_status.$" = "$.identifier_validation.validation.validation_status"
            }
          }
        }

        ResultSelector = {
          "scope.$" = "$.Payload"
        }

        ResultPath = "$.invoice_scope_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]
            IntervalSeconds = 3
            MaxAttempts     = 3
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
      # SQS2 HANDOFF
      # ----------------------------------------------------

      PublishToSQS2 = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::sqs:sendMessage"

        Parameters = {
          QueueUrl        = aws_sqs_queue.sqs2.id
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
      # TERMINAL STATES
      # ----------------------------------------------------

      ProcessingCompleted = {
        Type = "Succeed"
      }

      NotInvestigated = {
        Type = "Succeed"
      }

      NeedsManualReview = {
        Type = "Succeed"
      }

      InvalidInput = {
        Type  = "Fail"
        Error = "InvalidInput"
        Cause = "Missing case_id or message_id"
      }

      ProcessingFailed = {
        Type  = "Fail"
        Error = "ProcessingFailed"
        Cause = "SF2 processing failed"
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
