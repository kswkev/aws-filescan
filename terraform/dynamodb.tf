# Step 2.5 - one row per scanned file, written by filescan, read by filegateway.
# Non-key attributes (fileName, bucket, objectKey, size) need no declaration.

resource "aws_dynamodb_table" "results" {
  name         = local.results_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "fileId"

  attribute {
    name = "fileId"
    type = "S"
  }

  attribute {
    name = "status"
    type = "S"
  }

  attribute {
    name = "scannedAt"
    type = "S"
  }

  # GET /files?status=CLEAN|QUARANTINE, newest first.
  global_secondary_index {
    name            = local.status_index
    projection_type = "ALL"

    key_schema {
      attribute_name = "status"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "scannedAt"
      key_type       = "RANGE"
    }
  }

  lifecycle {
    # The emulator reports on_demand_throughput {-1, -1} (unlimited), which is never
    # set here; without this every plan would show a perpetual diff.
    ignore_changes = [on_demand_throughput]
  }
}
