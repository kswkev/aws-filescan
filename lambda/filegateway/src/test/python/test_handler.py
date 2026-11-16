import json

from filegateway import handler


def test_placeholder_returns_json_response():
    event = {"rawPath": "/uploads", "requestContext": {"http": {"method": "POST"}}}

    resp = handler.handler(event, None)

    assert resp["statusCode"] == 501
    assert resp["headers"]["content-type"] == "application/json"
    assert json.loads(resp["body"]) == {"message": "not implemented", "method": "POST", "path": "/uploads"}
