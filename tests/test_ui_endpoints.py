"""
Test Suite: Complete UI Endpoints & REST API Verification
Verifies all 50+ HTTP endpoints across:
- Gateway & State
- TDL Dynamic Reports
- Financial Statements
- Statutory Returns & Compliance
- Banking, BRS & Cheque Books
- Payroll & Statutory Deductions
- TDL Laboratory & AST Inspection
- Security, RBAC & Authentication (Token Lifecycle)
"""

import unittest
import os
import sys
import json
import urllib.request
import urllib.error
import threading
import time
from http.server import HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import TallyRequestHandler, DefaultHTTPServer
from engine.accounting_db import EnterpriseAccountingDB

class TestUIEndpoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]

        cls.test_db_path = f"test_ui_{int(time.time()*1000)%100000}.db"
        cls.db = EnterpriseAccountingDB(cls.test_db_path)
        import server
        cls.orig_db = server.db
        server.db = cls.db

        cls.server = DefaultHTTPServer(("127.0.0.1", cls.port), TallyRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        import server
        server.db = cls.orig_db
        cls.db.close()
        for ext in ["", "-wal", "-shm"]:
            f = cls.test_db_path + ext
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    def _get(self, path, token=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {"Connection": "close"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = resp.read().decode("utf-8")
                return resp.status, json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            data = e.read().decode("utf-8")
            return e.code, json.loads(data) if data else {}

    def _post(self, path, payload, token=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json", "Connection": "close"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = resp.read().decode("utf-8")
                return resp.status, json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            data = e.read().decode("utf-8")
            return e.code, json.loads(data) if data else {}

    def test_01_gateway_state_endpoint(self):
        status, data = self._get("/api/state")
        self.assertEqual(status, 200)
        self.assertIn("company", data)
        self.assertIn("ledgers", data)
        self.assertIn("stock_items", data)

    def test_02_system_help_and_statutory_config(self):
        status, data = self._get("/api/system/help")
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "100% Fully Offline")

        status, cfg = self._get("/api/statutory/config")
        self.assertEqual(status, 200)
        self.assertEqual(cfg["version"], "2026.1")
        self.assertIn("legal_basis", cfg)

    def test_03_smart_search_endpoint(self):
        status, data = self._get("/api/search?q=tax")
        self.assertEqual(status, 200)
        self.assertIn("results", data)
        self.assertGreater(len(data["results"]), 0)

    def test_04_core_financial_reports_trial_balance(self):
        status, data = self._get("/api/report/TrialBalance")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)
        self.assertEqual(data["title"], "Trial Balance")

    def test_05_core_financial_reports_balance_sheet(self):
        status, data = self._get("/api/report/BalanceSheet")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)
        self.assertEqual(data["title"], "Balance Sheet")

    def test_06_core_financial_reports_profit_and_loss(self):
        status, data = self._get("/api/report/ProfitAndLoss")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)
        self.assertEqual(data["title"], "Profit & Loss A/c")

    def test_07_core_financial_reports_day_book(self):
        status, data = self._get("/api/report/DayBook")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)
        self.assertEqual(data["title"], "Day Book")

    def test_08_core_financial_reports_funds_flow(self):
        status, data = self._get("/api/report/FundsFlow")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_09_core_financial_reports_budget_variance(self):
        status, data = self._get("/api/report/BudgetVariance")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_10_inventory_reports_stock_summary(self):
        status, data = self._get("/api/report/StockSummary")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_11_inventory_reports_ageing(self):
        status, data = self._get("/api/report/InventoryAgeing")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_12_inventory_reports_mfg_cost(self):
        status, data = self._get("/api/report/MfgCost")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_13_statutory_reports_gstr9(self):
        status, data = self._get("/api/report/GSTR9")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_14_statutory_reports_tds_register(self):
        status, data = self._get("/api/report/TdsRegister")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

    def test_15_banking_reports_brs_and_cheques(self):
        status, data = self._get("/api/report/ChequeRegister")
        self.assertEqual(status, 200)
        self.assertIn("parts", data)

        status, brs = self._get("/api/banking/brs?ledger=HDFC%20Bank")
        self.assertEqual(status, 200)
        self.assertIn("transactions", brs)

    def test_16_receivables_and_payables_endpoints(self):
        status, rec = self._get("/api/bills/receivables")
        self.assertEqual(status, 200)
        self.assertIsInstance(rec, list)

        status, pay = self._get("/api/bills/payables")
        self.assertEqual(status, 200)
        self.assertIsInstance(pay, list)

    def test_17_tdl_laboratory_endpoints(self):
        # 1. List definitions
        status, defs = self._get("/api/tdl/definitions")
        self.assertEqual(status, 200)
        self.assertGreater(len(defs), 0)

        # 2. Inspect definition
        status, insp = self._get("/api/tdl/inspect?type=Report&name=TrialBalance")
        self.assertEqual(status, 200)
        self.assertTrue(insp["found"])

        # 3. Inspect collection
        status, coll = self._get("/api/tdl/collection?name=LedgerCollection")
        self.assertEqual(status, 200)
        self.assertEqual(coll["collection_name"], "LedgerCollection")

        # 4. Diagnostics registry
        status, diag = self._get("/api/tdl/diagnostics")
        self.assertEqual(status, 200)
        self.assertIn("supported_definitions", diag)

    def test_18_auth_login_session_token_and_logout(self):
        # 1. Login with valid credentials
        status, res = self._post("/api/auth/login", {"username": "admin", "password": "admin123"})
        self.assertEqual(status, 200)
        self.assertEqual(res["status"], "success")
        self.assertIn("token", res)
        token = res["token"]

        # 2. Logout with token
        status, out = self._post("/api/auth/logout", {"token": token}, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(out["logged_out"])

    def test_19_rbac_unauthorized_post_blocked(self):
        # Create an auditor session (read-only)
        auditor_token = self.db.create_session({"id": 3, "username": "auditor", "role": "Auditor"})

        # Auditor trying to post a voucher must be rejected with 403 Forbidden
        status, res = self._post("/api/vouchers", {
            "voucher_type": "Journal",
            "date": "2026-06-01",
            "narration": "Illegal entry",
            "entries": [
                {"ledger_name": "Office Rent", "entry_type": "Dr", "amount": 100.0},
                {"ledger_name": "Cash", "entry_type": "Cr", "amount": 100.0}
            ]
        }, token=auditor_token)
        self.assertEqual(status, 403)
        self.assertIn("Forbidden", res.get("error", ""))

    def test_20_tdl_code_validation_and_injection(self):
        # 1. Validate syntax
        status, diag = self._post("/api/tdl/validate", {
            "code": "[Report: ValidTestReport]\nForm: ValidTestForm\nTitle: \"Test\""
        })
        self.assertEqual(status, 200)
        self.assertTrue(diag["valid"])

        # 2. Inject and compile live
        status, res = self._post("/api/tdl/apply", {
            "code": "[#Menu: Gateway of Tally]\nAdd: Item: \"Injected Menu Item\": Display: DayBook"
        })
        self.assertEqual(status, 200)
        self.assertTrue(res["applied"])

    def test_21_erpnext_fixed_assets_and_orders_api(self):
        # 1. Add Asset
        asset_code = f"FA-API-{int(time.time() * 1000) % 10000000}"
        status, a_res = self._post("/api/assets", {
            "asset_name": "API Test Workstation",
            "asset_code": asset_code,
            "category_name": "Computers & IT Assets",
            "purchase_cost": 75000.0,
            "scrap_value": 3000.0,
            "useful_life_months": 36,
            "depreciation_method": "SLM"
        })
        self.assertEqual(status, 201)
        self.assertGreater(a_res["asset_id"], 0)

        # 2. List Assets
        status, assets = self._get("/api/assets")
        self.assertEqual(status, 200)
        self.assertTrue(any(a["asset_code"] == asset_code for a in assets))

        # 3. Create Order
        status, o_res = self._post("/api/orders", {
            "order_type": "Sales Order",
            "party_name": "Cash",
            "date": "2026-10-31",
            "items": [{"item_name": "Dell 24\" FHD Monitor", "qty": 2, "rate": 12000.0}]
        })
        self.assertEqual(status, 201)
        self.assertIn("order", o_res)

        # 4. List Orders & Pending
        status, orders = self._get("/api/orders?type=Sales%20Order")
        self.assertEqual(status, 200)
        status, pending = self._get("/api/orders/pending?type=Sales%20Order")
        self.assertEqual(status, 200)

    def test_22_erpnext_pos_and_tcs_api(self):
        # 1. Post POS Invoice
        status, pos_res = self._post("/api/pos/invoice", {
            "date": "2026-10-31",
            "party_name": "Cash",
            "customer_phone": "9998887776",
            "cash_tendered": 5000.0,
            "items": [{"stock_item_name": "Dell 24\" FHD Monitor", "qty": 1, "rate": 4500.0, "amount": 4500.0}]
        })
        self.assertEqual(status, 201)

        # 2. List POS Invoices
        status, pos_invs = self._get("/api/pos/invoices?limit=10")
        self.assertEqual(status, 200)
        self.assertIsInstance(pos_invs, list)

        # 3. TCS Sections & Calculation
        status, sections = self._get("/api/tcs/sections")
        self.assertEqual(status, 200)
        self.assertTrue(any(s["section_code"] == "206C(1H)" for s in sections))

        status, tcs_calc = self._get("/api/tcs/calculate?party=Cash&amount=2500000")
        self.assertEqual(status, 200)
        self.assertIn("tcs_applicable", tcs_calc)

    def test_23_erpnext_gstr2b_and_challans_api(self):
        # 1. GSTR-2B Reconcile
        status, r_res = self._post("/api/gst/gstr2b_reconcile", {
            "return_period": "042026",
            "gstr2b_invoices": []
        })
        self.assertEqual(status, 200)
        self.assertIn("reconciliation", r_res)

        # 2. List Reconciliations
        status, recons = self._get("/api/gst/gstr2b_reconciliations")
        self.assertEqual(status, 200)

        # 3. Delivery Note
        status, dn_res = self._post("/api/challans/delivery_note", {
            "date": "2026-10-31",
            "customer_name": "Cash",
            "items": [{"stock_item_name": "Dell 24\" FHD Monitor", "qty": 1, "rate": 10000.0, "amount": 10000.0}]
        })
        self.assertEqual(status, 201)

        # 4. Receipt Note
        status, grn_res = self._post("/api/challans/receipt_note", {
            "date": "2026-10-31",
            "supplier_name": "Cash",
            "items": [{"stock_item_name": "Dell 24\" FHD Monitor", "qty": 1, "rate": 8000.0, "amount": 8000.0}]
        })
        self.assertEqual(status, 201)

        # 5. List Unbilled Challans
        status, chs = self._get("/api/challans")
        self.assertEqual(status, 200)

    def test_24_erpnext_credit_limit_and_statutory_api(self):
        # 1. Check & Update Credit Limit
        status, chk = self._get("/api/credit_limit/check?party=Cash&amount=10000")
        self.assertEqual(status, 200)
        self.assertIn("blocked", chk)

        status, upd = self._post("/api/credit_limit/update", {
            "party_name": "Cash",
            "credit_limit": 100000.0,
            "credit_days": 45,
            "enforce_credit_limit": 1
        })
        self.assertEqual(status, 200)

        # 2. Statutory Config
        status, stat = self._get("/api/statutory/config")
        self.assertEqual(status, 200)
        self.assertIn("tcs_sections", stat)
        self.assertIn("esi_statutory", stat)
        self.assertIn("fixed_asset_categories", stat)

        # 3. Render all 8 new reports via REST API
        rep_names = [
            "FixedAssets", "OrderTracking", "GSTR2BRecon", "LandedCost",
            "POSBilling", "ForexRevaluation", "Challans", "TCSRegister"
        ]
        for rn in rep_names:
            status, rep = self._get(f"/api/report/{rn}")
            self.assertEqual(status, 200)
            self.assertEqual(rep["report"], rn)
            self.assertIn("parts", rep)

    def test_25_advanced_enterprise_modules_api(self):
        # 1. Job Work API
        status, jw_res = self._post("/api/job_work", {
            "order_type": "Job Work Out",
            "job_worker_name": "Cash",
            "finished_item_name": "Assembled Desktop Combo",
            "finished_qty": 1.0,
            "service_rate": 800.0,
            "items": [{"stock_item_name": "RAM DDR4 16GB", "qty": 2, "rate": 3000.0}]
        })
        self.assertEqual(status, 201)
        jw_id = jw_res["order"]["job_work_id"]

        status, jw_list = self._get("/api/job_work")
        self.assertEqual(status, 200)

        status, jw_det = self._get(f"/api/job_work/order?id={jw_id}")
        self.assertEqual(status, 200)

        # 2. Quality Inspection API
        status, qi_res = self._post("/api/quality_inspections", {
            "reference_type": "Purchase",
            "stock_item_name": "RAM DDR4 16GB",
            "inspected_qty": 5.0,
            "parameters": [{"name": "Basic QC", "status": "Pass"}]
        })
        self.assertEqual(status, 201)
        qi_id = qi_res["inspection"]["inspection_id"]

        status, qi_list = self._get("/api/quality_inspections")
        self.assertEqual(status, 200)

        status, qi_det = self._get(f"/api/quality_inspections/detail?id={qi_id}")
        self.assertEqual(status, 200)

        # 3. Multi-Currency API
        status, curr_res = self._post("/api/currencies/exchange_rates", {
            "currency_symbol": "¥",
            "formal_name": "JPY",
            "effective_date": "2026-10-31",
            "standard_rate": 0.55
        })
        self.assertEqual(status, 201)

        status, curr_list = self._get("/api/currencies/exchange_rates")
        self.assertEqual(status, 200)

        # 4. Payment Reconciliation API
        status, unrec = self._get("/api/payment_reconciliation/unallocated?party=Cash")
        self.assertEqual(status, 200)
        self.assertIn("unpaid_bills", unrec)
        self.assertIn("unallocated_payments", unrec)

        # 5. Audit Diff API
        status, diff = self._get("/api/system/audit_diff?voucher_id=1")
        self.assertEqual(status, 200)
        self.assertIn("voucher_id", diff)

        # 6. Render all 5 new Phase 9 reports
        phase9_reports = ["JobWorkRegister", "QualityInspection", "CurrencyExchange", "PaymentRecon", "AuditDiff"]
        for rn in phase9_reports:
            status, rep = self._get(f"/api/report/{rn}")
            self.assertEqual(status, 200)
            self.assertEqual(rep["report"], rn)
            self.assertIn("parts", rep)

if __name__ == "__main__":
    unittest.main()
