# Step 2.7 / 5.3. The dev proxy reads filegateway_url (`terraform output -raw filegateway_url`).

output "filegateway_url" {
  value = aws_lambda_function_url.filegateway.function_url
}

output "filegateway_function" {
  value = aws_lambda_function.filegateway.function_name
}

output "filescan_function" {
  value = aws_lambda_function.filescan.function_name
}

output "upload_bucket" {
  value = aws_s3_bucket.upload.bucket
}

output "clean_bucket" {
  value = aws_s3_bucket.clean.bucket
}

output "quarantine_bucket" {
  value = aws_s3_bucket.quarantine.bucket
}

output "upload_queue_url" {
  value = aws_sqs_queue.upload_events.url
}

output "upload_dlq_url" {
  value = aws_sqs_queue.upload_events_dlq.url
}

output "results_table" {
  value = aws_dynamodb_table.results.name
}

output "filegateway_role_arn" {
  value = aws_iam_role.filegateway.arn
}

output "filescan_role_arn" {
  value = aws_iam_role.filescan.arn
}
