"""Opt-in LAN file transfers, isolated from the loopback mirroring controls."""

from __future__ import annotations

import hmac
import io
import json
import secrets
import shutil
import socket
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

WEB = Path(__file__).resolve().parent / "web"
MAX_FILE_SIZE = 1024 * 1024 * 1024
LAN_PORT = 8767


def local_addresses():
    from handset.wifi import _local_ipv4

    addresses = [ip for ip in _local_ipv4() if not ip.startswith(("169.254.", "100."))]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 53))  # Route lookup only; no packets are sent.
            primary = sock.getsockname()[0]
            if primary in addresses:
                addresses.remove(primary)
                addresses.insert(0, primary)
        except OSError:
            pass
    return list(dict.fromkeys(addresses))


class TransferManager:
    def __init__(self, folder=None, port=LAN_PORT):
        self.folder = Path(folder or Path.home() / "Downloads" / "Pane")
        self.port = port
        self.lock = threading.RLock()
        self.server = None
        self.token = None
        self.code = None
        self.pair_failures = []
        self.files = {}
        self.last_seen = 0

    def start(self):
        with self.lock:
            if self.server is None:
                manager = self

                class PhoneHandler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        manager.handle(self, "GET", False)

                    def do_POST(self):
                        manager.handle(self, "POST", False)

                    def log_message(self, *args):
                        pass

                class FileServer(ThreadingHTTPServer):
                    allow_reuse_address = False
                    daemon_threads = True

                server = FileServer(("0.0.0.0", self.port), PhoneHandler)
                self.token = secrets.token_urlsafe(32)
                self.code = f"{secrets.randbelow(100_000_000):08d}"
                self.pair_failures = []
                self.server = server
                self.last_seen = 0
                threading.Thread(target=server.serve_forever, name="pane-files", daemon=True).start()
        return self.status()

    def stop(self):
        with self.lock:
            server, self.server = self.server, None
            self.token = None
            self.code = None
            self.pair_failures = []
            self.last_seen = 0
        if server:
            server.shutdown()
            server.server_close()
        return self.status()

    def status(self):
        with self.lock:
            port = self.server.server_port if self.server else self.port
            urls = [f"http://{ip}:{port}/#{self.token}" for ip in local_addresses()] if self.token else []
            return {
                "enabled": self.server is not None,
                "connected": bool(self.token and time.time() - self.last_seen < 15),
                "urls": urls,
                "code": self.code,
                "folder": str(self.folder / "Received"),
                "maxSize": MAX_FILE_SIZE,
                "files": [{k: v for k, v in item.items() if k != "path"} for item in self.files.values()],
            }

    @staticmethod
    def reply(handler, payload, status=200, headers=None):
        body = json.dumps(payload).encode()
        TransferManager.send(handler, body, "application/json; charset=utf-8", status, headers)

    @staticmethod
    def send(handler, body, kind, status=200, headers=None):
        handler.send_response(status)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
        for key, value in (headers or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionError):
            pass

    def authorized(self, handler):
        cookie = SimpleCookie()
        try:
            cookie.load(handler.headers.get("Cookie", ""))
            supplied = cookie["pane_files"].value if "pane_files" in cookie else ""
        except Exception:
            return False
        with self.lock:
            return bool(self.token and supplied.isascii() and hmac.compare_digest(supplied, self.token))

    def handle(self, handler, method, desktop):
        path = urlparse(handler.path).path
        # Never expose the mirror, device APIs, or arbitrary repo files on the LAN listener.
        assets = {"/": "files-phone.html", "/files.js": "files.js", "/files.css": "files.css", "/logo.svg": "logo.svg"}
        if not desktop and method == "GET" and path in assets:
            name = assets[path]
            kind = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}[Path(name).suffix]
            self.send(handler, (WEB / name).read_bytes(), kind + "; charset=utf-8")
            return True
        if not path.startswith("/api/files"):
            if desktop:
                return False
            self.reply(handler, {"error": "Not found."}, 404)
            return True
        if desktop and urlparse("http://" + handler.headers.get("Host", "")).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.reply(handler, {"error": "Open Pane through localhost to manage files."}, 403)
            handler.close_connection = True
            return True
        # Same-origin writes, including pairing. No CORS or cross-site form access.
        origin = handler.headers.get("Origin")
        expected_origin = "http://" + handler.headers.get("Host", "")
        if (origin and origin != expected_origin) or handler.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(handler, {"error": "Open the transfer page directly to continue."}, 403)
            handler.close_connection = True
            return True
        if not desktop and path == "/api/files/pair" and method == "POST":
            try:
                length = int(handler.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError()
                payload = json.loads(handler.rfile.read(length))
                token = payload.get("token", "")
                code = payload.get("code", "")
                with self.lock:
                    valid = isinstance(token, str) and token.isascii() and self.token and hmac.compare_digest(token, self.token)
                    if not valid:
                        now = time.monotonic()
                        self.pair_failures = [t for t in self.pair_failures if now - t < 60]
                        if len(self.pair_failures) >= 5:
                            handler.close_connection = True
                            self.reply(handler, {"error": "Too many pairing attempts. Wait one minute, then try again."}, 429)
                            return True
                        valid = isinstance(code, str) and code.isascii() and self.code and hmac.compare_digest(code, self.code)
                        if not valid:
                            self.pair_failures.append(now)
                    token = self.token
                if not valid:
                    raise ValueError()
            except (ValueError, AttributeError):
                handler.close_connection = True
                self.reply(handler, {"error": "Enter the current pairing code from Pane, or scan its QR code."}, 403)
                return True
            self.reply(handler, {"ok": True}, headers={"Set-Cookie": f"pane_files={token}; HttpOnly; SameSite=Strict; Path=/"})
            return True
        if not desktop and not self.authorized(handler):
            handler.close_connection = True
            self.reply(handler, {"error": "Enter the pairing code shown in Pane to connect."}, 403)
            return True
        if not desktop:
            with self.lock:
                self.last_seen = time.time()
        try:
            if desktop and method == "POST" and path == "/api/files/start":
                self.reply(handler, self.start())
            elif desktop and method == "POST" and path == "/api/files/stop":
                self.reply(handler, self.stop())
            elif method == "GET" and path == "/api/files":
                state = self.status()
                if not desktop:
                    state = {"enabled": state["enabled"], "files": state["files"], "maxSize": MAX_FILE_SIZE}
                self.reply(handler, state)
            elif desktop and method == "GET" and path == "/api/files/qr":
                import qrcode
                from qrcode.image.svg import SvgPathImage

                urls = self.status()["urls"]
                if not urls:
                    self.reply(handler, {"error": "Start file sharing first."}, 409)
                else:
                    # The address index lets the PC choose the correct adapter.
                    from urllib.parse import parse_qs
                    index = int(parse_qs(urlparse(handler.path).query).get("address", ["0"])[0])
                    output = io.BytesIO()
                    qrcode.make(urls[index], image_factory=SvgPathImage, border=4).save(output)
                    self.send(handler, output.getvalue(), "image/svg+xml")
            elif method == "POST" and path == "/api/files/upload":
                self.upload(handler, "to-phone" if desktop else "to-pc")
            elif method == "GET" and path.startswith("/api/files/download/"):
                self.download(handler, path.removeprefix("/api/files/download/"), desktop)
            else:
                self.reply(handler, {"error": "Not found."}, 404)
        except (OSError, ValueError, IndexError) as error:
            handler.close_connection = True
            self.reply(handler, {"error": f"Transfer failed: {error}"}, 400)
        return True

    def upload(self, handler, direction):
        if not self.token:
            self.reply(handler, {"error": "Start file sharing first."}, 409)
            handler.close_connection = True
            return
        length = int(handler.headers.get("Content-Length", "-1"))
        if not 0 <= length <= MAX_FILE_SIZE:
            self.reply(handler, {"error": "Choose a file up to 1 GB."}, 413)
            handler.close_connection = True
            return
        raw_name = unquote(handler.headers.get("X-File-Name", "file"))
        name = raw_name.replace("\\", "/").split("/")[-1]
        name = "".join(c for c in name if ord(c) >= 32 and c not in '<>:"/\\|?*')[:180].strip(" .") or "file"
        if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}:
            name = "_" + name
        file_id = secrets.token_hex(12)
        directory = self.folder / ("Received" if direction == "to-pc" else "Shared")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{Path(name).stem}-{file_id[:8]}{Path(name).suffix}"
        temporary = target.with_name(target.name + ".part")
        # A stalled or interrupted client must not retain a worker / partial file forever.
        handler.connection.settimeout(60)
        try:
            with temporary.open("xb") as output:
                remaining = length
                while remaining:
                    chunk = handler.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("Upload interrupted. Choose the file again to retry.")
                    output.write(chunk)
                    remaining -= len(chunk)
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        item = {"id": file_id, "name": name, "size": length, "direction": direction, "created": time.time(), "path": target}
        with self.lock:
            self.files[file_id] = item
        self.reply(handler, {k: v for k, v in item.items() if k != "path"}, 201)

    def download(self, handler, file_id, desktop):
        with self.lock:
            item = self.files.get(file_id)
        if item is None or (not desktop and item["direction"] != "to-phone"):
            self.reply(handler, {"error": "File not found."}, 404)
            return
        with item["path"].open("rb") as source:
            handler.send_response(200)
            handler.send_header("Content-Type", "application/octet-stream")
            handler.send_header("Content-Length", str(item["size"]))
            handler.send_header("Content-Disposition", "attachment; filename=\"download\"; filename*=UTF-8''" + quote(item["name"], safe=""))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Referrer-Policy", "no-referrer")
            handler.end_headers()
            handler.connection.settimeout(60)
            try:
                shutil.copyfileobj(source, handler.wfile, 1024 * 1024)
            except (BrokenPipeError, ConnectionError, TimeoutError):
                pass


transfers = TransferManager()
