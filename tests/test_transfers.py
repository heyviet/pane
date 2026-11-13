import http.client
import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from handset.transfers import MAX_FILE_SIZE, TransferManager


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = TransferManager(self.temp.name, port=0)
        patcher = patch("handset.transfers.local_addresses", return_value=["127.0.0.1"])
        patcher.start()
        self.addCleanup(patcher.stop)
        manager = self.manager

        class DesktopHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                manager.handle(self, "GET", True)

            def do_POST(self):
                manager.handle(self, "POST", True)

            def log_message(self, *args):
                pass

        self.desktop = ThreadingHTTPServer(("127.0.0.1", 0), DesktopHandler)
        threading.Thread(target=self.desktop.serve_forever, daemon=True).start()
        self.manager.start()
        self.cookie = None

    def tearDown(self):
        self.manager.stop()
        self.desktop.shutdown()
        self.desktop.server_close()
        self.temp.cleanup()

    def request(self, path="/api/files", method="GET", body=None, headers=None, desktop=False):
        headers = dict(headers or {})
        if self.cookie and not desktop:
            headers.setdefault("Cookie", self.cookie)
        server = self.desktop if desktop else self.manager.server
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        conn.close()
        return result

    def pair(self):
        status, headers, _ = self.request("/api/files/pair", "POST", json.dumps({"token": self.manager.token}))
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]

    def test_round_trip_and_phone_access_boundaries(self):
        self.assertEqual(self.request()[0], 403)
        self.pair()
        payload = b"\x00binary\xff\r\n" * 1000
        name = "report & notes.pdf"
        code, _, data = self.request("/api/files/upload", "POST", payload, {"X-File-Name": quote(name)}, desktop=True)
        self.assertEqual(code, 201)
        shared = json.loads(data)
        code, headers, received = self.request("/api/files/download/" + shared["id"])
        self.assertEqual((code, received), (200, payload))
        self.assertIn("attachment", headers["Content-Disposition"])
        code, _, data = self.request("/api/files/upload", "POST", payload, {"X-File-Name": quote("../photo.heic")})
        self.assertEqual(code, 201)
        incoming = json.loads(data)
        self.assertEqual(incoming["name"], "photo.heic")
        self.assertEqual(self.request("/api/files/download/" + incoming["id"])[0], 404)
        self.assertEqual(self.request("/api/files/download/" + incoming["id"], desktop=True)[2], payload)
        saved = list((Path(self.temp.name) / "Received").iterdir())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), payload)
        phone_state = json.loads(self.request()[2])
        self.assertNotIn("urls", phone_state)
        self.assertNotIn("folder", phone_state)
        self.assertNotIn("path", phone_state["files"][0])
        self.assertTrue(self.manager.status()["connected"])

    def test_pairing_revocation_and_origin_checks(self):
        for token in ("wrong", "é", None):
            self.assertEqual(self.request("/api/files/pair", "POST", json.dumps({"token": token}))[0], 403)
        self.pair()
        self.assertEqual(self.request(headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request(desktop=True, headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("/api/files/stop", "POST")[0], 404)
        self.assertEqual(self.request("/api/screen")[0], 404)
        self.assertEqual(self.request("/../server.py")[0], 404)
        self.manager.stop()
        self.manager.start()
        self.assertEqual(self.request()[0], 403)

    def test_limits_names_and_qr(self):
        self.pair()
        code, _, _ = self.request("/api/files/upload", "POST", b"", {"Content-Length": str(MAX_FILE_SIZE + 1)})
        self.assertEqual(code, 413)
        for filename in ("CON.txt", "..\\..\\a.txt", "normal.txt", "normal.txt"):
            code, _, data = self.request("/api/files/upload", "POST", b"", {"X-File-Name": quote(filename)})
            self.assertEqual(code, 201)
            self.assertNotIn("\\", json.loads(data)["name"])
        self.assertEqual(len(list((Path(self.temp.name) / "Received").iterdir())), 4)
        code, headers, body = self.request("/api/files/qr", desktop=True)
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "image/svg+xml")
        self.assertIn(b"<svg", body)
        code, _, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Choose files or photos", body)

    def test_manual_code_pairing_and_rate_limit(self):
        code = self.manager.status()["code"]
        self.assertEqual(len(code), 8)
        status, headers, _ = self.request("/api/files/pair", "POST", json.dumps({"code": code}))
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.assertEqual(self.request()[0], 200)
        self.assertNotIn("code", json.loads(self.request()[2]))
        for _ in range(5):
            self.assertEqual(self.request("/api/files/pair", "POST", json.dumps({"code": "wrong"}))[0], 403)
        self.assertEqual(self.request("/api/files/pair", "POST", json.dumps({"code": code}))[0], 429)
        # The high-entropy QR link still works while code guesses are blocked.
        self.pair()
        self.manager.stop()
        self.manager.start()
        self.assertEqual(self.request()[0], 403)

    def test_interrupted_upload_cleans_partial_file(self):
        self.pair()
        with socket.create_connection(("127.0.0.1", self.manager.server.server_port), timeout=5) as sock:
            request = f"POST /api/files/upload HTTP/1.0\r\nHost: localhost\r\nCookie: {self.cookie}\r\nContent-Length: 100\r\nX-File-Name: partial.txt\r\n\r\nshort"
            sock.sendall(request.encode())
            sock.shutdown(socket.SHUT_WR)
            response = b""
            while chunk := sock.recv(4096):
                response += chunk
        self.assertIn(b"400 Bad Request", response)
        self.assertEqual(list((Path(self.temp.name) / "Received").iterdir()), [])
        self.assertEqual(self.manager.status()["files"], [])


if __name__ == "__main__":
    unittest.main()
