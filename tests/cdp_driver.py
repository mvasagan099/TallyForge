"""
Pure-Python Chrome DevTools Protocol (CDP) Driver
Zero third-party dependencies. Implements RFC 6455 WebSocket client to drive Google Chrome directly.
Supports element selection, clicking, text entry, dropdown changes, keyboard shortcuts, DOM assertions,
console log recording, and full-resolution screenshot capture.
"""

import os
import sys
import time
import json
import base64
import struct
import socket
import tempfile
import subprocess
import urllib.request
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

class WebSocketClient:
    def __init__(self, ws_url: str, timeout: float = 10.0):
        self.ws_url = ws_url
        self.timeout = timeout
        parsed = urllib.parse.urlparse(ws_url)
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path
        if parsed.query:
            self.path += f"?{parsed.query}"

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((self.host, self.port))
        self._handshake()

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode("utf-8"))
        res = b""
        while b"\r\n\r\n" not in res:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake failed: EOF")
            res += chunk
        status_line = res.split(b"\r\n")[0].decode("utf-8", errors="ignore")
        if "101" not in status_line:
            raise ConnectionError(f"WebSocket handshake failed: {status_line}")

    def send_text(self, text: str):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        l = len(payload)
        if l <= 125:
            hdr = bytearray([0x81, 0x80 | l])
        elif l <= 65535:
            hdr = bytearray([0x81, 0x80 | 126]) + struct.pack(">H", l)
        else:
            hdr = bytearray([0x81, 0x80 | 127]) + struct.pack(">Q", l)
        hdr += mask
        masked_payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + masked_payload)

    def recv_text(self) -> str:
        def read_exact(n: int) -> bytes:
            buf = bytearray()
            while len(buf) < n:
                chunk = self.sock.recv(n - len(buf))
                if not chunk:
                    raise EOFError("WebSocket closed")
                buf.extend(chunk)
            return bytes(buf)

        while True:
            hdr = read_exact(2)
            opcode = hdr[0] & 0x0F
            is_masked = bool(hdr[1] & 0x80)
            l = hdr[1] & 0x7F
            if l == 126:
                l = struct.unpack(">H", read_exact(2))[0]
            elif l == 127:
                l = struct.unpack(">Q", read_exact(8))[0]

            mask = read_exact(4) if is_masked else None
            data = read_exact(l)
            if is_masked:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

            if opcode == 0x01:  # Text frame
                return data.decode("utf-8", errors="replace")
            elif opcode == 0x08:  # Close frame
                self.close()
                raise ConnectionResetError("WebSocket received close frame")
            elif opcode == 0x09:  # Ping
                pong_hdr = bytearray([0x8A, 0x80 | len(data)]) + (mask or b"\x00\x00\x00\x00")
                self.sock.sendall(pong_hdr + data)
            # Ignore other control frames or continue loop

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


class CDPDriver:
    def __init__(self, cdp_port: int = 9333, headless: bool = True):
        self.cdp_port = cdp_port
        self.headless = headless
        self.temp_dir = tempfile.mkdtemp(prefix="tally_cdp_")
        self.proc: Optional[subprocess.Popen] = None
        self.ws: Optional[WebSocketClient] = None
        self.req_id = 0
        self.console_logs: List[Dict[str, Any]] = []
        self.network_events: List[Dict[str, Any]] = []

    def start(self):
        cmd = [
            CHROME_BIN,
            "--headless=new" if self.headless else "",
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self.temp_dir}",
            "--window-size=1280,800",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank"
        ]
        cmd = [c for c in cmd if c]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Wait for debugging endpoint
        ws_url = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.cdp_port}/json", timeout=1.0) as resp:
                    targets = json.loads(resp.read().decode())
                    page_targets = [t for t in targets if t.get("type") == "page"]
                    if page_targets:
                        ws_url = page_targets[0]["webSocketDebuggerUrl"]
                        break
            except Exception:
                time.sleep(0.15)

        if not ws_url:
            self.stop()
            raise RuntimeError(f"Could not connect to Chrome CDP on port {self.cdp_port}")

        self.ws = WebSocketClient(ws_url)
        # Enable Console and Page domains
        self.send_command("Runtime.enable")
        self.send_command("Page.enable")

    def stop(self):
        if self.ws:
            self.ws.close()
            self.ws = None
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def send_command(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.req_id += 1
        curr_id = self.req_id
        payload = {"id": curr_id, "method": method}
        if params is not None:
            payload["params"] = params
        self.ws.send_text(json.dumps(payload))

        # Wait for matching response id while buffering events
        while True:
            raw = self.ws.recv_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            # Record events
            if "method" in msg:
                if msg["method"] == "Runtime.consoleAPICalled":
                    self.console_logs.append(msg.get("params", {}))
                continue

            if msg.get("id") == curr_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP Error in {method}: {msg['error']}")
                return msg.get("result", {})

    def navigate(self, url: str):
        self.send_command("Page.navigate", {"url": url})
        # Wait for load event
        time.sleep(0.8)

    def evaluate(self, expression: str) -> Any:
        res = self.send_command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        })
        result_obj = res.get("result", {})
        if result_obj.get("subtype") == "error":
            raise RuntimeError(f"JS Evaluation Error: {result_obj.get('description')}")
        return result_obj.get("value")

    def wait_for_selector(self, selector: str, timeout: float = 6.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            val = self.evaluate(f"Boolean(document.querySelector({json.dumps(selector)}))")
            if val:
                return True
            time.sleep(0.15)
        raise TimeoutError(f"Selector '{selector}' did not appear within {timeout}s")

    def click(self, selector: str):
        self.wait_for_selector(selector)
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) throw new Error("Element not found: " + {json.dumps(selector)});
            el.scrollIntoView({{ behavior: 'instant', block: 'center' }});
            el.click();
            return true;
        }})()
        """
        self.evaluate(js)
        time.sleep(0.2)

    def type(self, selector: str, text: str):
        self.wait_for_selector(selector)
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) throw new Error("Element not found: " + {json.dumps(selector)});
            el.focus();
            el.value = {json.dumps(text)};
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return el.value;
        }})()
        """
        self.evaluate(js)

    def select(self, selector: str, value: str):
        self.wait_for_selector(selector)
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(selector)});
            if (!el) throw new Error("Select element not found: " + {json.dumps(selector)});
            el.value = {json.dumps(value)};
            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return el.value;
        }})()
        """
        self.evaluate(js)

    def get_text(self, selector: str) -> str:
        self.wait_for_selector(selector)
        return self.evaluate(f"document.querySelector({json.dumps(selector)}).innerText.trim()")

    def get_value(self, selector: str) -> str:
        self.wait_for_selector(selector)
        return self.evaluate(f"document.querySelector({json.dumps(selector)}).value")

    def press_key(self, key_name: str):
        # Dispatch keydown / keyup
        js = f"""
        (() => {{
            const ev = new KeyboardEvent('keydown', {{ key: {json.dumps(key_name)}, bubbles: true }});
            document.dispatchEvent(ev);
        }})()
        """
        self.evaluate(js)
        time.sleep(0.15)

    def screenshot(self, output_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        res = self.send_command("Page.captureScreenshot", {"format": "png"})
        b64_data = res.get("data", "")
        with open(output_path, "wb") as f:
            f.write(base64.b64decode(b64_data))
