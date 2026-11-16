#!/usr/bin/env python3
"""Local dev proxy for the AWS FileScan web UI (standard library only).

    python web/src/main/python/devproxy.py            then open http://127.0.0.1:8080

Why it exists (docs/PLAN.MD decision 6, docs/EMULATOR.MD): pre-signed URLs generated
inside the emulator are signed for host s3.us-east-1.amazonaws.com, which a browser can
neither resolve nor override. The proxy gives the browser a single same-origin endpoint:

    /          static UI from web/target/dist
    /api/*     -> filegateway Function URL (terraform output filegateway_url)
    /s3/*      -> emulator, sent with  Host: s3.us-east-1.amazonaws.com  so the
                  pre-signed signature still matches (GET / HEAD / PUT only)

TLS to the emulator is verified against its CA (.emulator/ca.pem, written by
scripts/emulator-env.ps1). The proxy listens on 127.0.0.1 only.

Downloads come from the same origin as the UI, so a user-uploaded HTML file must never
render there: /s3 responses are forced to download (Content-Disposition: attachment),
with nosniff and a sandbox CSP. The UI itself is served with a strict CSP.
"""
import argparse
import http.client
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[4]  # web/src/main/python/devproxy.py -> repo root
CHUNK = 64 * 1024
UPSTREAM_TIMEOUT = 60

# Only these request headers are forwarded upstream. Anything else (cookies, origin,
# x-amz-*) is dropped so the browser cannot alter what the signature covers.
FORWARD_REQUEST_HEADERS = ("content-type", "accept", "range", "if-match", "if-none-match", "if-modified-since")
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
              "transfer-encoding", "upgrade", "server", "date"}

UI_SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
DOWNLOAD_SECURITY_HEADERS = {
    "Content-Disposition": "attachment",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
    "Referrer-Policy": "no-referrer",
}

log = logging.getLogger("devproxy")
_QUERY_RE = re.compile(r"\?\S*")


def redact(text):
    """Strip query strings (pre-signed signatures / session tokens) from log lines."""
    return _QUERY_RE.sub("?<redacted>", text)  # ASCII only: Windows consoles mangle '…'


class Upstream:
    """A base URL to forward to, plus the Host header to present."""

    def __init__(self, base_url, ca_file=None, host_header=None):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"invalid upstream URL: {base_url!r}")
        self.base_url = base_url
        self.scheme = parts.scheme
        self.hostname = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.base_path = parts.path.rstrip("/")
        self.host_header = host_header or parts.netloc
        self.ssl_context = ssl.create_default_context(cafile=ca_file) if parts.scheme == "https" else None

    def connect(self):
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.hostname, self.port, timeout=UPSTREAM_TIMEOUT,
                                               context=self.ssl_context)
        return http.client.HTTPConnection(self.hostname, self.port, timeout=UPSTREAM_TIMEOUT)

    def target(self, rest):
        """Upstream request target for the part of the path after the route prefix.
        `rest` keeps its original percent-encoding and query string untouched, because
        the S3 signature covers the exact encoded path and query."""
        return self.base_path + "/" + rest.lstrip("/")


class _BodyReader:
    """File-like view of exactly `remaining` bytes of the request body."""

    def __init__(self, stream, remaining):
        self.stream, self.remaining = stream, remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        size = self.remaining if size is None or size < 0 else min(size, self.remaining)
        data = self.stream.read(size)
        self.remaining -= len(data)
        return data


def make_handler(dist_dir, api, s3):
    class ProxyHandler(SimpleHTTPRequestHandler):
        server_version = "filescan-devproxy"
        sys_version = ""

        def __init__(self, *args, **kwargs):
            self._extra_headers = {}
            super().__init__(*args, directory=str(dist_dir), **kwargs)

        # ---- dispatch
        def do_GET(self):
            self._dispatch()

        def do_HEAD(self):
            self._dispatch()

        def do_POST(self):
            self._dispatch()

        def do_PUT(self):
            self._dispatch()

        def do_DELETE(self):
            self._dispatch()

        def do_PATCH(self):
            self._dispatch()

        def do_OPTIONS(self):
            self._dispatch()

        def _dispatch(self):
            self._extra_headers = {}
            path = self.path
            if path == "/api" or path.startswith("/api/") or path.startswith("/api?"):
                return self._forward(api, path[len("/api"):], download=False)
            if path.startswith("/s3/"):
                if self.command not in ("GET", "HEAD", "PUT"):
                    return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "only GET, HEAD and PUT are proxied to S3")
                return self._forward(s3, path[len("/s3"):], download=self.command in ("GET", "HEAD"))
            if self.command not in ("GET", "HEAD"):
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")
            self._extra_headers = UI_SECURITY_HEADERS
            return super().do_GET() if self.command == "GET" else super().do_HEAD()

        # ---- static files
        def list_directory(self, path):
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return None

        def end_headers(self):
            for name, value in self._extra_headers.items():
                self.send_header(name, value)
            super().end_headers()

        # ---- proxying
        def _forward(self, upstream, rest, download):
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                return self._error(HTTPStatus.LENGTH_REQUIRED, "chunked request bodies are not supported")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")

            conn = upstream.connect()
            try:
                conn.putrequest(self.command, upstream.target(rest), skip_host=True, skip_accept_encoding=True)
                conn.putheader("Host", upstream.host_header)
                for name in FORWARD_REQUEST_HEADERS:
                    value = self.headers.get(name)
                    if value is not None:
                        conn.putheader(name.title(), value)  # e.g. "Content-Type"
                if length or self.command in ("POST", "PUT", "PATCH"):
                    conn.putheader("Content-Length", str(length))
                conn.endheaders()
                body = _BodyReader(self.rfile, length)
                while chunk := body.read(CHUNK):
                    conn.send(chunk)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                log.warning("upstream %s unavailable: %s", upstream.base_url, exc)
                return self._error(HTTPStatus.BAD_GATEWAY, f"upstream unavailable: {type(exc).__name__}")

            try:
                self.send_response(resp.status, resp.reason)
                overridden = {k.lower() for k in DOWNLOAD_SECURITY_HEADERS} if download else set()
                for name, value in resp.getheaders():
                    lname = name.lower()
                    if lname in HOP_BY_HOP or lname in overridden:
                        continue
                    self.send_header(name, value)
                self._extra_headers = DOWNLOAD_SECURITY_HEADERS if download else {}
                self.end_headers()
                if self.command != "HEAD":
                    shutil.copyfileobj(resp, self.wfile, CHUNK)
            finally:
                conn.close()

        def _error(self, status, message):
            body = json.dumps({"message": message}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._extra_headers = {}
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), redact(fmt % args))

    return ProxyHandler


def make_server(host, port, dist_dir, api, s3):
    return ThreadingHTTPServer((host, port), make_handler(dist_dir, api, s3))


def resolve_api_url(explicit):
    """--api-url, else $FILEGATEWAY_URL, else `terraform output -raw filegateway_url`."""
    if explicit:
        return explicit
    if os.environ.get("FILEGATEWAY_URL"):
        return os.environ["FILEGATEWAY_URL"]
    terraform = shutil.which("terraform")
    if not terraform:
        sys.exit("terraform not found on PATH: pass --api-url or set FILEGATEWAY_URL")
    result = subprocess.run([terraform, f"-chdir={REPO_ROOT / 'terraform'}", "output", "-raw", "filegateway_url"],
                            capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip().startswith("http"):
        sys.exit("could not read filegateway_url from Terraform (deploy first with scripts/deploy.ps1), "
                 "or pass --api-url")
    return result.stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1", help="listen address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--dist", type=Path, default=REPO_ROOT / "web" / "target" / "dist",
                        help="built UI directory (mvn package -pl web)")
    parser.add_argument("--api-url", help="filegateway Function URL (default: Terraform output)")
    parser.add_argument("--emulator", default="https://127.0.0.1:9297", help="emulator endpoint for /s3")
    parser.add_argument("--s3-host", default="s3.us-east-1.amazonaws.com",
                        help="Host header the pre-signed URLs were signed for")
    parser.add_argument("--ca", type=Path, default=REPO_ROOT / ".emulator" / "ca.pem",
                        help="emulator CA bundle (written by scripts/emulator-env.ps1)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not (args.dist / "index.html").is_file():
        sys.exit(f"{args.dist} has no index.html: build the UI first (mvn package -pl web)")
    api_url = resolve_api_url(args.api_url)
    needs_ca = urlsplit(api_url).scheme == "https" or urlsplit(args.emulator).scheme == "https"
    if needs_ca and not args.ca.is_file():
        sys.exit(f"emulator CA not found at {args.ca}: run  . .\\scripts\\emulator-env.ps1  first")
    ca = str(args.ca) if needs_ca else None

    api = Upstream(api_url, ca_file=ca)
    s3 = Upstream(args.emulator, ca_file=ca, host_header=args.s3_host)
    server = make_server(args.host, args.port, args.dist, api, s3)
    log.info("AWS FileScan UI on http://%s:%d", args.host, server.server_address[1])
    log.info("  /api -> %s", api_url)
    log.info("  /s3  -> %s (Host: %s)", args.emulator, args.s3_host)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
