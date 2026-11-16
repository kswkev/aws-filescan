# Phase 5 - deploy the two Python functions, wire SQS -> filescan, expose filegateway.
# The zips are built by Maven (`mvn package`) before Terraform runs; see docs/PLAN.MD 5.4.
# Runtime notes (docs/EMULATOR.MD): the emulator runs these as Python 3.14 with its own
# boto3; the zips must contain only the project's .py files.

locals {
  lambda_runtime = "python3.12"

  filegateway_zip = "${path.module}/../lambda/filegateway/target/filegateway.zip"
  filescan_zip    = "${path.module}/../lambda/filescan/target/filescan.zip"

  bucket_env = {
    UPLOAD_BUCKET     = aws_s3_bucket.upload.bucket
    CLEAN_BUCKET      = aws_s3_bucket.clean.bucket
    QUARANTINE_BUCKET = aws_s3_bucket.quarantine.bucket
    TABLE_NAME        = aws_dynamodb_table.results.name
  }
}

# Log groups are managed here so `terraform destroy` removes them too.
resource "aws_cloudwatch_log_group" "filegateway" {
  name              = "/aws/lambda/filegateway"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "filescan" {
  name              = "/aws/lambda/filescan"
  retention_in_days = 7
}

# ---------------------------------------------------------------- 5.1 functions

resource "aws_lambda_function" "filegateway" {
  function_name    = "filegateway"
  role             = aws_iam_role.filegateway.arn
  runtime          = local.lambda_runtime
  handler          = "filegateway.handler.handler"
  filename         = local.filegateway_zip
  source_code_hash = filebase64sha256(local.filegateway_zip)
  timeout          = 30
  memory_size      = 128

  environment {
    variables = merge(local.bucket_env, {
      STATUS_INDEX       = local.status_index
      URL_EXPIRY_SECONDS = tostring(var.url_expiry_seconds)
      PUBLIC_S3_BASE     = var.public_s3_base
    })
  }

  depends_on = [aws_iam_role_policy.filegateway, aws_cloudwatch_log_group.filegateway]
}

resource "aws_lambda_function" "filescan" {
  function_name    = "filescan"
  role             = aws_iam_role.filescan.arn
  runtime          = local.lambda_runtime
  handler          = "filescan.handler.handler"
  filename         = local.filescan_zip
  source_code_hash = filebase64sha256(local.filescan_zip)
  # Must stay <= the upload-events visibility timeout (60 s, sqs.tf).
  timeout     = 30
  memory_size = 128

  environment {
    variables = local.bucket_env
  }

  depends_on = [aws_iam_role_policy.filescan, aws_cloudwatch_log_group.filescan]
}

# ---------------------------------------------------------------- 5.2 SQS -> filescan

resource "aws_lambda_event_source_mapping" "upload_events" {
  event_source_arn                   = aws_sqs_queue.upload_events.arn
  function_name                      = aws_lambda_function.filescan.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 0
  # Only failed messages are retried (filescan returns batchItemFailures).
  function_response_types = ["ReportBatchItemFailures"]

  # The emulator validates sqs:ReceiveMessage on the role when the mapping is created.
  depends_on = [aws_iam_role_policy.filescan]
}

# ---------------------------------------------------------------- 5.3 filegateway URL

resource "aws_lambda_function_url" "filegateway" {
  function_name      = aws_lambda_function.filegateway.function_name
  authorization_type = "NONE"
  # No CORS: the browser only talks to the same-origin dev proxy (PLAN.MD decision 6).
}

# A public (auth NONE) Function URL needs both permissions; without them calls get 403.
resource "aws_lambda_permission" "filegateway_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.filegateway.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "filegateway_url_invoke" {
  statement_id             = "AllowInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.filegateway.function_name
  principal                = "*"
  invoked_via_function_url = true
}
