"""
Test Suite: Advanced Inventory & Manufacturing Verification
Covers:
- Stock categories and stock group hierarchy
- Compound UOM (Units of Measurement) definitions
- Multi-godown stock transfers via Stock Journal
- Batch numbers, manufacturing and expiry tracking
- Bill of Materials (BOM) production via Manufacturing Journal
- FIFO stock ageing analysis
- Physical stock audit and discrepancy reconciliation
"""

import unittest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB

class TestInventoryManufacturing(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_inventory.db")
        self.db = EnterpriseAccountingDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_01_stock_categories_and_groups(self):
        """Verifies stock category creation and association."""
        res1 = self.db.add_stock_category("Hardware Components", "Primary")
        res2 = self.db.add_stock_category("Peripherals", "Primary")
        self.assertTrue(res1)
        self.assertTrue(res2)

        cats = self.db.get_stock_categories()
        cat_names = [c["name"] for c in cats]
        self.assertIn("Hardware Components", cat_names)
        self.assertIn("Peripherals", cat_names)

    def test_02_compound_uom_conversion(self):
        """Verifies compound units (e.g. 1 Box = 10 Pcs)."""
        ok = self.db.add_compound_uom("Box", 10.0, "Pcs")
        self.assertTrue(ok)

        uoms = self.db.get_compound_uoms()
        box_uom = next((u for u in uoms if u["major_uom"] == "Box" and u["minor_uom"] == "Pcs"), None)
        self.assertIsNotNone(box_uom)
        self.assertEqual(box_uom["conversion_factor"], 10.0)
        self.assertEqual(box_uom["minor_uom"], "Pcs")

    def test_03_multi_godown_stock_transfer(self):
        """Transfers stock from Main Location to Warehouse B and verifies balances."""
        # 1. Purchase 20 units of Laser Printer into Main Location
        self.db.add_voucher(
            "Purchase", "2026-05-01", "Printer Inward",
            [
                {"ledger_name": "Purchase A/c", "entry_type": "Dr", "amount": 250000.0},
                {"ledger_name": "Apex Systems Ltd (Creditor)", "entry_type": "Cr", "amount": 250000.0}
            ],
            inventory_items=[
                {"stock_item_name": "HP Laser Printer 1020", "godown_name": "Main Location", "qty": 20.0, "rate": 12500.0, "amount": 250000.0, "direction": "In"}
            ]
        )

        # 2. Transfer 8 units to Warehouse B
        v_tf = self.db.add_voucher(
            "Stock Journal", "2026-05-05", "Inter-warehouse stock transfer",
            [
                {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": 100000.0},
                {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": 100000.0}
            ],
            inventory_items=[
                {"stock_item_name": "HP Laser Printer 1020", "godown_name": "Main Location", "qty": 8.0, "rate": 12500.0, "amount": 100000.0, "direction": "Out"},
                {"stock_item_name": "HP Laser Printer 1020", "godown_name": "Warehouse B", "qty": 8.0, "rate": 12500.0, "amount": 100000.0, "direction": "In"}
            ]
        )
        self.assertGreater(v_tf, 0)

        # Verify godown distribution
        gw_summary = self.db.get_godown_summary()
        wh_b = next((g for g in gw_summary if g["godown_name"] == "Warehouse B"), None)
        self.assertIsNotNone(wh_b)
        self.assertGreaterEqual(wh_b["total_qty"], 8.0)

    def test_04_batch_manufacturing_and_expiry(self):
        """Verifies batch numbering, manufacturing date, and expiry tracking."""
        batches = self.db.get_stock_batches("HP Laser Printer 1020")
        self.assertGreaterEqual(len(batches), 1)
        b = batches[0]
        self.assertEqual(b["batch_number"], "BATCH-2026-A1")
        self.assertEqual(b["mfg_date"], "2026-04-01")
        self.assertEqual(b["expiry_date"], "2029-03-31")

    def test_05_bom_production_manufacturing_journal(self):
        """Verifies BOM consumption and production of Assembled Desktops."""
        # Initial stock of component RAM: 30 Nos
        # BOM: 1 Combo needs 1 Monitor, 1 Mouse, 2 RAM
        # Produce 4 Combos: Consumes 4 Monitors, 4 Mice, 8 RAM
        v_mfg = self.db.process_manufacturing_journal("Assembled Desktop Combo", produce_qty=4.0, date="2026-05-20")
        self.assertGreater(v_mfg, 0)

        # Check finished item stock: 5 opening + 4 produced = 9
        combo = self.db.get_stock_item_summary("Assembled Desktop Combo")
        self.assertEqual(combo["closing_qty"], 9.0)

        # Check raw material consumption: RAM 30 opening - 8 consumed = 22
        ram = self.db.get_stock_item_summary("RAM DDR4 16GB")
        self.assertEqual(ram["closing_qty"], 22.0)

    def test_06_stock_ageing_analysis(self):
        """Verifies FIFO inventory ageing breakdown."""
        ageing = self.db.get_inventory_ageing_report(as_of_date="2026-10-31")
        self.assertIsInstance(ageing, list)
        self.assertGreater(len(ageing), 0)
        # Check that reports contain valid items and aging bucket keys
        for row in ageing:
            self.assertIn("stock_item", row)
            self.assertIn("total_qty", row)
            self.assertIn("qty_over_90_days", row)

    def test_07_physical_stock_audit_adjustment(self):
        """Verifies physical stock counting discrepancy auto-reconciliation."""
        # Current stock of Dell Monitor is 15. Count shows 12 (discrepancy of -3).
        res = self.db.process_physical_stock_voucher(
            date="2026-06-30",
            stock_item_name='Dell 24" FHD Monitor',
            actual_qty=12.0,
            godown_name="Main Location",
            narration="Quarterly physical stock verification"
        )
        self.assertEqual(res["actual_qty"], 12.0)
        self.assertEqual(res["discrepancy_qty"], -3.0)

        # Verify closing qty is exactly 12
        mon = self.db.get_stock_item_summary('Dell 24" FHD Monitor')
        self.assertEqual(mon["closing_qty"], 12.0)

if __name__ == "__main__":
    unittest.main()
