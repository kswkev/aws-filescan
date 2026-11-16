import json
import re

import pytest

from conftest import (CLEAN, QUARANTINE, TEST_EVENT_BODY, UPLOAD, keys, record, s3_event_body, sqs_event,
                      upload)
from filescan import handler as scanner

FILE_ID = "3a8a8223-21dd-467d-82b8-8d97033329c2"


# ----------------------------------------------------------------------------- 4.1 parsing

def test_test_event_is_skipped(aws):
    assert scanner.handler(sqs_event(TEST_EVENT_BODY), None) == {"batchItemFailures": []}
    assert aws["ddb"].scan(TableName="file-scan-results")["Count"] == 0


def test_url_encoded_keys_are_decoded(aws, force):
    force("CLEAN")
    key = f"{FILE_ID}/my report (final) & more+.txt"
    upload(aws["s3"], key)

    assert scanner.handler(sqs_event(s3_event_body(key)), None) == {"batchItemFailures": []}
    assert keys(aws["s3"], CLEAN) == [key]
    assert record(aws["ddb"], FILE_ID)["fileName"] == "my report (final) & more+.txt"


def test_non_create_events_are_ignored(aws):
    upload(aws["s3"], f"{FILE_ID}/a.txt")
    resp = scanner.handler(sqs_event(s3_event_body(f"{FILE_ID}/a.txt", event_name="ObjectRemoved:Delete")), None)
    assert resp == {"batchItemFailures": []}
    assert keys(aws["s3"], UPLOAD) == [f"{FILE_ID}/a.txt"]


def test_events_for_other_buckets_are_ignored(aws):
    aws["s3"].put_object(Bucket=CLEAN, Key="x/y.txt", Body=b"x")
    assert scanner.handler(sqs_event(s3_event_body("x/y.txt", bucket=CLEAN)), None) == {"batchItemFailures": []}
    assert record(aws["ddb"], "x") is None


@pytest.mark.parametrize("key, expected", [
    (f"{FILE_ID}/a.txt", (FILE_ID, "a.txt")),
    (f"{FILE_ID}/dir/a.txt", (FILE_ID, "dir/a.txt")),
    ("loose-file.txt", ("loose-file.txt", "loose-file.txt")),
    ("/leading", ("/leading", "/leading")),
    ("trailing/", ("trailing/", "trailing/")),
])
def test_split_key(key, expected):
    assert scanner.split_key(key) == expected


# ----------------------------------------------------------------------------- 4.2-4.4 scan, move, record

@pytest.mark.parametrize("decision, bucket, other", [("CLEAN", CLEAN, QUARANTINE), ("QUARANTINE", QUARANTINE, CLEAN)])
def test_object_is_moved_and_recorded(aws, force, decision, bucket, other):
    force(decision)
    key = f"{FILE_ID}/report.pdf"
    upload(aws["s3"], key, data=b"%PDF-1.7 fake", content_type="application/pdf")

    resp = scanner.handler(sqs_event(s3_event_body(key)), None)

    assert resp == {"batchItemFailures": []}
    assert keys(aws["s3"], UPLOAD) == []
    assert keys(aws["s3"], bucket) == [key]
    assert keys(aws["s3"], other) == []
    moved = aws["s3"].get_object(Bucket=bucket, Key=key)
    assert moved["Body"].read() == b"%PDF-1.7 fake"
    assert moved["ContentType"] == "application/pdf"  # metadata survives the copy

    rec = record(aws["ddb"], FILE_ID)
    assert rec["status"] == decision
    assert rec["bucket"] == bucket
    assert rec["fileName"] == "report.pdf"
    assert rec["objectKey"] == key
    assert rec["size"] == "13"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", rec["scannedAt"])


def test_decide_is_random_and_only_returns_known_statuses():
    seen = {scanner.decide() for _ in range(200)}
    assert seen == {"CLEAN", "QUARANTINE"}


def test_record_is_visible_through_status_index(aws, force):
    force("QUARANTINE")
    upload(aws["s3"], f"{FILE_ID}/a.txt")
    scanner.handler(sqs_event(s3_event_body(f"{FILE_ID}/a.txt")), None)

    items = aws["ddb"].query(
        TableName="file-scan-results", IndexName="status-index",
        KeyConditionExpression="#s = :s", ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": {"S": "QUARANTINE"}},
    )["Items"]
    assert [i["fileId"]["S"] for i in items] == [FILE_ID]


def test_multiple_records_in_one_message(aws, force):
    force("CLEAN")
    k1, k2 = "11111111-1111-1111-1111-111111111111/a.txt", "22222222-2222-2222-2222-222222222222/b.txt"
    upload(aws["s3"], k1)
    upload(aws["s3"], k2)
    body = json.loads(s3_event_body(k1))
    body["Records"] += json.loads(s3_event_body(k2))["Records"]

    assert scanner.handler(sqs_event(json.dumps(body)), None) == {"batchItemFailures": []}
    assert sorted(keys(aws["s3"], CLEAN)) == sorted([k1, k2])


# ----------------------------------------------------------------------------- 4.5 errors & idempotency

def test_only_failed_messages_are_reported(aws, force):
    force("CLEAN")
    upload(aws["s3"], f"{FILE_ID}/good.txt")
    event = sqs_event(
        "this is not json",                       # msg-0: malformed
        s3_event_body(f"{FILE_ID}/good.txt"),     # msg-1: fine
        TEST_EVENT_BODY,                          # msg-2: skipped
        json.dumps({"Records": [{"eventName": "ObjectCreated:Put"}]}),  # msg-3: missing s3 block
    )

    resp = scanner.handler(event, None)

    assert resp == {"batchItemFailures": [{"itemIdentifier": "msg-0"}, {"itemIdentifier": "msg-3"}]}
    assert keys(aws["s3"], CLEAN) == [f"{FILE_ID}/good.txt"]


def test_duplicate_delivery_is_a_no_op(aws, force):
    force("CLEAN", "QUARANTINE")  # a second roll would disagree
    key = f"{FILE_ID}/a.txt"
    upload(aws["s3"], key)
    event = sqs_event(s3_event_body(key))

    assert scanner.handler(event, None) == {"batchItemFailures": []}
    first = record(aws["ddb"], FILE_ID)
    assert scanner.handler(event, None) == {"batchItemFailures": []}

    assert record(aws["ddb"], FILE_ID) == first
    assert keys(aws["s3"], CLEAN) == [key]
    assert keys(aws["s3"], QUARANTINE) == []


def test_retry_after_partial_failure_reuses_recorded_decision(aws, force, monkeypatch):
    """Crash after the record is written but before the copy: the retry must finish the
    original move rather than roll again (which could put the file in both buckets)."""
    force("QUARANTINE", "CLEAN")
    key = f"{FILE_ID}/a.txt"
    upload(aws["s3"], key)
    event = sqs_event(s3_event_body(key))

    real_s3 = scanner._s3()

    class FailingCopy:
        def __getattr__(self, name):
            return getattr(real_s3, name)

        def copy_object(self, **kwargs):
            raise RuntimeError("simulated crash")

    with monkeypatch.context() as patched:
        patched.setattr(scanner, "_s3", lambda: FailingCopy())
        assert scanner.handler(event, None) == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert record(aws["ddb"], FILE_ID)["status"] == "QUARANTINE"
    assert keys(aws["s3"], UPLOAD) == [key]

    assert scanner.handler(event, None) == {"batchItemFailures": []}
    assert keys(aws["s3"], QUARANTINE) == [key]
    assert keys(aws["s3"], CLEAN) == []
    assert keys(aws["s3"], UPLOAD) == []


def test_missing_source_without_record_is_skipped(aws):
    resp = scanner.handler(sqs_event(s3_event_body(f"{FILE_ID}/deleted-before-scan.txt")), None)
    assert resp == {"batchItemFailures": []}
    assert record(aws["ddb"], FILE_ID) is None


def test_storage_errors_fail_the_message(aws, monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "no-such-table")
    upload(aws["s3"], f"{FILE_ID}/a.txt")
    resp = scanner.handler(sqs_event(s3_event_body(f"{FILE_ID}/a.txt")), None)
    assert resp == {"batchItemFailures": [{"itemIdentifier": "msg-0"}]}
    assert keys(aws["s3"], UPLOAD) == [f"{FILE_ID}/a.txt"]  # nothing moved


@pytest.mark.parametrize("exc, expected", [
    (type("E", (Exception,), {"response": {"Error": {"Code": "404"}}})(), True),
    (type("E", (Exception,), {"response": {"Error": {"Code": "NoSuchKey"}}})(), True),
    (type("E", (Exception,), {"response": {"ResponseMetadata": {"HTTPStatusCode": 404}}})(), True),
    (type("E", (Exception,), {"response": {"Error": {"Code": "AccessDenied"}}})(), False),
    (RuntimeError("boom"), False),
])
def test_is_not_found(exc, expected):
    assert scanner._is_not_found(exc) is expected


def test_empty_event():
    assert scanner.handler({}, None) == {"batchItemFailures": []}
    assert scanner.handler(None, None) == {"batchItemFailures": []}
