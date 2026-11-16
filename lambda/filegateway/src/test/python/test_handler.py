import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from conftest import CLEAN, QUARANTINE, UPLOAD, put_record, request
from filegateway import handler as gateway


# ----------------------------------------------------------------------------- 3.1 POST /uploads

def test_create_upload_returns_presigned_put_for_upload_bucket(aws):
    status, body, resp = request("POST", "/uploads", {"fileName": "report.pdf", "contentType": "application/pdf"})

    assert status == 201
    assert resp["headers"]["content-type"] == "application/json"
    assert set(body) == {"fileId", "fileName", "contentType", "uploadUrl", "expiresAt"}
    uuid.UUID(body["fileId"])  # valid UUID
    assert body["fileName"] == "report.pdf"
    assert body["contentType"] == "application/pdf"

    url = urlsplit(body["uploadUrl"])
    assert url.scheme == "https"
    assert url.path == f"/{UPLOAD}/{body['fileId']}/report.pdf"  # path-style, key = <fileId>/<name>
    qs = parse_qs(url.query)
    assert "X-Amz-Signature" in qs
    assert qs["X-Amz-Expires"] == ["300"]
    assert "content-type" in qs["X-Amz-SignedHeaders"][0]


def test_create_upload_expires_at_is_utc_iso_about_five_minutes_ahead(aws):
    _, body, _ = request("POST", "/uploads", {"fileName": "a.txt"})

    expires = datetime.fromisoformat(body["expiresAt"].replace("Z", "+00:00"))
    delta = (expires - datetime.now(timezone.utc)).total_seconds()
    assert body["expiresAt"].endswith("Z")
    assert 290 <= delta <= 301


def test_create_upload_defaults_content_type(aws):
    _, body, _ = request("POST", "/uploads", {"fileName": "blob.bin"})
    assert body["contentType"] == "application/octet-stream"


def test_create_upload_ids_are_unique(aws):
    ids = {request("POST", "/uploads", {"fileName": "same.txt"})[1]["fileId"] for _ in range(5)}
    assert len(ids) == 5


def test_create_upload_accepts_base64_body(aws):
    status, body, _ = request("POST", "/uploads", {"fileName": "b64.txt"}, base64_body=True)
    assert status == 201 and body["fileName"] == "b64.txt"


def test_expiry_is_configurable(aws, monkeypatch):
    monkeypatch.setenv("URL_EXPIRY_SECONDS", "60")
    _, body, _ = request("POST", "/uploads", {"fileName": "a.txt"})
    assert parse_qs(urlsplit(body["uploadUrl"]).query)["X-Amz-Expires"] == ["60"]


@pytest.mark.parametrize("raw, expected", [
    ("../../etc/passwd", "passwd"),
    ("C:\\Users\\me\\Documents\\cv.docx", "cv.docx"),
    ("  spaced name.txt  ", "spaced name.txt"),
    ("tab\there.txt", "tabhere.txt"),
    ("résumé – final.pdf", "résumé – final.pdf"),
])
def test_file_names_are_sanitised(aws, raw, expected):
    status, body, _ = request("POST", "/uploads", {"fileName": raw})
    assert status == 201
    assert body["fileName"] == expected
    assert urlsplit(body["uploadUrl"]).path.endswith("/" + expected) or "%" in body["uploadUrl"]


@pytest.mark.parametrize("payload", [
    {},
    {"fileName": ""},
    {"fileName": "   "},
    {"fileName": "../"},
    {"fileName": ".."},
    {"fileName": 42},
    {"fileName": "x" * 256},
    {"fileName": "a.txt", "contentType": "not a mime type"},
    {"fileName": "a.txt", "contentType": "text/html\r\nX-Evil: 1"},
])
def test_create_upload_rejects_invalid_input(aws, payload):
    status, body, _ = request("POST", "/uploads", payload)
    assert status == 400
    assert body["message"]


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"string"'])
def test_create_upload_rejects_bad_body(aws, raw):
    status, _, _ = request("POST", "/uploads", raw) if raw is not None else request("POST", "/uploads")
    assert status == 400


# ----------------------------------------------------------------------------- 3.2 GET /files/{fileId}

def test_get_file_pending_when_not_scanned_yet(aws):
    file_id = str(uuid.uuid4())
    status, body, _ = request("GET", f"/files/{file_id}")
    assert status == 200
    assert body == {"fileId": file_id, "status": "PENDING"}


@pytest.mark.parametrize("scan_status, bucket", [("CLEAN", CLEAN), ("QUARANTINE", QUARANTINE)])
def test_get_file_returns_result_with_download_link(aws, scan_status, bucket):
    file_id = str(uuid.uuid4())
    put_record(aws["ddb"], file_id, "doc.txt", scan_status, "2026-11-16T10:00:00Z", size=42)

    status, body, _ = request("GET", f"/files/{file_id}")

    assert status == 200
    assert body["fileId"] == file_id
    assert body["status"] == scan_status
    assert body["bucket"] == bucket
    assert body["fileName"] == "doc.txt"
    assert body["size"] == 42
    assert body["scannedAt"] == "2026-11-16T10:00:00Z"
    url = urlsplit(body["downloadUrl"])
    assert url.path == f"/{bucket}/{file_id}/doc.txt"
    assert "X-Amz-Signature" in parse_qs(url.query)


def test_get_file_does_not_sign_urls_for_unexpected_buckets(aws):
    file_id = str(uuid.uuid4())
    aws["ddb"].put_item(TableName="file-scan-results", Item={
        "fileId": {"S": file_id}, "status": {"S": "CLEAN"}, "bucket": {"S": UPLOAD},
        "objectKey": {"S": f"{file_id}/x"}, "scannedAt": {"S": "2026-11-16T10:00:00Z"},
    })
    _, body, _ = request("GET", f"/files/{file_id}")
    assert body["downloadUrl"] is None


def test_get_file_rejects_non_uuid(aws):
    status, _, _ = request("GET", "/files/not-a-uuid")
    assert status == 400


# ----------------------------------------------------------------------------- 3.3 GET /files?status=

def test_list_files_returns_only_requested_status_newest_first(aws):
    ids = {}
    for name, st, ts in [
        ("old.txt", "CLEAN", "2026-11-16T09:00:00Z"),
        ("new.txt", "CLEAN", "2026-11-16T11:00:00Z"),
        ("mid.txt", "CLEAN", "2026-11-16T10:00:00Z"),
        ("bad.txt", "QUARANTINE", "2026-11-16T10:30:00Z"),
    ]:
        ids[name] = str(uuid.uuid4())
        put_record(aws["ddb"], ids[name], name, st, ts)

    status, body, _ = request("GET", "/files", query={"status": "clean"})  # case-insensitive

    assert status == 200
    assert body["status"] == "CLEAN"
    assert [f["fileName"] for f in body["files"]] == ["new.txt", "mid.txt", "old.txt"]
    assert all(f["downloadUrl"] for f in body["files"])

    _, body, _ = request("GET", "/files", query={"status": "QUARANTINE"})
    assert [(f["fileId"], f["bucket"]) for f in body["files"]] == [(ids["bad.txt"], QUARANTINE)]


def test_list_files_empty(aws):
    status, body, _ = request("GET", "/files", query={"status": "QUARANTINE"})
    assert status == 200 and body["files"] == []


def test_list_files_respects_limit(aws):
    for i in range(5):
        put_record(aws["ddb"], str(uuid.uuid4()), f"f{i}.txt", "CLEAN", f"2026-11-16T10:0{i}:00Z")
    _, body, _ = request("GET", "/files", query={"status": "CLEAN", "limit": "2"})
    assert [f["fileName"] for f in body["files"]] == ["f4.txt", "f3.txt"]


@pytest.mark.parametrize("query", [None, {}, {"status": "PENDING"}, {"status": "CLEAN", "limit": "abc"}])
def test_list_files_rejects_bad_query(aws, query):
    status, _, _ = request("GET", "/files", query=query)
    assert status == 400


# ----------------------------------------------------------------------------- 3.4 PUBLIC_S3_BASE

def test_public_url_unchanged_when_base_not_set(monkeypatch):
    monkeypatch.delenv("PUBLIC_S3_BASE", raising=False)
    url = "https://s3.us-east-1.amazonaws.com/upload/k?X-Amz-Signature=abc"
    assert gateway.public_url(url) == url


@pytest.mark.parametrize("base", ["/s3", "/s3/"])
def test_public_url_swaps_scheme_and_host(monkeypatch, base):
    monkeypatch.setenv("PUBLIC_S3_BASE", base)
    url = "https://s3.us-east-1.amazonaws.com/upload/id/a%20b.txt?X-Amz-Signature=abc&X-Amz-Expires=300"
    assert gateway.public_url(url) == "/s3/upload/id/a%20b.txt?X-Amz-Signature=abc&X-Amz-Expires=300"


def test_upload_and_download_urls_use_public_base(aws, monkeypatch):
    monkeypatch.setenv("PUBLIC_S3_BASE", "/s3")
    _, up, _ = request("POST", "/uploads", {"fileName": "a.txt"})
    assert up["uploadUrl"].startswith(f"/s3/{UPLOAD}/{up['fileId']}/a.txt?")

    put_record(aws["ddb"], up["fileId"], "a.txt", "CLEAN", "2026-11-16T10:00:00Z")
    _, got, _ = request("GET", f"/files/{up['fileId']}")
    assert got["downloadUrl"].startswith(f"/s3/{CLEAN}/{up['fileId']}/a.txt?")


# ----------------------------------------------------------------------------- routing & errors

@pytest.mark.parametrize("method, path, expected", [
    ("GET", "/", 404),
    ("GET", "/nope", 404),
    ("GET", "/uploads", 405),
    ("DELETE", "/files", 405),
    ("POST", f"/files/{uuid.uuid4()}", 405),
    ("GET", "/files/a/b", 404),
])
def test_routing_errors(aws, method, path, expected):
    status, body, _ = request(method, path)
    assert status == expected
    assert body["message"]


def test_trailing_slash_is_tolerated(aws):
    status, _, _ = request("POST", "/uploads/", {"fileName": "a.txt"})
    assert status == 201


def test_unexpected_errors_return_500_without_details(aws, monkeypatch):
    monkeypatch.delenv("TABLE_NAME")
    status, body, _ = request("GET", f"/files/{uuid.uuid4()}")
    assert status == 500
    assert body == {"message": "internal error"}
