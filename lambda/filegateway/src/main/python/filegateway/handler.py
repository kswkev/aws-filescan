"""filegateway Lambda: HTTP API behind a Lambda Function URL.

Routes (paths are relative to the Function URL; the web dev proxy maps /api/* here):
  POST /uploads           body {"fileName", "contentType"} -> pre-signed PUT URL for the upload bucket
  GET  /files/{fileId}    -> scan status of one upload (PENDING until filescan records a result)
  GET  /files?status=S    -> files with status CLEAN or QUARANTINE, newest first

Constraints (docs/EMULATOR.MD): standard library + boto3 only, and code must run on
Python 3.12 and 3.14. The emulator's boto3 is a stand-in, so DynamoDB items are
(de)serialised by hand instead of via boto3.dynamodb.types.

Environment:
  UPLOAD_BUCKET, CLEAN_BUCKET, QUARANTINE_BUCKET, TABLE_NAME   required
  STATUS_INDEX        GSI on (status, scannedAt), default "status-index"
  URL_EXPIRY_SECONDS  lifetime of pre-signed URLs, default 300
  PUBLIC_S3_BASE      if set (locally "/s3"), replaces the scheme+host of every
                      pre-signed URL so the browser reaches S3 through the dev proxy
"""
import base64
import functools
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

STATUSES = ("CLEAN", "QUARANTINE")
MAX_FILE_NAME_LENGTH = 255
MAX_LIST_LIMIT = 1000
DEFAULT_LIST_LIMIT = 100
DEFAULT_CONTENT_TYPE = "application/octet-stream"
_CONTENT_TYPE_RE = re.compile(r"^[\w.+-]+/[\w.+-]+(\s*;\s*[\w.+-]+=[\w.+\"-]+)*$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class HttpError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ----------------------------------------------------------------------------- config & clients

def _env(name, default=None):
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


@functools.lru_cache(maxsize=None)
def _s3():
    # Path-style matches the emulator, and keeps the bucket in the URL path so that
    # PUBLIC_S3_BASE rewriting only has to swap scheme + host.
    # SigV4 explicitly: botocore otherwise presigns with legacy SigV2 in us-east-1.
    return boto3.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))


@functools.lru_cache(maxsize=None)
def _dynamodb():
    return boto3.client("dynamodb")


def _expiry_seconds():
    return int(_env("URL_EXPIRY_SECONDS", "300"))


# ----------------------------------------------------------------------------- helpers

def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }


def public_url(url):
    """Rewrite a pre-signed URL's scheme+host to PUBLIC_S3_BASE (if set).

    The signature covers the original host, so the dev proxy must forward the request
    with that Host header (s3.us-east-1.amazonaws.com in the emulator).
    """
    base = os.environ.get("PUBLIC_S3_BASE")
    if not base:
        return url
    parts = urlsplit(url)
    rewritten = base.rstrip("/") + parts.path
    if parts.query:
        rewritten += "?" + parts.query
    return rewritten


def sanitize_file_name(raw):
    """Return a safe object-key file name, or raise HttpError(400)."""
    if not isinstance(raw, str):
        raise HttpError(400, "fileName is required")
    # Drop any directory part, whichever separator the browser/OS used.
    name = re.split(r"[\\/]", raw)[-1]
    name = _CONTROL_CHARS_RE.sub("", name).strip()
    if name in ("", ".", ".."):
        raise HttpError(400, "fileName is required")
    if len(name) > MAX_FILE_NAME_LENGTH:
        raise HttpError(400, f"fileName must be at most {MAX_FILE_NAME_LENGTH} characters")
    return name


def _content_type(raw):
    if raw in (None, ""):
        return DEFAULT_CONTENT_TYPE
    if not isinstance(raw, str) or len(raw) > 255 or not _CONTENT_TYPE_RE.match(raw.strip()):
        raise HttpError(400, "contentType is not a valid MIME type")
    return raw.strip()


def _json_body(event):
    body = event.get("body")
    if not body:
        raise HttpError(400, "request body is required")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HttpError(400, "request body must be valid JSON")
    if not isinstance(parsed, dict):
        raise HttpError(400, "request body must be a JSON object")
    return parsed


def _from_item(item):
    """Plain dict from a low-level DynamoDB item (S and N attributes only)."""
    out = {}
    for key, typed in item.items():
        if "S" in typed:
            out[key] = typed["S"]
        elif "N" in typed:
            number = typed["N"]
            out[key] = int(number) if re.fullmatch(r"-?\d+", number) else float(number)
    return out


def _file_view(record):
    """API representation of a scan record, including a download link."""
    view = {
        "fileId": record.get("fileId"),
        "fileName": record.get("fileName"),
        "status": record.get("status"),
        "bucket": record.get("bucket"),
        "scannedAt": record.get("scannedAt"),
        "size": record.get("size"),
        "downloadUrl": None,
    }
    allowed_buckets = (_env("CLEAN_BUCKET"), _env("QUARANTINE_BUCKET"))
    if record.get("bucket") in allowed_buckets and record.get("objectKey"):
        view["downloadUrl"] = public_url(_s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": record["bucket"], "Key": record["objectKey"]},
            ExpiresIn=_expiry_seconds(),
        ))
    return view


# ----------------------------------------------------------------------------- routes

def create_upload(event):
    body = _json_body(event)
    file_name = sanitize_file_name(body.get("fileName"))
    content_type = _content_type(body.get("contentType"))
    file_id = str(uuid.uuid4())
    key = f"{file_id}/{file_name}"
    expiry = _expiry_seconds()

    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": _env("UPLOAD_BUCKET"), "Key": key, "ContentType": content_type},
        ExpiresIn=expiry,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expiry)
    logger.info("issued upload url fileId=%s key=%s", file_id, key)
    return _response(201, {
        "fileId": file_id,
        "fileName": file_name,
        "contentType": content_type,
        "uploadUrl": public_url(url),
        "expiresAt": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    })


def get_file(file_id):
    try:
        file_id = str(uuid.UUID(file_id))
    except ValueError:
        raise HttpError(400, "fileId must be a UUID")

    result = _dynamodb().get_item(TableName=_env("TABLE_NAME"), Key={"fileId": {"S": file_id}})
    item = result.get("Item")
    if not item:
        return _response(200, {"fileId": file_id, "status": "PENDING"})
    return _response(200, _file_view(_from_item(item)))


def list_files(query):
    status = (query.get("status") or "").upper()
    if status not in STATUSES:
        raise HttpError(400, "status must be CLEAN or QUARANTINE")
    try:
        limit = int(query.get("limit") or DEFAULT_LIST_LIMIT)
    except ValueError:
        raise HttpError(400, "limit must be an integer")
    limit = max(1, min(limit, MAX_LIST_LIMIT))

    records = []
    kwargs = {
        "TableName": _env("TABLE_NAME"),
        "IndexName": _env("STATUS_INDEX", "status-index"),
        # "status" is a DynamoDB reserved word.
        "KeyConditionExpression": "#status = :status",
        "ExpressionAttributeNames": {"#status": "status"},
        "ExpressionAttributeValues": {":status": {"S": status}},
        "ScanIndexForward": False,  # newest scannedAt first
    }
    while len(records) < limit:
        page = _dynamodb().query(Limit=limit - len(records), **kwargs)
        records.extend(_from_item(i) for i in page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    return _response(200, {"status": status, "files": [_file_view(r) for r in records[:limit]]})


def route(event):
    http = event.get("requestContext", {}).get("http", {})
    method = (http.get("method") or "").upper()
    path = (event.get("rawPath") or http.get("path") or "/").rstrip("/") or "/"
    query = event.get("queryStringParameters") or {}

    if path == "/uploads":
        if method != "POST":
            raise HttpError(405, "method not allowed")
        return create_upload(event)
    if path == "/files":
        if method != "GET":
            raise HttpError(405, "method not allowed")
        return list_files(query)
    match = re.fullmatch(r"/files/([^/]+)", path)
    if match:
        if method != "GET":
            raise HttpError(405, "method not allowed")
        return get_file(match.group(1))
    raise HttpError(404, "not found")


def handler(event, context):
    try:
        return route(event or {})
    except HttpError as e:
        return _response(e.status_code, {"message": e.message})
    except Exception:
        logger.exception("unhandled error")
        return _response(500, {"message": "internal error"})
