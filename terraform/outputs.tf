# Step 2.7. The filegateway Function URL output is added with the function in Phase 5.

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
