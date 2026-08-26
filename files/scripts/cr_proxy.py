"""Local stand-in for `gcloud run services proxy`.

Why this exists: the real command needs the `cloud-run-proxy` gcloud component,
and installing it writes to the SDK directory under Program Files, which needs
Administrator. This does the same job with the standard library.

Identical in shape to the real thing: binds localhost, attaches the caller's
identity token, forwards every request to the Cloud Run service, returns the
response untouched. It serves NOTHING of its own -- there is no local copy of
the app here, so every byte the browser renders came from Cloud Run.

    python cr_proxy.py <SERVICE_URL> [PORT]

The service URL is an argument, not a constant: nothing project-specific is
baked into this file. The identity token is fetched fresh at startup from
`gcloud auth print-identity-token` and never written to disk. Tokens last about
an hour -- if quotes start failing mid-session, restart this.

Nothing in this file is deployment-specific, which is why it can live in the
repo: no project id, no service URL, no token. It is an operator convenience for
reaching a private service, not part of the deployed system -- the Dockerfile
does not copy `scripts/`, so it never enters the image.
"""

import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if len(sys.argv) < 2:
    sys.exit(__doc__.strip().split("\n\n")[3].strip())

TARGET = sys.argv[1].rstrip("/")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080

print("fetching an identity token from gcloud ...", flush=True)
proc = subprocess.run(
    ["gcloud", "auth", "print-identity-token"],
    capture_output=True, text=True, shell=True,
)
TOKEN = proc.stdout.strip()
if not TOKEN:
    sys.exit(
        "could not obtain an identity token.\n"
        f"gcloud said: {proc.stderr.strip()[:400]}\n"
        "Run `gcloud auth login` and try again."
    )

# Hop-by-hop headers, plus Authorization which we replace ourselves.
HOP = {"host", "connection", "content-length", "transfer-encoding",
       "keep-alive", "upgrade", "authorization"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Authorization"] = f"Bearer {TOKEN}"

        request = urllib.request.Request(
            TARGET + self.path, data=body, headers=headers, method=method
        )
        try:
            # A quote runs the agent through two Vertex round trips: 15-40s
            # normally, more on a cold start. 300s leaves plenty of headroom,
            # which is the whole point of not relying on someone else's proxy
            # timeout during a recording.
            with urllib.request.urlopen(request, timeout=300) as response:
                payload, status, out = response.read(), response.status, response.headers
        except urllib.error.HTTPError as e:
            payload, status, out = e.read(), e.code, e.headers
        except Exception as e:  # noqa: BLE001
            payload, status, out = f"proxy error: {e}".encode(), 502, {}

        self.send_response(status)
        for k, v in (out.items() if out else []):
            # Content-Encoding is dropped: urllib already decoded the body, so
            # echoing the header would tell the browser to decode it twice.
            if k.lower() not in HOP and k.lower() != "content-encoding":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def log_message(self, fmt, *args):
        sys.stderr.write("  -> cloud run: " + fmt % args + "\n")


print(f"forwarding  http://127.0.0.1:{PORT}  ->  {TARGET}", flush=True)
print("open the first URL in a browser. Ctrl+C to stop.", flush=True)
try:
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
except KeyboardInterrupt:
    print("\nstopped.")
