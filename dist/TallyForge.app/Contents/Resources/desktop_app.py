"""
TallyForge Desktop Application Launcher & Process Manager
TallyForge — Reverse Engineered. Rebuilt. Reimagined.
An independently rebuilt accounting and business management application inspired by TallyPrime's functionality.
"""

import os
import sys
import time
import signal
import sqlite3
import argparse
import threading
import webbrowser
from http.server import HTTPServer

# Command-line argument parsing
parser = argparse.ArgumentParser(description="TallyForge — Reverse Engineered. Rebuilt. Reimagined.")
parser.add_argument("--data-dir", default=None, help="Directory to store tally_data.db and backups")
parser.add_argument("--port", type=int, default=8080, help="Local HTTP port (default: 8080)")
parser.add_argument("--host", default="127.0.0.1", help="Loopback host to bind (default: 127.0.0.1)")
parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch web browser")
cli_args, unknown = parser.parse_known_args()

if cli_args.data_dir:
    os.environ["TALLY_DATA_DIR"] = os.path.abspath(cli_args.data_dir)

from server import TallyRequestHandler, db, BASE_DIR, DATA_DIR

def run_server(host="127.0.0.1", port=8080):
    server_address = (host, port)
    httpd = HTTPServer(server_address, TallyRequestHandler)
    print(f"[Desktop App] Server started at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except Exception:
        pass
    finally:
        httpd.server_close()

def main():
    print("=" * 60)
    print("  TALLYFORGE — REVERSE ENGINEERED. REBUILT. REIMAGINED.")
    print("  Mode: 100% Fully Offline | Zero Internet Required")
    print(f"  Resource Path : {BASE_DIR}")
    print(f"  User Data Path: {DATA_DIR}")
    print("=" * 60)

    # 1. Pre-flight Database Integrity Verification
    db_file = os.path.join(DATA_DIR, "tally_data.db")
    print(f"[*] Checking database integrity at: {db_file}")
    try:
        conn = sqlite3.connect(db_file)
        cur = conn.cursor()
        res = cur.execute("PRAGMA integrity_check;").fetchone()
        conn.close()
        if res and res[0] == "ok":
            print("[+] SQLite Database Integrity: OK")
        else:
            print(f"[!] Database integrity warning: {res}")
    except Exception as e:
        print(f"[!] Database check error: {e}")

    # 2. Start HTTP Server in Daemon Thread
    host = cli_args.host
    port = cli_args.port
    server_thread = threading.Thread(target=run_server, args=(host, port), daemon=True)
    server_thread.start()

    time.sleep(0.8)

    # 3. Open UI
    url = f"http://{host}:{port}"
    print(f"[*] Tally application ready at {url}")

    if cli_args.no_browser:
        print("[*] Headless mode enabled (--no-browser). Running in background.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] Shutting down Tally server cleanly...")
            sys.exit(0)

    # Optional pywebview for true native window if installed
    opened_window = False
    try:
        import webview
        print("[+] Native desktop windowing engine detected (pywebview).")
        webview.create_window("TallyForge — Reverse Engineered. Rebuilt. Reimagined.", url, width=1280, height=800)
        opened_window = True
        webview.start()
    except ImportError:
        pass

    if not opened_window:
        webbrowser.open(url)
        print("[+] Opened TallyForge in default browser.")
        print("[*] Press Ctrl+C in this terminal to shut down TallyForge cleanly.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] Shutting down Tally server cleanly...")
            sys.exit(0)

if __name__ == "__main__":
    main()
