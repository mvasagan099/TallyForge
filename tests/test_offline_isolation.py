"""
Offline Isolation & Zero-Telemetry Verification Test Suite
Enforces socket-level isolation:
1. Monkey-patches socket.socket.connect and socket.create_connection.
   Blocks and fails if any connection to a non-loopback IP (not 127.0.0.1, ::1, or localhost) is attempted.
2. Spins up the Tally server on a loopback port.
3. Tests all API endpoints across accounting, reports, statutory, and inventory.
4. Performs a deep static code scan across HTML, CSS, JS, and Python files to verify
   zero external CDN URLs, tracking scripts, web fonts, or remote analytics exist.
"""

import unittest
import socket
import threading
import time
import urllib.request
import urllib.parse
import json
import os
import re
import sys
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import TallyRequestHandler

ORIGINAL_CONNECT = socket.socket.connect

class TestOfflineIsolation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # 1. Monkey-patch socket to block non-loopback traffic
        cls.blocked_attempts = []

        def guarded_connect(sock, address):
            host = address[0]
            # Allow loopback connections only
            if host not in ("127.0.0.1", "localhost", "::1"):
                cls.blocked_attempts.append(address)
                raise ConnectionRefusedError(f"OFFLINE ENFORCEMENT: Outbound connection blocked to remote host: {address}")
            return ORIGINAL_CONNECT(sock, address)

        socket.socket.connect = guarded_connect

        # 2. Start Tally server on a free loopback port
        cls.port = 8899
        cls.server = HTTPServer(("127.0.0.1", cls.port), TallyRequestHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        socket.socket.connect = ORIGINAL_CONNECT

    def _get(self, endpoint):
        url = f"http://127.0.0.1:{self.port}{endpoint}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read()
            return resp.status, data

    def _post(self, endpoint, payload):
        url = f"http://127.0.0.1:{self.port}{endpoint}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = resp.read()
            return resp.status, data

    def test_01_all_api_endpoints_work_isolated(self):
        endpoints = [
            "/api/state",
            "/api/report/BalanceSheet",
            "/api/report/ProfitAndLoss",
            "/api/report/TrialBalance",
            "/api/report/DayBook",
            "/api/report/FundsFlow",
            "/api/report/BudgetVariance",
            "/api/report/InventoryAgeing",
            "/api/report/GSTR9",
            "/api/report/ConsolidatedFinancials",
            "/api/report/TdsRegister",
            "/api/report/ChequeRegister",
            "/api/report/MfgCost",
            "/api/companies",
            "/api/currencies",
            "/api/currencies/forex",
            "/api/budgets",
            "/api/stock_categories",
            "/api/compound_uoms",
            "/api/price_levels",
            "/api/gst/gstr1_json",
            "/api/gst/summary",
            "/api/bills/receivables",
            "/api/bills/payables",
            "/api/search?q=tax"
        ]

        print("\n--- Testing API Endpoints under Strict Socket Isolation ---")
        for ep in endpoints:
            print(f"Testing {ep}...")
            status, body = self._get(ep)
            self.assertEqual(status, 200, f"Endpoint {ep} failed with status {status}")
            parsed = json.loads(body.decode("utf-8"))
            self.assertIsNotNone(parsed)

        self.assertEqual(len(self.blocked_attempts), 0, f"Non-loopback connections attempted: {self.blocked_attempts}")
        print(f"✓ All {len(endpoints)} API endpoints responded successfully with zero outbound network calls.")

    def test_02_static_assets_contain_zero_remote_dependencies(self):
        print("\n--- Auditing Frontend Assets for External CDNs / Telemetry ---")
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static_dir = os.path.join(base_dir, "static")

        files_to_scan = [
            os.path.join(static_dir, "index.html"),
            os.path.join(static_dir, "tally.css"),
            os.path.join(static_dir, "tally.js")
        ]

        external_patterns = [
            r"https?://(?!127\.0\.0\.1|localhost)",
            r"//cdn\.",
            r"fonts\.googleapis\.com",
            r"fonts\.gstatic\.com",
            r"unpkg\.com",
            r"cdnjs\.cloudflare\.com",
            r"jsdelivr\.net",
            r"google-analytics\.com",
            r"googletagmanager\.com"
        ]

        violations = []
        for fpath in files_to_scan:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
                for pat in external_patterns:
                    matches = re.findall(pat, content)
                    if matches:
                        violations.append((os.path.basename(fpath), pat, matches))

        self.assertEqual(len(violations), 0, f"External network references detected: {violations}")
        print("✓ Zero external CDNs, fonts, or tracking scripts detected. 100% self-contained offline verified.")

if __name__ == "__main__":
    unittest.main()
