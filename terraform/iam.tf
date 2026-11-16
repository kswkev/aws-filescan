# Step 2.6 - execution roles. The emulator ENFORCES IAM, including for calls made
# from inside functions (docs/EMULATOR.MD), so these are the exact permissions needed.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "logs" {
  statement {
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:*:*:*"]
  }
}

# ---------------------------------------------------------------- filegateway

resource "aws_iam_role" "filegateway" {
  name               = "filegateway-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "filegateway" {
  source_policy_documents = [data.aws_iam_policy_document.logs.json]

  # Pre-signed PUT URLs carry the signer's permissions.
  statement {
    sid       = "SignUploads"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.upload.arn}/*"]
  }

  # Pre-signed GET URLs for download links.
  statement {
    sid     = "SignDownloads"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.clean.arn}/*",
      "${aws_s3_bucket.quarantine.arn}/*",
    ]
  }

  statement {
    sid     = "ReadResults"
    actions = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      aws_dynamodb_table.results.arn,
      "${aws_dynamodb_table.results.arn}/index/${local.status_index}",
    ]
  }
}

resource "aws_iam_role_policy" "filegateway" {
  name   = "filegateway"
  role   = aws_iam_role.filegateway.id
  policy = data.aws_iam_policy_document.filegateway.json
}

# ---------------------------------------------------------------- filescan

resource "aws_iam_role" "filescan" {
  name               = "filescan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "filescan" {
  source_policy_documents = [data.aws_iam_policy_document.logs.json]

  # Required by the SQS event source mapping.
  statement {
    sid       = "ConsumeUploadEvents"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.upload_events.arn]
  }

  statement {
    sid       = "TakeFromUpload"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.upload.arn}/*"]
  }

  # Without ListBucket, S3 answers HeadObject on a missing key with 403 instead of 404,
  # so filescan could not tell "already moved" (a duplicate delivery) from "access denied".
  statement {
    sid       = "DetectMissingUploads"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.upload.arn]
  }

  statement {
    sid     = "PlaceScannedFile"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.clean.arn}/*",
      "${aws_s3_bucket.quarantine.arn}/*",
    ]
  }

  statement {
    sid       = "RecordResult"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem"]
    resources = [aws_dynamodb_table.results.arn]
  }
}

resource "aws_iam_role_policy" "filescan" {
  name   = "filescan"
  role   = aws_iam_role.filescan.id
  policy = data.aws_iam_policy_document.filescan.json
}
