# Step 2.3 - upload event queue with a dead-letter queue.

resource "aws_sqs_queue" "upload_events_dlq" {
  name                      = local.upload_dlq
  message_retention_seconds = 1209600 # 14 days, to inspect failures
}

resource "aws_sqs_queue" "upload_events" {
  name = local.upload_queue
  # Must be >= the filescan Lambda timeout (30 s) so in-flight messages are not redelivered.
  visibility_timeout_seconds = 60

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.upload_events_dlq.arn
    maxReceiveCount     = 3
  })
}

# Only the upload bucket may publish to the queue.
resource "aws_sqs_queue_policy" "upload_events" {
  queue_url = aws_sqs_queue.upload_events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowUploadBucketNotifications"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.upload_events.arn
      Condition = {
        ArnLike = { "aws:SourceArn" = aws_s3_bucket.upload.arn }
      }
    }]
  })
}
