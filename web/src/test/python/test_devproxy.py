"""devproxy tests against a fake (plain HTTP) upstream that records what it receives."""
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import devproxy


class FakeUpstream(BaseHTTPRequestHandler):
    """Records each request; answers like S3 / the Function URL would."""
    received = []

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        FakeUpstream.received.append({
            "method": self.command, "path": self.path, "headers": dict(self.headers.items()), "body": body,
        })
        if self.path.startswith("/fail"):
            payload, status, ctype = b"<Error/>", 403, "application/xml"
        elif self.command == "POST":
            payload, status, ctype = json.dumps({"fileId": "abc"}).encode(), 201, "application/json"
        elif self.command == "PUT":
            payload, status, ctype = b"", 200, "text/plain"
        else:
            payload, status, ctype = b"<script>alert(1)</script>", 200, "text/html"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", '"etag-1"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_HEAD = do_DELETE = _handle

    def log_message(self, *args):
        pass


def _serve(server):
    # Short poll interval so shutdown() in teardown is fast.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    return server


@pytest.fixture
def proxy(tmp_path):
    FakeUpstream.received = []
    upstream = _serve(ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream))
    up = f"http://127.0.0.1:{upstream.server_address[1]}"

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!DOCTYPE html><title>ui</title>")
    (dist / "app.js").write_text("console.log('ui');")

    api = devproxy.Upstream(f"{up}/_aws/lambda-url/abc123/")
    s3 = devproxy.Upstream(up, host_header="s3.us-east-1.amazonaws.com")
    server = _serve(devproxy.make_server("127.0.0.1", 0, dist, api, s3))
    port = server.server_address[1]

    def call(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    yield call, up
    server.shutdown()
    server.server_close()
    upstream.shutdown()
    upstream.server_close()


# ----------------------------------------------------------------------------- static UI

def test_serves_index_with_security_headers(proxy):
    call, _ = proxy
    resp, body = call("GET", "/")
    assert resp.status == 200
    assert b"<title>ui</title>" in body
    assert "default-src 'self'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Content-Type-Options") == "nosniff"


def test_serves_assets(proxy):
    call, _ = proxy
    resp, body = call("GET", "/app.js")
    assert resp.status == 200 and body == b"console.log('ui');"


def test_no_directory_listing(proxy):
    call, _ = proxy
    resp, _ = call("GET", "/assets/")
    assert resp.status == 404


def test_static_rejects_writes(proxy):
    call, _ = proxy
    resp, body = call("POST", "/index.html", body=b"x")
    assert resp.status == 405
    assert json.loads(body)["message"]


# ----------------------------------------------------------------------------- /api

def test_api_post_is_forwarded_to_function_url(proxy):
    call, up = proxy
    payload = json.dumps({"fileName": "a.txt", "contentType": "text/plain"}).encode()
    resp, body = call("POST", "/api/uploads", body=payload,
                      headers={"Content-Type": "application/json", "Cookie": "secret=1", "Origin": "http://evil"})

    assert resp.status == 201
    assert json.loads(body) == {"fileId": "abc"}
    req = FakeUpstream.received[-1]
    assert req["method"] == "POST"
    assert req["path"] == "/_aws/lambda-url/abc123/uploads"
    assert req["body"] == payload
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["headers"]["Host"] == up.split("//")[1]
    assert "Cookie" not in req["headers"] and "Origin" not in req["headers"]


def test_api_query_string_is_preserved(proxy):
    call, _ = proxy
    call("GET", "/api/files?status=CLEAN&limit=5")
    assert FakeUpstream.received[-1]["path"] == "/_aws/lambda-url/abc123/files?status=CLEAN&limit=5"


def test_api_responses_are_not_forced_to_download(proxy):
    call, _ = proxy
    resp, _ = call("GET", "/api/files/x")
    assert resp.getheader("Content-Disposition") is None


# ----------------------------------------------------------------------------- /s3

SIGNED = ("/upload/0f1e/a%20b%2Bc.txt?x-id=PutObject&X-Amz-Algorithm=AWS4-HMAC-SHA256"
          "&X-Amz-Signature=deadbeef&X-Amz-Security-Token=a%2Fb%2Bc%3D")


def test_s3_put_keeps_exact_path_and_signs_for_s3_host(proxy):
    call, _ = proxy
    data = bytes(range(256)) * 4096  # 1 MiB, binary
    resp, _ = call("PUT", "/s3" + SIGNED, body=data, headers={"Content-Type": "application/pdf"})

    assert resp.status == 200
    req = FakeUpstream.received[-1]
    assert req["method"] == "PUT"
    assert req["path"] == SIGNED  # no re-encoding: the signature covers path + query
    assert req["headers"]["Host"] == "s3.us-east-1.amazonaws.com"
    assert req["headers"]["Content-Type"] == "application/pdf"
    assert req["body"] == data


def test_s3_get_is_forced_to_download_safely(proxy):
    call, _ = proxy
    resp, body = call("GET", "/s3/clean/0f1e/page.html?X-Amz-Signature=x")

    assert resp.status == 200
    assert body == b"<script>alert(1)</script>"
    assert resp.getheader("Content-Disposition") == "attachment"
    assert resp.getheader("Content-Security-Policy") == "sandbox"
    assert resp.getheader("X-Content-Type-Options") == "nosniff"
    assert resp.getheader("ETag") == '"etag-1"'


def test_s3_head(proxy):
    call, _ = proxy
    resp, body = call("HEAD", "/s3/clean/k?sig=1")
    assert resp.status == 200 and body == b""
    assert FakeUpstream.received[-1]["method"] == "HEAD"


def test_s3_errors_are_passed_through(proxy):
    call, _ = proxy
    resp, body = call("GET", "/s3/fail/k")
    assert resp.status == 403 and body == b"<Error/>"


@pytest.mark.parametrize("method", ["DELETE", "POST"])
def test_s3_only_allows_get_head_put(proxy, method):
    call, _ = proxy
    before = len(FakeUpstream.received)
    resp, _ = call(method, "/s3/upload/k", body=b"")
    assert resp.status == 405
    assert len(FakeUpstream.received) == before  # never reached upstream


# ----------------------------------------------------------------------------- failures & logging

def test_upstream_down_gives_502(tmp_path):
    (tmp_path / "index.html").write_text("ui")
    dead = devproxy.Upstream("http://127.0.0.1:9")  # nothing listens on the discard port
    server = _serve(devproxy.make_server("127.0.0.1", 0, tmp_path, dead, dead))
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request("GET", "/api/files?status=CLEAN")
        resp = conn.getresponse()
        assert resp.status == 502
        assert "upstream unavailable" in json.loads(resp.read())["message"]
    finally:
        server.shutdown()
        server.server_close()


def test_log_lines_hide_signatures():
    line = '"PUT /s3/upload/k?X-Amz-Signature=abc&X-Amz-Security-Token=tok HTTP/1.1" 200 -'
    redacted = devproxy.redact(line)
    assert "abc" not in redacted and "tok" not in redacted
    assert "/s3/upload/k?<redacted>" in redacted


@pytest.mark.parametrize("url", ["ftp://x", "not a url", "https://"])
def test_invalid_upstream_urls_are_rejected(url):
    with pytest.raises(ValueError):
        devproxy.Upstream(url)


def test_upstream_target_joins_paths():
    assert devproxy.Upstream("https://h:1/_aws/lambda-url/id/").target("/uploads") == "/_aws/lambda-url/id/uploads"
    assert devproxy.Upstream("https://h:1").target("/bucket/k?q=1") == "/bucket/k?q=1"
