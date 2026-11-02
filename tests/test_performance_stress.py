"""
Scalability, Stress & Performance Benchmark Suite (Phase 5.8)
Benchmarks high-volume voucher throughput, report calculation latency,
live search indexing speed, database storage footprint, and backup/restore latency.
"""

import unittest
import os
import time
import tempfile
from engine.accounting_db import EnterpriseAccountingDB


class TestPerformanceStress(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix="_perf.db")
        os.close(self.temp_db_fd)
        self.db = EnterpriseAccountingDB(self.temp_db_path)
        self.company_id = self.db.active_company_id

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    # 1. Bulk Voucher Insertion Throughput (1,000 vouchers)
    def test_01_bulk_voucher_insertion_throughput(self):
        t0 = time.time()
        with self.db.conn:
            for i in range(1, 1001):
                cur = self.db.conn.cursor()
                cur.execute("""
                    INSERT INTO voucher (voucher_number, voucher_type, date, narration, total_amount, company_id)
                    VALUES (?, 'Payment', '2026-05-15', 'Bulk Perf Test', 1000.0, ?)
                """, (f"PERF-{i:05d}", self.company_id))
                v_id = cur.lastrowid
                cur.execute("INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount) VALUES (?, 'Office Rent', 'Dr', 1000.0)", (v_id,))
                cur.execute("INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount) VALUES (?, 'Cash', 'Cr', 1000.0)", (v_id,))

        elapsed = time.time() - t0
        rate = 1000.0 / elapsed if elapsed > 0 else 999999
        print(f"\n[PERF] 1,000 Vouchers posted in {elapsed:.3f}s -> {rate:.1f} vouchers/sec")
        self.assertGreater(rate, 1000.0, "Throughput should exceed 1,000 vouchers/sec")

    # 2. Trial Balance Calculation Latency
    def test_02_trial_balance_calculation_latency(self):
        # Insert 500 vouchers first
        with self.db.conn:
            for i in range(1, 501):
                cur = self.db.conn.cursor()
                cur.execute("""
                    INSERT INTO voucher (voucher_number, voucher_type, date, narration, total_amount, company_id)
                    VALUES (?, 'Receipt', '2026-05-15', 'TB Perf', 500.0, ?)
                """, (f"TB-PERF-{i:04d}", self.company_id))
                vid = cur.lastrowid
                cur.execute("INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount) VALUES (?, 'HDFC Bank', 'Dr', 500.0)", (vid,))
                cur.execute("INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount) VALUES (?, 'Sales A/c', 'Cr', 500.0)", (vid,))

        t0 = time.time()
        tb = self.db.get_trial_balance()
        tb_latency = (time.time() - t0) * 1000.0
        print(f"[PERF] Trial Balance calculated in {tb_latency:.2f}ms across {len(tb)} accounts")
        self.assertLess(tb_latency, 50.0, "Trial balance calculation should take < 50ms")

    # 3. Balance Sheet & Profit & Loss Latency
    def test_03_balance_sheet_and_pl_latency(self):
        t0 = time.time()
        fin = self.db.get_financial_summary()
        latency = (time.time() - t0) * 1000.0
        print(f"[PERF] Financial Summary & P&L calculated in {latency:.2f}ms")
        self.assertLess(latency, 50.0, "Financial summary calculation should take < 50ms")
        self.assertIn("net_profit", fin)

    # 4. Smart Search Latency
    def test_04_smart_search_latency(self):
        t0 = time.time()
        # Search ledgers and vouchers
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT * FROM ledger WHERE name LIKE '%Bank%'")
        ledgers = cursor.fetchall()
        search_latency = (time.time() - t0) * 1000.0
        print(f"[PERF] Indexed query latency: {search_latency:.2f}ms")
        self.assertLess(search_latency, 20.0, "Search query should take < 20ms")

    # 5. Database Footprint Compactness
    def test_05_database_footprint_compactness(self):
        size_bytes = os.path.getsize(self.temp_db_path)
        size_kb = size_bytes / 1024.0
        print(f"[PERF] Active database file size: {size_kb:.1f} KB")
        # Should be lightweight (< 2MB)
        self.assertLess(size_kb, 2048.0, "Clean SQLite database footprint should be < 2MB")

    # 6. Local Backup & Restore Latency
    def test_06_backup_and_restore_speed(self):
        backup_dir = os.path.join(tempfile.gettempdir(), "perf_backups")
        t0 = time.time()
        b_path = self.db.create_local_backup(backup_dir)
        b_time = (time.time() - t0) * 1000.0

        t1 = time.time()
        res = self.db.restore_local_backup(b_path)
        r_time = (time.time() - t1) * 1000.0
        print(f"[PERF] Backup: {b_time:.2f}ms | Restore: {r_time:.2f}ms")

        self.assertTrue(res)
        self.assertLess(b_time, 150.0)
        self.assertLess(r_time, 150.0)


if __name__ == "__main__":
    unittest.main()
