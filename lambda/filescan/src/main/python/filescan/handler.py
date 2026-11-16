"""filescan Lambda: consumes S3 ObjectCreated events from the upload-events SQS queue.

Placeholder created in Phase 1. The scan logic is implemented in Phase 4:
parse the S3 event, randomly choose CLEAN or QUARANTINE, move the object to the
matching bucket and record the result in DynamoDB.

Runs on python3.12 (declared) and Python 3.14 (what the emulator actually uses):
standard library + boto3 only.
"""


def handler(event, context):
    # Partial-batch response: an empty list means every message succeeded.
    return {"batchItemFailures": []}
