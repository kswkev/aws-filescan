locals {
  upload_bucket     = "upload"
  clean_bucket      = "clean"
  quarantine_bucket = "quarantine"

  upload_queue = "upload-events"
  upload_dlq   = "upload-events-dlq"

  results_table = "file-scan-results"
  status_index  = "status-index"
}
