import json
import os

import boto3
import pytest
from moto import mock_aws

from filegateway import handler as gateway

UPLOAD, CLEAN, QUARANTINE, TABLE, INDEX = "upload", "clean", "quarantine", "file-scan-results", "status-index"


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
        "STATUS_INDEX": INDEX,
    }.items():
        monkeypatch.setenv(var, value)
    # Never let tests talk to the real emulator, even if emulator-env.ps1 was loaded.
    for var in ("AWS_ENDPOINT_URL", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE",
                "AWS_CA_BUNDLE", "PUBLIC_S3_BASE", "URL_EXPIRY_SECONDS"):
        monkeypatch.delenv(var, raising=False)

    with mock_aws():
        gateway._s3.cache_clear()
        gateway._dynamodb.cache_clear()
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
                "IndexName": INDEX,
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "scannedAt", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        yield {"s3": s3, "ddb": ddb}
    gateway._s3.cache_clear()
    gateway._dynamodb.cache_clear()


def put_record(ddb, file_id, file_name, status, scanned_at, size=7):
    """Insert a row shaped like the one filescan writes (PLAN.MD step 4.4)."""
    bucket = CLEAN if status == "CLEAN" else QUARANTINE
    ddb.put_item(TableName=TABLE, Item={
        "fileId": {"S": file_id},
        "fileName": {"S": file_name},
        "status": {"S": status},
        "bucket": {"S": bucket},
        "objectKey": {"S": f"{file_id}/{file_name}"},
        "scannedAt": {"S": scanned_at},
        "size": {"N": str(size)},
    })


def request(method, path, body=None, query=None, base64_body=False):
    """Build a Lambda Function URL (payload v2) event and invoke the handler."""
    event = {
        "version": "2.0",
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}},
        "isBase64Encoded": False,
    }
    if query is not None:
        event["queryStringParameters"] = query
    if body is not None:
        raw = body if isinstance(body, str) else json.dumps(body)
        if base64_body:
            import base64
            raw = base64.b64encode(raw.encode()).decode()
            event["isBase64Encoded"] = True
        event["body"] = raw
    resp = gateway.handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]), resp
