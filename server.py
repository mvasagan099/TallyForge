"""
Enterprise Tally & TDL Offline Engine Server
Implements all 2026 TallyPrime modules:
- Multi-company & Accounting (Vouchers, Ledgers, Bill-wise allocations)
- Voucher Alteration, Cancellation & Auditing
- Inventory, Godowns & Manufacturing (BOM)
- GST Management & GSTR-1/GSTR-3B offline JSON returns
- Banking & BRS Reconciliation (Manual & CSV statement upload)
- Payroll processing & Payslips
- Audit Trail / Edit Log & SHA-256 Backups / Safe Restore
- Alt+G Live Smart Search across accounts, items, vouchers, and reports
- TDL Studio with live compile and syntax validation (line-by-line diagnostics)
"""

import os
import sys
import shutil
import json
import urllib.parse
import threading
import functools
from engine.company_store import CompanyStore
from engine.extension_api import get_route, post_route
from http.server import HTTPServer, BaseHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer as DefaultHTTPServer
except ImportError:
    DefaultHTTPServer = HTTPServer
from typing import Dict, Any, List

from engine.accounting_db import EnterpriseAccountingDB, AccountingDB
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.runtime import TDLRuntime
from engine.tdl_laboratory import TDLLaboratory
from engine.tdl_manager import TDLExtensionManager
from engine.statutory_config import (
    GST_TAX_SLABS, GST_STATE_CODES, TDS_SECTIONS_CONFIG,
    TCS_SECTIONS_CONFIG, ESI_STATUTORY_CONFIG, FIXED_ASSET_CATEGORIES,
    STATUTORY_LEGAL_BASIS, STATUTORY_CONFIG_VERSION
)

# Determine Resource Directory (read-only bundled static assets & TDL definitions)
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    RESOURCE_DIR = sys._MEIPASS
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_DIR = RESOURCE_DIR
STATIC_DIR = os.path.join(RESOURCE_DIR, "static")
DEFS_DIR = os.path.join(RESOURCE_DIR, "definitions")

# Determine User Data Directory (writable database, audit trail, backups)
if os.environ.get("TALLY_DATA_DIR"):
    DATA_DIR = os.path.abspath(os.environ["TALLY_DATA_DIR"])
elif getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    if os.access(exe_dir, os.W_OK):
        DATA_DIR = os.path.join(exe_dir, "data")
    else:
        DATA_DIR = os.path.join(os.path.expanduser("~"), "TallyPrimeData")
else:
    DATA_DIR = RESOURCE_DIR

os.makedirs(DATA_DIR, exist_ok=True)
db_name = os.environ.get("TALLY_DB_NAME", "tally_data.db")
db_path = os.path.join(DATA_DIR, db_name)
# Keep an empty folder empty until the user creates a company. Never copy bundled books.
# The in-memory engine supports existing state/version APIs before selection.
db = EnterpriseAccountingDB(db_path) if os.path.isfile(db_path) else EnterpriseAccountingDB(":memory:", seed_demo=False)
tdl_manager = TDLExtensionManager(DEFS_DIR)
symbol_table = tdl_manager.reload_all()
parser = tdl_manager.parser

base_tdl_path = os.path.join(DEFS_DIR, "base.tdl")
ext_tdl_path = os.path.join(DEFS_DIR, "custom_extension.tdl")
test_suite_tdl = os.path.join(DEFS_DIR, "tdl_test_suite.tdl")

runtime = TDLRuntime(symbol_table, db)
lab = TDLLaboratory(symbol_table, db, runtime)

company_store = CompanyStore(DATA_DIR, os.path.join(DATA_DIR, "company-path.json"),
                             explicit=bool(os.environ.get("TALLY_DATA_DIR")), legacy_name=db_name)
_company_lock = threading.RLock()
_company_generation = None
_company_contexts = {}
_bootstrap_db = db

def bind_company(candidate):
    global db
    db = candidate
    runtime.db = candidate
    lab.db = candidate
    lab.runtime.db = candidate


def activate_company(candidate):
    global _company_generation
    import secrets
    # One context per browser window. Requests bind their own DB under the engine lock.
    _company_generation = secrets.token_urlsafe(32)
    _company_contexts[_company_generation] = candidate
    bind_company(candidate)
    return _company_generation


def serialized_company_request(fn):
    @functools.wraps(fn)
    def guarded(self):
        global _company_generation
        with _company_lock:
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin', '')
            if host and urllib.parse.urlparse('http://' + host).hostname not in ('127.0.0.1', 'localhost', '::1'):
                return self.send_json(403, {'error': 'Untrusted application host'})
            if origin:
                parsed_origin = urllib.parse.urlparse(origin)
                if parsed_origin.scheme not in ('http', 'https') or parsed_origin.hostname not in ('127.0.0.1', 'localhost', '::1') or (host and parsed_origin.netloc != host):
                    return self.send_json(403, {'error': 'Cross-origin company access is not allowed'})
            context = self.headers.get("X-Company-Context")
            if context:
                if context not in _company_contexts:
                    return self.send_json(409, {"error": "Company session is closed. Select the company again."})
                bind_company(_company_contexts[context])
                _company_generation = context
            elif _company_contexts:
                bind_company(_bootstrap_db)
                _company_generation = None
            return fn(self)
    return guarded


class TallyRequestHandler(BaseHTTPRequestHandler):

    def get_authenticated_user(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts session token from Authorization: Bearer <token> or query parameter.
        Validates current account access. Local owner mode applies only to unsecured companies without supplied credentials.
        """
        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
        elif "token" in query:
            token = query["token"][0]

        if token:
            session = db.validate_session(token)
            if not session:
                return None
            access = session.get('company_access', 'ALL')
            if access != 'ALL':
                try:
                    allowed = json.loads(access) if access.startswith('[') else access.split(',')
                    if str(db.active_company_id) not in {str(c) for c in allowed}:
                        return None
                except (ValueError, TypeError):
                    return None
            return session
        secured = db.get_company().get('security_enabled', False)
        if secured or self.client_address[0] not in ('127.0.0.1', '::1'):
            return None
        # Explicit local, unsecured company mode. Invalid tokens never fall back to Admin.
        return {"username": "local_owner", "role": "Admin", "company_access": "ALL"}

    def send_json(self, status: int, data: Any):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, file_path: str, content_type: str):
        if not os.path.exists(file_path):
            self.send_error(404, "File Not Found")
            return
        with open(file_path, "rb") as f:
            content = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    @serialized_company_request
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self.serve_static(os.path.join(STATIC_DIR, "index.html"), "text/html")
        elif path.startswith("/static/"):
            rel_path = path[len("/static/"):]
            local_path = os.path.realpath(os.path.join(STATIC_DIR, rel_path))
            if os.path.commonpath([os.path.realpath(STATIC_DIR), local_path]) != os.path.realpath(STATIC_DIR):
                return self.send_json(403, {'error': 'Invalid asset path'})
            if rel_path.endswith(".css"):
                return self.serve_static(local_path, "text/css")
            elif rel_path.endswith(".js"):
                return self.serve_static(local_path, "application/javascript")
            else:
                return self.serve_static(local_path, "application/octet-stream")
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # Enforce RBAC permission for API routes
        user = self.get_authenticated_user(query)
        if path.startswith("/api/"):
            if not user and path not in ('/api/storage', '/api/system/version'):
                return self.send_json(401, {"error": "Sign in to the selected company"})
            if not user:
                user = {'role': 'Viewer'}
            if not db.check_rbac_permission(user["role"], path, "GET"):
                return self.send_json(403, {"error": f"Forbidden: Role '{user['role']}' is not permitted to access {path}"})

        if path == "/api/storage":
            if self.client_address[0] not in ('127.0.0.1', '::1'):
                return self.send_json(403, {'error': 'Company catalogue requires local access'})
            try:
                return self.send_json(200, company_store.catalogue(db.db_path, db.active_company_id))
            except (OSError, ValueError) as exc:
                return self.send_json(400, {"error": str(exc)})

        if path == '/api/export/excel':
            try:
                from engine.data_exchange import export_workbook
                content = export_workbook(db, query.get('kind', ['Ledgers'])[0])
                self.send_response(200)
                self.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition', 'attachment; filename="Company_Export.xlsx"')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as exc:
                return self.send_json(400, {'error': str(exc)})
        try:
            extra = get_route(path, query, db, company_store)
            if extra is not None:
                return self.send_json(*extra)
        except Exception as exc:
            return self.send_json(400, {'error': str(exc)})

        # API: Current Application State
        if path == "/api/state":
            company = db.get_company()
            ledgers = db.get_ledgers()
            stock_items = db.get_stock_items()
            godowns = db.get_godowns()
            uoms = db.get_uoms()
            groups = db.get_groups()
            employees = db.get_employees() if db.check_rbac_permission(user["role"], "/api/payroll/employees", "GET") else []
            cost_centres = db.get_cost_centres()
            menu_def = symbol_table.get_definition("Menu", "Gateway of Tally")
            menu_dict = menu_def.to_dict() if menu_def else {"items": []}
            return self.send_json(200, {
                "company": company,
                "company_context": str(_company_generation),
                "ledgers": ledgers,
                "stock_items": stock_items,
                "godowns": godowns,
                "uoms": uoms,
                "groups": groups,
                "employees": employees,
                "cost_centres": cost_centres,
                "menu": menu_dict,
                "schema_version": db.get_schema_version(),
                "app_version": "7.2.0-Enterprise"
            })

        elif path == "/api/system/version":
            return self.send_json(200, {
                "version": "7.2.0",
                "app_name": "TallyPrime Enterprise Offline",
                "schema_version": db.get_schema_version(),
                "offline": True,
                "statutory_version": STATUTORY_CONFIG_VERSION
            })

        elif path == "/api/system/migrations":
            return self.send_json(200, {
                "current_schema_version": db.get_schema_version(),
                "history": db.get_migration_history()
            })

        # API: Render TDL Report
        elif path.startswith("/api/report/"):
            report_name = urllib.parse.unquote(path[len("/api/report/"):])
            kwargs = {}
            if "ledger" in query:
                kwargs["ledger"] = query["ledger"][0]
            if "group" in query:
                kwargs["group"] = query["group"][0]
            if "month" in query:
                kwargs["month"] = query["month"][0]
            if "vtype" in query:
                kwargs["vtype"] = query["vtype"][0]
            if "view" in query:
                kwargs["view"] = query["view"][0]
            if "item" in query:
                kwargs["item"] = query["item"][0]
            if "party" in query:
                kwargs["party"] = query["party"][0]
            if "from_date" in query:
                kwargs["from_date"] = query["from_date"][0]
            if "to_date" in query:
                kwargs["to_date"] = query["to_date"][0]

            rendered = runtime.render_report(report_name, **kwargs)
            return self.send_json(200, rendered)

        # API: Alt+G Live Smart Search
        elif path == "/api/search":
            q = query.get("q", [""])[0].strip().lower()
            results = []
            if q:
                # 1. Reports match
                reports = [
                    {"label": "Balance Sheet", "action": "Display", "target": "BalanceSheet", "category": "Report"},
                    {"label": "GST Summary (outward / inward by rate)", "action": "Display", "target": "GSTSummary", "category": "Report"},
                    {"label": "Cash / Bank Books", "action": "Display", "target": "CashBankBooks", "category": "Report"},
                    {"label": "Purchase Orders (pending)", "action": "Display", "target": "PurchaseOrders", "category": "Report"},
                    {"label": "Sales Orders (pending)", "action": "Display", "target": "SalesOrders", "category": "Report"},
                    {"label": "Group Summary", "action": "Display", "target": "GroupSummary", "category": "Report"},
                    {"label": "Sales Register", "action": "Display", "target": "SalesRegister", "category": "Report"},
                    {"label": "Purchase Register", "action": "Display", "target": "PurchaseRegister", "category": "Report"},
                    {"label": "Payment Register", "action": "Display", "target": "PaymentRegister", "category": "Report"},
                    {"label": "Receipt Register", "action": "Display", "target": "ReceiptRegister", "category": "Report"},
                    {"label": "Journal Register", "action": "Display", "target": "JournalRegister", "category": "Report"},
                    {"label": "Contra Register", "action": "Display", "target": "ContraRegister", "category": "Report"},
                    {"label": "Credit Note Register", "action": "Display", "target": "CreditNoteRegister", "category": "Report"},
                    {"label": "Debit Note Register", "action": "Display", "target": "DebitNoteRegister", "category": "Report"},
                    {"label": "Profit & Loss A/c", "action": "Display", "target": "ProfitAndLoss", "category": "Report"},
                    {"label": "Trial Balance", "action": "Display", "target": "TrialBalance", "category": "Report"},
                    {"label": "Stock Summary", "action": "Display", "target": "StockSummary", "category": "Report"},
                    {"label": "Day Book", "action": "Display", "target": "DayBook", "category": "Report"},
                    {"label": "Bills Receivable (Ageing)", "action": "Display", "target": "OutstandingReceivables", "category": "Report"},
                    {"label": "Bills Payable (Ageing)", "action": "Display", "target": "OutstandingPayables", "category": "Report"},
                    {"label": "Bank Reconciliation (BRS)", "action": "Display", "target": "BankReconciliation", "category": "Report"},
                    {"label": "GSTR-1 Outward Return", "action": "Display", "target": "GSTR1", "category": "Statutory"},
                    {"label": "GSTR-3B Monthly Summary", "action": "Display", "target": "GSTR3B", "category": "Statutory"},
                    {"label": "GSTR-9 Annual Return", "action": "Display", "target": "GSTR9", "category": "Statutory"},
                    {"label": "Payroll Pay Sheet", "action": "Display", "target": "PayrollSummary", "category": "Report"},
                    {"label": "Ratio Analysis", "action": "Display", "target": "RatioAnalysis", "category": "Report"},
                    {"label": "Cash Flow Statement", "action": "Display", "target": "CashFlow", "category": "Report"},
                    {"label": "Funds Flow Statement", "action": "Display", "target": "FundsFlow", "category": "Report"},
                    {"label": "Budget Variance Analysis", "action": "Display", "target": "BudgetVariance", "category": "Report"},
                    {"label": "Inventory Ageing Analysis", "action": "Display", "target": "InventoryAgeing", "category": "Report"},
                    {"label": "Consolidated Financials", "action": "Display", "target": "ConsolidatedFinancials", "category": "Report"},
                    {"label": "TDS Withholding Register", "action": "Display", "target": "TdsRegister", "category": "Statutory"},
                    {"label": "Cheque Register & Books", "action": "Display", "target": "ChequeRegister", "category": "Banking"},
                    {"label": "Manufacturing & BOM Cost Sheet", "action": "Display", "target": "MfgCost", "category": "Inventory"},
                    {"label": "Cost Centre Breakdown", "action": "Display", "target": "CostCentres", "category": "Report"},
                    {"label": "Statutory Audit (Edit Log)", "action": "Display", "target": "AuditEditLog", "category": "Audit"},
                    {"label": "Fixed Assets & Depreciation", "action": "Display", "target": "FixedAssets", "category": "Asset"},
                    {"label": "Sales & Purchase Orders", "action": "Display", "target": "OrderTracking", "category": "Order"},
                    {"label": "GSTR-2B ITC Reconciliation", "action": "Display", "target": "GSTR2BRecon", "category": "Statutory"},
                    {"label": "Landed Cost Allocation", "action": "Display", "target": "LandedCost", "category": "Inventory"},
                    {"label": "Point of Sale (POS) Billing", "action": "Display", "target": "POSBilling", "category": "Billing"},
                    {"label": "Forex Revaluation Statement", "action": "Display", "target": "ForexRevaluation", "category": "Forex"},
                    {"label": "Delivery & Receipt Challans", "action": "Display", "target": "Challans", "category": "Inventory"},
                    {"label": "TCS Tax Collected at Source", "action": "Display", "target": "TCSRegister", "category": "Statutory"},
                    {"label": "Job Work & Subcontracting", "action": "Display", "target": "JobWorkRegister", "category": "JobWork"},
                    {"label": "Quality Control & Inspection", "action": "Display", "target": "QualityInspection", "category": "QC"},
                    {"label": "Multi-Currency Exchange Rates", "action": "Display", "target": "CurrencyExchange", "category": "Forex"},
                    {"label": "Payment & Bill Reconciliation", "action": "Display", "target": "PaymentRecon", "category": "Banking"},
                    {"label": "MCA Edit Log Diff Viewer", "action": "Display", "target": "AuditDiff", "category": "Audit"},
                    {"label": "MSME 45-Day Compliance", "action": "Display", "target": "MSMECompliance", "category": "Statutory"},
                    {"label": "Schedule III Balance Sheet", "action": "Display", "target": "ScheduleIIIBalanceSheet", "category": "Report"},
                    {"label": "Rejections In & Out Register", "action": "Display", "target": "RejectionsRegister", "category": "Inventory"},
                    {"label": "TDL Studio", "action": "Display", "target": "TDLStudio", "category": "System"}
                ]
                for r in reports:
                    if q in r["label"].lower() or q in r["target"].lower():
                        results.append(r)

                # 2. Ledgers match
                for l in db.get_ledgers():
                    if q in l["name"].lower() or q in l["group_name"].lower():
                        results.append({
                            "label": f"Ledger: {l['name']} ({l['group_name']})",
                            "action": "Display",
                            "target": f"GeneralLedger:{l['name']}",
                            "category": "Ledger"
                        })

                # 3. Stock Items match
                for s in db.get_stock_items():
                    if q in s["name"].lower() or q in s.get("hsn_code", "").lower():
                        results.append({
                            "label": f"Item: {s['name']} (HSN: {s.get('hsn_code', '')})",
                            "action": "Display",
                            "target": "StockSummary",
                            "category": "Stock Item"
                        })

                # 4. Vouchers match
                for row in db.conn.execute("SELECT id,voucher_type,voucher_number,narration,total_amount FROM voucher WHERE company_id=? AND (LOWER(voucher_number) LIKE ? OR LOWER(voucher_type) LIKE ? OR LOWER(COALESCE(narration,'')) LIKE ?) ORDER BY date DESC,id DESC LIMIT 100", (db.active_company_id, '%'+q+'%', '%'+q+'%', '%'+q+'%')):
                    v = dict(row)
                    if q in v["voucher_number"].lower() or q in v["voucher_type"].lower() or q in v.get("narration", "").lower():
                        results.append({
                            "label": f"Voucher: {v['voucher_type']} #{v['voucher_number']} (₹{v['total_amount']:,.2f})",
                            "action": "Display",
                            "target": "DayBook",
                            "category": "Voucher"
                        })

            return self.send_json(200, {"query": q, "results": results[:25]})

        # API: GST Summary & GSTR-1 JSON Return Export
        elif path == "/api/gst/gstr1_json":
            return self.send_json(200, db.generate_gstr1_json())

        elif path == "/api/gst/summary":
            return self.send_json(200, db.get_gst_summary())

        # API: Banking BRS
        elif path.startswith("/api/banking/brs"):
            bank_ledger = query.get("ledger", ["HDFC Bank"])[0]
            return self.send_json(200, db.get_bank_reconciliation(bank_ledger))

        # API: Receivables & Payables
        elif path == "/api/bills/receivables":
            return self.send_json(200, db.get_outstanding_bills("Sundry Debtors"))

        elif path == "/api/bills/payables":
            return self.send_json(200, db.get_outstanding_bills("Sundry Creditors"))

        # API: Payroll & Employees
        elif path in ("/api/payroll/employees", "/api/employees"):
            return self.send_json(200, db.get_employees())

        # API: Audit Edit Logs
        elif path == "/api/edit_logs":
            limit = int(query.get("limit", [50])[0])
            return self.send_json(200, db.get_edit_logs(limit))

        # API: Stock Items & Godowns
        elif path == "/api/stock_items":
            return self.send_json(200, db.get_stock_items())

        elif path == "/api/godowns":
            return self.send_json(200, db.get_godowns())

        elif path == "/api/uoms":
            return self.send_json(200, db.get_uoms())

        elif path == "/api/groups":
            return self.send_json(200, db.get_groups())

        elif path == "/api/cost_centres":
            return self.send_json(200, db.get_cost_centres())

        elif path == "/api/ratio_analysis":
            return self.send_json(200, db.get_ratio_analysis())

        elif path == "/api/financial_summary":
            return self.send_json(200, db.get_financial_summary())

        elif path == "/api/vouchers":
            v_type = query.get("type", [None])[0]
            limit = int(query.get("limit", [100])[0])
            fdate = query.get("from_date", [None])[0]
            tdate = query.get("to_date", [None])[0]
            return self.send_json(200, db.get_vouchers(limit, voucher_type=v_type, from_date=fdate, to_date=tdate))

        elif path == "/api/voucher" or path.startswith("/api/vouchers/"):
            vid_str = query.get("id", [None])[0] or query.get("voucher_id", [None])[0]
            if not vid_str and path.startswith("/api/vouchers/"):
                vid_str = path[len("/api/vouchers/"):].strip()
            if vid_str and vid_str.isdigit():
                v = db.get_voucher(int(vid_str))
                if v:
                    return self.send_json(200, v)
                return self.send_json(404, {"error": "Voucher not found"})
            return self.send_json(400, {"error": "voucher id required"})

        elif path == "/api/data/verify":
            return self.send_json(200, db.verify_data_integrity())

        elif path == "/api/users":
            return self.send_json(200, db.get_users())

        elif path == "/api/system/audit_history":
            return self.send_json(200, db.get_edit_logs())

        # PHASE 3 GET ENDPOINTS
        elif path == "/api/companies":
            return self.send_json(200, db.get_companies())

        elif path == "/api/currencies":
            return self.send_json(200, db.get_currencies())

        elif path == "/api/currencies/forex":
            return self.send_json(200, db.calculate_forex_gain_loss())

        elif path == "/api/budgets":
            return self.send_json(200, db.get_budgets())

        elif path == "/api/budgets/variance":
            b_name = query.get("name", [None])[0]
            return self.send_json(200, db.get_budget_variance_report(b_name))

        elif path == "/api/stock_categories":
            return self.send_json(200, db.get_stock_categories())

        elif path == "/api/compound_uoms":
            return self.send_json(200, db.get_compound_uoms())

        elif path == "/api/price_levels":
            return self.send_json(200, db.get_price_levels())

        elif path == "/api/price_list":
            return self.send_json(200, db.get_price_list())

        elif path == "/api/inventory/ageing":
            return self.send_json(200, db.get_inventory_ageing_report())

        elif path == "/api/manufacturing/cost_report":
            bom_n = query.get("bom", [None])[0]
            return self.send_json(200, db.get_manufacturing_cost_report(bom_n))

        elif path == "/api/gst/gstr9":
            return self.send_json(200, db.get_gstr9_summary())

        elif path == "/api/gst/einvoice_json":
            v_id = int(query.get("voucher_id", [1])[0])
            try:
                return self.send_json(200, db.generate_einvoice_json(v_id))
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/gst/eway_bill_json":
            v_id = int(query.get("voucher_id", [1])[0])
            try:
                return self.send_json(200, db.generate_eway_bill_json(v_id))
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/tds/sections":
            return self.send_json(200, db.get_tds_sections())

        elif path == "/api/tds/register":
            return self.send_json(200, db.get_tds_register())

        elif path == "/api/banking/cheque_books":
            return self.send_json(200, db.get_cheque_books())

        elif path == "/api/banking/payment_advice":
            v_id = int(query.get("voucher_id", [1])[0])
            try:
                return self.send_json(200, db.get_payment_advice(v_id))
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/reports/funds_flow":
            return self.send_json(200, db.get_funds_flow_statement())

        elif path == "/api/reports/scenario":
            sc_name = query.get("name", ["Provisional Scenario"])[0]
            return self.send_json(200, db.get_scenario_report(sc_name))

        elif path == "/api/interest/overdue":
            rate = float(query.get("rate", [18.0])[0])
            return self.send_json(200, db.calculate_overdue_interest(rate))

        elif path == "/api/auth/users":
            return self.send_json(200, db.get_users())

        elif path == "/api/payroll/payslip":
            code = query.get("emp_code", ["EMP001"])[0]
            try:
                slip = db.calculate_employee_salary(code)
                return self.send_json(200, slip)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # PHASE 4: STATUTORY CONFIGURATION & TDL LABORATORY
        elif path == "/api/statutory/config":
            return self.send_json(200, {
                "version": STATUTORY_CONFIG_VERSION,
                "gst_slabs": GST_TAX_SLABS,
                "state_codes": GST_STATE_CODES,
                "tds_sections": TDS_SECTIONS_CONFIG,
                "tcs_sections": TCS_SECTIONS_CONFIG,
                "esi_statutory": ESI_STATUTORY_CONFIG,
                "fixed_asset_categories": FIXED_ASSET_CATEGORIES,
                "legal_basis": STATUTORY_LEGAL_BASIS
            })

        elif path == "/api/tdl/definitions":
            dtype = query.get("type", [None])[0]
            return self.send_json(200, lab.list_all_definitions(dtype))

        elif path == "/api/tdl/inspect":
            dtype = query.get("type", ["Report"])[0]
            dname = query.get("name", ["BalanceSheet"])[0]
            return self.send_json(200, lab.inspect_definition(dtype, dname))

        elif path == "/api/tdl/collection":
            cname = query.get("name", ["StockItemsCollection"])[0]
            limit = int(query.get("limit", [10])[0])
            return self.send_json(200, lab.inspect_collection(cname, limit))

        elif path == "/api/tdl/diagnostics":
            return self.send_json(200, lab.get_supported_constructs_registry())

        elif path == "/api/tdl/extensions":
            return self.send_json(200, tdl_manager.list_extensions())

        elif path == "/api/tdl/extensions/inspect":
            fname = query.get("filename", ["custom_extension.tdl"])[0]
            try:
                return self.send_json(200, tdl_manager.inspect_extension(fname))
            except Exception as e:
                return self.send_json(404, {"error": str(e)})

        elif path == "/api/tdl/runtime_errors":
            return self.send_json(200, tdl_manager.get_runtime_errors())

        elif path == "/api/tdl/compatibility":
            return self.send_json(200, tdl_manager.get_compatibility_report())

        elif path == "/api/system/help":
            return self.send_json(200, {
                "shortcuts": [
                    {"key": "Alt+G", "action": "Go To Smart Search across reports, vouchers, masters"},
                    {"key": "F2", "action": "Change Current Working Accounting Date"},
                    {"key": "F3 / Alt+K", "action": "Company Management & Switching"},
                    {"key": "F4", "action": "Contra Voucher (Bank/Cash transfers)"},
                    {"key": "F5", "action": "Payment Voucher"},
                    {"key": "F6", "action": "Receipt Voucher"},
                    {"key": "F7", "action": "Journal Voucher (Adjustments, Deprec, TDS)"},
                    {"key": "F8", "action": "Sales Invoice (B2B, GST, Inventory)"},
                    {"key": "F9", "action": "Purchase Invoice (B2B, ITC, Stock)"},
                    {"key": "F10", "action": "Stock Items Master"},
                    {"key": "Alt+F5", "action": "Debit Note Voucher Entry"},
                    {"key": "Alt+F6", "action": "Credit Note Voucher Entry"},
                    {"key": "Alt+F7", "action": "Manufacturing Journal (BOM assembly)"},
                    {"key": "Ctrl+F4", "action": "Monthly Payroll Processing"},
                    {"key": "F11", "action": "Company Statutory & Feature Configuration"},
                    {"key": "F12", "action": "TDL Studio Live Definition Language IDE"},
                    {"key": "Ctrl+N", "action": "Open Bottom Calculator Drawer"},
                    {"key": "Ctrl+M / Esc", "action": "Close Calculator Drawer / Gateway"},
                    {"key": "Ctrl+A", "action": "Accept & Save Voucher / Form"},
                    {"key": "Esc", "action": "Back / Return to Gateway"}
                ],
                "version": "7.2.0 (2026 Enterprise Offline)",
                "mode": "100% Fully Offline"
            })

        # API: Current Loaded TDL Code
        elif path == "/api/tdl/code":
            tdl_files = [base_tdl_path, ext_tdl_path]
            combined = ""
            for p in tdl_files:
                if os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        combined += f.read() + "\n\n"
            return self.send_json(200, {"code": combined})

        # ERPNext Extension Module GET Endpoints
        elif path == "/api/assets":
            return self.send_json(200, db.get_fixed_assets())

        elif path == "/api/assets/schedule":
            aid = int(query.get("asset_id", [1])[0])
            try:
                return self.send_json(200, db.calculate_depreciation_schedule(aid))
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/assets/register":
            return self.send_json(200, db.get_fixed_asset_register())

        elif path == "/api/landed_cost":
            return self.send_json(200, db.get_landed_cost_vouchers())

        elif path == "/api/orders":
            otype = query.get("type", [None])[0]
            ostatus = query.get("status", [None])[0]
            return self.send_json(200, db.get_orders(order_type=otype, status=ostatus))

        elif path == "/api/orders/pending":
            otype = query.get("type", [None])[0]
            return self.send_json(200, db.get_pending_orders_report(order_type=otype))

        elif path == "/api/credit_limit/check":
            party = query.get("party", [""])[0]
            amt = float(query.get("amount", [0.0])[0])
            return self.send_json(200, db.check_credit_limit(party, amt))

        elif path == "/api/gst/gstr2b_reconciliations":
            return self.send_json(200, db.get_gstr2b_reconciliations())

        elif path == "/api/gst/gstr2b_recon_details":
            recon_id = int(query.get("recon_id", [1])[0])
            return self.send_json(200, db.get_gstr2b_recon_details(recon_id))

        elif path == "/api/pos/invoices":
            lim = int(query.get("limit", [50])[0])
            return self.send_json(200, db.get_pos_invoices(limit=lim))

        elif path == "/api/tcs/sections":
            return self.send_json(200, db.get_tcs_sections())

        elif path == "/api/tcs/calculate":
            party = query.get("party", [""])[0]
            amt = float(query.get("amount", [0.0])[0])
            return self.send_json(200, db.calculate_tcs(party, amt))

        elif path == "/api/tcs/register":
            return self.send_json(200, db.get_tcs_register())

        elif path == "/api/challans":
            return self.send_json(200, db.get_unbilled_challans())

        # Subcontracting & Job Work
        elif path == "/api/job_work":
            otype = query.get("type", [None])[0]
            ostatus = query.get("status", [None])[0]
            return self.send_json(200, db.get_job_work_orders(order_type=otype, status=ostatus))

        elif path == "/api/job_work/order":
            jid = int(query.get("id", [1])[0])
            res = db.get_job_work_order_details(jid)
            if not res:
                return self.send_json(404, {"error": "Job Work Order not found"})
            return self.send_json(200, res)

        # Quality Inspection & Testing
        elif path == "/api/quality_inspections":
            qstatus = query.get("status", [None])[0]
            item = query.get("item", [None])[0]
            return self.send_json(200, db.get_quality_inspections(status=qstatus, stock_item_name=item))

        elif path == "/api/quality_inspections/detail":
            qid = int(query.get("id", [1])[0])
            res = db.get_quality_inspection_details(qid)
            if not res:
                return self.send_json(404, {"error": "Quality Inspection not found"})
            return self.send_json(200, res)

        # Multi-Currency & Historical Rates
        elif path == "/api/currencies/exchange_rates":
            sym = query.get("symbol", [None])[0]
            return self.send_json(200, db.get_currency_exchange_rates(currency_symbol=sym))

        # Payment & Bill Reconciliation
        elif path == "/api/payment_reconciliation/unallocated":
            party = query.get("party", ["Cash"])[0]
            return self.send_json(200, db.get_unreconciled_bills_and_payments(party))

        # MCA Rule 3(1) Edit Log Diff
        elif path == "/api/system/audit_diff":
            vid = int(query.get("voucher_id", [1])[0])
            return self.send_json(200, db.get_voucher_diff(vid))

        elif path == "/api/system/audit_version_history":
            vid = int(query.get("voucher_id", [1])[0])
            return self.send_json(200, db.get_voucher_version_history(vid))

        # Company Multiple Addresses
        elif path == "/api/company/addresses":
            return self.send_json(200, db.get_company_addresses())

        # MSME Section 43B(h) Compliance
        elif path == "/api/msme/compliance":
            as_of = query.get("as_of", [None])[0]
            return self.send_json(200, db.get_msme_compliance_report(as_of_date=as_of))

        # Schedule III Financial Statements
        elif path == "/api/reports/schedule_iii":
            as_of = query.get("as_of", [None])[0]
            return self.send_json(200, db.get_schedule_iii_balance_sheet(as_of_date=as_of))

        # Rejections In & Out
        elif path == "/api/rejections":
            rtype = query.get("type", [None])[0]
            return self.send_json(200, db.get_rejections(rejection_type=rtype))

        # Negative Stock Policy
        elif path == "/api/inventory/negative_stock_policy":
            return self.send_json(200, {"allow_negative_stock": db.get_negative_stock_policy()})

        # Tally XML Data Interchange Export
        elif path == "/api/tally/xml/export":
            from engine.tally_xml_export import export_company_xml
            import re as _re
            xml_bytes = export_company_xml(db)
            safe = _re.sub(r'[^A-Za-z0-9._ -]+', '_', db.get_company().get('name', 'company')).strip() or 'company'
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{safe}.xml"')
            self.send_header("Content-Length", str(len(xml_bytes)))
            self.end_headers()
            self.wfile.write(xml_bytes)
            return

        else:
            self.send_error(404, "Endpoint not found")

    @serialized_company_request
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8")
        try:
            data = json.loads(body) if body else {}
        except Exception:
            return self.send_json(400, {"error": "Invalid JSON payload"})

        # Enforce Origin/CSRF Verification for local offline integrity
        origin = self.headers.get("Origin", "")
        if origin:
            parsed_origin = urllib.parse.urlparse(origin)
            if parsed_origin.hostname not in ("127.0.0.1", "localhost", "::1", None) and origin != "null":
                return self.send_json(403, {"error": f"CSRF Blocked: Cross-Origin request from '{origin}' not permitted."})

        # Enforce RBAC permission for mutating POST actions (skip login)
        user = self.get_authenticated_user(query)
        if path not in ('/api/auth/login', '/api/storage/select'):
            if not user:
                return self.send_json(401, {"error": "Sign in to the selected company"})
            if not db.check_rbac_permission(user["role"], path, "POST"):
                return self.send_json(403, {"error": f"Forbidden: Role '{user['role']}' is not permitted to perform POST on {path}"})

        if path.startswith("/api/storage/"):
            if path != "/api/storage/select" and user["role"] != "Admin":
                return self.send_json(403, {"error": "Only Admin can manage company storage"})
            # Storage changes are local desktop operations, never accepted from remote clients.
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_json(403, {"error": "Company storage requires local access"})
            try:
                if path == '/api/storage/native/inspect':
                    from engine.native_tally import inspect
                    return self.send_json(200, inspect(company_store.root, data.get('storage_key')))
                if path == '/api/storage/native/read':
                    from engine.native_records import read
                    return self.send_json(200, read(company_store.root, data.get('storage_key'),
                        kind=data.get('kind', 'masters'), offset=data.get('offset', 0),
                        limit=data.get('limit', 50), search=data.get('search', ''),
                        sequence=data.get('sequence'), source=data.get('source')))
                if path == '/api/storage/native/links':
                    from engine.native_links import read_link_matches
                    return self.send_json(200, read_link_matches(company_store.root, data.get('storage_key'),
                        data.get('voucher_guid'), limit=data.get('limit',50), offset=data.get('offset',0)))
                if path == '/api/storage/native/directory':
                    from engine.native_directory import read_directory
                    return self.send_json(200, read_directory(company_store.root,data.get('storage_key')))
                if path == '/api/storage/native/reference/verify':
                    from engine.native_reference import verify_references
                    return self.send_json(200, verify_references(company_store.root,data.get('storage_key')))
                if path == '/api/storage/native/books':
                    from engine.native_reference import verify_references
                    return self.send_json(200, verify_references(company_store.root,data.get('storage_key'),include_books=True))
                if path == '/api/storage/native/sales/preview':
                    from engine.native_sales import read_sales_preview
                    return self.send_json(200, read_sales_preview(company_store.root,data.get('storage_key'),data.get('sequence')))
                if path == '/api/storage/import/start':
                    from engine import tally_exchange
                    return self.send_json(202, tally_exchange.start(company_store, data))
                if path == '/api/storage/import/status':
                    from engine import tally_exchange
                    return self.send_json(200, tally_exchange.status(data.get('job')))
                if path == '/api/storage/export/xml':
                    from engine import tally_exchange
                    return self.send_json(200, tally_exchange.export_to_file(company_store, db, data))
                if path == "/api/storage/path":
                    company_store.set_path(data.get("data_path"))
                elif path == "/api/storage/create":
                    key = company_store.create(data)
                    return self.send_json(201, {"storage_key": key})
                elif path == "/api/storage/select":
                    candidate = company_store.open(data.get("storage_key"))
                    try:
                        if candidate.get_company().get('security_enabled'):
                            account = candidate.authenticate_user(data.get('username', ''), data.get('password', ''))
                            if not account:
                                candidate.close()
                                return self.send_json(401, {'error': 'Company username and password required'})
                            access = account.get('company_access', 'ALL')
                            allowed = json.loads(access) if access.startswith('[') else access.split(',')
                            if access != 'ALL' and str(candidate.active_company_id) not in {str(x) for x in allowed}:
                                candidate.close()
                                return self.send_json(403, {'error': 'Account cannot access this company'})
                            token = candidate.create_session(account)
                        else:
                            token = ''
                        activate_company(candidate)
                    except Exception:
                        candidate.close()
                        raise
                    return self.send_json(200, {"company_context": str(_company_generation), "token": token})
                else:
                    return self.send_json(404, {"error": "Unknown storage operation"})
                return self.send_json(200, company_store.catalogue(db.db_path, db.active_company_id))
            except Exception as exc:
                return self.send_json(400, {"error": str(exc)})

        try:
            extra = post_route(path, data, db, company_store, user, contexts=_company_contexts)
            if extra is not None:
                return self.send_json(*extra)
        except Exception as exc:
            return self.send_json(400, {'error': str(exc)})

        # API: Validate TDL Code (Syntax checking with line numbers)
        if path == "/api/tdl/validate":
            code = data.get("code", "")
            diagnostics = parser.validate_syntax(code)
            return self.send_json(200, {
                "valid": len([d for d in diagnostics if d["severity"] == "error"]) == 0,
                "diagnostics": diagnostics
            })

        # API: Hot-reload / Apply TDL Code
        elif path in ("/api/tdl/load", "/api/tdl/apply"):
            code = data.get("code", "")
            if not code.strip():
                return self.send_json(400, {"error": "No TDL code provided"})
            try:
                parser.parse_string(code)
                total_defs = sum(len(d) for d in symbol_table.definitions.values())
                return self.send_json(200, {
                    "status": "success",
                    "applied": True,
                    "definitions_count": total_defs,
                    "modified_count": 1,
                    "total_definitions": total_defs,
                    "definitions": symbol_table.all_definitions()
                })
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: TDL Extensions Management
        elif path == "/api/tdl/extensions/import":
            fname = data.get("filename", "custom_import.tdl")
            content = data.get("content", "")
            desc = data.get("description", "")
            if not content:
                return self.send_json(400, {"error": "No TDL content provided"})
            res = tdl_manager.import_extension(fname, content, desc)
            return self.send_json(200, res)

        elif path == "/api/tdl/extensions/toggle":
            fname = data.get("filename", "")
            enabled = bool(data.get("enabled", True))
            ok = tdl_manager.toggle_extension(fname, enabled)
            return self.send_json(200, {"status": "success" if ok else "not_found", "filename": fname, "enabled": enabled})

        elif path == "/api/tdl/extensions/remove":
            fname = data.get("filename", "")
            try:
                tdl_manager.remove_extension(fname)
                return self.send_json(200, {"status": "success", "removed": fname})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/tdl/extensions/reload":
            st = tdl_manager.reload_all()
            total_defs = sum(len(d) for d in st.definitions.values())
            return self.send_json(200, {"status": "success", "reloaded": True, "total_definitions": total_defs})

        # API: Add Voucher (Double Entry, Inventory, Bill Allocations)
        elif path == "/api/vouchers":
            v_type = data.get("voucher_type", "Payment")
            v_date = data.get("date", "")
            narration = data.get("narration", "")
            entries = data.get("entries", [])
            inv_items = data.get("inventory_items", [])
            bill_allocations = data.get("bill_allocations", [])
            curr_sym = data.get("currency_symbol", "₹")
            ex_rate = float(data.get("exchange_rate", 1.0))

            if not entries:
                return self.send_json(400, {"error": "No voucher entries provided"})
            try:
                v_id = db.add_voucher(v_type, v_date, narration, entries, inv_items, bill_allocations, curr_sym, ex_rate, gst_registration_id=data.get('gst_registration_id'))
                return self.send_json(201, {"status": "success", "voucher_id": v_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Alter Voucher
        elif path == "/api/vouchers/alter":
            v_id = int(data.get("voucher_id", 0))
            v_date = data.get("date", "")
            narration = data.get("narration", "")
            entries = data.get("entries", [])
            inv_items = data.get("inventory_items", [])
            bill_allocations = data.get("bill_allocations", [])
            user_name = data.get("user_name", "Admin")

            if not v_id or not entries:
                return self.send_json(400, {"error": "voucher_id and entries required"})
            try:
                db.alter_voucher(v_id, v_date, narration, entries, inv_items, bill_allocations, user_name)
                return self.send_json(200, {"status": "success", "voucher_id": v_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Cancel Voucher
        elif path == "/api/vouchers/cancel":
            v_id = int(data.get("voucher_id", 0))
            reason = data.get("reason", "Cancelled by User")
            user_name = data.get("user_name", "Admin")
            if not v_id:
                return self.send_json(400, {"error": "voucher_id required"})
            try:
                db.cancel_voucher(v_id, reason, user_name)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Delete Voucher
        elif path == "/api/vouchers/delete":
            v_id = int(data.get("voucher_id", 0))
            user_name = data.get("user_name", "Admin")
            if not v_id:
                return self.send_json(400, {"error": "voucher_id required"})
            try:
                db.delete_voucher(v_id, user_name)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Manufacturing Produce (BOM processing)
        elif path == "/api/manufacturing/produce":
            bom_name = data.get("bom_name", "Assembled Desktop Combo")
            qty = float(data.get("qty", 1.0))
            date = data.get("date", "")
            try:
                v_id = db.process_manufacturing_journal(bom_name, qty, date)
                return self.send_json(201, {"status": "success", "voucher_id": v_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Payroll Processing
        elif path == "/api/payroll/process":
            month = data.get("month", "October 2026")
            pdate = data.get("date", "2026-10-31")
            try:
                v_id = db.process_payroll_voucher(month, pdate, expected_preview=data.get("preview_id"))
                return self.send_json(201, {"status": "success", "voucher_id": v_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Banking Reconcile (Manual)
        elif path == "/api/banking/reconcile":
            recon_id = int(data.get("recon_id", 0))
            bdate = data.get("bank_date", "")
            if not recon_id or not bdate:
                return self.send_json(400, {"error": "recon_id and bank_date required"})
            try:
                db.reconcile_transaction(recon_id, bdate)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Banking Statement CSV Upload & Auto-reconciliation
        elif path == "/api/banking/upload_statement":
            bank_ledger = data.get("bank_ledger", "HDFC Bank")
            csv_content = data.get("csv_content", "")
            if not csv_content.strip():
                return self.send_json(400, {"error": "csv_content is required"})
            try:
                recon_summary = db.auto_reconcile_statement_csv(bank_ledger, csv_content)
                return self.send_json(200, {"status": "success", "summary": recon_summary})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Local Backup Creation
        elif path == "/api/backup/create":
            try:
                bdir = os.path.join(os.path.dirname(os.path.abspath(db.db_path)), "backups")
                fpath = db.create_local_backup(backup_dir=bdir)
                chk = ""
                with open(fpath, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                    chk = b_data.get("sha256_checksum", "")
                return self.send_json(200, {"status": "success", "backup_file": fpath, "backup_path": fpath, "sha256": chk})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Local Backup Safe Restore
        elif path == "/api/backup/restore":
            backup_file = data.get("backup_file", "")
            if not backup_file:
                return self.send_json(400, {"error": "backup_file path required"})
            try:
                success = db.restore_local_backup(backup_file)
                return self.send_json(200, {"status": "success", "restored": success})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Ledger
        elif path == "/api/ledgers":
            name = data.get("name", "").strip()
            group_name = data.get("group_name", "").strip()
            op_bal = float(data.get("opening_balance", 0.0))
            op_type = data.get("opening_type", "Dr")
            contact = data.get("contact_person", "")
            gstin = data.get("gstin", "")
            credit_limit = float(data.get("credit_limit", 0.0))
            if not name or not group_name:
                return self.send_json(400, {"error": "Name and group are required"})
            try:
                db.add_ledger(name, group_name, op_bal, op_type, contact, gstin, "Karnataka", credit_limit)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Stock Item
        elif path == "/api/stock_items":
            name = data.get("name", "").strip()
            group_name = data.get("group_name", "Primary").strip()
            uom_symbol = data.get("uom_symbol", "Nos").strip()
            hsn = data.get("hsn_code", "")
            rate = float(data.get("gst_rate", 18.0))
            op_qty = float(data.get("opening_qty", 0.0))
            op_rate = float(data.get("opening_rate", 0.0))
            godown = data.get("godown_name", "Main Location")
            if not name:
                return self.send_json(400, {"error": "Item name is required"})
            try:
                db.add_stock_item(name, group_name, uom_symbol, hsn, rate, op_qty, op_rate, godown)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Cost Centre
        elif path == "/api/cost_centres":
            name = data.get("name", "").strip()
            category = data.get("category_name", "Departments").strip()
            parent = data.get("parent", "Primary").strip()
            if not name:
                return self.send_json(400, {"error": "Cost centre name is required"})
            try:
                db.add_cost_centre(name, category, parent)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Group
        elif path == "/api/groups":
            name = data.get("name", "").strip()
            parent = data.get("parent", "Primary").strip()
            is_rev = bool(data.get("is_revenue", False))
            affects_gross = bool(data.get("affects_gross", False))
            if not name:
                return self.send_json(400, {"error": "Group name is required"})
            try:
                db.add_group(name, parent, is_rev, affects_gross)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Godown
        elif path == "/api/godowns":
            name = data.get("name", "").strip()
            location = data.get("location", "Default Location").strip()
            if not name:
                return self.send_json(400, {"error": "Godown name is required"})
            try:
                db.add_godown(name, location)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Unit of Measure (UOM)
        elif path == "/api/uoms":
            symbol = data.get("symbol", "").strip()
            formal_name = data.get("formal_name", symbol).strip()
            decimals = int(data.get("decimal_places", 0))
            if not symbol:
                return self.send_json(400, {"error": "UOM symbol is required"})
            try:
                db.add_uom(symbol, formal_name, decimals)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Add Employee
        elif path == "/api/employees":
            emp_code = data.get("emp_code", "").strip()
            name = data.get("name", "").strip()
            department = data.get("department", "Operations").strip()
            designation = data.get("designation", "Executive").strip()
            basic_salary = float(data.get("basic_salary", 30000.0))
            pan = data.get("pan", "").strip()
            pf_uan = data.get("pf_uan", "").strip()
            bank_acc = data.get("bank_acc_no", "").strip()
            if not emp_code or not name:
                return self.send_json(400, {"error": "Employee Code and Name are required"})
            try:
                db.add_employee(emp_code, name, department, designation, basic_salary, pan, pf_uan, bank_acc)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Update Company
        elif path == "/api/company":
            c_name = data.get("name", "").strip()
            c_addr = data.get("address", "")
            c_gstin = data.get("gstin", "")
            c_state = data.get("state", "Karnataka")
            c_state_code = data.get("state_code", "29")
            if not c_name:
                return self.send_json(400, {"error": "Company name is required"})
            try:
                db.update_company(c_name, c_addr, c_gstin, c_state, c_state_code)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # PHASE 3 POST ENDPOINTS
        elif path == "/api/companies/create":
            if os.path.basename(db.db_path) == "company.db":
                return self.send_json(400, {"error": "Create a separate company using the company data folder chooser"})
            name = data.get("name", "").strip()
            if not name:
                return self.send_json(400, {"error": "Company name is required"})
            try:
                new_id = db.create_company(
                    name=name,
                    formal_name=data.get("formal_name", ""),
                    gstin=data.get("gstin", ""),
                    state=data.get("state", "Karnataka"),
                    state_code=data.get("state_code", "29"),
                    address=data.get("address", ""),
                    financial_year_from=data.get("financial_year_from", "2026-04-01"),
                    financial_year_to=data.get("financial_year_to", "2027-03-31")
                )
                return self.send_json(201, {"status": "success", "company_id": new_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/switch":
            cid = int(data.get("company_id", 1))
            if db.select_company(cid):
                return self.send_json(200, {"status": "success", "active_company_id": cid})
            return self.send_json(404, {"error": f"Company ID {cid} not found"})

        elif path == "/api/companies/consolidate":
            if os.path.basename(db.db_path) == "company.db":
                return self.send_json(400, {"error": "Consolidation across separate company files is not yet supported"})
            gname = data.get("group_name", "").strip()
            mids = data.get("member_company_ids", [])
            if not gname or not mids:
                return self.send_json(400, {"error": "group_name and member_company_ids required"})
            try:
                gid = db.consolidate_companies(gname, mids)
                return self.send_json(201, {"status": "success", "group_company_id": gid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/alter":
            cid = int(data.get("company_id", db.active_company_id))
            name = data.get("name", "").strip()
            if not name:
                return self.send_json(400, {"error": "Company name required"})
            try:
                db.alter_company(
                    cid,
                    name,
                    formal_name=data.get("formal_name", name),
                    gstin=data.get("gstin", ""),
                    state=data.get("state", "Karnataka"),
                    state_code=data.get("state_code", "29"),
                    financial_year_from=data.get("financial_year_from", "2026-04-01"),
                    financial_year_to=data.get("financial_year_to", "2027-03-31"),
                    address=data.get("address", ""),
                    pincode=data.get("pincode", "560001"),
                    currency_symbol=data.get("currency_symbol", "₹")
                )
                return self.send_json(200, {"status": "success", "company_id": cid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/shut":
            cid = int(data.get("company_id", db.active_company_id))
            try:
                if os.path.basename(db.db_path) == "company.db":
                    context = self.headers.get('X-Company-Context')
                    if context in _company_contexts:
                        _company_contexts.pop(context).close()
                    bind_company(_bootstrap_db)
                    return self.send_json(200, {"status": "success", "active_company_id": None})
                next_id = db.shut_company(cid)
                return self.send_json(200, {"status": "success", "shut_company_id": cid, "active_company_id": next_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/delete":
            cid = int(data.get("company_id", 0))
            if not cid:
                return self.send_json(400, {"error": "company_id required"})
            try:
                if os.path.basename(db.db_path) == 'company.db':
                    result = company_store.archive_company(db)
                    path_to_close = os.path.realpath(db.db_path)
                    for context, engine in list(_company_contexts.items()):
                        if os.path.realpath(engine.db_path) == path_to_close:
                            _company_contexts.pop(context).close()
                    bind_company(_bootstrap_db)
                    return self.send_json(200, {"status": "success", "message": "Company removed from the list; a recovery copy is retained.", **result})
                db.delete_company(cid)
                return self.send_json(200, {"status": "success", "deleted_company_id": cid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/security":
            cid = int(data.get("company_id", db.active_company_id))
            sec = bool(data.get("security_enabled", False))
            pwd = data.get("vault_password", "")
            try:
                if sec and db.conn.execute("SELECT COUNT(*) FROM user_account WHERE role='Admin' AND is_active=1").fetchone()[0] == 0:
                    db.create_user(data.get('admin_username', ''), data.get('admin_password', ''), 'Admin')
                db.update_company_security(cid, sec, pwd)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Authenticated TallyVault Export (.tve)
        elif path == "/api/company/vault/export":
            cid = int(data.get("company_id", db.active_company_id))
            pwd = data.get("vault_password", "")
            out_path = data.get("output_path", os.path.join(os.path.dirname(os.path.abspath(db.db_path)), f"vault_export_company_{cid}.tve"))
            try:
                res = db.export_authenticated_company_vault(cid, pwd, out_path)
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # API: Authenticated TallyVault Import (.tve)
        elif path == "/api/company/vault/import":
            f_path = data.get("vault_file_path", "")
            pwd = data.get("vault_password", "")
            try:
                res = db.import_authenticated_company_vault(f_path, pwd)
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/companies/update_fy":
            cid = int(data.get("company_id", db.active_company_id))
            fy_start = data.get("fy_start", "2026-04-01")
            fy_end = data.get("fy_end", "2027-03-31")
            try:
                db.update_company_financial_year(cid, fy_start, fy_end)
                return self.send_json(200, {"status": "success", "financial_year_from": fy_start, "financial_year_to": fy_end})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/data/repair":
            try:
                res = db.repair_data()
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/data/migrate":
            try:
                res = db.migrate_data()
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/users":
            uname = data.get("username", "").strip()
            pwd = data.get("password", "").strip()
            role = data.get("role", "Data Entry").strip()
            fname = data.get("full_name", "").strip()
            if not uname or not pwd:
                return self.send_json(400, {"error": "username and password required"})
            try:
                db.create_user(uname, pwd, role, fname)
                return self.send_json(201, {"status": "success", "username": uname, "role": role})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/users/update":
            uid = int(data.get("user_id", 0))
            role = data.get("role", "Data Entry").strip()
            fname = data.get("full_name", "").strip()
            is_act = int(data.get("is_active", 1))
            if not uid:
                return self.send_json(400, {"error": "user_id required"})
            try:
                db.update_user(uid, role, fname, is_act)
                return self.send_json(200, {"status": "success", "user_id": uid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/users/delete":
            uid = int(data.get("user_id", 0))
            if not uid:
                return self.send_json(400, {"error": "user_id required"})
            try:
                db.delete_user(uid)
                return self.send_json(200, {"status": "success", "deleted_user_id": uid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/tdl/compatibility_report":
            code = data.get("code", "")
            ext_name = data.get("name", "CustomExtension")
            rep = lab.generate_compatibility_report(code, ext_name)
            return self.send_json(200, rep)

        elif path == "/api/company/close_fy":
            cur_end = data.get("current_fy_end", "2027-03-31")
            next_start = data.get("next_fy_start", "2027-04-01")
            cap = data.get("capital_account", "Capital Account")
            try:
                res = db.close_financial_year(cur_end, next_start, cap)
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/company/split_fy":
            sdate = data.get("split_date", "2026-10-01")
            default_arch = os.path.join(os.path.dirname(os.path.abspath(db.db_path)), "backups", "tally_archive_prior.db")
            arch = data.get("archive_db_path", default_arch)
            try:
                res = db.split_financial_year(sdate, arch)
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/currencies":
            sym = data.get("symbol", "").strip()
            fname = data.get("formal_name", "").strip()
            rate = float(data.get("rate_of_exchange", 1.0))
            is_b = bool(data.get("is_base", False))
            if not sym or not fname:
                return self.send_json(400, {"error": "symbol and formal_name required"})
            try:
                db.add_currency(sym, fname, rate, is_b)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/budgets":
            bname = data.get("name", "").strip()
            lname = data.get("ledger_name", "").strip()
            bamt = float(data.get("budgeted_amount", 0.0))
            pfrom = data.get("period_from", "2026-04-01")
            pto = data.get("period_to", "2027-03-31")
            if not bname or not lname:
                return self.send_json(400, {"error": "Budget name and ledger_name required"})
            try:
                db.add_budget(bname, lname, bamt, pfrom, pto)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/stock_categories":
            cname = data.get("name", "").strip()
            parent = data.get("parent", "Primary").strip()
            if not cname:
                return self.send_json(400, {"error": "Category name required"})
            try:
                db.add_stock_category(cname, parent)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/compound_uoms":
            maj = data.get("major_uom", "").strip()
            conv = float(data.get("conversion_factor", 1.0))
            minor = data.get("minor_uom", "").strip()
            if not maj or not minor:
                return self.send_json(400, {"error": "major_uom and minor_uom required"})
            try:
                db.add_compound_uom(maj, conv, minor)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/price_levels":
            pname = data.get("name", "").strip()
            if not pname:
                return self.send_json(400, {"error": "Price level name required"})
            try:
                db.add_price_level(pname)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/price_list":
            plevel = data.get("price_level", "").strip()
            sitem = data.get("stock_item_name", "").strip()
            rate = float(data.get("rate", 0.0))
            min_q = float(data.get("min_qty", 1.0))
            disc = float(data.get("discount_pct", 0.0))
            if not plevel or not sitem:
                return self.send_json(400, {"error": "price_level and stock_item_name required"})
            try:
                db.set_price_list_item(plevel, sitem, rate, min_q, disc)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/inventory/physical_stock":
            sdate = data.get("date", "")
            sitem = data.get("stock_item_name", "").strip()
            act_qty = float(data.get("actual_qty", 0.0))
            godown = data.get("godown_name", "Main Location")
            narr = data.get("narration", "")
            if not sitem:
                return self.send_json(400, {"error": "stock_item_name required"})
            try:
                res = db.process_physical_stock_voucher(sdate, sitem, act_qty, godown, narr)
                return self.send_json(201, {"status": "success", "result": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/vouchers/duplicate":
            vid = int(data.get("voucher_id", 0))
            ndate = data.get("date")
            nvnum = data.get("voucher_number")
            if not vid:
                return self.send_json(400, {"error": "voucher_id required"})
            try:
                dup_id = db.duplicate_voucher(vid, ndate, nvnum)
                return self.send_json(201, {"status": "success", "new_voucher_id": dup_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/banking/cheque_books":
            bledger = data.get("bank_ledger_name", "").strip()
            bname = data.get("book_name", "").strip()
            fno = int(data.get("from_no", 100001))
            tno = int(data.get("to_no", 100100))
            if not bledger or not bname:
                return self.send_json(400, {"error": "bank_ledger_name and book_name required"})
            try:
                db.add_cheque_book(bledger, bname, fno, tno)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/banking/issue_cheque":
            bledger = data.get("bank_ledger_name", "").strip()
            vid = int(data.get("voucher_id", 0))
            inst_no = data.get("instrument_no")
            if not bledger or not vid:
                return self.send_json(400, {"error": "bank_ledger_name and voucher_id required"})
            try:
                res = db.issue_cheque(bledger, vid, inst_no)
                return self.send_json(200, res)
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/tds/sections":
            scode = data.get("section_code", "").strip()
            desc = data.get("description", "").strip()
            thresh = float(data.get("threshold_amount", 0.0))
            r_ind = float(data.get("rate_individual", 1.0))
            r_oth = float(data.get("rate_other", 2.0))
            if not scode:
                return self.send_json(400, {"error": "section_code required"})
            try:
                db.add_tds_section(scode, desc, thresh, r_ind, r_oth)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/auth/login":
            uname = data.get("username", "").strip()
            pwd = data.get("password", "").strip()
            user = db.authenticate_user(uname, pwd)
            if user:
                token = db.create_session(user)
                return self.send_json(200, {"status": "success", "token": token, "user": user})
            return self.send_json(401, {"error": "Invalid username or password"})

        elif path == "/api/auth/logout":
            token = data.get("token", "")
            if token:
                db.revoke_session(token)
            return self.send_json(200, {"status": "success", "logged_out": True})

        elif path == "/api/tdl/variable":
            expr = data.get("expression", "")
            ctx = data.get("context")
            return self.send_json(200, lab.inspect_variable(expr, ctx))

        elif path == "/api/auth/register":
            uname = data.get("username", "").strip()
            pwd = data.get("password", "").strip()
            role = data.get("role", "Data Entry").strip()
            fname = data.get("full_name", "").strip()
            if not uname or not pwd:
                return self.send_json(400, {"error": "username and password required"})
            try:
                db.create_user(uname, pwd, role, fname)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # ERPNext Extension Module POST Endpoints
        elif path == "/api/assets":
            try:
                aid = db.add_fixed_asset(
                    asset_name=data.get("asset_name", ""),
                    asset_code=data.get("asset_code", ""),
                    category_name=data.get("category_name", "Computers & IT Equipment"),
                    purchase_date=data.get("purchase_date", "2026-04-01"),
                    in_service_date=data.get("in_service_date", data.get("purchase_date", "2026-04-01")),
                    purchase_cost=float(data.get("purchase_cost", 0.0)),
                    scrap_value=float(data.get("scrap_value", 0.0)),
                    useful_life_months=int(data.get("useful_life_months", 60)),
                    depreciation_method=data.get("depreciation_method", "SLM"),
                    depreciation_rate_pct=float(data.get("depreciation_rate_pct", 20.0)),
                    asset_ledger=data.get("asset_ledger", "Computers & IT Equipment"),
                    depr_expense_ledger=data.get("depr_expense_ledger", "Depreciation Expense"),
                    accum_depr_ledger=data.get("accum_depr_ledger", "Accumulated Depreciation"),
                    notes=data.get("notes", "")
                )
                return self.send_json(201, {"status": "success", "asset_id": aid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/assets/depreciate":
            entry_id = int(data.get("entry_id", 0))
            user_name = user.get("username", "admin")
            try:
                vid = db.post_depreciation_entry(entry_id, user_name=user_name)
                return self.send_json(200, {"status": "success", "voucher_id": vid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/assets/dispose":
            aid = int(data.get("asset_id", 0))
            disposal_date = data.get("disposal_date", "2026-10-31")
            sale_amount = float(data.get("sale_amount", 0.0))
            disposal_ledger = data.get("disposal_ledger", "Bank Accounts")
            user_name = user.get("username", "admin")
            try:
                res = db.dispose_fixed_asset(aid, disposal_date, sale_amount, disposal_ledger=disposal_ledger, user_name=user_name)
                return self.send_json(200, {"status": "success", "disposal": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/landed_cost":
            pv_id = int(data.get("purchase_voucher_id", 0))
            exp_ledger = data.get("expense_ledger", "Freight & Cartage Inward")
            amount = float(data.get("amount", 0.0))
            alloc_method = data.get("allocation_method", "Value")
            narration = data.get("narration", "")
            user_name = user.get("username", "admin")
            try:
                lcv_id = db.apply_landed_cost(pv_id, exp_ledger, amount, allocation_method=alloc_method, narration=narration, user_name=user_name)
                return self.send_json(201, {"status": "success", "landed_cost_voucher_id": lcv_id})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/orders":
            order_type = data.get("order_type", "Sales Order")
            party_name = data.get("party_name", "")
            date_str = data.get("date", "2026-04-01")
            items = data.get("items", [])
            due_date = data.get("due_date", "")
            narration = data.get("narration", "")
            try:
                res = db.create_order(order_type, party_name, date_str, items, due_date=due_date, narration=narration)
                return self.send_json(201, {"status": "success", "order": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/orders/fulfill":
            order_id = int(data.get("order_id", 0))
            fulfillment_voucher_id = int(data.get("fulfillment_voucher_id", 0))
            fulfilled_items = data.get("fulfilled_items")
            try:
                res = db.fulfill_order(order_id, fulfillment_voucher_id, fulfilled_items=fulfilled_items)
                return self.send_json(200, {"status": "success", "order": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/credit_limit/update":
            party = data.get("party_name", "")
            limit = float(data.get("credit_limit", 0.0))
            days = int(data.get("credit_days", 30))
            enforce = int(data.get("enforce_credit_limit", 0))
            try:
                db.set_ledger_credit_terms(party, credit_limit=limit, credit_days=days, enforce_credit_limit=enforce)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/gst/gstr2b_reconcile":
            period = data.get("return_period", "042026")
            invoices = data.get("gstr2b_invoices", [])
            tol = float(data.get("tolerance", 1.0))
            try:
                res = db.reconcile_gstr2b(period, invoices, tolerance=tol)
                return self.send_json(200, {"status": "success", "reconciliation": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/pos/invoice":
            dt = data.get("date", "2026-04-01")
            party = data.get("party_name", "Cash")
            items = data.get("items", [])
            cash = float(data.get("cash_tendered", 0.0))
            card = float(data.get("card_tendered", 0.0))
            upi = float(data.get("upi_tendered", 0.0))
            cheque = float(data.get("cheque_tendered", 0.0))
            phone = data.get("customer_phone", "")
            narration = data.get("narration", "POS Billing")
            try:
                res = db.add_pos_voucher(dt, party, items, cash_tendered=cash, card_tendered=card, upi_tendered=upi, cheque_tendered=cheque, customer_phone=phone, narration=narration)
                return self.send_json(201, {"status": "success", "invoice": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/tcs/sections":
            scode = data.get("section_code", "")
            desc = data.get("description", "")
            thresh = float(data.get("threshold_amount", 5000000.0))
            rate = float(data.get("rate", 0.1))
            rate_pan_missing = float(data.get("rate_pan_missing", 1.0))
            try:
                db.add_tcs_section(scode, desc, threshold_amount=thresh, rate=rate, rate_pan_missing=rate_pan_missing)
                return self.send_json(201, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/forex/revalue":
            dt = data.get("date", "")
            narration = data.get("narration", "Forex Exchange Rate Revaluation")
            user_name = user.get("username", "admin")
            try:
                res = db.post_forex_revaluation_voucher(date=dt, narration=narration, user_name=user_name)
                return self.send_json(200, {"status": "success", "result": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/challans/delivery_note":
            dt = data.get("date", "2026-04-01")
            customer = data.get("customer_name", "")
            items = data.get("items", [])
            order_no = data.get("order_no", "")
            narration = data.get("narration", "")
            try:
                res = db.record_delivery_note(dt, customer, items, order_no=order_no, narration=narration)
                return self.send_json(201, {"status": "success", "challan": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/challans/receipt_note":
            dt = data.get("date", "2026-04-01")
            supplier = data.get("supplier_name", "")
            items = data.get("items", [])
            order_no = data.get("order_no", "")
            narration = data.get("narration", "")
            try:
                res = db.record_receipt_note(dt, supplier, items, order_no=order_no, narration=narration)
                return self.send_json(201, {"status": "success", "challan": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Subcontracting & Job Work Orders
        elif path == "/api/job_work":
            otype = data.get("order_type", "Job Work Out")
            ono = data.get("order_no")
            worker = data.get("job_worker_name", "Cash")
            odt = data.get("order_date", "2026-10-31")
            due = data.get("due_date", "2026-11-15")
            fitem = data.get("finished_item_name", "Assembled Desktop Combo")
            fqty = float(data.get("finished_qty", 1.0))
            srate = float(data.get("service_rate", 0.0))
            items = data.get("items", [])
            narr = data.get("narration", "")
            try:
                res = db.create_job_work_order(otype, ono, worker, odt, due, fitem, fqty, srate, items, narr)
                return self.send_json(201, {"status": "success", "order": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/job_work/dispatch":
            oid = int(data.get("order_id", 0))
            items = data.get("items", [])
            dt = data.get("date", "2026-10-31")
            try:
                res = db.dispatch_job_work_materials(oid, items, date=dt)
                return self.send_json(200, {"status": "success", "dispatch": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/job_work/receive":
            oid = int(data.get("order_id", 0))
            fqty = float(data.get("finished_qty", 1.0))
            consumed = data.get("consumed_items")
            dt = data.get("date", "2026-10-31")
            bill = bool(data.get("post_service_bill", True))
            try:
                res = db.receive_job_work_finished_goods(oid, fqty, consumed_items=consumed, date=dt, post_service_bill=bill)
                return self.send_json(200, {"status": "success", "receipt": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Quality Control & Inspections
        elif path == "/api/quality_inspections":
            rtype = data.get("reference_type", "Purchase")
            rno = data.get("reference_no", "")
            item = data.get("stock_item_name", "")
            batch = data.get("batch_no", "")
            dt = data.get("inspection_date", "2026-10-31")
            insp = data.get("inspector_name", "QC Manager")
            iqty = float(data.get("inspected_qty", 1.0))
            ssize = float(data.get("sample_size", 1.0))
            params = data.get("parameters", [])
            remarks = data.get("remarks", "")
            try:
                res = db.create_quality_inspection(rtype, rno, item, batch, dt, insp, iqty, ssize, params, remarks)
                return self.send_json(201, {"status": "success", "inspection": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Multi-Currency Historical Exchange Rates
        elif path == "/api/currencies/exchange_rates":
            sym = data.get("currency_symbol", "$")
            fname = data.get("formal_name", "USD")
            edt = data.get("effective_date", "2026-10-31")
            srate = float(data.get("standard_rate", 84.5))
            brate = float(data.get("buying_rate")) if data.get("buying_rate") is not None else None
            sllrate = float(data.get("selling_rate")) if data.get("selling_rate") is not None else None
            try:
                res = db.set_currency_exchange_rate(sym, fname, edt, srate, brate, sllrate)
                return self.send_json(201, {"status": "success", "rate": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Payment & Bill Reconciliation
        elif path == "/api/payment_reconciliation/allocate":
            pid = int(data.get("payment_voucher_id", 0))
            party = data.get("party_ledger", "")
            bref = data.get("bill_ref_no", "")
            amt = float(data.get("allocated_amount", 0.0))
            woff = float(data.get("write_off_amount", 0.0))
            dt = data.get("date", "2026-10-31")
            try:
                res = db.reconcile_payment_against_bill(pid, party, bref, amt, write_off_amount=woff, date=dt)
                return self.send_json(200, {"status": "success", "reconciliation": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Company Multiple Addresses
        elif path == "/api/company/addresses":
            bname = data.get("branch_name", "Branch Office")
            addr = data.get("address", "")
            city = data.get("city", "")
            st = data.get("state", "Karnataka")
            sc = data.get("state_code", "29")
            pin = data.get("pincode", "560001")
            gst = data.get("gstin", "")
            isp = int(data.get("is_primary", 0))
            try:
                aid = db.add_company_address(bname, addr, city=city, state=st, state_code=sc, pincode=pin, gstin=gst, is_primary=isp)
                return self.send_json(201, {"status": "success", "address_id": aid})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/company/addresses/delete":
            aid = int(data.get("address_id", 0))
            try:
                db.delete_company_address(aid)
                return self.send_json(200, {"status": "success"})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Rejections In & Out
        elif path == "/api/rejections/in":
            dt = data.get("date", "2026-10-31")
            cust = data.get("customer_name", "Cash")
            items = data.get("items", [])
            ord_no = data.get("order_no", "")
            narr = data.get("narration", "")
            try:
                res = db.record_rejections_in(dt, cust, items, order_no=ord_no, narration=narr)
                return self.send_json(201, {"status": "success", "rejection": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        elif path == "/api/rejections/out":
            dt = data.get("date", "2026-10-31")
            supp = data.get("supplier_name", "Cash")
            items = data.get("items", [])
            ord_no = data.get("order_no", "")
            narr = data.get("narration", "")
            try:
                res = db.record_rejections_out(dt, supp, items, order_no=ord_no, narration=narr)
                return self.send_json(201, {"status": "success", "rejection": res})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Negative Stock Policy
        elif path == "/api/inventory/negative_stock_policy":
            allow = bool(data.get("allow_negative_stock", True))
            try:
                db.set_negative_stock_policy(allow)
                return self.send_json(200, {"status": "success", "allow_negative_stock": allow})
            except Exception as e:
                return self.send_json(400, {"error": str(e)})

        # Tally XML Data Interchange Import
        elif path == "/api/tally/xml/import":
            xml_content = data.get("xml_data") or data.get("content") or ""
            if not xml_content:
                return self.send_json(400, {"error": "Missing 'xml_data' parameter"})
            if self.client_address[0] not in ("127.0.0.1", "::1") or user.get("role") != "Admin":
                return self.send_json(403, {"error": "Only a local Admin can import company data"})
            from engine import tally_exchange
            try:
                name = data.get("company_name") or None
                if not name:                                  # no name in the request or the file: use the active company's
                    try:
                        name = db.get_company().get("name") or None
                    except Exception:
                        name = None
                res = tally_exchange.import_xml_text(company_store, xml_content, name)
            except Exception as exc:
                return self.send_json(400, {"success": False, "error": str(exc)})
            return self.send_json(200 if res.get("success") else 400, res)

        else:
            self.send_error(404, "Endpoint not found")

def run(host: str = "127.0.0.1", port: int = 8080):
    server_address = (host, port)
    httpd = DefaultHTTPServer(server_address, TallyRequestHandler)
    print(f"============================================================")
    print(f"  TALLYPRIME ENTERPRISE OFFLINE RUNTIME ENGINE RUNNING")
    print(f"  100% Fully Offline - Zero Internet Required")
    print(f"  URL: http://{host}:{port}")
    print(f"============================================================")
    from engine.backup_scheduler import start
    start(company_store, _company_lock)
    httpd.serve_forever()

if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8080
    run(port=p)
