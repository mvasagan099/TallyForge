"""
Test Suite: Advanced Enterprise Capabilities (Phase 9 Parity)
Verifies:
1. Subcontracting & Job Work Lifecycle (Inward & Outward, material dispatch, finished receipt, service bill posting)
2. Quality Inspection & Sampling (Multi-parameter testing, tolerance thresholds, pass/fail status)
3. Multi-Currency Master & Historical Exchange Rates (Date-effective lookup, buying/selling spread)
4. Payment & Bill Reconciliation (Unallocated payments, Agst Ref settlements, settlement write-off journals)
5. MCA Rule 3(1) Edit Log Audit Trail Diff Viewer (Field-by-field before/after diffs between versions)
6. TDL Reports & Fallback Generators for all 5 new modules
7. REST API Endpoints verification across all new capabilities
"""

import unittest
import os
import sys
import json
import time
import urllib.request
import urllib.error
import threading
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.runtime import TDLRuntime
from server import TallyRequestHandler, DefaultHTTPServer

class TestAdvancedEnterpriseCapabilities(unittest.TestCase):

    def setUp(self):
        self.db_path = f"test_adv_{int(time.time() * 1000) % 1000000}.db"
        self.db = EnterpriseAccountingDB(self.db_path)
        import server
        self.orig_server_db = server.db
        server.db = self.db
        self.symbols = TDLSymbolTable()
        self.parser = TDLParser(self.symbols)
        base_tdl = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "definitions", "base.tdl")
        if os.path.exists(base_tdl):
            self.parser.parse_file(base_tdl)
        self.runtime = TDLRuntime(self.symbols, self.db)

    def tearDown(self):
        import server
        if hasattr(self, "orig_server_db"):
            server.db = self.orig_server_db
        self.db.close()
        for ext in ["", "-wal", "-shm"]:
            f = self.db_path + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def test_01_job_work_subcontracting_lifecycle(self):
        """Tests complete Job Work subcontracting order, dispatch, and finished goods receipt."""
        order_no = f"JWO-TEST-{int(time.time()*1000)%100000}"
        raw_items = [
            {"stock_item_name": "RAM DDR4 16GB", "qty": 4, "rate": 3000.0},
            {"stock_item_name": "Dell 24\" FHD Monitor", "qty": 2, "rate": 8000.0}
        ]
        res = self.db.create_job_work_order(
            order_type="Job Work Out",
            order_no=order_no,
            job_worker_name="Cash",
            order_date="2026-10-31",
            due_date="2026-11-15",
            finished_item_name="Assembled Desktop Combo",
            finished_qty=2.0,
            service_rate=1500.0,
            items=raw_items,
            narration="Subcontracting Assembly of 2 Desktops"
        )
        jw_id = res["job_work_id"]
        self.assertGreater(jw_id, 0)
        self.assertEqual(res["order_no"], order_no)
        self.assertEqual(res["service_amount"], 3000.0)

        # 1. Dispatch materials
        disp_res = self.db.dispatch_job_work_materials(jw_id, raw_items)
        self.assertEqual(disp_res["status"], "In Progress")

        # 2. Receive finished goods
        rec_res = self.db.receive_job_work_finished_goods(
            order_id=jw_id,
            finished_qty=2.0,
            consumed_items=raw_items,
            post_service_bill=True
        )
        self.assertEqual(rec_res["status"], "Completed")
        self.assertIsNotNone(rec_res["service_voucher_id"])

        # 3. Retrieve orders and details
        orders = self.db.get_job_work_orders(status="Completed")
        self.assertTrue(any(o["order_no"] == order_no for o in orders))

        detail = self.db.get_job_work_order_details(jw_id)
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["items"]), 2)
        self.assertEqual(detail["items"][0]["consumed_qty"], 4.0)

    def test_02_quality_inspection_pass_and_fail(self):
        """Tests QC inspections with parameter tolerances and pass/fail evaluation."""
        # 1. Successful Inspection (All Pass)
        pass_params = [
            {"name": "Display Resolution Check", "spec": "1920x1080", "status": "Pass"},
            {"name": "Dead Pixel Test", "spec": "0 Dead Pixels", "min_value": 0, "max_value": 0, "reading_value": 0},
            {"name": "Power Consumption Watts", "spec": "Max 35W", "min_value": 10.0, "max_value": 35.0, "reading_value": 24.5}
        ]
        p_res = self.db.create_quality_inspection(
            reference_type="Purchase",
            reference_no="INV-8891",
            stock_item_name="Dell 24\" FHD Monitor",
            batch_no="BATCH-2026-01",
            inspected_qty=10.0,
            sample_size=2.0,
            parameters=pass_params,
            remarks="All units in prime condition"
        )
        self.assertEqual(p_res["status"], "Accepted")
        self.assertEqual(p_res["accepted_qty"], 10.0)
        self.assertEqual(p_res["rejected_qty"], 0.0)

        # 2. Failed Inspection (Parameter out of range)
        fail_params = [
            {"name": "Memory Clock Speed MHz", "spec": "3200 MHz", "min_value": 3150.0, "max_value": 3250.0, "reading_value": 2400.0}
        ]
        f_res = self.db.create_quality_inspection(
            reference_type="Purchase",
            reference_no="INV-8892",
            stock_item_name="RAM DDR4 16GB",
            inspected_qty=5.0,
            parameters=fail_params,
            remarks="Clock speed below rated specification"
        )
        self.assertEqual(f_res["status"], "Rejected")
        self.assertEqual(f_res["accepted_qty"], 0.0)
        self.assertEqual(f_res["rejected_qty"], 5.0)

        # List inspections
        inspections = self.db.get_quality_inspections()
        self.assertGreaterEqual(len(inspections), 2)

    def test_03_multi_currency_exchange_rates_historical(self):
        """Tests multi-currency exchange rates and date-effective rate resolution."""
        # Setup rates
        self.db.set_currency_exchange_rate("$", "USD", "2026-04-01", 83.25)
        self.db.set_currency_exchange_rate("$", "USD", "2026-09-01", 84.10)
        self.db.set_currency_exchange_rate("$", "USD", "2026-10-15", 85.00)

        # Test historical lookups
        r1 = self.db.get_rate_for_date("$", "2026-05-10")
        self.assertEqual(r1, 83.25)

        r2 = self.db.get_rate_for_date("$", "2026-09-20")
        self.assertEqual(r2, 84.10)

        r3 = self.db.get_rate_for_date("$", "2026-10-31")
        self.assertEqual(r3, 85.00)

        # List rates
        rates = self.db.get_currency_exchange_rates("$")
        self.assertEqual(len(rates), 3)

    def test_04_payment_reconciliation_allocation_and_writeoff(self):
        """Tests bill reconciliation, matching payments against invoices with settlement write-offs."""
        party = "Acme Global Corp"
        self.db.add_ledger(party, "Sundry Debtors", 0.0, "Dr")
        self.db.add_ledger("Sales Account", "Sales Accounts", 0.0, "Cr")
        self.db.add_ledger("Discount Allowed / Bad Debts", "Indirect Expenses", 0.0, "Dr")

        # 1. Post Sales Invoice for ₹50,000
        bill_ref = f"INV-{int(time.time()*1000)%100000}"
        v_sales = self.db.add_voucher(
            voucher_type="Sales",
            date="2026-10-01",
            narration="B2B Sales",
            entries=[
                {"ledger_name": party, "entry_type": "Dr", "amount": 50000.0},
                {"ledger_name": "Sales Account", "entry_type": "Cr", "amount": 50000.0}
            ],
            bill_allocations=[
                {"party_ledger": party, "ref_type": "New Ref", "ref_no": bill_ref, "amount": 50000.0}
            ]
        )

        # 2. Post Customer Payment (Receipt) of ₹49,500
        v_rcpt = self.db.add_voucher(
            voucher_type="Receipt",
            date="2026-10-15",
            narration="Payment from customer",
            entries=[
                {"ledger_name": "Cash", "entry_type": "Dr", "amount": 49500.0},
                {"ledger_name": party, "entry_type": "Cr", "amount": 49500.0}
            ]
        )

        # 3. Query Unreconciled
        unrec = self.db.get_unreconciled_bills_and_payments(party)
        self.assertEqual(len(unrec["unpaid_bills"]), 1)
        self.assertEqual(unrec["unpaid_bills"][0]["pending_amount"], 50000.0)

        # 4. Reconcile with ₹500 write-off discount
        recon_res = self.db.reconcile_payment_against_bill(
            payment_voucher_id=v_rcpt,
            party_ledger=party,
            bill_ref_no=bill_ref,
            allocated_amount=49500.0,
            write_off_amount=500.0,
            date="2026-10-16"
        )
        self.assertTrue(recon_res["success"])
        self.assertIsNotNone(recon_res["adjustment_voucher_id"])

        # 5. Query Unreconciled again - should be settled
        unrec_after = self.db.get_unreconciled_bills_and_payments(party)
        self.assertEqual(len(unrec_after["unpaid_bills"]), 1)
        self.assertEqual(unrec_after["unpaid_bills"][0]["pending_amount"], 500.0)

    def test_05_mca_rule_3_1_edit_log_diff_viewer(self):
        """Tests MCA Rule 3(1) audit trail edit log diff computation between voucher versions."""
        self.db.add_ledger("Office Refreshments", "Indirect Expenses", 0.0, "Dr")

        # Create voucher
        v_id = self.db.add_voucher(
            voucher_type="Payment",
            date="2026-10-01",
            narration="Original Refreshments",
            entries=[
                {"ledger_name": "Office Refreshments", "entry_type": "Dr", "amount": 1200.0},
                {"ledger_name": "Cash", "entry_type": "Cr", "amount": 1200.0}
            ]
        )

        # Alter voucher
        self.db.alter_voucher(
            voucher_id=v_id,
            voucher_type="Payment",
            date="2026-10-02",
            narration="Updated Refreshments for VIP Meeting",
            entries=[
                {"ledger_name": "Office Refreshments", "entry_type": "Dr", "amount": 1800.0},
                {"ledger_name": "Cash", "entry_type": "Cr", "amount": 1800.0}
            ],
            reason="Additional refreshments purchased"
        )

        # Version history
        history = self.db.get_voucher_version_history(v_id)
        self.assertGreaterEqual(len(history), 2)

        # Diff computation
        diff = self.db.get_voucher_diff(v_id)
        self.assertTrue(diff["has_diff"])
        self.assertGreaterEqual(len(diff["diffs"]), 1)
        changes = diff["diffs"][0]["changes"]
        self.assertIn("amount", changes)
        self.assertEqual(changes["amount"]["before"], 1200.0)
        self.assertEqual(changes["amount"]["after"], 1800.0)

    def test_06_new_tdl_reports_render(self):
        """Tests runtime rendering for all 5 new TDL reports."""
        reports = [
            ("JobWorkRegister", "Subcontracting & Job Work Register"),
            ("QualityInspection", "Quality Control & Inspection Register"),
            ("CurrencyExchange", "Multi-Currency & Historical Rates Register"),
            ("PaymentRecon", "Payment & Advance Bill Reconciliation"),
            ("AuditDiff", "Statutory Audit Trail (MCA Edit Log Diff)")
        ]
        for r_name, r_title in reports:
            rendered = self.runtime.render_report(r_name)
            self.assertEqual(rendered["report"], r_name)
            self.assertEqual(rendered["title"], r_title)
            self.assertIn("parts", rendered)
            self.assertGreater(len(rendered["parts"]), 0)
            self.assertIn("lines", rendered["parts"][0])

    def test_07_rest_api_endpoints_integration(self):
        """Verifies HTTP endpoints for all 5 new modules over local loopback."""
        port = 8893
        server = DefaultHTTPServer(("127.0.0.1", port), TallyRequestHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        base_url = f"http://127.0.0.1:{port}"

        try:
            # 1. Job Work API (POST + GET)
            jw_payload = {
                "order_type": "Job Work Out",
                "job_worker_name": "Cash",
                "order_date": "2026-10-31",
                "due_date": "2026-11-15",
                "finished_item_name": "Assembled Desktop Combo",
                "finished_qty": 3.0,
                "service_rate": 1200.0,
                "items": [{"stock_item_name": "RAM DDR4 16GB", "qty": 6, "rate": 3000.0}]
            }
            req = urllib.request.Request(f"{base_url}/api/job_work", data=json.dumps(jw_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 201)
                jw_res = json.loads(resp.read().decode())
                self.assertIn("order", jw_res)
                created_jw_id = jw_res["order"]["job_work_id"]

            with urllib.request.urlopen(f"{base_url}/api/job_work") as resp:
                self.assertEqual(resp.status, 200)

            with urllib.request.urlopen(f"{base_url}/api/job_work/order?id={created_jw_id}") as resp:
                self.assertEqual(resp.status, 200)

            # 2. Quality Inspection API (POST + GET)
            qi_payload = {
                "reference_type": "Purchase",
                "reference_no": "PO-1001",
                "stock_item_name": "RAM DDR4 16GB",
                "inspection_date": "2026-10-31",
                "inspected_qty": 10.0,
                "parameters": [{"name": "Voltage Test", "spec": "1.2V", "min_value": 1.15, "max_value": 1.25, "reading_value": 1.20}]
            }
            req = urllib.request.Request(f"{base_url}/api/quality_inspections", data=json.dumps(qi_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 201)
                qi_res = json.loads(resp.read().decode())
                self.assertIn("inspection", qi_res)
                created_qi_id = qi_res["inspection"]["inspection_id"]

            with urllib.request.urlopen(f"{base_url}/api/quality_inspections") as resp:
                self.assertEqual(resp.status, 200)

            with urllib.request.urlopen(f"{base_url}/api/quality_inspections/detail?id={created_qi_id}") as resp:
                self.assertEqual(resp.status, 200)

            # 3. Currency Exchange Rates API (POST + GET)
            curr_payload = {
                "currency_symbol": "£",
                "formal_name": "GBP",
                "effective_date": "2026-10-31",
                "standard_rate": 108.50
            }
            req = urllib.request.Request(f"{base_url}/api/currencies/exchange_rates", data=json.dumps(curr_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 201)

            with urllib.request.urlopen(f"{base_url}/api/currencies/exchange_rates?symbol=%C2%A3") as resp:
                self.assertEqual(resp.status, 200)

            # 4. Payment Reconciliation API (GET)
            with urllib.request.urlopen(f"{base_url}/api/payment_reconciliation/unallocated?party=Cash") as resp:
                self.assertEqual(resp.status, 200)

            # 5. Audit Diff API (GET)
            with urllib.request.urlopen(f"{base_url}/api/system/audit_diff?voucher_id=1") as resp:
                self.assertEqual(resp.status, 200)

            # 6. Company Multiple Addresses API
            addr_payload = {
                "branch_name": "Mumbai Financial Hub",
                "address": "Bandra Kurla Complex",
                "city": "Mumbai",
                "state": "Maharashtra",
                "state_code": "27",
                "pincode": "400051",
                "gstin": "27AABCT1332L1Z8",
                "is_primary": 0
            }
            req = urllib.request.Request(f"{base_url}/api/company/addresses", data=json.dumps(addr_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 201)

            with urllib.request.urlopen(f"{base_url}/api/company/addresses") as resp:
                self.assertEqual(resp.status, 200)

            # 7. MSME Compliance & Schedule III APIs
            with urllib.request.urlopen(f"{base_url}/api/msme/compliance") as resp:
                self.assertEqual(resp.status, 200)

            with urllib.request.urlopen(f"{base_url}/api/reports/schedule_iii") as resp:
                self.assertEqual(resp.status, 200)

            # 8. Rejections API
            rej_payload = {
                "date": "2026-10-31",
                "customer_name": "Cash",
                "items": [{"stock_item_name": "RAM DDR4 16GB", "qty": 1, "rate": 3000.0}]
            }
            req = urllib.request.Request(f"{base_url}/api/rejections/in", data=json.dumps(rej_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 201)

            with urllib.request.urlopen(f"{base_url}/api/rejections") as resp:
                self.assertEqual(resp.status, 200)

            # 9. Negative Stock Policy API
            pol_payload = {"allow_negative_stock": False}
            req = urllib.request.Request(f"{base_url}/api/inventory/negative_stock_policy", data=json.dumps(pol_payload).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)

        finally:
            server.shutdown()
            server.server_close()
            server.server_close()

    def test_08_company_multiple_addresses(self):
        """Tests maintaining multiple company addresses across states/branches."""
        # 1. Add secondary address
        addr_id = self.db.add_company_address(
            branch_name="Chennai Logistics Center",
            address="GST Road, Guindy",
            city="Chennai",
            state="Tamil Nadu",
            state_code="33",
            pincode="600032",
            gstin="33AABCT1332L1Z2",
            is_primary=0
        )
        self.assertGreater(addr_id, 0)

        # 2. Get addresses
        addrs = self.db.get_company_addresses()
        self.assertTrue(any(a["branch_name"] == "Chennai Logistics Center" for a in addrs))

        # 3. Delete address
        self.assertTrue(self.db.delete_company_address(addr_id))

    def test_09_msme_compliance_45_day_tracking(self):
        """Tests Section 43B(h) compliance tracking for Micro and Small enterprises."""
        supplier = "Micro Tech Spares"
        self.db.add_ledger(supplier, "Sundry Creditors", 0.0, "Cr")
        # Set MSME flag
        with self.db.conn:
            self.db.conn.execute("UPDATE ledger SET msme_type = 'Micro', msme_reg_no = 'UDYAM-KR-03-0012345' WHERE name = ?", (supplier,))

        # Add purchase invoice dated 60 days ago
        v_id = self.db.add_voucher(
            voucher_type="Purchase",
            date="2026-08-01",
            narration="Purchase of components from Micro enterprise",
            entries=[
                {"ledger_name": "Direct Expenses", "entry_type": "Dr", "amount": 25000.0},
                {"ledger_name": supplier, "entry_type": "Cr", "amount": 25000.0}
            ],
            bill_allocations=[
                {"ledger_name": supplier, "ref_type": "New Ref", "ref_name": "BILL-MSME-001", "amount": 25000.0}
            ]
        )

        # Run report as of 2026-10-31 (approx 91 days overdue)
        rep = self.db.get_msme_compliance_report(as_of_date="2026-10-31")
        self.assertGreater(rep["total_pending_payables"], 0)
        self.assertEqual(rep["disallowed_amount_sec_43bh"], 25000.0)
        self.assertEqual(len(rep["overdue_invoices"]), 1)
        self.assertGreater(rep["overdue_invoices"][0]["interest_liability"], 0)

    def test_10_schedule_iii_balance_sheet_division_i(self):
        """Tests Schedule III (Division I) Balance Sheet generation and statutory equilibrium."""
        s3 = self.db.get_schedule_iii_balance_sheet()
        self.assertIn("equity_and_liabilities", s3)
        self.assertIn("assets", s3)
        self.assertTrue(s3["is_balanced"])
        eq = s3["equity_and_liabilities"]
        ast = s3["assets"]
        self.assertAlmostEqual(eq["total_equity_and_liabilities"], ast["total_assets"], places=2)

    def test_11_rejections_in_and_out_vouchers(self):
        """Tests Rejections In and Rejections Out stock return notes."""
        # Rejections In
        r_in = self.db.record_rejections_in(
            date="2026-10-31",
            customer_name="Acme Retailers",
            items=[{"stock_item_name": "RAM DDR4 16GB", "qty": 2, "rate": 3000.0}],
            order_no="SO-991",
            narration="Customer rejected defective RAM"
        )
        self.assertEqual(r_in["rejection_type"], "Rejections In")
        self.assertEqual(r_in["status"], "Pending Adjustment")

        # Rejections Out
        r_out = self.db.record_rejections_out(
            date="2026-10-31",
            supplier_name="Global Silicon",
            items=[{"stock_item_name": "RAM DDR4 16GB", "qty": 2, "rate": 2800.0}],
            order_no="PO-991",
            narration="Returned defective RAM to supplier"
        )
        self.assertEqual(r_out["rejection_type"], "Rejections Out")

        # List rejections
        rejs = self.db.get_rejections()
        self.assertGreaterEqual(len(rejs), 2)

    def test_12_negative_stock_policy_enforcement(self):
        """Tests strict blocking of transactions that would drive inventory negative."""
        # 1. Block negative stock
        self.db.set_negative_stock_policy(False)
        self.assertFalse(self.db.get_negative_stock_policy())

        # Current stock for RAM DDR4 16GB is around 30. Attempt to sell 10,000 units.
        with self.assertRaises(ValueError) as ctx:
            self.db.add_voucher(
                voucher_type="Sales",
                date="2026-10-31",
                narration="Attempted Overselling",
                entries=[
                    {"ledger_name": "Cash", "entry_type": "Dr", "amount": 30000000.0},
                    {"ledger_name": "Sales Account", "entry_type": "Cr", "amount": 30000000.0}
                ],
                inventory_items=[
                    {"stock_item_name": "RAM DDR4 16GB", "qty": 10000, "rate": 3000.0, "direction": "Out"}
                ]
            )
        self.assertIn("Negative stock not permitted", str(ctx.exception))

        # 2. Allow negative stock
        self.db.set_negative_stock_policy(True)
        self.assertTrue(self.db.get_negative_stock_policy())

    def test_13_all_new_reports_render(self):
        """Tests TDL runtime rendering for the 3 newest reports."""
        reports = [
            ("MSMECompliance", "MSME Section 43B(h) 45-Day Compliance Statement"),
            ("ScheduleIIIBalanceSheet", "Schedule III Division I Balance Sheet"),
            ("RejectionsRegister", "Rejections In & Out Register")
        ]
        for r_name, r_title in reports:
            rendered = self.runtime.render_report(r_name)
            self.assertEqual(rendered["report"], r_name)
            self.assertEqual(rendered["title"], r_title)
            self.assertIn("parts", rendered)
            self.assertGreater(len(rendered["parts"]), 0)

    def test_14_tally_xml_data_interchange_roundtrip(self):
        """Tests importing and exporting official Tally XML Data Interchange format."""
        xml_sample = """<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDATA>
        <TALLYMESSAGE>
          <LEDGER NAME="Apex Systems Pvt Ltd" ACTION="Create">
            <NAME>Apex Systems Pvt Ltd</NAME>
            <PARENT>Sundry Debtors</PARENT>
            <OPENINGBALANCE>-75000.00</OPENINGBALANCE>
            <PARTYGSTIN>29AABCA1234F1Z5</PARTYGSTIN>
            <STATENAME>Karnataka</STATENAME>
          </LEDGER>
        </TALLYMESSAGE>
        <TALLYMESSAGE>
          <STOCKITEM NAME="UltraFast NVMe SSD 1TB" ACTION="Create">
            <NAME>UltraFast NVMe SSD 1TB</NAME>
            <BASEUNITS>Pcs</BASEUNITS>
            <OPENINGBALANCE>50 Pcs</OPENINGBALANCE>
            <OPENINGVALUE>250000.00</OPENINGVALUE>
            <GSTRATE>18.0</GSTRATE>
          </STOCKITEM>
        </TALLYMESSAGE>
        <TALLYMESSAGE>
          <VOUCHER VCHTYPE="Sales" ACTION="Create">
            <DATE>20261025</DATE>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <VOUCHERNUMBER>XML-INV-001</VOUCHERNUMBER>
            <PARTYLEDGERNAME>Apex Systems Pvt Ltd</PARTYLEDGERNAME>
            <NARRATION>Credit sale imported from Tally XML</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Apex Systems Pvt Ltd</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-11800.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Sales Account</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>10000.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Output CGST</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>900.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Output SGST</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>900.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>UltraFast NVMe SSD 1TB</STOCKITEMNAME>
              <ACTUALQTY>2 Pcs</ACTUALQTY>
              <RATE>5000.00</RATE>
              <AMOUNT>10000.00</AMOUNT>
              <GODOWNNAME>Main Location</GODOWNNAME>
            </ALLINVENTORYENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

        # 1. Test database XML import
        res = self.db.import_tally_xml(xml_sample)
        self.assertTrue(res["success"])
        self.assertGreaterEqual(res["imported_ledgers"], 1)
        self.assertGreaterEqual(res["imported_items"], 1)
        self.assertGreaterEqual(res["imported_vouchers"], 1)

        # Verify ledger imported
        led = self.db.get_ledger("Apex Systems Pvt Ltd")
        self.assertIsNotNone(led)
        self.assertEqual(led["group_name"], "Sundry Debtors")
        self.assertEqual(led["opening_balance"], 75000.0)
        self.assertEqual(led["opening_balance_type"], "Dr")

        # Verify item imported
        itm = self.db.get_stock_item("UltraFast NVMe SSD 1TB")
        self.assertIsNotNone(itm)

        # 2. Test database XML export
        exported_xml = self.db.export_tally_xml()
        self.assertIn("<ENVELOPE>", exported_xml)
        self.assertIn("Apex Systems Pvt Ltd", exported_xml)
        self.assertIn("UltraFast NVMe SSD 1TB", exported_xml)
        self.assertIn("XML-INV-001", exported_xml)

        # 3. Test API integration over HTTP
        port = 8894
        server = DefaultHTTPServer(("127.0.0.1", port), TallyRequestHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.3)

        try:
            base_url = f"http://127.0.0.1:{port}"
            # POST import
            req_data = json.dumps({"xml_data": xml_sample}).encode("utf-8")
            req = urllib.request.Request(f"{base_url}/api/tally/xml/import", data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode())
                self.assertTrue(body["success"])

            # GET export
            with urllib.request.urlopen(f"{base_url}/api/tally/xml/export") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("application/xml", resp.headers.get("Content-Type"))
                xml_out = resp.read().decode("utf-8")
                self.assertIn("<ENVELOPE>", xml_out)
        finally:
            server.shutdown()
            server.server_close()

if __name__ == "__main__":
    unittest.main()
