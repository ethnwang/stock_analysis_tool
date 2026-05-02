from __future__ import annotations

import json
import logging
import platform
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from integrations.plaid_sync import exchange_public_token

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).parent.parent / ".env"
DEFAULT_PORT = 8080

LINK_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
    <title>StockBot — Link {institution_title}</title>
    <script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            background: #f5f5f5;
        }}
        #status {{
            text-align: center;
            font-size: 1.2em;
            color: #333;
        }}
    </style>
</head>
<body>
    <div id="status">Connecting to {institution_title}...</div>
    <script>
        const handler = Plaid.create({{
            token: '{link_token}',
            onSuccess: async (publicToken, metadata) => {{
                document.getElementById('status').innerText = 'Exchanging token...';
                try {{
                    const resp = await fetch('/callback', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{
                            public_token: publicToken,
                            institution: '{institution}'
                        }})
                    }});
                    const result = await resp.json();
                    if (result.ok) {{
                        document.getElementById('status').innerText =
                            'Success! {institution_title} linked. You can close this tab.';
                    }} else {{
                        document.getElementById('status').innerText =
                            'Error: ' + result.error;
                    }}
                }} catch (err) {{
                    document.getElementById('status').innerText =
                        'Error: ' + err.message;
                }}
            }},
            onExit: (err, metadata) => {{
                if (err) {{
                    document.getElementById('status').innerText =
                        'Link exited with error: ' + (err.display_message || err.error_code);
                }} else {{
                    document.getElementById('status').innerText =
                        'Link cancelled. Close this tab and try again if needed.';
                }}
            }}
        }});
        handler.open();
    </script>
</body>
</html>
"""


def _update_env(key: str, value: str) -> None:
    lines = []
    found = False

    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()

    updated = []
    for line in lines:
        if line.startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)

    if not found:
        updated.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(updated) + "\n")
    ENV_PATH.chmod(0o600)


def run_link_server(
    link_token: str,
    institution: str,
    client_id: str,
    secret: str,
    plaid_env: str,
    port: int = DEFAULT_PORT,
) -> str:
    html = LINK_HTML_TEMPLATE.format(
        link_token=link_token,
        institution=institution,
        institution_title=institution.title(),
    )

    result: dict[str, str] = {}

    class LinkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(html.encode())
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            if self.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            public_token = body.get("public_token", "")

            try:
                access_token = exchange_public_token(client_id, secret, public_token, plaid_env)
                env_key = f"PLAID_ACCESS_TOKEN_{institution.upper()}"
                _update_env(env_key, access_token)
                result["access_token"] = access_token

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())

                threading.Thread(target=httpd.shutdown, daemon=True).start()
            except (ConnectionError, TimeoutError, ValueError) as exc:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(exc)}).encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    try:
        httpd = HTTPServer(("127.0.0.1", port), LinkHandler)
    except OSError as exc:
        logger.error("Could not start server on port %d: %s", port, exc)
        logger.error("Try closing other applications using that port.")
        return ""

    url = f"http://localhost:{port}"
    logger.info("Opening browser to %s", url)
    logger.info("Log into %s in the Plaid window...", institution.title())
    logger.info("(If browser doesn't open, go to %s manually)", url)

    if "microsoft" in platform.uname().release.lower():
        subprocess.Popen(["cmd.exe", "/c", "start", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Link cancelled.")
        httpd.server_close()
        return ""

    httpd.server_close()
    return result.get("access_token", "")
