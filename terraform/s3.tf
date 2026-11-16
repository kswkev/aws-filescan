# Step 2.2 - buckets. No CORS rules: the browser only talks to the same-origin dev proxy
# (docs/PLAN.MD decision 6). force_destroy lets `terraform destroy` clean up a local
# emulator even when the buckets still hold test files.

resource "aws_s3_bucket" "upload" {
  bucket        = local.upload_bucket
  force_destroy = true
}

resource "aws_s3_bucket" "clean" {
  bucket        = local.clean_bucket
  force_destroy = true
}

resource "aws_s3_bucket" "quarantine" {
  bucket        = local.quarantine_bucket
  force_destroy = true
}

# Step 2.4 - S3 -> SQS on every new upload. The queue policy must exist first,
# otherwise S3 cannot validate the destination.
resource "aws_s3_bucket_notification" "upload" {
  bucket = aws_s3_bucket.upload.id

  queue {
    queue_arn = aws_sqs_queue.upload_events.arn
    events    = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_sqs_queue_policy.upload_events]
}
