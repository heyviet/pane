"""Local device window. Binds to loopback only."""

from __future__ import annotations

import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from handset.device import read_handset, snapshot
from handset.mux import MuxError
from handset.screen import restart_screen, screen_status, start_screen
from handset.wifi import pair_for_wifi, wifi_paired_udids
from handset.transfers import transfers
from handset.clipboard import clipboard_action

ROOT = Path(__file__).resolve().parent / "web"
HOST = "127.0.0.1"
PORT = 8765
MIRROR_PREFIX = "/mirror"
STREAM_HOST = "127.0.0.1"
STREAM_PORT = 8766
_HOP_BY_HOP = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade"}
)
_CLIENT_GONE = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if transfers.handle(self, "GET", True):
            return
        path = urlparse(self.path).path
        if path == MIRROR_PREFIX or path.startswith(f"{MIRROR_PREFIX}/"):
            self._proxy_mirror("GET")
            return
        if path == "/api/status":
            payload = snapshot()
            payload["screen"] = screen_status()
            payload["wifi"] = {"paired": wifi_paired_udids()}
            self._json(payload)
            return
        if path == "/api/phone-video-thumb":
            self._phone_video_thumb()
            return
        if path == "/api/screen":
            self._json(screen_status())
            return
        if path.startswith("/api/devices/"):
            udid = unquote(path.removeprefix("/api/devices/")).strip("/")
            if not udid or "/" in udid:
                self._json({"error": "Missing device id."}, status=400)
                return
            self._device(udid)
            return
        self._file(path)

    def do_POST(self) -> None:
        if transfers.handle(self, "POST", True):
            return
        path = urlparse(self.path).path
        if path == "/api/clipboard/action":
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if (urlparse("http://" + host).hostname not in {"127.0.0.1", "localhost", "::1"}
                    or (origin and origin != "http://" + host)
                    or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                self.close_connection = True
                self._json({"error": "Open Pane through localhost to use the clipboard."}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Invalid clipboard request.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
                    raise ValueError("Invalid clipboard action.")
                path_arg = payload.get("path")
                if path_arg is not None and not isinstance(path_arg, str):
                    raise ValueError("Invalid clipboard action.")
                self._json(clipboard_action(payload["action"], path_arg))
            except (ValueError, OSError, RuntimeError) as error:
                self._json({"error": str(error)}, 502)
            return
        if path in {"/api/screenshot", "/api/screenshot/open"}:
            self._screenshot(path)
            return
        if path == MIRROR_PREFIX or path.startswith(f"{MIRROR_PREFIX}/"):
            self._proxy_mirror("POST")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:  # bad JSON, or a body that isn't UTF-8 text at all
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        udid = payload.get("udid") if isinstance(payload.get("udid"), str) else None
        if path == "/api/screen/restart":
            self._json(restart_screen(udid))
            return
        if path == "/api/wifi/pair":
            if not udid:
                self._json({"ok": False, "error": "Plug in the iPhone first."}, status=400)
                return
            self._json(pair_for_wifi(udid))
            return
        if path != "/api/screen":
            self._json({"error": "Unknown action."}, status=404)
            return
        transport = payload.get("transport")
        if transport not in {"auto", "usb", "wifi"}:
            transport = "auto"
        self._json(start_screen(udid, transport))

    def log_message(self, fmt: str, *args) -> None:
        return

    def _device(self, udid: str) -> None:
        try:
            self._json(read_handset(udid))
        except MuxError as error:
            self._json({"udid": udid, "paired": False, "error": str(error)}, status=409)

    def _screenshot(self, path: str) -> None:
        """Save a screenshot through the Windows save dialog, or open / reveal a saved one."""
        from handset import screenshots
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        # Same guard as the clipboard: these open dialogs and files on the PC.
        if (urlparse("http://" + host).hostname not in {"127.0.0.1", "localhost", "::1"}
                or (origin and origin != "http://" + host)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self.close_connection = True
            self._json({"error": "Open Pane through localhost to save screenshots."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if path == "/api/screenshot":
                if not 0 < length <= screenshots.MAX_BYTES:
                    raise ValueError("That screenshot is too large.")
                name = unquote(self.headers.get("X-File-Name", ""))
                self._json(screenshots.save(self.rfile.read(length), name))
                return
            if not 0 < length <= 4096:
                raise ValueError("Invalid screenshot request.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or payload.get("action") not in {"open", "folder"}:
                raise ValueError("Invalid screenshot request.")
            if payload["action"] == "open":
                screenshots.open_file(payload.get("path"))
            else:
                screenshots.show_in_folder(payload.get("path"))
            self._json({"ok": True})
        except (ValueError, OSError, RuntimeError) as error:
            self._json({"error": str(error)}, 400)

    def _phone_video_thumb(self) -> None:
        from urllib.parse import parse_qs
        from handset.phone_videos import valid_path
        from handset.screen import phone_video_thumbnail
        host = self.headers.get("Host", "")
        if (urlparse("http://" + host).hostname not in {"127.0.0.1", "localhost", "::1"}
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._json({"error": "Open Pane through localhost."}, 403)
            return
        try:
            data = phone_video_thumbnail(valid_path(parse_qs(urlparse(self.path).query).get("path", [""])[0]))
        except (ValueError, OSError, RuntimeError) as error:
            self._json({"error": str(error)}, 404)
            return
        if not data:
            self._json({"error": "No thumbnail."}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _mirror_upstream_path(self) -> str:
        path = unquote(urlparse(self.path).path)
        upstream = path.removeprefix(MIRROR_PREFIX) or "/"
        if not upstream.startswith("/"):
            upstream = f"/{upstream}"
        query = urlparse(self.path).query
        return upstream + (f"?{query}" if query else "")

    def _write_client(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            self.wfile.flush()
        except _CLIENT_GONE:
            return False
        return True

    def _proxy_mirror_stream(self, method: str, target: str) -> None:
        """Relay the live body de-chunked; this server speaks HTTP/1.0, so the
        response is delimited by closing the connection."""
        conn = http.client.HTTPConnection(STREAM_HOST, STREAM_PORT, timeout=600)
        started = False
        self.close_connection = True
        try:
            conn.request(method, target, headers={"Connection": "close"})
            response = conn.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() in _HOP_BY_HOP or key.lower() in {"transfer-encoding", "content-length"}:
                    continue
                self.send_header(key, value)
            self.end_headers()
            started = True
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                if not self._write_client(chunk):
                    return
        except (OSError, http.client.HTTPException) as error:
            if not started:
                self._json({"error": f"Screen stream is not running ({error})."}, status=503)
        finally:
            conn.close()

    def _proxy_mirror(self, method: str) -> None:
        target = self._mirror_upstream_path()
        upstream_path = target.split("?", 1)[0]
        is_live_stream = method == "GET" and upstream_path.endswith(".bin")
        if is_live_stream:
            self._proxy_mirror_stream(method, target)
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length and method in {"POST", "PUT"} else None

        headers: dict[str, str] = {}
        for name in ("Content-Type", "Accept"):
            value = self.headers.get(name)
            if value:
                headers[name] = value

        conn = http.client.HTTPConnection(STREAM_HOST, STREAM_PORT, timeout=120)
        started = False
        try:
            conn.request(method, target, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()
            self.send_response(response.status)
            started = True
            for key, value in response.getheaders():
                if key.lower() in _HOP_BY_HOP:
                    continue
                self.send_header(key, value)
            self.end_headers()
            if payload:
                self._write_client(payload)
        except OSError as error:
            if not started:
                self._json({"error": f"Screen stream is not running ({error})."}, status=503)
        finally:
            conn.close()

    def _file(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        root = ROOT.resolve()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json({"error": "Not found."}, status=404)
            return
        if not target.is_file():
            self._json({"error": "Not found."}, status=404)
            return
        kind = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class SingleServer(ThreadingHTTPServer):
    # On Windows, address reuse lets a second copy bind the same port and steal requests.
    allow_reuse_address = False


def main() -> None:
    server = SingleServer((HOST, PORT), Handler)
    print(f"Handset listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
