import json
from urllib.parse import quote_plus

import boto3
import pytest
from moto import mock_aws

from filescan import handler as scanner

UPLOAD, CLEAN, QUARANTINE, TABLE = "upload", "clean", "quarantine", "file-scan-results"


@pytest.fixture
def aws(monkeypatch):
    """Moto-backed S3 + DynamoDB matching the Terraform layout (terraform/*.tf)."""
    for var, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "UPLOAD_BUCKET": UPLOAD,
        "CLEAN_BUCKET": CLEAN,
        "QUARANTINE_BUCKET": QUARANTINE,
        "TABLE_NAME": TABLE,
    }.items():
        monkeypatch.setenv(var, value)
    # Never let tests talk to the real emulator, even if emulator-env.ps1 was loaded.
    for var in ("AWS_ENDPOINT_URL", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_CA_BUNDLE"):
        monkeypatch.delenv(var, raising=False)

    with mock_aws():
        scanner._s3.cache_clear()
        scanner._dynamodb.cache_clear()
        s3 = boto3.client("s3")
        for bucket in (UPLOAD, CLEAN, QUARANTINE):
            s3.create_bucket(Bucket=bucket)
        ddb = boto3.client("dynamodb")
        ddb.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "fileId", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "scannedAt", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "fileId", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "status-index",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "scannedAt", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        yield {"s3": s3, "ddb": ddb}
    scanner._s3.cache_clear()
    scanner._dynamodb.cache_clear()


@pytest.fixture
def force(monkeypatch):
    """force("CLEAN") / force("QUARANTINE", "CLEAN") - scripted scan decisions."""
    def _force(*decisions):
        seq = list(decisions)
        monkeypatch.setattr(scanner, "decide", lambda: seq.pop(0) if len(seq) > 1 else seq[0])
    return _force


# Shapes captured from the aws-emulator's real S3 -> SQS delivery (Phase 0 probe).
def s3_event_body(key, bucket=UPLOAD, size=7, event_name="ObjectCreated:Put"):
    return json.dumps({"Records": [{
        "eventVersion": "2.1",
        "eventSource": "aws:s3",
        "awsRegion": "us-east-1",
        "eventTime": "2026-11-16T15:58:21.068827Z",
        "eventName": event_name,
        "userIdentity": {"principalId": "f7b11509f4d675c3"},
        "requestParameters": {"sourceIPAddress": "127.0.0.1"},
        "responseElements": {"x-amz-request-id": "27668CE2F105E268", "x-amz-id-2": "27668ce2f105e268"},
        "s3": {
            "s3SchemaVersion": "1.0",
            "configurationId": "23bedc06c60e1ab162afcf5d1a08066d",
            "bucket": {"name": bucket, "ownerIdentity": {"principalId": "f7b11509f4d675c3"}, "arn": f"arn:aws:s3:::{bucket}"},
            # S3 URL-encodes keys in notifications (spaces become '+').
            "object": {"key": quote_plus(key, safe="/"), "sequencer": "18E891BF023319A8", "size": size, "eTag": "af5597c29467a96523a70787c319f4db"},
        },
    }]})


TEST_EVENT_BODY = json.dumps({
    "Service": "Amazon S3", "Event": "s3:TestEvent", "Time": "2026-11-16T15:58:20.511744Z",
    "Bucket": UPLOAD, "RequestId": "7589FED7E78DD215", "HostId": "7589fed7e78dd215",
})


def sqs_event(*bodies):
    return {"Records": [{
        "messageId": f"msg-{i}",
        "receiptHandle": f"rh-{i}",
        "body": body,
        "attributes": {"ApproximateReceiveCount": "1"},
        "messageAttributes": {},
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:upload-events",
        "awsRegion": "us-east-1",
    } for i, body in enumerate(bodies)]}


def upload(s3, key, data=b"hello", content_type="text/plain"):
    s3.put_object(Bucket=UPLOAD, Key=key, Body=data, ContentType=content_type)


def keys(s3, bucket):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket).get("Contents", [])]


def record(ddb, file_id):
    item = ddb.get_item(TableName=TABLE, Key={"fileId": {"S": file_id}}).get("Item")
    return {k: next(iter(v.values())) for k, v in item.items()} if item else None
