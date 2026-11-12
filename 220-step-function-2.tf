
# ==========================================================
# 220 - STEP FUNCTION 2
# PSC - PAYMENT STATUS CHECK
# ==========================================================

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
        Sid    = "InvokeProcessingLambdas"
        Effect = "Allow"

        Action = [
          "lambda:InvokeFunction"
        ]

        Resource = [
          aws_lambda_function.mail_preprocessing.arn,
          aws_lambda_function.identifier_validation.arn,
          aws_lambda_function.invoice_scope.arn
        ]
      },
      {
        Sid    = "InvokeClassificationAgentCore"
        Effect = "Allow"

        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]

        Resource = [
          aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn
        ]
      },
      {
        Sid    = "InvokeExtractionAgentCore"
        Effect = "Allow"

        Action = [
          "bedrock-agentcore:InvokeAgentRuntime"
        ]

        Resource = [
          aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn
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
    Comment = "PSC - SF2 Classification, Extraction and Invoice Processing"

    StartAt = "ValidateInput"

    States = {

      # ----------------------------------------------------
      # VALIDATE SQS1 INPUT
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
              },
              {
                Variable     = "$.processing_status"
                StringEquals = "EMAIL_PROCESSED"
              }
            ]

            Next = "PreprocessEmail"
          }
        ]

        Default = "InvalidInput"
      }

      # ----------------------------------------------------
      # 230 - MAIL PREPROCESSING LAMBDA
      # ----------------------------------------------------

      PreprocessEmail = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.mail_preprocessing.arn
          "Payload.$"  = "$"
        }

        ResultPath = "$.preprocessing_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]

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

        Next = "CheckPreprocessing"
      }

      # ----------------------------------------------------
      # CHECK PREPROCESSING OUTPUT
      # ----------------------------------------------------

      CheckPreprocessing = {
        Type = "Choice"

        Choices = [
          {
            Variable     = "$.preprocessing_result.Payload.processing_status"
            StringEquals = "PREPROCESSED"

            Next = "PrepareClassificationInput"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # PREPARE CLASSIFICATION INPUT
      # AgentCore expects body, not preprocessed_body
      # ----------------------------------------------------

      PrepareClassificationInput = {
        Type = "Pass"

        Parameters = {
          "case_id.$"         = "$.case_id"
          "message_id.$"      = "$.message_id"
          "conversation_id.$" = "$.conversation_id"
          "subject.$"         = "$.preprocessing_result.Payload.subject"
          "body.$"            = "$.preprocessing_result.Payload.preprocessed_body"
          "attachments.$"     = "$.preprocessing_result.Payload.attachments"
        }

        ResultPath = "$.classification_input"

        Next = "InvokeClassificationAgent"
      }

      # ----------------------------------------------------
      # 260 - CLASSIFICATION AGENTCORE RUNTIME
      # ----------------------------------------------------

      InvokeClassificationAgent = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::aws-sdk:bedrockagentcore:invokeAgentRuntime"

        Parameters = {
          AgentRuntimeArn = aws_bedrockagentcore_agent_runtime.classification.agent_runtime_arn

          "RuntimeSessionId.$" = "States.Hash($$.Execution.Id, 'SHA-256')"

          "Payload.$" = "States.JsonToString($.classification_input)"

          Qualifier = "DEFAULT"
        }

        ResultPath = "$.classification_result"

        TimeoutSeconds = 120

        Retry = [
          {
            ErrorEquals = [
              "BedrockAgentCore.ThrottlingException",
              "BedrockAgentCore.ServiceException"
            ]

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

        Next = "ParseClassificationResult"
      }

      # ----------------------------------------------------
      # PARSE CLASSIFICATION RESPONSE
      # ----------------------------------------------------

      ParseClassificationResult = {
        Type = "Pass"

        Parameters = {
          "result.$" = "States.StringToJson($.classification_result.Response)"
        }

        ResultPath = "$.classification"

        Next = "CheckClassification"
      }

      # ----------------------------------------------------
      # CLASSIFICATION DECISION
      # ----------------------------------------------------

      CheckClassification = {
        Type = "Choice"

        Choices = [
          {
            And = [
              {
                Variable     = "$.classification.result.status"
                StringEquals = "CLASSIFIED"
              },
              {
                Variable     = "$.classification.result.classification"
                StringEquals = "PAYMENT_STATUS"
              }
            ]

            Next = "PrepareExtractionInput"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # PREPARE EXTRACTION INPUT (265)
      # ----------------------------------------------------

      PrepareExtractionInput = {
        Type = "Pass"

        Parameters = {
          "case_id.$"     = "$.case_id"
          "message_id.$"  = "$.message_id"
          "subject.$"     = "$.preprocessing_result.Payload.subject"
          "body.$"        = "$.preprocessing_result.Payload.preprocessed_body"
          "attachments.$" = "$.preprocessing_result.Payload.attachments"
        }

        ResultPath = "$.extraction_input"

        Next = "InvokeExtractionAgent"
      }

      # ----------------------------------------------------
      # INVOKE EXTRACTION AGENTCORE (265)
      # ----------------------------------------------------

      InvokeExtractionAgent = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::aws-sdk:bedrockagentcore:invokeAgentRuntime"

        Parameters = {
          AgentRuntimeArn = aws_bedrockagentcore_agent_runtime.extraction.agent_runtime_arn

          "RuntimeSessionId.$" = "States.Hash($$.Execution.Id, 'SHA-256')"

          "Payload.$" = "States.JsonToString($.extraction_input)"

          Qualifier = "DEFAULT"
        }

        ResultPath     = "$.extraction_result"
        TimeoutSeconds = 120

        Retry = [
          {
            ErrorEquals = [
              "BedrockAgentCore.ThrottlingException",
              "BedrockAgentCore.ServiceException"
            ]

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

        Next = "ParseExtractionResult"
      }

      # ----------------------------------------------------
      # PARSE EXTRACTION RESPONSE
      # ----------------------------------------------------

      ParseExtractionResult = {
        Type = "Pass"

        Parameters = {
          "result.$" = "States.StringToJson($.extraction_result.Response)"
        }

        ResultPath = "$.extraction"

        Next = "CheckExtractionResult"
      }

      # ----------------------------------------------------
      # CHECK EXTRACTION STATUS
      # ----------------------------------------------------

      CheckExtractionResult = {
        Type = "Choice"

        Choices = [
          {
            Variable     = "$.extraction.result.status"
            StringEquals = "EXTRACTED"

            Next = "CheckInvoiceIdentifiers"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # CHECK EXTRACTED IDENTIFIERS
      # ----------------------------------------------------

      CheckInvoiceIdentifiers = {
        Type = "Choice"

        Choices = [
          {
            Variable  = "$.extraction.result.payment_info[0]"
            IsPresent = true

            Next = "ValidateIdentifiers"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # 270 - IDENTIFIER VALIDATION LAMBDA
      # ----------------------------------------------------

      ValidateIdentifiers = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.identifier_validation.arn

          Payload = {
            "case_id.$"      = "$.case_id"
            "message_id.$"   = "$.message_id"
            "payment_info.$" = "$.extraction.result.payment_info"
          }
        }

        ResultPath = "$.identifier_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]

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

        Next = "CheckIdentifierValidation"
      }

      # ----------------------------------------------------
      # VALIDATION DECISION
      # ----------------------------------------------------

      CheckIdentifierValidation = {
        Type = "Choice"

        Choices = [
          {
            And = [
              {
                Variable     = "$.identifier_result.Payload.validation_status"
                StringEquals = "VALIDATED"
              },
              {
                Variable     = "$.identifier_result.Payload.next_stage"
                StringEquals = "INVOICE_SCOPE_DETERMINATION"
              }
            ]

            Next = "PrepareInvoiceScopeInput"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # PREPARE INVOICE SCOPE INPUT
      # ----------------------------------------------------

      PrepareInvoiceScopeInput = {
        Type = "Pass"

        Parameters = {
          "case_id.$"           = "$.case_id"
          "message_id.$"        = "$.message_id"
          "invoice_ids.$"       = "$.identifier_result.Payload.invoice_ids"
          "validation_status.$" = "$.identifier_result.Payload.validation_status"
        }

        ResultPath = "$.scope_input"

        Next = "DetermineInvoiceScope"
      }

      # ----------------------------------------------------
      # 280 - INVOICE SCOPE LAMBDA
      # ----------------------------------------------------

      DetermineInvoiceScope = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::lambda:invoke"

        Parameters = {
          FunctionName = aws_lambda_function.invoice_scope.arn
          "Payload.$"  = "$.scope_input"
        }

        ResultPath = "$.scope_result"

        Retry = [
          {
            ErrorEquals = [
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException"
            ]

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

        Next = "CheckInvoiceScope"
      }

      # ----------------------------------------------------
      # CHECK SCOPE AND AURORA PERSISTENCE
      # ----------------------------------------------------

      CheckInvoiceScope = {
        Type = "Choice"

        Choices = [
          {
            And = [
              {
                Variable     = "$.scope_result.Payload.scope_status"
                StringEquals = "SCOPE_DETERMINED"
              },
              {
                Variable      = "$.scope_result.Payload.data_persisted"
                BooleanEquals = true
              },
              {
                Variable     = "$.scope_result.Payload.next_stage"
                StringEquals = "SQS2"
              }
            ]

            Next = "PublishToSQS2"
          }
        ]

        Default = "ManualReviewRequired"
      }

      # ----------------------------------------------------
      # 290 - SQS2
      # ----------------------------------------------------

      PublishToSQS2 = {
        Type     = "Task"
        Resource = "arn:${data.aws_partition.current.partition}:states:::sqs:sendMessage"

        Parameters = {
          QueueUrl = aws_sqs_queue.sqs2.id

          "MessageBody.$" = "States.JsonToString($.scope_result.Payload)"
        }

        ResultPath = "$.sqs2_result"

        Retry = [
          {
            ErrorEquals = ["States.TaskFailed"]

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

      # ----------------------------------------------------
      # FAILURE STATES
      # ----------------------------------------------------

      ManualReviewRequired = {
        Type  = "Fail"
        Error = "ManualReviewRequired"
        Cause = "Case requires manual review or integration completion"
      }

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
