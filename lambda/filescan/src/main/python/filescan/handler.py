"""filescan Lambda: consumes S3 ObjectCreated events from the upload-events SQS queue.

For every uploaded object it "scans" the file by randomly choosing CLEAN or QUARANTINE,
moves the object to the matching bucket and records the result in DynamoDB.

Ordering is chosen so that retries are safe (SQS is at-least-once, and the function can
fail part-way through):

  1. Look up the record for the fileId. If one exists, reuse its decision; otherwise
     decide now and write the record BEFORE touching S3. A retry therefore always
     re-applies the same decision instead of rolling the dice again, so a file can never
     end up in both buckets.
  2. Copy upload/<key> -> clean|quarantine/<key> (overwrite is harmless).
  3. Delete upload/<key>.

If the source object is already gone, the file was either fully processed earlier or
deleted by someone else; both are treated as success so the message is not retried.

Failed messages are reported individually via batchItemFailures (the event source
mapping uses ReportBatchItemFailures), so one bad message never forces the whole batch
to be retried. Messages that keep failing land in upload-events-dlq after 3 attempts.

Constraints (docs/EMULATOR.MD): standard library + boto3 only; Python 3.12 and 3.14.

Environment:
  UPLOAD_BUCKET, CLEAN_BUCKET, QUARANTINE_BUCKET, TABLE_NAME   required
"""
import functools
import json
import logging
import os
import random
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLEAN, QUARANTINE = "CLEAN", "QUARANTINE"
_random = random.SystemRandom()


# ----------------------------------------------------------------------------- config & clients

def _env(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@functools.lru_cache(maxsize=None)
def _s3():
    return boto3.client("s3")


@functools.lru_cache(maxsize=None)
def _dynamodb():
    return boto3.client("dynamodb")


def _is_not_found(exc):
    """True for S3 404s. Checked structurally: the emulator's boto3 is a stand-in, so
    don't rely on botocore exception classes.

    S3 only returns 404 for a missing key if the caller has s3:ListBucket on the bucket;
    otherwise it returns 403, which is (correctly) NOT treated as missing here. The
    filescan role therefore needs ListBucket on the upload bucket (terraform/iam.tf)."""
    error = getattr(exc, "response", None) or {}
    code = str(error.get("Error", {}).get("Code", ""))
    status = error.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404


# ----------------------------------------------------------------------------- scanning

def decide():
    """The simulated virus scan: a fair coin flip. Patched in tests."""
    return _random.choice((CLEAN, QUARANTINE))


def target_bucket(status):
    return _env("CLEAN_BUCKET") if status == CLEAN else _env("QUARANTINE_BUCKET")


def split_key(key):
    """upload/<fileId>/<fileName> (as issued by filegateway) -> (fileId, fileName).
    Keys without a '/' (e.g. uploaded by hand) use the whole key for both."""
    file_id, sep, file_name = key.partition("/")
    return (file_id, file_name) if sep and file_id and file_name else (key, key)


def _now():
    # Millisecond precision; this exact format sorts chronologically as a string,
    # which the status-index range key relies on.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _get_record(file_id):
    item = _dynamodb().get_item(TableName=_env("TABLE_NAME"), Key={"fileId": {"S": file_id}}).get("Item")
    return {k: next(iter(v.values())) for k, v in item.items()} if item else None


def _put_record(file_id, file_name, status, key, size):
    _dynamodb().put_item(TableName=_env("TABLE_NAME"), Item={
        "fileId": {"S": file_id},
        "fileName": {"S": file_name},
        "status": {"S": status},
        "bucket": {"S": target_bucket(status)},
        "objectKey": {"S": key},
        "scannedAt": {"S": _now()},
        "size": {"N": str(int(size))},
    })


def process_object(bucket, key):
    """Scan and move one uploaded object. Raises on failure (the message is retried)."""
    upload_bucket = _env("UPLOAD_BUCKET")
    if bucket != upload_bucket:
        logger.warning("ignoring event for unexpected bucket=%s key=%s", bucket, key)
        return None

    file_id, file_name = split_key(key)
    record = _get_record(file_id)

    try:
        head = _s3().head_object(Bucket=upload_bucket, Key=key)
    except Exception as exc:
        if not _is_not_found(exc):
            raise
        if record:
            logger.info("already processed fileId=%s status=%s", file_id, record.get("status"))
        else:
            logger.warning("source object gone and never scanned, skipping key=%s", key)
        return record.get("status") if record else None

    if record and record.get("objectKey") == key:
        status = record["status"]  # retry: re-apply the original decision
        logger.info("resuming fileId=%s with recorded status=%s", file_id, status)
    else:
        status = decide()
        _put_record(file_id, file_name, status, key, head.get("ContentLength", 0))

    destination = target_bucket(status)
    _s3().copy_object(Bucket=destination, Key=key, CopySource={"Bucket": upload_bucket, "Key": key})
    _s3().delete_object(Bucket=upload_bucket, Key=key)
    logger.info("scanned fileId=%s key=%s status=%s -> %s", file_id, key, status, destination)
    return status


def process_message(body):
    """Handle one SQS message body (an S3 event notification)."""
    message = json.loads(body)
    if message.get("Event") == "s3:TestEvent":
        logger.info("skipping s3:TestEvent")
        return
    for record in message.get("Records", []):
        if not record.get("eventName", "").startswith("ObjectCreated"):
            logger.info("skipping event %s", record.get("eventName"))
            continue
        s3 = record["s3"]
        process_object(s3["bucket"]["name"], unquote_plus(s3["object"]["key"]))


def handler(event, context):
    failures = []
    for message in (event or {}).get("Records", []):
        message_id = message.get("messageId")
        try:
            process_message(message.get("body") or "")
        except Exception:
            logger.exception("failed to process messageId=%s", message_id)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
