"""filegateway Lambda: entry point for the Function URL.

Placeholder created in Phase 1. Routes are implemented in Phase 3:
  POST /uploads          -> pre-signed upload URL
  GET  /files/{fileId}   -> scan status of one upload
  GET  /files?status=... -> list clean / quarantined files

Runs on python3.12 (declared) and Python 3.14 (what the emulator actually uses):
standard library + boto3 only.
"""
import json


def _response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event, context):
    http = (event or {}).get("requestContext", {}).get("http", {})
    return _response(501, {
        "message": "not implemented",
        "method": http.get("method"),
        "path": (event or {}).get("rawPath"),
    })
