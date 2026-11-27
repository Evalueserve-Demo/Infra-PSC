# =========================================================
# 590 - REGIONAL REST API GATEWAY
# Project: Boeing Helpdesk Payment Status Check
# =========================================================

locals {
  backend_gateway_name = "${var.project_name}-${var.environment}-backend-gateway"
}

# ---------------------------------------------------------
# 1. REGIONAL REST API GATEWAY
# ---------------------------------------------------------

resource "aws_api_gateway_rest_api" "backend" {
  name        = local.backend_gateway_name
  description = "PSC backend API for Next.js frontend"

  endpoint_configuration {
    types = ["REGIONAL"]
  }

  tags = {
    Name = local.backend_gateway_name
  }
}

# ---------------------------------------------------------
# 2. /cases RESOURCE
# ---------------------------------------------------------

resource "aws_api_gateway_resource" "cases" {
  rest_api_id = aws_api_gateway_rest_api.backend.id
  parent_id   = aws_api_gateway_rest_api.backend.root_resource_id
  path_part   = "cases"
}

# ---------------------------------------------------------
# 3. /cases/{id} RESOURCE
# ---------------------------------------------------------

resource "aws_api_gateway_resource" "case_details" {
  rest_api_id = aws_api_gateway_rest_api.backend.id
  parent_id   = aws_api_gateway_resource.cases.id
  path_part   = "{id}"
}

# ---------------------------------------------------------
# 4. GET /cases METHOD
# ---------------------------------------------------------

resource "aws_api_gateway_method" "get_cases" {
  rest_api_id      = aws_api_gateway_rest_api.backend.id
  resource_id      = aws_api_gateway_resource.cases.id
  http_method      = "GET"
  authorization    = "AWS_IAM"
  api_key_required = false
}

# ---------------------------------------------------------
# 5. GET /cases/{id} METHOD
# ---------------------------------------------------------

resource "aws_api_gateway_method" "get_case_details" {
  rest_api_id      = aws_api_gateway_rest_api.backend.id
  resource_id      = aws_api_gateway_resource.case_details.id
  http_method      = "GET"
  authorization    = "AWS_IAM"
  api_key_required = false
}

# ---------------------------------------------------------
# 6. GET /cases LAMBDA INTEGRATION
# ---------------------------------------------------------

resource "aws_api_gateway_integration" "get_cases" {
  rest_api_id = aws_api_gateway_rest_api.backend.id
  resource_id = aws_api_gateway_resource.cases.id
  http_method = aws_api_gateway_method.get_cases.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.backend_api.invoke_arn
}

# ---------------------------------------------------------
# 7. GET /cases/{id} LAMBDA INTEGRATION
# ---------------------------------------------------------

resource "aws_api_gateway_integration" "get_case_details" {
  rest_api_id = aws_api_gateway_rest_api.backend.id
  resource_id = aws_api_gateway_resource.case_details.id
  http_method = aws_api_gateway_method.get_case_details.http_method

  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = aws_lambda_function.backend_api.invoke_arn
}

# ---------------------------------------------------------
# 8. API GATEWAY LAMBDA INVOCATION PERMISSION
# ---------------------------------------------------------

resource "aws_lambda_permission" "backend_api_gateway" {
  statement_id  = "AllowPSCAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.backend_api.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_api_gateway_rest_api.backend.execution_arn}/${var.environment}/GET/cases"
}

resource "aws_lambda_permission" "backend_api_gateway_details" {
  statement_id  = "AllowPSCAPIGatewayDetailsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.backend_api.function_name
  principal     = "apigateway.amazonaws.com"

  source_arn = "${aws_api_gateway_rest_api.backend.execution_arn}/${var.environment}/GET/cases/*"
}

# ---------------------------------------------------------
# 9. API DEPLOYMENT
# ---------------------------------------------------------

resource "aws_api_gateway_deployment" "backend" {
  rest_api_id = aws_api_gateway_rest_api.backend.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.cases,
      aws_api_gateway_resource.case_details,
      aws_api_gateway_method.get_cases,
      aws_api_gateway_method.get_case_details,
      aws_api_gateway_integration.get_cases,
      aws_api_gateway_integration.get_case_details
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.get_cases,
    aws_api_gateway_integration.get_case_details,
    aws_lambda_permission.backend_api_gateway,
    aws_lambda_permission.backend_api_gateway_details
  ]
}

# ---------------------------------------------------------
# 10. API STAGE
# ---------------------------------------------------------

resource "aws_api_gateway_stage" "backend" {
  deployment_id = aws_api_gateway_deployment.backend.id
  rest_api_id   = aws_api_gateway_rest_api.backend.id
  stage_name    = var.environment

  tags = {
    Name = "${local.backend_gateway_name}-stage"
  }
}

# ---------------------------------------------------------
# 11. OUTPUTS
# ---------------------------------------------------------

output "backend_api_gateway_id" {
  description = "PSC Regional REST API Gateway ID"
  value       = aws_api_gateway_rest_api.backend.id
}

output "backend_api_gateway_execution_arn" {
  description = "API Gateway execution ARN for ECS task IAM policy"
  value       = aws_api_gateway_rest_api.backend.execution_arn
}

output "backend_api_gateway_url" {
  description = "PSC backend API base URL"
  value       = aws_api_gateway_stage.backend.invoke_url
}

output "backend_api_cases_url" {
  description = "PSC cases API endpoint"
  value       = "${aws_api_gateway_stage.backend.invoke_url}/cases"
}