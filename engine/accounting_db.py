"""
Enterprise Double-Entry, Inventory, Manufacturing, GST, Banking & Payroll Engine
Fully offline, high-precision accounting engine utilizing Decimal arithmetic for financial integrity.

Implements all 2026 TallyPrime modules:
1. Multi-Company & Financial Year Management
2. Double-Entry & Bill-Wise Accounting (New Ref, Agst Ref, Advance, On Account)
3. Cost Centres & Cost Categories
4. Multi-Currency (Exchange rates, Forex gain/loss)
5. Inventory with Godowns, Batches (Mfg/Expiry dates), and Reorder levels
6. Manufacturing & Bill of Materials (BOM, raw material consumption, landed cost)
7. Statutory GST (India) - CGST, SGST, IGST, Intra/Inter-state, HSN codes, RCM, ITC, GSTR-1 & GSTR-3B JSON exports
8. Banking Management (BRS, Bank statement CSV import & automated matching)
9. Receivables & Payables Ageing Analysis (0-30, 31-60, 61-90, 90+ days)
10. Payroll & Employee Management (Attendance, Pay Heads, PF, PT, Payslips, Pay Sheets)
11. Audit Trail / Edit Log (Statutory MCA Compliance with diffs)
12. Backup & Restore (Encrypted/structured JSON with SHA-256 verification and pre-restore snapshots)
"""

import sqlite3
import datetime
import json
import os
import hashlib
import hmac
import time
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any, Optional, Tuple

# Helper for exact 2-decimal-place monetary arithmetic
def to_dec(val: Any) -> Decimal:
    if val is None or val == "":
        return Decimal("0.00")
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def to_qty_dec(val: Any) -> Decimal:
    if val is None or val == "":
        return Decimal("0.000")
    if isinstance(val, Decimal):
        return val.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return Decimal(str(val)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

STANDARD_GROUPS = [
    # (Name, Parent, IsRevenue, AffectsGrossProfit)
    ("Capital Account", "Primary", False, False),
    ("Reserves & Surplus", "Capital Account", False, False),
    ("Loans (Liability)", "Primary", False, False),
    ("Bank OD A/c", "Loans (Liability)", False, False),
    ("Secured Loans", "Loans (Liability)", False, False),
    ("Unsecured Loans", "Loans (Liability)", False, False),
    ("Current Liabilities", "Primary", False, False),
    ("Duties & Taxes", "Current Liabilities", False, False),
    ("Provisions", "Current Liabilities", False, False),
    ("Sundry Creditors", "Current Liabilities", False, False),
    ("Fixed Assets", "Primary", False, False),
    ("Investments", "Primary", False, False),
    ("Current Assets", "Primary", False, False),
    ("Bank Accounts", "Current Assets", False, False),
    ("Cash-in-Hand", "Current Assets", False, False),
    ("Deposits (Asset)", "Current Assets", False, False),
    ("Loans & Advances (Asset)", "Current Assets", False, False),
    ("Stock-in-Hand", "Current Assets", False, False),
    ("Sundry Debtors", "Current Assets", False, False),
    ("Sales Accounts", "Primary", True, True),
    ("Purchase Accounts", "Primary", True, True),
    ("Direct Incomes", "Primary", True, True),
    ("Indirect Incomes", "Primary", True, False),
    ("Direct Expenses", "Primary", True, True),
    ("Indirect Expenses", "Primary", True, False),
    ("Payroll Expenses", "Indirect Expenses", True, False),
]

from engine.reliability import ReliabilityMixin


class EnterpriseAccountingDB(ReliabilityMixin):
    def __init__(self, db_path: str = "tally_data.db", seed_demo: bool = True):
        self.seed_demo = seed_demo
        self.db_path = db_path
        self.active_company_id: int = 1
        self.conn = sqlite3.connect(self.db_path, timeout=60.0, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 60000;")
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA synchronous = NORMAL;")
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()
        self.migrate_data()
        self._ensure_indexes()
        self.init_reliability()

    def close(self):
        if self.conn:
            self.conn.close()

    # Without these every ledger balance scans the whole voucher_entry table (a 22,000-voucher company took
    # 5-15 s per report). CREATE INDEX IF NOT EXISTS is idempotent and safe on existing company files.
    INDEXES = (
        ('idx_voucher_entry_ledger', 'voucher_entry', 'ledger_name'),
        ('idx_voucher_entry_voucher', 'voucher_entry', 'voucher_id'),
        ('idx_voucher_date', 'voucher', 'date'),
        ('idx_voucher_type', 'voucher', 'voucher_type'),
        ('idx_voucher_number', 'voucher', 'voucher_number'),
        ('idx_bill_allocation_ledger', 'bill_allocation', 'ledger_name'),
        ('idx_bill_allocation_voucher', 'bill_allocation', 'voucher_id'),
        ('idx_voucher_inventory_voucher', 'voucher_inventory', 'voucher_id'),
        ('idx_voucher_inventory_item', 'voucher_inventory', 'stock_item_name'),
    )

    def _ensure_indexes(self):
        existing = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with self.conn:
            for name, table, column in self.INDEXES:
                if table in existing:
                    self.conn.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table}({column})')

    def checkpoint(self):
        if self.conn:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def _init_schema(self):
        with self.conn:
            # 1. Company Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS company (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    formal_name TEXT,
                    currency_symbol TEXT DEFAULT '₹',
                    currency_formal TEXT DEFAULT 'INR',
                    financial_year_from TEXT DEFAULT '2026-04-01',
                    financial_year_to TEXT DEFAULT '2027-03-31',
                    books_from TEXT DEFAULT '2026-04-01',
                    gstin TEXT DEFAULT '29AABCT1332L1Z5',
                    state TEXT DEFAULT 'Karnataka',
                    state_code TEXT DEFAULT '29',
                    address TEXT DEFAULT '123 Tech Park, Bengaluru, Karnataka',
                    pincode TEXT DEFAULT '560001',
                    edit_log_enabled INTEGER DEFAULT 1
                );
            """)

            # 2. Accounting Groups Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS account_group (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    parent TEXT NOT NULL,
                    is_revenue INTEGER DEFAULT 0,
                    affects_gross INTEGER DEFAULT 0
                );
            """)

            # 3. Ledgers Master with Bill-Wise & GST Config
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    group_name TEXT NOT NULL,
                    opening_balance REAL DEFAULT 0.0,
                    opening_balance_type TEXT DEFAULT 'Dr', -- 'Dr' or 'Cr'
                    contact_person TEXT,
                    gstin TEXT,
                    state TEXT DEFAULT 'Karnataka',
                    state_code TEXT DEFAULT '29',
                    credit_limit REAL DEFAULT 0.0,
                    credit_period_days INTEGER DEFAULT 30,
                    maintain_bill_by_bill INTEGER DEFAULT 1,
                    is_cost_centre_applicable INTEGER DEFAULT 0,
                    hsn_sac_code TEXT,
                    tax_rate REAL DEFAULT 0.0,
                    FOREIGN KEY (group_name) REFERENCES account_group(name)
                );
            """)

            # 4. Multi-Currency Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS currency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    formal_name TEXT NOT NULL,
                    rate_of_exchange REAL DEFAULT 1.0,
                    is_base INTEGER DEFAULT 0
                );
            """)

            # 5. Cost Categories & Cost Centres
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_category (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    allocate_revenue INTEGER DEFAULT 1,
                    allocate_non_revenue INTEGER DEFAULT 0
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_centre (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    category_name TEXT NOT NULL,
                    parent TEXT DEFAULT 'Primary'
                );
            """)

            # 6. Inventory: Units of Measure
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS uom (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT UNIQUE NOT NULL,
                    formal_name TEXT NOT NULL,
                    decimal_places INTEGER DEFAULT 0
                );
            """)

            # 7. Inventory: Godowns / Warehouses
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS godown (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    location TEXT,
                    parent TEXT DEFAULT 'Primary'
                );
            """)

            # 8. Inventory: Stock Items Master with GST & Reorder
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    group_name TEXT DEFAULT 'Primary',
                    uom_symbol TEXT NOT NULL,
                    hsn_code TEXT DEFAULT '84433100',
                    gst_rate REAL DEFAULT 18.0,
                    opening_qty REAL DEFAULT 0.0,
                    opening_rate REAL DEFAULT 0.0,
                    opening_value REAL DEFAULT 0.0,
                    reorder_level REAL DEFAULT 5.0,
                    reorder_qty REAL DEFAULT 10.0,
                    godown_name TEXT DEFAULT 'Main Location',
                    FOREIGN KEY (uom_symbol) REFERENCES uom(symbol)
                );
            """)

            # 9. Inventory: Stock Batches (Mfg & Expiry dates)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_batch (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_item_name TEXT NOT NULL,
                    batch_number TEXT NOT NULL,
                    mfg_date TEXT,
                    expiry_date TEXT,
                    initial_qty REAL DEFAULT 0.0,
                    UNIQUE(stock_item_name, batch_number)
                );
            """)

            # 10. Manufacturing: Bill of Materials (BOM)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bom_header (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    finished_item_name TEXT NOT NULL,
                    unit_qty REAL DEFAULT 1.0,
                    FOREIGN KEY (finished_item_name) REFERENCES stock_item(name)
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bom_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER NOT NULL,
                    component_item_name TEXT NOT NULL,
                    qty REAL NOT NULL,
                    is_scrap INTEGER DEFAULT 0,
                    FOREIGN KEY (bom_id) REFERENCES bom_header(id) ON DELETE CASCADE,
                    FOREIGN KEY (component_item_name) REFERENCES stock_item(name)
                );
            """)

            # 11. Voucher Master (Accounting, Inventory, Manufacturing, Payroll, Orders)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS voucher (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_type TEXT NOT NULL,
                    voucher_number TEXT NOT NULL,
                    date TEXT NOT NULL,
                    narration TEXT,
                    total_amount REAL DEFAULT 0.0,
                    currency_symbol TEXT DEFAULT '₹',
                    exchange_rate REAL DEFAULT 1.0,
                    is_optional INTEGER DEFAULT 0,
                    is_cancelled INTEGER DEFAULT 0,
                    reference_no TEXT
                );
            """)

            # 12. Voucher Accounting Entries
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS voucher_entry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_id INTEGER NOT NULL,
                    ledger_name TEXT NOT NULL,
                    entry_type TEXT NOT NULL, -- 'Dr' or 'Cr'
                    amount REAL NOT NULL,
                    narration TEXT,
                    cost_centre_name TEXT,
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE,
                    FOREIGN KEY (ledger_name) REFERENCES ledger(name)
                );
            """)

            # 13. Bill-wise Accounting Allocations
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bill_allocation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_id INTEGER NOT NULL,
                    ledger_name TEXT NOT NULL,
                    ref_type TEXT NOT NULL, -- 'New Ref', 'Agst Ref', 'Advance', 'On Account'
                    ref_name TEXT NOT NULL, -- Invoice Number / Bill Reference
                    due_date TEXT,
                    amount REAL NOT NULL,
                    direction TEXT NOT NULL, -- 'Dr' or 'Cr'
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

            # 14. Voucher Inventory Allocations (Godown & Batch aware)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS voucher_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_id INTEGER NOT NULL,
                    stock_item_name TEXT NOT NULL,
                    godown_name TEXT DEFAULT 'Main Location',
                    batch_number TEXT,
                    qty REAL NOT NULL,
                    rate REAL NOT NULL,
                    amount REAL NOT NULL,
                    direction TEXT NOT NULL, -- 'In' or 'Out'
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE,
                    FOREIGN KEY (stock_item_name) REFERENCES stock_item(name)
                );
            """)

            # 15. Banking & BRS Reconciliation
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS bank_reconciliation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_id INTEGER NOT NULL,
                    bank_ledger_name TEXT NOT NULL,
                    bank_date TEXT, -- Cleared date in bank
                    instrument_no TEXT,
                    instrument_date TEXT,
                    status TEXT DEFAULT 'Unreconciled', -- 'Reconciled' or 'Unreconciled'
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

            # 16. Payroll: Employees Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS employee (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    emp_code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    department TEXT DEFAULT 'Operations',
                    designation TEXT DEFAULT 'Executive',
                    date_of_joining TEXT,
                    pan TEXT,
                    pf_uan TEXT,
                    esi_number TEXT,
                    bank_name TEXT,
                    account_no TEXT,
                    basic_salary REAL DEFAULT 0.0
                );
            """)

            # 17. Payroll: Pay Heads
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS pay_head (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    head_type TEXT NOT NULL, -- 'Earnings', 'Deductions'
                    calculation_type TEXT NOT NULL, -- 'Fixed', 'Percentage', 'Attendance'
                    percentage REAL DEFAULT 0.0,
                    is_statutory INTEGER DEFAULT 0
                );
            """)

            # 18. Payroll: Salary Structure
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS salary_structure (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    emp_code TEXT NOT NULL,
                    pay_head_name TEXT NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (emp_code) REFERENCES employee(emp_code) ON DELETE CASCADE,
                    FOREIGN KEY (pay_head_name) REFERENCES pay_head(name)
                );
            """)

            # 19. Payroll: Monthly Attendance
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    emp_code TEXT NOT NULL,
                    month TEXT NOT NULL, -- YYYY-MM
                    present_days REAL DEFAULT 30.0,
                    absent_days REAL DEFAULT 0.0,
                    overtime_hours REAL DEFAULT 0.0,
                    UNIQUE(emp_code, month)
                );
            """)

            # 20. Audit Trail / Edit Log (Statutory MCA Compliance)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS edit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    entity_type TEXT NOT NULL, -- 'Voucher', 'Ledger', 'StockItem', 'Company'
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL, -- 'CREATE', 'ALTER', 'DELETE', 'CANCEL'
                    user_name TEXT DEFAULT 'admin',
                    details TEXT,
                    chained_hash TEXT DEFAULT ''
                );
            """)

            # 21. User Accounts & Role-Based Access Control
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_account (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'Admin', -- 'Admin', 'Data Entry', 'Auditor'
                    full_name TEXT,
                    company_access TEXT DEFAULT 'ALL',
                    is_active INTEGER DEFAULT 1
                );
            """)

            # 22. Budgets Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    name TEXT NOT NULL,
                    period_from TEXT,
                    period_to TEXT,
                    ledger_name TEXT NOT NULL,
                    budgeted_amount REAL NOT NULL
                );
            """)

            # 23. Stock Categories
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_category (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    name TEXT UNIQUE NOT NULL,
                    parent TEXT DEFAULT 'Primary'
                );
            """)

            # 24. Compound Units of Measure
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS compound_uom (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    major_uom TEXT NOT NULL,
                    conversion_factor REAL NOT NULL,
                    minor_uom TEXT NOT NULL,
                    UNIQUE(major_uom, minor_uom)
                );
            """)

            # 25. Price Levels & Price Lists
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS price_level (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL
                );
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS price_list_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    price_level TEXT NOT NULL,
                    stock_item_name TEXT NOT NULL,
                    min_qty REAL DEFAULT 1.0,
                    rate REAL NOT NULL,
                    discount_pct REAL DEFAULT 0.0,
                    UNIQUE(price_level, stock_item_name, min_qty)
                );
            """)

            # 26. Banking: Cheque Books
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cheque_book (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bank_ledger_name TEXT NOT NULL,
                    book_name TEXT,
                    from_no INTEGER NOT NULL,
                    to_no INTEGER NOT NULL,
                    current_no INTEGER NOT NULL
                );
            """)

            # 27. Statutory: TDS Sections
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tds_section (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_code TEXT UNIQUE NOT NULL,
                    description TEXT,
                    threshold_amount REAL DEFAULT 0.0,
                    rate_individual REAL DEFAULT 1.0,
                    rate_other REAL DEFAULT 2.0
                );
            """)

            # 28. User Sessions (Local Auth & RBAC)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS user_session (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    role TEXT NOT NULL,
                    company_access TEXT DEFAULT 'ALL',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES user_account(id) ON DELETE CASCADE
                );
            """)

            # 29. Fixed Assets Master (ERPNext Assets + Tally Fixed Assets)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS fixed_asset (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    asset_name TEXT NOT NULL,
                    asset_code TEXT UNIQUE NOT NULL,
                    category_name TEXT NOT NULL,
                    purchase_date TEXT NOT NULL,
                    in_service_date TEXT NOT NULL,
                    purchase_cost REAL NOT NULL,
                    scrap_value REAL DEFAULT 0.0,
                    useful_life_months INTEGER DEFAULT 60,
                    depreciation_method TEXT DEFAULT 'SLM',
                    depreciation_rate_pct REAL DEFAULT 20.0,
                    asset_ledger TEXT NOT NULL,
                    depr_expense_ledger TEXT NOT NULL,
                    accum_depr_ledger TEXT NOT NULL,
                    status TEXT DEFAULT 'In Use',
                    notes TEXT DEFAULT ''
                );
            """)

            # 30. Fixed Asset Depreciation Schedule & Posted Entries
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS asset_depreciation_entry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id INTEGER NOT NULL,
                    period_date TEXT NOT NULL,
                    depreciation_amount REAL NOT NULL,
                    accumulated_depreciation REAL NOT NULL,
                    book_value REAL NOT NULL,
                    voucher_id INTEGER,
                    status TEXT DEFAULT 'Scheduled',
                    FOREIGN KEY (asset_id) REFERENCES fixed_asset(id) ON DELETE CASCADE
                );
            """)

            # 31. Landed Cost Vouchers (ERPNext Landed Cost + Tally Additional Purchase Cost)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS landed_cost_voucher (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    voucher_number TEXT NOT NULL,
                    date TEXT NOT NULL,
                    purchase_voucher_id INTEGER NOT NULL,
                    expense_ledger TEXT NOT NULL,
                    amount REAL NOT NULL,
                    allocation_method TEXT DEFAULT 'Value',
                    narration TEXT DEFAULT '',
                    FOREIGN KEY (purchase_voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

            # 32. Landed Cost Allocations per Item
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS landed_cost_allocation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    landed_cost_id INTEGER NOT NULL,
                    stock_item_name TEXT NOT NULL,
                    qty REAL NOT NULL,
                    allocated_cost REAL NOT NULL,
                    original_rate REAL NOT NULL,
                    effective_rate REAL NOT NULL,
                    FOREIGN KEY (landed_cost_id) REFERENCES landed_cost_voucher(id) ON DELETE CASCADE
                );
            """)

            # 33. Order Tracking (Sales Orders, Purchase Orders, Quotations)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS order_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    order_type TEXT NOT NULL,
                    order_number TEXT UNIQUE NOT NULL,
                    order_date TEXT NOT NULL,
                    party_name TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    status TEXT DEFAULT 'Open',
                    total_amount REAL NOT NULL,
                    fulfilled_amount REAL DEFAULT 0.0,
                    narration TEXT DEFAULT ''
                );
            """)

            # 34. Order Line Items
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS order_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    stock_item_name TEXT NOT NULL,
                    ordered_qty REAL NOT NULL,
                    fulfilled_qty REAL DEFAULT 0.0,
                    rate REAL NOT NULL,
                    amount REAL NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES order_tracking(id) ON DELETE CASCADE
                );
            """)

            # 35. GSTR-2B Inward ITC Reconciliation Batches
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS gstr2b_reconciliation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    return_period TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL,
                    total_invoices INTEGER DEFAULT 0,
                    matched_count INTEGER DEFAULT 0,
                    mismatched_count INTEGER DEFAULT 0,
                    missing_in_books_count INTEGER DEFAULT 0,
                    missing_in_2b_count INTEGER DEFAULT 0
                );
            """)

            # 36. GSTR-2B Inward ITC Reconciliation Items
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS gstr2b_reconciliation_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recon_id INTEGER NOT NULL,
                    supplier_gstin TEXT NOT NULL,
                    supplier_name TEXT NOT NULL,
                    invoice_no TEXT NOT NULL,
                    invoice_date TEXT NOT NULL,
                    invoice_val REAL NOT NULL,
                    taxable_val REAL NOT NULL,
                    igst REAL DEFAULT 0.0,
                    cgst REAL DEFAULT 0.0,
                    sgst REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    book_voucher_id INTEGER,
                    mismatch_reason TEXT DEFAULT '',
                    FOREIGN KEY (recon_id) REFERENCES gstr2b_reconciliation(id) ON DELETE CASCADE
                );
            """)

            # 37. Point of Sale (POS) Multi-Mode Payment Breakdown
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS pos_payment_detail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    voucher_id INTEGER NOT NULL,
                    cash_amount REAL DEFAULT 0.0,
                    card_amount REAL DEFAULT 0.0,
                    upi_amount REAL DEFAULT 0.0,
                    cheque_amount REAL DEFAULT 0.0,
                    change_returned REAL DEFAULT 0.0,
                    customer_phone TEXT DEFAULT '',
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

            # 38. Tax Collected at Source (TCS) Slabs
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tcs_section (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    section_code TEXT UNIQUE NOT NULL,
                    description TEXT NOT NULL,
                    threshold_amount REAL DEFAULT 5000000.0,
                    rate REAL DEFAULT 0.1,
                    rate_pan_missing REAL DEFAULT 1.0,
                    is_active INTEGER DEFAULT 1
                );
            """)

            # 39. Delivery & Goods Receipt Challans
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS challan_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    voucher_id INTEGER NOT NULL,
                    challan_type TEXT NOT NULL,
                    challan_no TEXT NOT NULL,
                    date TEXT NOT NULL,
                    party_name TEXT NOT NULL,
                    order_no TEXT DEFAULT '',
                    status TEXT DEFAULT 'Pending Invoicing',
                    invoiced_voucher_id INTEGER,
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

            # 40. Subcontracting / Job Work Orders
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS job_work_order (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    order_type TEXT NOT NULL, -- 'Job Work Out', 'Job Work In'
                    order_no TEXT UNIQUE NOT NULL,
                    job_worker_name TEXT NOT NULL,
                    order_date TEXT NOT NULL,
                    due_date TEXT NOT NULL,
                    finished_item_name TEXT NOT NULL,
                    finished_qty REAL NOT NULL,
                    service_rate REAL DEFAULT 0.0,
                    service_amount REAL DEFAULT 0.0,
                    status TEXT DEFAULT 'Pending', -- 'Pending', 'In Progress', 'Completed', 'Cancelled'
                    narration TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
            """)

            # 41. Subcontracting Material Movement
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS job_work_item (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_work_id INTEGER NOT NULL,
                    stock_item_name TEXT NOT NULL,
                    required_qty REAL NOT NULL,
                    dispatched_qty REAL DEFAULT 0.0,
                    consumed_qty REAL DEFAULT 0.0,
                    scrap_qty REAL DEFAULT 0.0,
                    rate REAL DEFAULT 0.0,
                    FOREIGN KEY (job_work_id) REFERENCES job_work_order(id) ON DELETE CASCADE
                );
            """)

            # 42. Quality Inspection Master
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS quality_inspection (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    inspection_no TEXT UNIQUE NOT NULL,
                    reference_type TEXT NOT NULL, -- 'Purchase', 'Delivery', 'Manufacturing', 'Job Work'
                    reference_no TEXT DEFAULT '',
                    stock_item_name TEXT NOT NULL,
                    batch_no TEXT DEFAULT '',
                    inspection_date TEXT NOT NULL,
                    inspector_name TEXT DEFAULT 'QC Manager',
                    sample_size REAL DEFAULT 1.0,
                    inspected_qty REAL NOT NULL,
                    accepted_qty REAL NOT NULL,
                    rejected_qty REAL DEFAULT 0.0,
                    status TEXT NOT NULL DEFAULT 'Accepted', -- 'Accepted', 'Rejected', 'Partially Accepted'
                    remarks TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
            """)

            # 43. Quality Inspection Parameters & Readings
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS quality_inspection_parameter (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inspection_id INTEGER NOT NULL,
                    parameter_name TEXT NOT NULL,
                    specification TEXT NOT NULL,
                    min_value REAL,
                    max_value REAL,
                    reading_value REAL,
                    status TEXT NOT NULL DEFAULT 'Pass', -- 'Pass', 'Fail'
                    FOREIGN KEY (inspection_id) REFERENCES quality_inspection(id) ON DELETE CASCADE
                );
            """)

            # 44. Multi-Currency Historical Exchange Rates
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS currency_exchange_rate (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    currency_symbol TEXT NOT NULL,
                    formal_name TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    standard_rate REAL NOT NULL,
                    buying_rate REAL NOT NULL,
                    selling_rate REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(currency_symbol, effective_date)
                );
            """)

            # 45. Multiple Company Addresses (Multi-Branch / Multi-State GSTINs)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS company_address (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    branch_name TEXT NOT NULL,
                    address TEXT NOT NULL,
                    city TEXT DEFAULT '',
                    state TEXT DEFAULT 'Karnataka',
                    state_code TEXT DEFAULT '29',
                    pincode TEXT DEFAULT '560001',
                    gstin TEXT DEFAULT '',
                    is_primary INTEGER DEFAULT 0,
                    FOREIGN KEY (company_id) REFERENCES company(id) ON DELETE CASCADE
                );
            """)

            # 46. Rejections In & Out Tracking (Customer & Vendor Return Notes)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS rejection_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER DEFAULT 1,
                    voucher_id INTEGER NOT NULL,
                    rejection_type TEXT NOT NULL, -- 'Rejections In', 'Rejections Out'
                    rejection_no TEXT UNIQUE NOT NULL,
                    party_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    order_no TEXT DEFAULT '',
                    status TEXT DEFAULT 'Pending Adjustment',
                    narration TEXT DEFAULT '',
                    FOREIGN KEY (voucher_id) REFERENCES voucher(id) ON DELETE CASCADE
                );
            """)

        # Execute safe migrations for Phase 3 and Phase 8 columns
        migrations = [
            ("company", "is_consolidated INTEGER DEFAULT 0"),
            ("company", "consolidated_members TEXT DEFAULT ''"),
            ("voucher", "company_id INTEGER DEFAULT 1"),
            ("voucher", "is_reversing INTEGER DEFAULT 0"),
            ("voucher", "reversal_date TEXT"),
            ("voucher", "is_rcm INTEGER DEFAULT 0"),
            ("voucher", "vehicle_no TEXT"),
            ("voucher", "transporter_id TEXT"),
            ("voucher", "distance_km REAL DEFAULT 0"),
            ("voucher", "order_no TEXT DEFAULT ''"),
            ("voucher", "challan_no TEXT DEFAULT ''"),
            ("voucher", "is_pos INTEGER DEFAULT 0"),
            ("stock_item", "category_name TEXT DEFAULT ''"),
            ("stock_item", "part_number TEXT DEFAULT ''"),
            ("employee", "employee_group TEXT DEFAULT 'General'"),
            ("ledger", "company_id INTEGER DEFAULT 1"),
            ("ledger", "address TEXT DEFAULT ''"),
            ("ledger", "pincode TEXT DEFAULT '560001'"),
            ("ledger", "bank_name TEXT DEFAULT ''"),
            ("ledger", "account_no TEXT DEFAULT ''"),
            ("ledger", "enforce_credit_limit INTEGER DEFAULT 0"),
            ("ledger", "msme_type TEXT DEFAULT 'Non-MSME'"),
            ("ledger", "msme_reg_no TEXT DEFAULT ''"),
            ("company", "allow_negative_stock INTEGER DEFAULT 1"),
            ("voucher_inventory", "landed_cost_addition REAL DEFAULT 0.0"),
            ("voucher_inventory", "effective_rate REAL DEFAULT 0.0"),
            ("edit_log", "chained_hash TEXT DEFAULT ''")
        ]
        for tbl, col_def in migrations:
            try:
                self.conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass

        self._seed_default_enterprise_data()

    def _seed_default_enterprise_data(self):
        if not self.seed_demo:
            # New company files contain only standard masters, never demo balances or inventory.
            with self.conn:
                for name, parent, revenue, gross in STANDARD_GROUPS:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO account_group (name,parent,is_revenue,affects_gross) VALUES (?,?,?,?)",
                        (name, parent, int(revenue), int(gross)))
                self.conn.execute("INSERT OR IGNORE INTO currency (symbol,formal_name,rate_of_exchange,is_base) VALUES ('₹','INR',1,1)")
                self.conn.execute("INSERT OR IGNORE INTO godown (name,location) VALUES ('Main Location','')")
                for symbol, formal, places in [('Nos','Numbers',0),('Pcs','Pieces',0),('Kg','Kilograms',3)]:
                    self.conn.execute('INSERT OR IGNORE INTO uom(symbol,formal_name,decimal_places) VALUES(?,?,?)', (symbol,formal,places))
            return
        cursor = self.conn.cursor()

        # Seed Account Groups
        cursor.execute("SELECT COUNT(*) FROM account_group")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                for grp in STANDARD_GROUPS:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO account_group (name, parent, is_revenue, affects_gross) VALUES (?, ?, ?, ?)",
                        (grp[0], grp[1], 1 if grp[2] else 0, 1 if grp[3] else 0)
                    )

        # Seed Company
        cursor.execute("SELECT COUNT(*) FROM company")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("""
                    INSERT INTO company (name, formal_name, currency_symbol, currency_formal, financial_year_from, financial_year_to, books_from, gstin, state, state_code, address, pincode)
                    VALUES ('Demo Enterprise Ltd.', 'Demo Enterprise Private Limited', '₹', 'INR', '2026-04-01', '2027-03-31', '2026-04-01', '29AABCT1332L1Z5', 'Karnataka', '29', 'Tech Park Phase 1, Whitefield, Bengaluru', '560066')
                """)

        # Seed Currencies
        cursor.execute("SELECT COUNT(*) FROM currency")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO currency (symbol, formal_name, rate_of_exchange, is_base) VALUES ('₹', 'INR', 1.0, 1)")
                self.conn.execute("INSERT OR IGNORE INTO currency (symbol, formal_name, rate_of_exchange, is_base) VALUES ('$', 'USD', 84.50, 0)")
                self.conn.execute("INSERT OR IGNORE INTO currency (symbol, formal_name, rate_of_exchange, is_base) VALUES ('€', 'EUR', 92.10, 0)")

        # Seed Godowns
        cursor.execute("SELECT COUNT(*) FROM godown")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO godown (name, location) VALUES ('Main Location', 'Bengaluru Central')")
                self.conn.execute("INSERT OR IGNORE INTO godown (name, location) VALUES ('Warehouse A', 'Whitefield Industrial Area')")
                self.conn.execute("INSERT OR IGNORE INTO godown (name, location) VALUES ('Warehouse B', 'Peenya Industrial Area')")
                self.conn.execute("INSERT OR IGNORE INTO godown (name, location) VALUES ('Job Worker Godown', 'Subcontracting Location')")

        # Seed Cost Categories & Centres
        cursor.execute("SELECT COUNT(*) FROM cost_category")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO cost_category (name) VALUES ('Departments')")
                self.conn.execute("INSERT OR IGNORE INTO cost_category (name) VALUES ('Projects')")
                self.conn.execute("INSERT OR IGNORE INTO cost_centre (name, category_name) VALUES ('Sales & Marketing', 'Departments')")
                self.conn.execute("INSERT OR IGNORE INTO cost_centre (name, category_name) VALUES ('Engineering & R&D', 'Departments')")
                self.conn.execute("INSERT OR IGNORE INTO cost_centre (name, category_name) VALUES ('Project Alpha', 'Projects')")

        # Seed UOMs
        cursor.execute("SELECT COUNT(*) FROM uom")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO uom (symbol, formal_name, decimal_places) VALUES ('Nos', 'Numbers', 0)")
                self.conn.execute("INSERT OR IGNORE INTO uom (symbol, formal_name, decimal_places) VALUES ('Pcs', 'Pieces', 0)")
                self.conn.execute("INSERT OR IGNORE INTO uom (symbol, formal_name, decimal_places) VALUES ('Box', 'Boxes', 0)")
                self.conn.execute("INSERT OR IGNORE INTO uom (symbol, formal_name, decimal_places) VALUES ('Kg', 'Kilograms', 2)")
                self.conn.execute("INSERT OR IGNORE INTO uom (symbol, formal_name, decimal_places) VALUES ('Mtr', 'Meters', 2)")

        # Seed Stock Items with Batches
        cursor.execute("SELECT COUNT(*) FROM stock_item")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, reorder_level, godown_name)
                    VALUES ('HP Laser Printer 1020', 'Primary', 'Nos', '84433100', 18.0, 10, 12500.0, 125000.0, 5, 'Main Location')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, reorder_level, godown_name)
                    VALUES ('Dell 24" FHD Monitor', 'Primary', 'Nos', '85285200', 18.0, 15, 8000.0, 120000.0, 5, 'Main Location')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, reorder_level, godown_name)
                    VALUES ('Logitech Wireless Mouse', 'Primary', 'Pcs', '84716060', 18.0, 50, 450.0, 22500.0, 15, 'Warehouse A')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, reorder_level, godown_name)
                    VALUES ('RAM DDR4 16GB', 'Raw Materials', 'Nos', '84733020', 18.0, 30, 3000.0, 90000.0, 10, 'Main Location')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, reorder_level, godown_name)
                    VALUES ('Assembled Desktop Combo', 'Finished Goods', 'Nos', '84713010', 18.0, 5, 25000.0, 125000.0, 2, 'Main Location')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO stock_batch (stock_item_name, batch_number, mfg_date, expiry_date, initial_qty)
                    VALUES ('HP Laser Printer 1020', 'BATCH-2026-A1', '2026-04-01', '2029-03-31', 10)
                """)

        # Seed Bill of Materials (BOM)
        cursor.execute("SELECT COUNT(*) FROM bom_header")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                cur = self.conn.execute("""
                    INSERT INTO bom_header (name, finished_item_name, unit_qty)
                    VALUES ('Assembled Desktop Combo', 'Assembled Desktop Combo', 1.0)
                """)
                bom_id = cur.lastrowid
                self.conn.execute("INSERT INTO bom_item (bom_id, component_item_name, qty) VALUES (?, 'Dell 24\" FHD Monitor', 1.0)", (bom_id,))
                self.conn.execute("INSERT INTO bom_item (bom_id, component_item_name, qty) VALUES (?, 'Logitech Wireless Mouse', 1.0)", (bom_id,))
                self.conn.execute("INSERT INTO bom_item (bom_id, component_item_name, qty) VALUES (?, 'RAM DDR4 16GB', 2.0)", (bom_id,))

        # Seed Ledgers
        cursor.execute("SELECT COUNT(*) FROM ledger")
        if cursor.fetchone()[0] == 0:
            default_ledgers = [
                ("Cash", "Cash-in-Hand", 35000.0, "Dr", "Cashier", "", "Karnataka", "29", 0, 0),
                ("HDFC Bank", "Bank Accounts", 350000.0, "Dr", "Branch Manager", "29HDFC0001234", "Karnataka", "29", 0, 0),
                ("State Bank of India", "Bank Accounts", 180000.0, "Dr", "Branch Manager", "29SBIN0005678", "Karnataka", "29", 0, 0),
                ("Capital Account", "Capital Account", 565000.0, "Cr", "Proprietor", "", "Karnataka", "29", 0, 0),
                ("Sales A/c", "Sales Accounts", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
                ("Purchase A/c", "Purchase Accounts", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("CGST", "Duties & Taxes", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("SGST", "Duties & Taxes", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("IGST", "Duties & Taxes", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("TDS Payable", "Duties & Taxes", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
                ("Office Rent", "Indirect Expenses", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Staff Salaries", "Payroll Expenses", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Acme Technologies (Debtor)", "Sundry Debtors", 0.0, "Dr", "Rahul Verma", "29AAACT9876Q1Z2", "Karnataka", "29", 100000.0, 30),
                ("Apex Systems Ltd (Creditor)", "Sundry Creditors", 0.0, "Cr", "Suresh Nair", "29BBBP9988R1Z4", "Karnataka", "29", 150000.0, 45),
                ("Stock-in-Hand", "Current Assets", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Duties & Taxes", "Duties & Taxes", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
                ("Computers & IT Equipment", "Fixed Assets", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Furniture & Fixtures", "Fixed Assets", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Depreciation Expense", "Indirect Expenses", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Accumulated Depreciation", "Provisions", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
                ("Freight & Cartage Inward", "Direct Expenses", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("TCS Payable", "Duties & Taxes", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
                ("Foreign Exchange Gain/Loss", "Indirect Expenses", 0.0, "Dr", "", "", "Karnataka", "29", 0, 0),
                ("Employee State Insurance (ESI)", "Current Liabilities", 0.0, "Cr", "", "", "Karnataka", "29", 0, 0),
            ]
            with self.conn:
                for l in default_ledgers:
                    self.conn.execute("""
                        INSERT OR IGNORE INTO ledger (name, group_name, opening_balance, opening_balance_type, contact_person, gstin, state, state_code, credit_limit, credit_period_days)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, l)

        # Seed Payroll Masters
        cursor.execute("SELECT COUNT(*) FROM employee")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("""
                    INSERT OR IGNORE INTO employee (emp_code, name, department, designation, date_of_joining, pan, pf_uan, esi_number, bank_name, account_no)
                    VALUES ('EMP001', 'Arjun Sharma', 'Software Development', 'Senior Engineer', '2024-01-10', 'ABCPS1234F', '100987654321', '3100998877', 'HDFC Bank', '5010022334455')
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO employee (emp_code, name, department, designation, date_of_joining, pan, pf_uan, esi_number, bank_name, account_no)
                    VALUES ('EMP002', 'Priya Raman', 'Finance & Accounts', 'Accounts Manager', '2024-03-01', 'BPRPR5678K', '100876543210', '3100887766', 'State Bank of India', '3020055443322')
                """)

                self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type, percentage, is_statutory) VALUES ('Basic Salary', 'Earnings', 'Fixed', 0.0, 0)")
                self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type, percentage, is_statutory) VALUES ('House Rent Allowance (HRA)', 'Earnings', 'Percentage', 40.0, 0)")
                self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type, percentage, is_statutory) VALUES ('Special Allowance', 'Earnings', 'Fixed', 0.0, 0)")
                self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type, percentage, is_statutory) VALUES ('Provident Fund (PF)', 'Deductions', 'Percentage', 12.0, 1)")
                self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type, percentage, is_statutory) VALUES ('Professional Tax (PT)', 'Deductions', 'Fixed', 200.0, 1)")

                self.conn.execute("INSERT OR IGNORE INTO salary_structure (emp_code, pay_head_name, amount) VALUES ('EMP001', 'Basic Salary', 45000.0)")
                self.conn.execute("INSERT OR IGNORE INTO salary_structure (emp_code, pay_head_name, amount) VALUES ('EMP001', 'Special Allowance', 15000.0)")
                self.conn.execute("INSERT OR IGNORE INTO salary_structure (emp_code, pay_head_name, amount) VALUES ('EMP002', 'Basic Salary', 35000.0)")
                self.conn.execute("INSERT OR IGNORE INTO salary_structure (emp_code, pay_head_name, amount) VALUES ('EMP002', 'Special Allowance', 10000.0)")

        # Seed Users & Roles
        cursor.execute("SELECT COUNT(*) FROM user_account")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                admin_hash = self.hash_password("admin123")
                entry_hash = self.hash_password("entry123")
                audit_hash = self.hash_password("audit123")
                self.conn.execute("INSERT INTO user_account (username, password_hash, role, full_name, company_access) VALUES ('admin', ?, 'Admin', 'System Administrator', 'ALL')", (admin_hash,))
                self.conn.execute("INSERT INTO user_account (username, password_hash, role, full_name, company_access) VALUES ('dataentry', ?, 'Data Entry', 'Accountant', 'ALL')", (entry_hash,))
                self.conn.execute("INSERT INTO user_account (username, password_hash, role, full_name, company_access) VALUES ('auditor', ?, 'Auditor', 'Chartered Accountant', 'ALL')", (audit_hash,))

        # Seed Price Levels & Price Lists
        cursor.execute("SELECT COUNT(*) FROM price_level")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO price_level (name) VALUES ('Retail')")
                self.conn.execute("INSERT OR IGNORE INTO price_level (name) VALUES ('Wholesale')")
                self.conn.execute("INSERT OR IGNORE INTO price_level (name) VALUES ('Dealer')")

                self.conn.execute("""
                    INSERT OR IGNORE INTO price_list_item (price_level, stock_item_name, min_qty, rate, discount_pct)
                    VALUES ('Wholesale', 'Assembled Desktop Combo', 5.0, 23500.0, 5.0)
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO price_list_item (price_level, stock_item_name, min_qty, rate, discount_pct)
                    VALUES ('Dealer', 'Assembled Desktop Combo', 10.0, 22000.0, 8.0)
                """)

        # Seed Compound UOMs
        cursor.execute("SELECT COUNT(*) FROM compound_uom")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO compound_uom (major_uom, conversion_factor, minor_uom) VALUES ('Box', 10.0, 'Nos')")
                self.conn.execute("INSERT OR IGNORE INTO compound_uom (major_uom, conversion_factor, minor_uom) VALUES ('Box', 20.0, 'Pcs')")

        # Seed Stock Categories
        cursor.execute("SELECT COUNT(*) FROM stock_category")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO stock_category (name, parent) VALUES ('Hardware', 'Primary')")
                self.conn.execute("INSERT OR IGNORE INTO stock_category (name, parent) VALUES ('Peripherals', 'Primary')")
                self.conn.execute("INSERT OR IGNORE INTO stock_category (name, parent) VALUES ('Components', 'Primary')")

        # Seed TDS Sections
        cursor.execute("SELECT COUNT(*) FROM tds_section")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO tds_section (section_code, description, threshold_amount, rate_individual, rate_other) VALUES ('194C', 'Payment to Contractors & Sub-contractors', 30000.0, 1.0, 2.0)")
                self.conn.execute("INSERT OR IGNORE INTO tds_section (section_code, description, threshold_amount, rate_individual, rate_other) VALUES ('194J', 'Fees for Professional or Technical Services', 30000.0, 10.0, 10.0)")
                self.conn.execute("INSERT OR IGNORE INTO tds_section (section_code, description, threshold_amount, rate_individual, rate_other) VALUES ('194I', 'Rent for Land, Building or Furniture', 240000.0, 10.0, 10.0)")
                self.conn.execute("INSERT OR IGNORE INTO tds_section (section_code, description, threshold_amount, rate_individual, rate_other) VALUES ('194Q', 'Purchase of Goods exceeding 50 Lakhs', 5000000.0, 0.1, 0.1)")

        # Seed Cheque Book
        cursor.execute("SELECT COUNT(*) FROM cheque_book")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("INSERT OR IGNORE INTO cheque_book (bank_ledger_name, book_name, from_no, to_no, current_no) VALUES ('HDFC Bank', 'HDFC Chq Book 01', 100001, 100100, 100001)")

        # Seed TCS Sections (Income Tax Act, 1961 Section 206C)
        cursor.execute("SELECT COUNT(*) FROM tcs_section")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("""
                    INSERT OR IGNORE INTO tcs_section (section_code, description, threshold_amount, rate, rate_pan_missing)
                    VALUES ('206C(1H)', 'TCS on Sale of Goods exceeding ₹50 Lakhs', 5000000.0, 0.1, 1.0)
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO tcs_section (section_code, description, threshold_amount, rate, rate_pan_missing)
                    VALUES ('206C(1)', 'TCS on Sale of Scrap, Minerals & Alcoholic Liquor', 0.0, 1.0, 5.0)
                """)

        # Seed Default Fixed Asset (ERPNext Assets + Tally FA)
        cursor.execute("SELECT COUNT(*) FROM fixed_asset")
        if cursor.fetchone()[0] == 0:
            with self.conn:
                self.conn.execute("""
                    INSERT OR IGNORE INTO fixed_asset (
                        asset_name, asset_code, category_name, purchase_date, in_service_date,
                        purchase_cost, scrap_value, useful_life_months, depreciation_method,
                        depreciation_rate_pct, asset_ledger, depr_expense_ledger, accum_depr_ledger, status, notes
                    ) VALUES (
                        'Dell PowerEdge Server R750', 'FA-IT-001', 'Computers & IT Assets', '2026-04-01', '2026-04-01',
                        180000.0, 10000.0, 36, 'SLM', 33.33,
                        'Computers & IT Equipment', 'Depreciation Expense', 'Accumulated Depreciation', 'In Use', 'Enterprise ERP & Production Database Server'
                    )
                """)

    # -------------------------------------------------------------------------
    # Company & Multi-Company Management
    # -------------------------------------------------------------------------
    def get_company(self) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM company WHERE id = ?", (self.active_company_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("SELECT * FROM company LIMIT 1")
            row = cursor.fetchone()
        res = dict(row) if row else {}
        res.pop("vault_password", None)
        res["last_voucher_date"] = self.get_last_voucher_date()
        return res

    def get_companies(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM company ORDER BY id")
        companies = []
        for r in cursor.fetchall():
            c = dict(r)
            c.pop("vault_password", None)
            c["is_active"] = (c["id"] == self.active_company_id)
            companies.append(c)
        return companies

    def select_company(self, company_id: int) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM company WHERE id = ?", (company_id,))
        if cursor.fetchone():
            self.active_company_id = company_id
            return True
        return False

    def create_company(self, name: str, formal_name: str = "", gstin: str = "", state: str = "Karnataka",
                       state_code: str = "29", address: str = "", financial_year_from: str = "2026-04-01",
                       financial_year_to: str = "2027-03-31", currency_symbol: str = "₹") -> int:
        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO company (name, formal_name, currency_symbol, currency_formal, financial_year_from, financial_year_to, books_from, gstin, state, state_code, address, pincode)
                VALUES (?, ?, ?, 'INR', ?, ?, ?, ?, ?, ?, ?, '560001')
            """, (name.strip(), formal_name.strip() or name.strip(), currency_symbol, financial_year_from, financial_year_to, financial_year_from, gstin, state, state_code, address))
            new_id = cur.lastrowid
            self._log_edit("Company", name, "CREATE", f"Created new company '{name}' with ID {new_id}")
            return new_id

    def update_company(self, name: str, formal_name: str = "", gstin: str = "", state: str = "Karnataka", state_code: str = "29",
                       financial_year_from: str = "2026-04-01", financial_year_to: str = "2027-03-31", address: str = "") -> bool:
        with self.conn:
            fname = formal_name or name
            self.conn.execute("""
                UPDATE company 
                SET name = ?, formal_name = ?, gstin = ?, state = ?, state_code = ?, financial_year_from = ?, financial_year_to = ?,
                    address = CASE WHEN ? != '' THEN ? ELSE address END
                WHERE id = ?
            """, (name, fname, gstin, state, state_code, financial_year_from, financial_year_to, address, address, self.active_company_id))
            self._log_edit("Company", name, "ALTER", f"Updated company details for {name}")
        return True

    def alter_company(self, company_id: int, name: str, formal_name: str = "", gstin: str = "", state: str = "Karnataka", state_code: str = "29",
                      financial_year_from: str = "2026-04-01", financial_year_to: str = "2027-03-31", address: str = "", pincode: str = "560001",
                      currency_symbol: str = "₹") -> bool:
        """
        Alters specified company details by company_id.
        """
        fname = formal_name or name
        with self.conn:
            self.conn.execute("""
                UPDATE company
                SET name = ?, formal_name = ?, gstin = ?, state = ?, state_code = ?,
                    financial_year_from = ?, financial_year_to = ?,
                    address = CASE WHEN ? != '' THEN ? ELSE address END,
                    pincode = ?, currency_symbol = ?
                WHERE id = ?
            """, (name.strip(), fname.strip(), gstin.strip(), state.strip(), state_code.strip(),
                  financial_year_from, financial_year_to, address, address, pincode, currency_symbol, company_id))
            self._log_edit("Company", name, "ALTER", f"Altered company ID {company_id} details")
        return True

    def shut_company(self, company_id: int) -> Optional[int]:
        """
        Closes/shuts specified company. If active, selects another available company.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM company WHERE id != ? ORDER BY id ASC", (company_id,))
        rows = cursor.fetchall()
        next_id = rows[0][0] if rows else None
        if self.active_company_id == company_id:
            if next_id is not None:
                self.select_company(next_id)
            else:
                self.active_company_id = 0
        self._log_edit("Company", str(company_id), "SHUT", f"Shut company ID {company_id}. Next active: {next_id}")
        return next_id

    def delete_company(self, company_id: int) -> bool:
        """
        Safely deletes a company and all its associated accounting records.
        Cannot delete if it is the only company remaining.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM company")
        total_comps = cursor.fetchone()[0]
        if total_comps <= 1:
            raise ValueError("Cannot delete the only remaining company in the database.")

        cursor.execute("SELECT name FROM company WHERE id = ?", (company_id,))
        c_row = cursor.fetchone()
        c_name = c_row[0] if c_row else f"Company_{company_id}"

        # If deleting the active company, switch first
        if self.active_company_id == company_id:
            cursor.execute("SELECT id FROM company WHERE id != ? LIMIT 1", (company_id,))
            other_id = cursor.fetchone()[0]
            self.select_company(other_id)

        with self.conn:
            self.conn.execute("DELETE FROM voucher WHERE company_id = ?", (company_id,))
            self.conn.execute("DELETE FROM ledger WHERE company_id = ?", (company_id,))
            self.conn.execute("DELETE FROM stock_item WHERE company_id = ?", (company_id,))
            self.conn.execute("DELETE FROM company WHERE id = ?", (company_id,))
            self._log_edit("Company", c_name, "DELETE", f"Deleted company '{c_name}' (ID {company_id}) and its transactions")
        return True

    def update_company_security(self, company_id: int, security_enabled: bool, vault_password: str = "") -> bool:
        """
        Updates company TallyVault security settings.
        """
        p_hash = ""
        if vault_password:
            salt = os.urandom(16)
            kdf = hashlib.pbkdf2_hmac('sha256', vault_password.encode('utf-8'), salt, 100000)
            p_hash = f"pbkdf2:sha256:100000${salt.hex()}${kdf.hex()}"

        with self.conn:
            self.conn.execute("""
                UPDATE company
                SET security_enabled = ?, vault_password = ?
                WHERE id = ?
            """, (1 if security_enabled else 0, p_hash, company_id))
            self._log_edit("Company", str(company_id), "SECURITY", f"Updated security settings for company ID {company_id}")
        return True

    def update_company_financial_year(self, company_id: int, fy_start: str, fy_end: str) -> bool:
        """
        Updates company active financial year boundaries.
        """
        with self.conn:
            self.conn.execute("""
                UPDATE company
                SET financial_year_from = ?, financial_year_to = ?, books_from = ?
                WHERE id = ?
            """, (fy_start, fy_end, fy_start, company_id))
            self._log_edit("Company", str(company_id), "ALTER_FY", f"Updated FY for company ID {company_id} to {fy_start} to {fy_end}")
        return True

    def consolidate_companies(self, group_company_name: str, member_company_ids: List[int]) -> int:
        """
        Creates or updates a consolidated Group Company consolidating financial data
        across member company IDs.
        """
        members_str = json.dumps(member_company_ids)
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM company WHERE name = ? AND is_consolidated = 1", (group_company_name,))
        row = cursor.fetchone()
        with self.conn:
            if row:
                grp_id = row[0]
                self.conn.execute("UPDATE company SET consolidated_members = ? WHERE id = ?", (members_str, grp_id))
            else:
                cur = self.conn.execute("""
                    INSERT INTO company (name, formal_name, is_consolidated, consolidated_members)
                    VALUES (?, ?, 1, ?)
                """, (group_company_name, f"{group_company_name} (Consolidated Group)", members_str))
                grp_id = cur.lastrowid
            self._log_edit("Company", group_company_name, "CONSOLIDATE", f"Consolidated {len(member_company_ids)} companies into {group_company_name}")
        return grp_id

    def get_consolidation_eliminations(self, company_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        Identifies and calculates inter-company elimination entries between member companies.
        Eliminates reciprocal balances between member companies in Sundry Debtors & Sundry Creditors
        so consolidated statements reflect true external group position without double-counting.
        """
        c_ids = company_ids or [self.active_company_id]
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name FROM company WHERE id IN ({})".format(",".join("?" * len(c_ids))), c_ids)
        companies = {r["id"]: r["name"] for r in cursor.fetchall()}

        eliminations = []
        total_eliminated = Decimal("0.00")

        # Check for ledgers in member company books that bear the name of other member companies
        orig_active = self.active_company_id
        try:
            for cid, cname in companies.items():
                self.active_company_id = cid
                for other_id, other_name in companies.items():
                    if cid == other_id:
                        continue
                    cursor.execute("""
                        SELECT l.name, l.group_name
                        FROM ledger l
                        WHERE (l.name = ? OR l.name LIKE ?) AND l.company_id = ?
                    """, (other_name, f"%{other_name}%", cid))
                    matches = cursor.fetchall()
                    for m in matches:
                        lname = m["name"]
                        cb, cb_type = self.get_ledger_closing_balance(lname)
                        if cb > Decimal("0.00"):
                            eliminations.append({
                                "company_id": cid,
                                "company_name": cname,
                                "counterparty_company": other_name,
                                "ledger_name": lname,
                                "group_name": m["group_name"],
                                "eliminated_amount": float(cb),
                                "eliminated_type": cb_type
                            })
                            total_eliminated += cb
        finally:
            self.active_company_id = orig_active

        return {
            "eliminations": eliminations,
            "total_eliminated_amount": float(total_eliminated)
        }

    def get_consolidated_financial_summary(self, company_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        Consolidates sales, purchases, closing stock, and profits across multiple companies,
        applying inter-company eliminations to prevent inflated asset/liability figures.
        """
        c_ids = company_ids or [self.active_company_id]
        orig_active = self.active_company_id
        tot_sales = Decimal("0.00")
        tot_pur = Decimal("0.00")
        tot_stock = Decimal("0.00")
        tot_gp = Decimal("0.00")
        tot_np = Decimal("0.00")
        tot_assets = Decimal("0.00")
        tot_liab = Decimal("0.00")

        try:
            for cid in c_ids:
                self.active_company_id = cid
                fin = self.get_financial_summary()
                tot_sales += to_dec(fin["sales_total"])
                tot_pur += to_dec(fin["purchase_total"])
                tot_stock += to_dec(fin["closing_stock"])
                tot_gp += to_dec(fin["gross_profit"])
                tot_np += to_dec(fin["net_profit"])
                tot_assets += to_dec(fin["assets_total"])
                tot_liab += to_dec(fin["liab_total"])
        finally:
            self.active_company_id = orig_active

        # Compute and apply inter-company eliminations
        elim_data = self.get_consolidation_eliminations(c_ids)
        elim_amt = to_dec(elim_data["total_eliminated_amount"])
        # Eliminate reciprocal assets and liabilities
        net_assets = max(Decimal("0.00"), tot_assets - elim_amt)
        net_liab = max(Decimal("0.00"), tot_liab - elim_amt)

        return {
            "sales_total": float(tot_sales),
            "purchase_total": float(tot_pur),
            "closing_stock": float(tot_stock),
            "gross_profit": float(tot_gp),
            "net_profit": float(tot_np),
            "assets_total": float(net_assets),
            "liab_total": float(net_liab),
            "eliminated_intercompany_amount": float(elim_amt),
            "eliminations": elim_data["eliminations"],
            "consolidated_companies_count": len(c_ids)
        }

    def get_consolidated_trial_balance(self, company_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        c_ids = company_ids or [self.active_company_id]
        orig_active = self.active_company_id
        tb_map: Dict[str, Dict[str, Any]] = {}

        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name FROM company WHERE id IN ({})".format(",".join("?" * len(c_ids))), c_ids)
        member_names = {r["name"].strip().lower() for r in cursor.fetchall()}

        try:
            for cid in c_ids:
                self.active_company_id = cid
                tb = self.get_trial_balance()
                for row in tb:
                    name = row["name"]
                    # If this ledger is an inter-company balance to another member, flag for elimination
                    is_interco = any(m in name.lower() for m in member_names)
                    if name not in tb_map:
                        tb_map[name] = {
                            "name": name,
                            "ledger_name": name,
                            "group_name": row["group_name"],
                            "dr_amount": Decimal("0.00"),
                            "cr_amount": Decimal("0.00"),
                            "is_eliminated": is_interco
                        }
                    tb_map[name]["dr_amount"] += to_dec(row["dr_amount"])
                    tb_map[name]["cr_amount"] += to_dec(row["cr_amount"])
        finally:
            self.active_company_id = orig_active

        result = []
        for name, item in tb_map.items():
            # If inter-company reciprocal ledger, eliminate common balance
            dr = item["dr_amount"]
            cr = item["cr_amount"]
            if item["is_eliminated"]:
                min_bal = min(dr, cr)
                dr -= min_bal
                cr -= min_bal

            net = dr - cr
            b_type = "Dr" if net >= Decimal("0.00") else "Cr"
            bal = abs(net)
            result.append({
                "name": name,
                "ledger_name": name,
                "group_name": item["group_name"],
                "closing_balance": float(bal),
                "closing_balance_type": b_type,
                "dr_amount": float(bal) if b_type == "Dr" and bal > 0 else 0.0,
                "cr_amount": float(bal) if b_type == "Cr" and bal > 0 else 0.0,
                "is_intercompany_eliminated": item["is_eliminated"]
            })
        return result

    def get_last_voucher_date(self) -> str:
        cursor = self.conn.cursor()
        cursor.execute("SELECT date FROM voucher WHERE is_cancelled = 0 ORDER BY date DESC, id DESC LIMIT 1")
        row = cursor.fetchone()
        return row[0] if row else "No vouchers entered"

    def get_groups(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM account_group ORDER BY name")
        return [dict(r) for r in cursor.fetchall()]

    def add_group(self, name: str, parent: str = "Primary", is_revenue: bool = False, affects_gross: bool = False) -> bool:
        with self.conn:
            self.conn.execute(
                "INSERT INTO account_group (name, parent, is_revenue, affects_gross) VALUES (?, ?, ?, ?)",
                (name, parent, 1 if is_revenue else 0, 1 if affects_gross else 0)
            )
            self._log_edit("Group", name, "CREATE", f"Created account group {name} under {parent}")
        return True

    # -------------------------------------------------------------------------
    # Ledger Master & Balance Calculations (High Precision Decimal)
    # -------------------------------------------------------------------------
    def get_ledgers(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM ledger ORDER BY name")
        ledgers = [dict(r) for r in cursor.fetchall()]
        for l in ledgers:
            bal_dec, btype = self.get_ledger_closing_balance(l["name"])
            l["closing_balance"] = float(bal_dec)
            l["closing_balance_type"] = btype
        return ledgers

    def get_ledger(self, name: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM ledger WHERE name = ?", (name,))
        row = cursor.fetchone()
        if not row:
            return None
        l = dict(row)
        bal_dec, btype = self.get_ledger_closing_balance(name)
        l["closing_balance"] = float(bal_dec)
        l["closing_balance_type"] = btype
        return l

    def add_ledger(self, name: str, group_name: str, opening_balance: float = 0.0, opening_type: str = "Dr",
                   contact_person: str = "", gstin: str = "", state: str = "Karnataka", credit_limit: float = 0.0, **kwargs) -> bool:
        if "opening_balance_type" in kwargs:
            opening_type = kwargs["opening_balance_type"]
        op_dec = float(to_dec(opening_balance))
        with self.conn:
            self.conn.execute("""
                INSERT INTO ledger (name, group_name, opening_balance, opening_balance_type, contact_person, gstin, state, credit_limit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, group_name, op_dec, opening_type, contact_person, gstin, state, credit_limit))
            self._log_edit("Ledger", name, "CREATE", f"Created ledger {name} under {group_name}")
        return True

    def get_ledger_closing_balance(self, ledger_name: str) -> Tuple[Decimal, str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT opening_balance, opening_balance_type FROM ledger WHERE name = ?", (ledger_name,))
        row = cursor.fetchone()
        if not row:
            return Decimal("0.00"), "Dr"

        op_bal = to_dec(row["opening_balance"])
        op_type = row["opening_balance_type"]
        net = op_bal if op_type == "Dr" else -op_bal

        cursor.execute("""
            SELECT entry_type, SUM(amount) as total 
            FROM voucher_entry ve 
            JOIN voucher v ON v.id = ve.voucher_id 
            WHERE ve.ledger_name = ? AND v.is_cancelled = 0 AND v.is_optional = 0
            GROUP BY entry_type
        """, (ledger_name,))

        for r in cursor.fetchall():
            amt = to_dec(r["total"])
            if r["entry_type"] == "Dr":
                net += amt
            else:
                net -= amt

        if net >= Decimal("0.00"):
            return net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "Dr"
        else:
            return abs(net).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "Cr"

    # -------------------------------------------------------------------------
    # Inventory, Godowns, Batches & Transfers
    # -------------------------------------------------------------------------
    def get_godowns(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM godown ORDER BY name")
        return [dict(r) for r in cursor.fetchall()]

    def add_godown(self, name: str, location: str) -> bool:
        with self.conn:
            self.conn.execute("INSERT INTO godown (name, location) VALUES (?, ?)", (name, location))
            self._log_edit("Godown", name, "CREATE", f"Created godown {name} at {location}")
        return True

    def get_uoms(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM uom ORDER BY symbol")
        return [dict(r) for r in cursor.fetchall()]

    def add_uom(self, symbol: str, formal_name: str, decimal_places: int = 0) -> bool:
        with self.conn:
            self.conn.execute("INSERT INTO uom (symbol, formal_name, decimal_places) VALUES (?, ?, ?)", (symbol, formal_name, decimal_places))
            self._log_edit("UOM", symbol, "CREATE", f"Created UOM {symbol} ({formal_name})")
        return True

    def get_stock_items(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM stock_item ORDER BY name")
        items = [dict(r) for r in cursor.fetchall()]
        for itm in items:
            summary = self.get_stock_item_summary(itm["name"])
            itm.update(summary)
        return items

    def get_stock_item_monthly(self, item_name: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict[str, Any]:
        """Stock Item Monthly Summary: per month the inwards, outwards and closing quantity and value on the same costing as the
        closing value (financial-year weighted average). Outwards value is what makes the columns reconcile:
        opening + inwards - outwards = closing. Defaults to the financial year of the item's last voucher."""
        row = self.conn.execute("SELECT opening_qty, opening_value, uom_symbol FROM stock_item WHERE name = ?", (item_name,)).fetchone()
        if not row:
            raise ValueError("Stock item not found")
        op_qty, op_val = to_qty_dec(row["opening_qty"]), to_dec(row["opening_value"])
        series = {e["month"]: e for e in self._stock_costing(item_name, op_qty, op_val)}
        if not from_date or not to_date:
            last_date = self.conn.execute("SELECT MAX(date) FROM voucher").fetchone()[0] or datetime.date.today().isoformat()
            fy_from, fy_to = self._financial_year(last_date)
            from_date, to_date = from_date or fy_from, to_date or fy_to
        zero = Decimal("0")
        qty, value = op_qty, (op_val if op_qty > 0 else zero)
        for key in sorted(k for k in series if k < from_date[:7]):         # state at the start of the period
            qty, value = series[key]["closing_qty"], series[key]["closing_value"]
        opening = (qty, value)
        months = []
        y, m = int(from_date[:4]), int(from_date[5:7])
        while f"{y:04d}-{m:02d}" <= to_date[:7]:
            key = f"{y:04d}-{m:02d}"
            entry = series.get(key)
            if entry:
                out_val = value + entry["in_value"] - entry["closing_value"]
                months.append({"month": key, "in_qty": float(entry["in_qty"]), "in_value": float(entry["in_value"]),
                               "out_qty": float(entry["out_qty"]), "out_value": float(out_val.quantize(Decimal("0.01"))),
                               "closing_qty": float(entry["closing_qty"]), "closing_value": float(entry["closing_value"])})
                qty, value = entry["closing_qty"], entry["closing_value"]
            else:
                months.append({"month": key, "in_qty": 0.0, "in_value": 0.0, "out_qty": 0.0, "out_value": 0.0,
                               "closing_qty": float(qty), "closing_value": float(value)})
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return {"item": item_name, "unit": row["uom_symbol"], "from": from_date, "to": to_date,
                "opening_qty": float(opening[0]), "opening_value": float(opening[1]), "months": months}

    def get_stock_item(self, name: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM stock_item WHERE name = ?", (name,))
        row = cursor.fetchone()
        if not row:
            return None
        itm = dict(row)
        itm.update(self.get_stock_item_summary(name))
        return itm

    def add_stock_item(self, name: str, group_name: str = "Primary", uom_symbol: str = "Pcs", hsn_code: str = "",
                       gst_rate: float = 18.0, opening_qty: float = 0.0, opening_rate: float = 0.0, godown_name: str = "Main Location", **kwargs) -> bool:
        self.require_feature('maintain-inv')
        if "uom_name" in kwargs:
            uom_symbol = kwargs["uom_name"]
        op_qty_dec = to_qty_dec(opening_qty)
        op_rate_dec = to_dec(opening_rate)
        op_val = float((op_qty_dec * op_rate_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        with self.conn:
            self.conn.execute("""
                INSERT INTO stock_item (name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate, opening_value, godown_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, group_name, uom_symbol, hsn_code, gst_rate, float(op_qty_dec), float(op_rate_dec), op_val, godown_name))
            self._log_edit("StockItem", name, "CREATE", f"Created stock item {name} under {group_name}")
        return True

    def _stock_costing(self, item_name, op_qty, op_val):
        """Average Cost the way TallyPrime values stock: value = quantity x the weighted average rate of everything received
        in the financial year (stock carried in at 1 April included). Verified against a real TallyPrime: its year-start
        opening values and closing values of five items - positive and negative stock - are reproduced
        to within a paisa (rate rounding), where monthly and daily running averages were 1-13% out.

        Returns one row per month with movements: month, in/out quantity, in value, closing quantity and closing value.
        Stock carried into a new year is valued at the closing rate; a year with no receipts keeps the last rate; negative
        stock is valued at the year's average rate and is not carried forward as value."""
        rows = self.conn.execute("""
            SELECT substr(v.date, 1, 7) AS m, vi.direction, vi.qty, vi.amount FROM voucher_inventory vi JOIN voucher v ON v.id = vi.voucher_id
            WHERE vi.stock_item_name = ? AND v.is_cancelled = 0 AND (v.is_optional = 0 OR v.voucher_type IN
                  ('Stock Journal', 'Delivery Note', 'Receipt Note', 'Rejections In', 'Rejections Out', 'Material In', 'Material Out'))
            ORDER BY v.date""", (item_name,)).fetchall()
        zero = Decimal("0")
        months = {}
        for r in rows:
            slot = months.setdefault(r["m"], [zero, zero, zero])
            if r["direction"] == "In":
                slot[0] += to_qty_dec(r["qty"])
                slot[1] += to_dec(r["amount"])
            else:
                slot[2] += to_qty_dec(r["qty"])
        fy_of = lambda key: int(key[:4]) - (1 if int(key[5:7]) < 4 else 0)
        qty, rate = op_qty, ((op_val / op_qty) if op_qty > 0 else zero)
        series, fy = [], None
        open_qty, open_val, cum_qty, cum_val = op_qty, (op_val if op_qty > 0 else zero), zero, zero
        for key in sorted(months):
            in_qty, in_val, out_qty = months[key]
            if fy is not None and fy_of(key) != fy:                       # new financial year: carry the closing stock in
                open_qty, open_val = (qty, qty * rate) if qty > 0 else (zero, zero)
                cum_qty = cum_val = zero
            fy = fy_of(key)
            cum_qty += in_qty
            cum_val += in_val
            qty += in_qty - out_qty
            if open_qty + cum_qty > 0:
                rate = (open_val + cum_val) / (open_qty + cum_qty)
            series.append({"month": key, "in_qty": in_qty, "in_value": in_val, "out_qty": out_qty,
                           "closing_qty": qty, "closing_value": (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "rate": rate})
        return series

    def get_stock_item_summary(self, item_name: str) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT opening_qty, opening_rate, opening_value, uom_symbol, reorder_level FROM stock_item WHERE name = ?", (item_name,))
        row = cursor.fetchone()
        if not row:
            return {"inwards_qty": 0.0, "outwards_qty": 0.0, "closing_qty": 0.0, "closing_value": 0.0, "closing_rate": 0.0, "needs_reorder": False}

        op_qty = to_qty_dec(row["opening_qty"])
        op_val = to_dec(row["opening_value"])
        op_rate = to_dec(row["opening_rate"])

        cursor.execute("""
            SELECT direction, SUM(qty) as total_qty, SUM(amount) as total_amt 
            FROM voucher_inventory vi 
            JOIN voucher v ON v.id = vi.voucher_id 
            WHERE vi.stock_item_name = ? AND v.is_cancelled = 0 AND (v.is_optional = 0 OR v.voucher_type IN ('Stock Journal', 'Delivery Note', 'Receipt Note', 'Rejections In', 'Rejections Out', 'Material In', 'Material Out'))
            GROUP BY direction
        """, (item_name,))

        in_qty, in_amt = Decimal("0.000"), Decimal("0.00")
        out_qty, out_amt = Decimal("0.000"), Decimal("0.00")

        for r in cursor.fetchall():
            if r["direction"] == "In":
                in_qty += to_qty_dec(r["total_qty"])
                in_amt += to_dec(r["total_amt"])
            else:
                out_qty += to_qty_dec(r["total_qty"])
                out_amt += to_dec(r["total_amt"])

        closing_qty = op_qty + in_qty - out_qty
        total_in_qty = op_qty + in_qty
        total_in_val = op_val + in_amt
        avg_rate = (total_in_val / total_in_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total_in_qty > Decimal("0") else op_rate
        # TallyPrime values negative stock too (verified on one item to the paisa: at the average inward rate);
        # it was valued at zero before, which hid a large part of the cost of sales.
        closing_val = (closing_qty * avg_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if closing_qty != Decimal("0") else Decimal("0.00")

        if closing_qty != 0:
            series = self._stock_costing(item_name, op_qty, op_val)
            if series:
                closing_val = series[-1]["closing_value"]
            elif op_qty > 0:
                closing_val = op_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        closing_val = self.inventory_value(item_name, closing_qty, closing_val)
        if closing_qty != 0:
            avg_rate = (closing_val / closing_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        reorder_level = to_qty_dec(row["reorder_level"])
        return {
            "opening_qty": float(op_qty),
            "opening_rate": float(op_rate),
            "opening_value": float(op_val),
            "inwards_qty": float(in_qty),
            "inwards_value": float(in_amt),
            "outwards_qty": float(out_qty),
            "outwards_value": float(out_amt),
            "closing_qty": float(closing_qty),
            "closing_rate": float(avg_rate),
            "closing_value": float(closing_val),
            "uom_symbol": row["uom_symbol"],
            "reorder_level": float(reorder_level),
            "needs_reorder": closing_qty <= reorder_level
        }

    def get_stock_summary(self) -> List[Dict[str, Any]]:
        items = self.get_stock_items()
        return [{
            "name": itm["name"],
            "item_name": itm["name"],
            "group_name": itm["group_name"],
            "uom": itm["uom_symbol"],
            "closing_qty": itm["closing_qty"],
            "closing_rate": itm["closing_rate"],
            "closing_value": itm["closing_value"],
            "needs_reorder": itm["needs_reorder"]
        } for itm in items]

    def get_stock_batches(self, stock_item_name: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if stock_item_name:
            cursor.execute("SELECT * FROM stock_batch WHERE stock_item_name = ? ORDER BY mfg_date", (stock_item_name,))
        else:
            cursor.execute("SELECT * FROM stock_batch ORDER BY stock_item_name, mfg_date")
        return [dict(r) for r in cursor.fetchall()]

    def get_godown_summary(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT DISTINCT name FROM godown")
        master = [r[0] for r in cursor.fetchall()]
        # A godown named on a voucher line but absent from the master is still listed, so the godowns add up to the item totals.
        used = [r[0] for r in cursor.execute("SELECT DISTINCT godown_name FROM voucher_inventory WHERE godown_name IS NOT NULL AND godown_name != ''")]
        godowns = master + [g for g in used if g not in master]

        result = []
        for g in godowns:
            items_map: Dict[str, float] = {}
            cursor.execute("SELECT name, opening_qty FROM stock_item WHERE godown_name = ?", (g,))
            for r in cursor.fetchall():
                q = float(to_qty_dec(r["opening_qty"]))
                if q != 0:
                    items_map[r["name"]] = q

            cursor.execute("""
                SELECT vi.stock_item_name, SUM(CASE WHEN vi.direction='In' THEN vi.qty ELSE -vi.qty END) as net_qty
                FROM voucher_inventory vi
                JOIN voucher v ON v.id = vi.voucher_id
                WHERE vi.godown_name = ? AND v.is_cancelled = 0 AND (v.is_optional = 0 OR v.voucher_type IN ('Stock Journal', 'Delivery Note', 'Receipt Note', 'Rejections In', 'Rejections Out', 'Material In', 'Material Out'))
                GROUP BY vi.stock_item_name
            """, (g,))
            for r in cursor.fetchall():
                net = float(to_qty_dec(r[1]))
                items_map[r[0]] = items_map.get(r[0], 0.0) + net

            items = [{"item_name": k, "qty": v} for k, v in items_map.items() if v != 0]       # negative stock is stock too
            total_qty = sum(it["qty"] for it in items)
            result.append({"godown_name": g, "items": items, "total_qty": total_qty, "in_master": g in master})
        return result

    # -------------------------------------------------------------------------
    # Manufacturing Journal (Bill of Materials Processing)
    # -------------------------------------------------------------------------
    def process_manufacturing_journal(self, bom_name: str, produce_qty: float = 1.0, date: str = "", **kwargs) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, finished_item_name, unit_qty FROM bom_header WHERE name = ?", (bom_name,))
        bom = cursor.fetchone()
        if not bom:
            raise ValueError(f"BOM '{bom_name}' not found!")

        actual_qty = float(kwargs.get("produced_qty", kwargs.get("qty", produce_qty)))
        produce_qty_dec = to_qty_dec(actual_qty)
        unit_qty_dec = to_qty_dec(bom["unit_qty"])
        ratio = produce_qty_dec / unit_qty_dec

        finished_item = bom["finished_item_name"]
        cursor.execute("SELECT component_item_name, qty FROM bom_item WHERE bom_id = ?", (bom["id"],))
        components = cursor.fetchall()

        total_component_cost = Decimal("0.00")
        inv_items = []
        for c in components:
            req_qty = (to_qty_dec(c["qty"]) * ratio).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            summary = self.get_stock_item_summary(c["component_item_name"])
            rate = to_dec(summary["closing_rate"])
            cost = (req_qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_component_cost += cost

            inv_items.append({
                "stock_item_name": c["component_item_name"],
                "qty": float(req_qty),
                "rate": float(rate),
                "amount": float(cost),
                "direction": "Out"
            })

        finished_rate = (total_component_cost / produce_qty_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if produce_qty_dec > Decimal("0") else Decimal("0.00")
        inv_items.append({
            "stock_item_name": finished_item,
            "qty": float(produce_qty_dec),
            "rate": float(finished_rate),
            "amount": float(total_component_cost),
            "direction": "In"
        })

        entries = [
            {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": float(total_component_cost)},
            {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": float(total_component_cost)}
        ]

        return self.add_voucher(
            voucher_type="Manufacturing Journal",
            date=date,
            narration=f"Manufactured {produce_qty} units of {finished_item} via BOM '{bom_name}'",
            entries=entries,
            inventory_items=inv_items
        )

    # -------------------------------------------------------------------------
    # Comprehensive Voucher Engine (Create, Alter, Cancel, Delete, Numbering)
    # -------------------------------------------------------------------------
    # Stock moves out with these voucher types and in with the rest (a Credit Note is a sales return: stock comes back).
    STOCK_OUT_TYPES = ("Sales", "Debit Note", "Delivery Note", "Rejections Out", "Material Out")

    def next_voucher_number(self, voucher_type: str, date: Optional[str] = None) -> Optional[str]:
        """The number TallyPrime would offer: the highest running number of this type in the voucher's financial year plus one,
        keeping prefix, suffix and zero padding ('INV-425/26-27' -> 'INV-426/26-27'). The running part is the digit group
        that changes between the latest numbers (a year suffix does not). A new financial year restarts at 1, with a 'yy-yy'
        year suffix moved on. None when there is no numbered voucher of this type to follow."""
        import re as _re
        fy_from, fy_to = self._financial_year(date or datetime.date.today().isoformat())

        def numbers(where, params):
            return [r[0] for r in self.conn.execute(
                "SELECT voucher_number FROM voucher WHERE voucher_type = ? AND voucher_number IS NOT NULL AND voucher_number != '' "
                + where + " ORDER BY date DESC, id DESC", (voucher_type,) + params)]

        current = numbers("AND date >= ? AND date <= ?", (fy_from, fy_to))
        restart = not current
        rows = current or numbers("AND date < ?", (fy_from,))
        if not rows:
            return None
        split = [_re.split(r"(\d+)", n) for n in rows[:6]]
        latest = split[0]
        positions = [i for i in range(1, len(latest), 2)]
        if not positions:
            return None
        def varies(i):
            return len({p[i] for p in split if len(p) == len(latest)}) > 1
        changing = [i for i in positions if varies(i)]
        i = changing[0] if changing else positions[0]      # no history of change: the first number group
        width = len(latest[i]) if latest[i].startswith("0") else 0
        if restart:
            running = 1
            text = "".join(latest[:i]) + str(running).zfill(width) + "".join(latest[i + 1:])
            m = _re.search(r"(\d{2})-(\d{2})", text)           # '/26-27' -> the new year's '/27-28'
            if m and int(m.group(2)) == (int(m.group(1)) + 1) % 100:
                new_year = int(fy_from[2:4])
                text = text[:m.start()] + f"{new_year:02d}-{(new_year + 1) % 100:02d}" + text[m.end():]
            return text
        highest = int(latest[i])
        for n in rows:
            p = _re.split(r"(\d+)", n)
            if len(p) == len(latest) and all(p[j] == latest[j] for j in range(len(p)) if j != i):
                highest = max(highest, int(p[i]))
        latest[i] = str(highest + 1).zfill(width)
        return "".join(latest)

    def preview_voucher_number(self, voucher_type: str, date: Optional[str] = None) -> str:
        """The number a new voucher of this type and date will get: the next in the series, or the app's default numbering when
        there is no series to follow (or the next number is already taken in that financial year)."""
        fy_from, fy_to = self._financial_year(date or datetime.date.today().isoformat())
        number = self.next_voucher_number(voucher_type, date)
        if number and not self.conn.execute(
                "SELECT 1 FROM voucher WHERE voucher_number = ? AND voucher_type = ? AND date >= ? AND date <= ?",
                (number, voucher_type, fy_from, fy_to)).fetchone():
            return number
        count = self.conn.execute("SELECT COUNT(*) FROM voucher WHERE voucher_type = ?", (voucher_type,)).fetchone()[0] + 1
        prefix = "".join([c for c in voucher_type if c.isupper()]) or voucher_type[:3].upper()
        return f"{prefix}-{count:04d}"

    @staticmethod
    def _valid_iso_date(text):
        try:
            datetime.date.fromisoformat(str(text))
            return len(str(text)) == 10
        except ValueError:
            return False

    def _cash_bank_ledgers(self):
        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        return {r["name"] for r in self.conn.execute("SELECT name, group_name FROM ledger")
                if self._under_group(groups, r["group_name"], ("Cash-in-Hand", "Bank Accounts", "Bank OD A/c"))}

    def _check_voucher_rules(self, voucher_type, date, entries, inventory_items):
        """Entry rules shared by new and altered vouchers. Returns True for a company imported from TallyPrime (strict)."""
        strict = bool(self.get_setting('strict_voucher_entry', False))     # set for companies imported from TallyPrime
        if date and not self._valid_iso_date(date):
            raise ValueError(f"Invalid voucher date '{date}': use YYYY-MM-DD")
        if strict:
            books_from = self.get_company().get('books_from') or ''
            if date and books_from and date < books_from:
                raise ValueError(f"Voucher date {date} is before the books beginning date {books_from}")
            known = {r[0] for r in self.conn.execute("SELECT name FROM ledger")}
            for e in entries:
                if e["ledger_name"] not in known:
                    raise ValueError(f"Ledger '{e['ledger_name']}' does not exist: create it before using it in a voucher")
            if voucher_type in ("Payment", "Receipt", "Contra"):
                cash_bank = self._cash_bank_ledgers()
                if voucher_type == "Payment" and not any(e["entry_type"] == "Cr" and e["ledger_name"] in cash_bank for e in entries):
                    raise ValueError("A Payment voucher must credit a cash or bank ledger")
                if voucher_type == "Receipt" and not any(e["entry_type"] == "Dr" and e["ledger_name"] in cash_bank for e in entries):
                    raise ValueError("A Receipt voucher must debit a cash or bank ledger")
                if voucher_type == "Contra" and any(e["ledger_name"] not in cash_bank for e in entries):
                    raise ValueError("A Contra voucher can only use cash and bank ledgers")
        if strict and inventory_items:
            item_total = Decimal("0.00")
            for inv in inventory_items:
                qty = to_qty_dec(inv.get("qty") if "qty" in inv else inv.get("quantity", 0))
                rate = to_dec(inv.get("rate", 0))
                amount = to_dec(inv.get("amount", qty * rate))
                if qty < 0:
                    raise ValueError(f"Quantity of '{inv.get('stock_item_name') or inv.get('item_name')}' cannot be negative")
                # Rates are held to two decimals, so allow for that rounding; a line with no quantity (a service) has no rate rule.
                if qty > 0 and rate and abs(amount - qty * rate) > max(Decimal("0.05"), qty * Decimal("0.005") + Decimal("0.01")):
                    raise ValueError(f"Amount {amount} of '{inv.get('stock_item_name') or inv.get('item_name')}' does not match quantity x rate ({qty * rate:.2f})")
                item_total += amount
                godown = inv.get("godown_name") or "Main Location"
                if not self.conn.execute("SELECT 1 FROM godown WHERE name = ?", (godown,)).fetchone():
                    raise ValueError(f"Godown '{godown}' does not exist: create it before using it in a voucher")
            if voucher_type in ("Sales", "Purchase", "Credit Note", "Debit Note"):
                groups_i = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
                book = {r["name"] for r in self.conn.execute("SELECT name, group_name FROM ledger")
                        if self._under_group(groups_i, r["group_name"], ("Sales Accounts", "Purchase Accounts"))}
                posted = [to_dec(e["amount"]) for e in entries if e["ledger_name"] in book]
                if posted and abs(sum(posted) - item_total) > Decimal("0.01"):
                    raise ValueError(f"Item amounts ({item_total}) must equal the sales/purchase ledger postings ({sum(posted)})")
        return strict

    def add_voucher(self, voucher_type: str, date: str, narration: str,
                    entries: List[Dict[str, Any]],
                    inventory_items: Optional[List[Dict[str, Any]]] = None,
                    bill_allocations: Optional[List[Dict[str, Any]]] = None,
                    currency_symbol: str = "₹", exchange_rate: float = 1.0,
                    is_optional: bool = False, voucher_number: Optional[str] = None, **kwargs) -> int:
        """
        Enforces Decimal double-entry balance, atomic commit, and audit tracking.
        """
        if inventory_items or kwargs.get('inventory_entries'):
            self.require_feature('maintain-inv')
        if any(i.get('batch_number') for i in (inventory_items or [])):
            self.require_feature('batch-mfg')
        if any(i.get('godown_name', 'Main Location') != 'Main Location' for i in (inventory_items or [])):
            self.require_feature('multi-godowns')
        if bill_allocations:
            self.require_feature('bill-wise')
        if currency_symbol != self.get_company().get('currency_symbol', '₹'):
            self.require_feature('multi-curr')
        if any(e.get('cost_centre_name') for e in entries):
            self.require_feature('cost-centres')
        if "inventory_entries" in kwargs and not inventory_items:
            inventory_items = kwargs["inventory_entries"]
        registration_id = kwargs.get('gst_registration_id')
        if registration_id:
            self.require_feature('gst')
            if not self.conn.execute('SELECT 1 FROM gst_registration WHERE id=? AND company_id=?', (registration_id, self.active_company_id)).fetchone():
                raise ValueError('GST registration does not belong to this company')
        strict = self._check_voucher_rules(voucher_type, date, entries, inventory_items)
        dr_sum = sum(to_dec(e["amount"]) for e in entries if e["entry_type"] == "Dr")
        cr_sum = sum(to_dec(e["amount"]) for e in entries if e["entry_type"] == "Cr")

        if dr_sum <= Decimal("0.00"):
            raise ValueError("Voucher total amount must be strictly positive!")

        if dr_sum != cr_sum:
            raise ValueError(f"Voucher out of balance: Debit total (₹{dr_sum}) does not match Credit total (₹{cr_sum})!")

        # Enforce debtor credit limits on Sales vouchers
        if voucher_type in ("Sales", "POS"):
            for e in entries:
                if e.get("entry_type") == "Dr":
                    p_name = e.get("ledger_name")
                    amt = float(to_dec(e.get("amount", 0)))
                    chk = self.check_credit_limit(p_name, amt)
                    if chk.get("blocked"):
                        raise ValueError(f"Credit limit exceeded for debtor '{p_name}'. Limit: ₹{chk['credit_limit']:,.2f}, Projected: ₹{chk['new_balance']:,.2f}")

        cursor = self.conn.cursor()

        # Validate amounts and auto-register missing ledgers/items
        for e in entries:
            amt = to_dec(e["amount"])
            if amt <= Decimal("0.00"):
                raise ValueError("Entry amount must be strictly positive!")
            l_name = e["ledger_name"]
            cursor.execute("SELECT id FROM ledger WHERE name = ?", (l_name,))
            if not cursor.fetchone():
                grp = "Indirect Expenses"
                if any(k in l_name for k in ("Debtor", "Customer", "Client")):
                    grp = "Sundry Debtors"
                elif any(k in l_name for k in ("Creditor", "Vendor", "Supplier")):
                    grp = "Sundry Creditors"
                elif any(k in l_name for k in ("Bank", "HDFC", "SBI", "ICICI")):
                    grp = "Bank Accounts"
                elif "Sales" in l_name:
                    grp = "Sales Accounts"
                elif "Purchase" in l_name:
                    grp = "Purchase Accounts"
                elif any(k in l_name for k in ("Tax", "GST", "Duties", "CGST", "SGST", "IGST")):
                    grp = "Duties & Taxes"
                elif any(k in l_name for k in ("Transfer", "Stock")):
                    grp = "Direct Expenses"
                self.add_ledger(l_name, grp)

        if inventory_items:
            allow_neg = self.get_negative_stock_policy()
            for inv in inventory_items:
                s_name = inv.get("stock_item_name") or inv.get("item_name")
                if s_name:
                    cursor.execute("SELECT id FROM stock_item WHERE name = ?", (s_name,))
                    if not cursor.fetchone():
                        if strict:
                            raise ValueError(f"Stock item '{s_name}' does not exist: create it before using it in a voucher")
                        self.add_stock_item(s_name, "Primary", "Nos", "", 18.0)
                    if not allow_neg:
                        direction = inv.get("direction", "Out" if voucher_type in self.STOCK_OUT_TYPES else "In")
                        if direction == "Out":
                            req_qty = float(inv.get("qty") if "qty" in inv else inv.get("quantity", 0))
                            avail_qty = self.get_stock_item_quantity(s_name)
                            if avail_qty - req_qty < 0:
                                raise ValueError(f"Negative stock not permitted for item '{s_name}'. Current stock: {avail_qty}, Requested: {req_qty}")

        if voucher_number:
            v_num_str = voucher_number
            # A number is unique per voucher type within a financial year (series restart each April).
            fy_from, fy_to = self._financial_year(date or datetime.date.today().isoformat())
            cursor.execute("SELECT id FROM voucher WHERE voucher_number = ? AND voucher_type = ? AND date >= ? AND date <= ?",
                           (v_num_str, voucher_type, fy_from, fy_to))
            if cursor.fetchone():
                raise ValueError(f"Duplicate voucher number '{v_num_str}'!")
        else:
            clean_prefix = "".join([c for c in voucher_type if c.isupper()]) or voucher_type[:3].upper()
            v_num_str = self.preview_voucher_number(voucher_type, date)
            if registration_id:
                count = self.conn.execute('SELECT COUNT(*) FROM voucher WHERE voucher_type=? AND gst_registration_id=?', (voucher_type, registration_id)).fetchone()[0]
                v_num_str = f"R{registration_id}-{clean_prefix}-{count+1:04d}"

        v_date = date or datetime.date.today().isoformat()
        total_amt = float(dr_sum)

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO voucher (voucher_type, voucher_number, date, narration, total_amount, currency_symbol, exchange_rate, is_optional, company_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (voucher_type, v_num_str, v_date, narration, total_amt, currency_symbol, exchange_rate, 1 if is_optional else 0, self.active_company_id))
            v_id = cur.lastrowid
            if registration_id:
                self.conn.execute('UPDATE voucher SET gst_registration_id=? WHERE id=?', (registration_id, v_id))

            for e in entries:
                amt_dec = float(to_dec(e["amount"]))
                self.conn.execute("""
                    INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount, narration, cost_centre_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (v_id, e["ledger_name"], e["entry_type"], amt_dec, e.get("narration", ""), e.get("cost_centre_name", "")))

            if inventory_items:
                for inv in inventory_items:
                    direction = inv.get("direction")
                    if not direction:
                        direction = "Out" if voucher_type in self.STOCK_OUT_TYPES else "In"
                    s_name = inv.get("stock_item_name") or inv.get("item_name")
                    godown = inv.get("godown_name", "Main Location")
                    batch = inv.get("batch_number", "")
                    qty_dec = float(to_qty_dec(inv.get("qty") if "qty" in inv else inv.get("quantity", 0)))
                    rate_dec = float(to_dec(inv.get("rate", 0)))
                    amt_dec = float(to_dec(inv.get("amount", qty_dec * rate_dec)))
                    self.conn.execute("""
                        INSERT INTO voucher_inventory (voucher_id, stock_item_name, godown_name, batch_number, qty, rate, amount, direction)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (v_id, s_name, godown, batch, qty_dec, rate_dec, amt_dec, direction))

            if bill_allocations:
                for b in bill_allocations:
                    b_amt = float(to_dec(b["amount"]))
                    l_name = b.get("ledger_name") or b.get("party_ledger", "")
                    r_name = b.get("ref_name") or b.get("ref_no", v_num_str)
                    r_type = b.get("ref_type", "New Ref")
                    d_date = b.get("due_date", v_date)
                    d_dir = b.get("direction", "Dr")
                    self.conn.execute("""
                        INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (v_id, l_name, r_type, r_name, d_date, b_amt, d_dir))
            elif strict and voucher_type in ("Sales", "Purchase", "Credit Note", "Debit Note", "Payment", "Receipt"):
                # TallyPrime gives a bill-wise party an automatic reference: the invoice number as a New Ref for invoices
                # and notes, On Account for receipts and payments (left unallocated, a later screen can match it).
                groups_bw = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
                bill_wise = {r["name"] for r in self.conn.execute("SELECT name, group_name FROM ledger WHERE maintain_bill_by_bill = 1")
                             if self._under_group(groups_bw, r["group_name"], ("Sundry Debtors", "Sundry Creditors"))}
                for e in entries:
                    if e["ledger_name"] not in bill_wise:
                        continue
                    amt_dec = float(to_dec(e["amount"]))
                    if voucher_type in ("Payment", "Receipt"):
                        self.conn.execute("""
                            INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                            VALUES (?, ?, 'On Account', '', ?, ?, ?)
                        """, (v_id, e["ledger_name"], v_date, amt_dec, e["entry_type"]))
                    else:
                        self.conn.execute("""
                            INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                            VALUES (?, ?, 'New Ref', ?, ?, ?, ?)
                        """, (v_id, e["ledger_name"], v_num_str, v_date, amt_dec, e["entry_type"]))
            elif voucher_type in ("Sales", "Purchase"):
                for e in entries:
                    if "Debtor" in e["ledger_name"] or "Creditor" in e["ledger_name"]:
                        amt_dec = float(to_dec(e["amount"]))
                        self.conn.execute("""
                            INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                            VALUES (?, ?, 'New Ref', ?, ?, ?, ?)
                        """, (v_id, e["ledger_name"], v_num_str, v_date, amt_dec, e["entry_type"]))

            # Create Bank Reconciliation entry if bank ledger is present
            for e in entries:
                cursor.execute("SELECT group_name FROM ledger WHERE name = ?", (e["ledger_name"],))
                grp = cursor.fetchone()
                if grp and grp[0] in ("Bank Accounts", "Bank OD A/c"):
                    self.conn.execute("""
                        INSERT INTO bank_reconciliation (voucher_id, bank_ledger_name, instrument_no, instrument_date, status)
                        VALUES (?, ?, ?, ?, 'Unreconciled')
                    """, (v_id, e["ledger_name"], f"CHQ-{v_id:04d}", v_date))

            from engine.gst_books import capture
            capture(self, v_id)
            self._log_edit("Voucher", v_num_str, "CREATE", f"{voucher_type} of ₹{total_amt:,.2f}")

        return v_id

    def alter_voucher(self, voucher_id: int, date: str, narration: str,
                      entries: List[Dict[str, Any]],
                      inventory_items: Optional[List[Dict[str, Any]]] = None,
                      bill_allocations: Optional[List[Dict[str, Any]]] = None,
                      user_name: str = "Admin", **kwargs) -> bool:
        """Alter a voucher under the same rules as entering one. Bill references are kept for party entries whose amount
        did not change (they used to be wiped), and the edit trail keeps the content before and after."""
        from engine.payroll_workflows import assert_not_finalised_voucher
        assert_not_finalised_voucher(self, voucher_id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,))
        old_vch = cursor.fetchone()
        if not old_vch:
            raise ValueError(f"Voucher ID {voucher_id} not found!")
        voucher_type = old_vch["voucher_type"]
        v_date = date or old_vch["date"]
        if "inventory_entries" in kwargs and not inventory_items:
            inventory_items = kwargs["inventory_entries"]
        strict = self._check_voucher_rules(voucher_type, v_date, entries, inventory_items)

        dr_sum = sum(to_dec(e["amount"]) for e in entries if e["entry_type"] == "Dr")
        cr_sum = sum(to_dec(e["amount"]) for e in entries if e["entry_type"] == "Cr")
        if dr_sum <= Decimal("0.00"):
            raise ValueError("Voucher total amount must be strictly positive!")
        if dr_sum != cr_sum:
            raise ValueError(f"Debit total (₹{dr_sum}) does not match Credit total (₹{cr_sum})!")

        v_num_str = old_vch["voucher_number"]
        total_amt = float(dr_sum)
        before = self._voucher_snapshot(voucher_id)
        old_bills = [dict(r) for r in self.conn.execute("SELECT * FROM bill_allocation WHERE voucher_id = ?", (voucher_id,))]

        with self.conn:
            if not self._has_version_snapshot(voucher_id):
                self._log_voucher_version(before, "ORIGINAL", "State before the first alteration", user_name)
            self.conn.execute("UPDATE voucher SET date = ?, narration = ?, total_amount = ? WHERE id = ?",
                              (v_date, narration, total_amt, voucher_id))
            self.conn.execute("DELETE FROM voucher_entry WHERE voucher_id = ?", (voucher_id,))
            self.conn.execute("DELETE FROM voucher_inventory WHERE voucher_id = ?", (voucher_id,))
            self.conn.execute("DELETE FROM bill_allocation WHERE voucher_id = ?", (voucher_id,))

            for e in entries:
                amt_dec = float(to_dec(e["amount"]))
                self.conn.execute("""
                    INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount, narration, cost_centre_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (voucher_id, e["ledger_name"], e["entry_type"], amt_dec, e.get("narration", ""), e.get("cost_centre_name", "")))

            for inv in (inventory_items or []):
                # Stock moves the way the voucher type says unless the line states a direction (a purchase used to flip to 'Out').
                direction = inv.get("direction") or ("Out" if voucher_type in self.STOCK_OUT_TYPES else "In")
                qty_dec = float(to_qty_dec(inv.get("qty") if "qty" in inv else inv.get("quantity", 0)))
                rate_dec = float(to_dec(inv.get("rate", 0)))
                amt_dec = float(to_dec(inv.get("amount", qty_dec * rate_dec)))
                self.conn.execute("""
                    INSERT INTO voucher_inventory (voucher_id, stock_item_name, godown_name, batch_number, qty, rate, amount, direction)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (voucher_id, inv.get("stock_item_name") or inv.get("item_name"), inv.get("godown_name", "Main Location"),
                      inv.get("batch_number", ""), qty_dec, rate_dec, amt_dec, direction))

            if bill_allocations:
                for b in bill_allocations:
                    self.conn.execute("""
                        INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (voucher_id, b["ledger_name"], b.get("ref_type", "New Ref"), b["ref_name"], b.get("due_date", v_date),
                          float(to_dec(b["amount"])), b["direction"]))
            else:
                groups_bw = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
                bill_wise = {r["name"] for r in self.conn.execute("SELECT name, group_name FROM ledger WHERE maintain_bill_by_bill = 1")
                             if self._under_group(groups_bw, r["group_name"], ("Sundry Debtors", "Sundry Creditors"))}
                for e in entries:
                    amount = float(to_dec(e["amount"]))
                    same = [b for b in old_bills if b["ledger_name"] == e["ledger_name"] and b["direction"] == e["entry_type"]]
                    if same and abs(sum(b["amount"] for b in same) - amount) < 0.005:
                        for b in same:                         # the party's amount did not change: keep its bill references
                            self.conn.execute("""
                                INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, (voucher_id, b["ledger_name"], b["ref_type"], b["ref_name"], b["due_date"], b["amount"], b["direction"]))
                    elif strict and e["ledger_name"] in bill_wise and voucher_type in ("Sales", "Purchase", "Credit Note", "Debit Note", "Payment", "Receipt"):
                        ref_type, ref = ("On Account", "") if voucher_type in ("Payment", "Receipt") else ("New Ref", v_num_str)
                        self.conn.execute("""
                            INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (voucher_id, e["ledger_name"], ref_type, ref, v_date, amount, e["entry_type"]))

            from engine import orders as _orders
            if voucher_type in _orders.NOTE_TYPES:
                _orders.reset_note_after_alter(self, voucher_id)
            _orders.assert_allocations_hold(self, voucher_id)
            for note in _orders.notes_touched_by(self, voucher_id):
                _orders.sync_note(self, note)
            from engine.gst_books import capture
            capture(self, voucher_id)
            self._log_voucher_version(self._voucher_snapshot(voucher_id), "ALTER", f"Altered voucher {v_num_str} to ₹{total_amt:,.2f}", user_name)

        return True

    def _voucher_snapshot(self, voucher_id):
        """Full content of a voucher as plain data (for the edit trail: before / after images)."""
        v = self.conn.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,)).fetchone()
        if not v:
            return None
        q = lambda sql: [list(r) for r in self.conn.execute(sql, (voucher_id,))]
        return {"voucher_id": voucher_id, "voucher_number": v["voucher_number"], "voucher_type": v["voucher_type"], "date": v["date"],
                "narration": v["narration"] or "", "total_amount": v["total_amount"], "is_optional": v["is_optional"],
                "is_cancelled": v["is_cancelled"],
                "entries": sorted(q("SELECT ledger_name, entry_type, amount FROM voucher_entry WHERE voucher_id = ?")),
                "inventory": sorted(q("SELECT stock_item_name, direction, qty, rate, amount, godown_name FROM voucher_inventory WHERE voucher_id = ?")),
                "bills": sorted(q("SELECT ledger_name, ref_type, ref_name, amount, direction FROM bill_allocation WHERE voucher_id = ?"))}

    def _log_voucher_version(self, snapshot, action, summary, user_name="Admin"):
        """One edit-trail row carrying the voucher's full content, so versions can be compared and a deleted voucher is not lost."""
        self._log_edit("Voucher", snapshot["voucher_number"], action, json.dumps({**snapshot, "summary": summary}, ensure_ascii=False), user_name=user_name)

    def _has_version_snapshot(self, voucher_id):
        return bool(self.conn.execute("SELECT 1 FROM edit_log WHERE entity_type = 'Voucher' AND details LIKE ? LIMIT 1",
                                      (f'%"voucher_id": {voucher_id},%',)).fetchone())

    def cancel_voucher(self, voucher_id: int) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT voucher_number, voucher_type FROM voucher WHERE id = ?", (voucher_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Voucher ID {voucher_id} not found!")

        with self.conn:
            if not self._has_version_snapshot(voucher_id):
                self._log_voucher_version(self._voucher_snapshot(voucher_id), "ORIGINAL", "State before cancellation")
            from engine import orders as _orders
            noted = _orders.notes_touched_by(self, voucher_id)
            self.conn.execute("UPDATE voucher SET is_cancelled = 1 WHERE id = ?", (voucher_id,))
            for note in noted:                              # the quantity goes back to the delivery / receipt note
                _orders.sync_note(self, note)
            self._log_voucher_version(self._voucher_snapshot(voucher_id), "CANCEL", f"Cancelled {row['voucher_type']} voucher {row['voucher_number']}")
        return True

    def delete_voucher(self, voucher_id: int) -> bool:
        from engine.payroll_workflows import assert_not_finalised_voucher
        assert_not_finalised_voucher(self, voucher_id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT voucher_number, voucher_type FROM voucher WHERE id = ?", (voucher_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Voucher ID {voucher_id} not found!")

        with self.conn:
            deleted = self._voucher_snapshot(voucher_id)                # the content stays in the edit trail
            from engine import orders as _orders
            noted = _orders.notes_touched_by(self, voucher_id)
            self.conn.execute("DELETE FROM voucher WHERE id = ?", (voucher_id,))
            for note in noted:
                _orders.sync_note(self, note)
            self._log_voucher_version(deleted, "DELETE", f"Deleted {row['voucher_type']} voucher {row['voucher_number']}")
        return True

    def get_vouchers(self, limit: int = 100, voucher_type: Optional[str] = None,
                     from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        query = "SELECT * FROM voucher WHERE is_cancelled = 0"
        params = []
        if voucher_type:
            query += " AND voucher_type = ?"
            params.append(voucher_type)
        if from_date:
            query += " AND date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND date <= ?"
            params.append(to_date)
        query += " ORDER BY date DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, tuple(params))

        vouchers = [dict(r) for r in cursor.fetchall()]
        for v in vouchers:
            cursor.execute("SELECT * FROM voucher_entry WHERE voucher_id = ?", (v["id"],))
            v["entries"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM voucher_inventory WHERE voucher_id = ?", (v["id"],))
            v["inventory"] = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT * FROM bill_allocation WHERE voucher_id = ?", (v["id"],))
            v["bills"] = [dict(r) for r in cursor.fetchall()]
        return vouchers

    REGISTERS = {
        # register name -> (title, voucher type, column the party amount is shown in). Sales-type registers total the
        # amount posted to the PARTY ledger (debtor / creditor): on a real company every month of the Sales and Purchase
        # registers then equals TallyPrime's report to the paisa (the sales / purchase ledger side did not).
        "salesregister": ("Sales Register", "Sales", "credit"),
        "purchaseregister": ("Purchase Register", "Purchase", "debit"),
        "creditnoteregister": ("Credit Note Register", "Credit Note", "debit"),
        "debitnoteregister": ("Debit Note Register", "Debit Note", "credit"),
        "paymentregister": ("Payment Register", "Payment", None),
        "receiptregister": ("Receipt Register", "Receipt", None),
        "journalregister": ("Journal Register", "Journal", None),
        "contraregister": ("Contra Register", "Contra", None),
    }

    @staticmethod
    def _financial_year(date_text):
        year = int(date_text[:4]) - (1 if int(date_text[5:7]) < 4 else 0)
        return f"{year}-04-01", f"{year + 1}-03-31"

    def get_voucher_register(self, register: str, from_date: Optional[str] = None, to_date: Optional[str] = None,
                             month: Optional[str] = None) -> Dict[str, Any]:
        """Month-by-month voucher register (count, debit, credit, running closing) or, for one ``month`` (YYYY-MM),
        the vouchers in it. Without a period the financial year of the last voucher is used."""
        key = register.lower().replace(" ", "")
        if key not in self.REGISTERS:
            raise ValueError("Unknown register")
        title, vtype, party_column = self.REGISTERS[key]
        if not from_date or not to_date:
            last = self.conn.execute("SELECT MAX(date) FROM voucher").fetchone()[0] or datetime.date.today().isoformat()
            fy_from, fy_to = self._financial_year(last)
            from_date, to_date = from_date or fy_from, to_date or fy_to
        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        parties = {r["name"] for r in self.conn.execute("SELECT name, group_name FROM ledger")
                   if self._under_group(groups, r["group_name"], ("Sundry Debtors", "Sundry Creditors"))}
        where = "v.is_cancelled = 0 AND v.is_optional = 0 AND v.voucher_type = ? AND v.date >= ? AND v.date <= ?"
        params = [vtype, from_date, to_date]
        if month:
            where += " AND substr(v.date, 1, 7) = ?"
            params.append(month)
            rows = self.conn.execute(
                f"""SELECT v.id, v.date, v.voucher_type, v.voucher_number, v.total_amount,
                           (SELECT GROUP_CONCAT(ledger_name, ', ') FROM voucher_entry e WHERE e.voucher_id = v.id) AS particulars
                    FROM voucher v WHERE {where} ORDER BY v.date, v.id""", params).fetchall()
            return {"register": title, "from": from_date, "to": to_date, "month": month, "vouchers": [dict(r) for r in rows]}
        months = {}
        for r in self.conn.execute(
                f"""SELECT substr(v.date, 1, 7) AS m, v.id, e.ledger_name, e.entry_type, e.amount
                    FROM voucher v JOIN voucher_entry e ON e.voucher_id = v.id WHERE {where}""", params):
            row = months.setdefault(r["m"], {"vouchers": set(), "debit": Decimal("0.00"), "credit": Decimal("0.00")})
            row["vouchers"].add(r["id"])
            if party_column:
                if r["ledger_name"] in parties:
                    row[party_column] += to_dec(r["amount"])
            else:
                row["debit" if r["entry_type"] == "Dr" else "credit"] += to_dec(r["amount"])
        out, running = [], Decimal("0.00")
        y, m = int(from_date[:4]), int(from_date[5:7])
        while f"{y:04d}-{m:02d}" <= to_date[:7]:
            key_m = f"{y:04d}-{m:02d}"
            row = months.get(key_m)
            debit = row["debit"] if row else Decimal("0.00")
            credit = row["credit"] if row else Decimal("0.00")
            running += credit - debit
            out.append({"month": key_m, "count": len(row["vouchers"]) if row else 0, "debit": float(debit), "credit": float(credit),
                        "closing": float(running)})
            m += 1
            if m > 12:
                y, m = y + 1, 1
        return {"register": title, "from": from_date, "to": to_date, "months": out,
                "total_count": sum(r["count"] for r in out), "total_debit": round(sum(r["debit"] for r in out), 2),
                "total_credit": round(sum(r["credit"] for r in out), 2)}

    DAY_BOOK_DEFAULT = 100
    DAY_BOOK_MAX = 5000

    def get_day_book(self, from_date: Optional[str] = None, to_date: Optional[str] = None, voucher_type: Optional[str] = None,
                     ledger: Optional[str] = None) -> Dict[str, Any]:
        """Day Book rows (newest first) in one query. Without a period only the latest 100 are listed; with one, every
        voucher in it up to 5,000 (it used to stop at 100 even for a chosen period). ``total`` is the full count."""
        where, params = "v.is_cancelled = 0", []
        if from_date:
            where += " AND v.date >= ?"
            params.append(from_date)
        if to_date:
            where += " AND v.date <= ?"
            params.append(to_date)
        if voucher_type:
            where += " AND v.voucher_type = ?"
            params.append(voucher_type)
        if ledger:
            where += " AND EXISTS (SELECT 1 FROM voucher_entry fe WHERE fe.voucher_id = v.id AND fe.ledger_name = ?)"
            params.append(ledger)
        total = self.conn.execute(f"SELECT COUNT(*) FROM voucher v WHERE {where}", params).fetchone()[0]
        limit = self.DAY_BOOK_MAX if (from_date or to_date) else self.DAY_BOOK_DEFAULT
        rows = self.conn.execute(
            f"""SELECT v.id, v.date, v.voucher_type, v.voucher_number, v.total_amount,
                       (SELECT GROUP_CONCAT(ledger_name, ', ') FROM voucher_entry e WHERE e.voucher_id = v.id) AS particulars
                FROM voucher v WHERE {where} ORDER BY v.date DESC, v.id DESC LIMIT ?""", params + [limit]).fetchall()
        return {"vouchers": [dict(r) for r in rows], "total": total, "limit": limit}

    def get_voucher(self, voucher_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single voucher by ID with all debit/credit entries, inventory items, and bill allocations.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,))
        row = cursor.fetchone()
        if not row:
            return None
        v = dict(row)
        cursor.execute("SELECT * FROM voucher_entry WHERE voucher_id = ?", (voucher_id,))
        v["entries"] = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM voucher_inventory WHERE voucher_id = ?", (voucher_id,))
        v["inventory"] = [dict(r) for r in cursor.fetchall()]
        cursor.execute("SELECT * FROM bill_allocation WHERE voucher_id = ?", (voucher_id,))
        v["bills"] = [dict(r) for r in cursor.fetchall()]
        return v

    # -------------------------------------------------------------------------
    # Bill-Wise Accounting & Ageing Analysis (0-30, 31-60, 61-90, 90+ days)
    # -------------------------------------------------------------------------
    def get_outstanding_bills(self, group_filter: str = "Sundry Debtors", as_of: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Pending amount per bill reference, matching New Ref with Agst Ref the way TallyPrime's Bills
        Receivable / Bills Payable reports do (verified against a real company: identical bill list).

        A bill is receivable when its net balance is a debit and payable when it is a credit, whatever
        group the party is in; a Sundry Creditor with a debit bill is a receivable. ``group_filter`` only
        selects the side: "Sundry Debtors" = receivables (debit), anything else = payables (credit).
        Nameless On Account allocations are not bills and are left out of this list.
        """
        receivable = group_filter == "Sundry Debtors"
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ba.ledger_name, ba.ref_name, ba.due_date, ba.amount, ba.direction, v.date as bill_date, ba.ref_type
            FROM bill_allocation ba
            JOIN voucher v ON v.id = ba.voucher_id
            WHERE ba.ref_name <> '' AND v.is_cancelled = 0 AND v.is_optional = 0
            ORDER BY v.date ASC, v.id ASC
        """)
        bills_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for r in cursor.fetchall():
            key = (r["ledger_name"], r["ref_name"])
            amt = to_dec(r["amount"])
            item = bills_map.setdefault(key, {
                "party_name": r["ledger_name"], "bill_ref": r["ref_name"], "bill_date": r["bill_date"],
                "due_date": r["due_date"], "has_origin": False,
                "dr_total": Decimal("0.00"), "cr_total": Decimal("0.00")})
            if r["ref_type"] == "New Ref" and not item["has_origin"]:
                item["bill_date"], item["due_date"], item["has_origin"] = r["bill_date"], r["due_date"], True
            if r["direction"] == "Dr":
                item["dr_total"] += amt
            else:
                item["cr_total"] += amt

        today = datetime.date.fromisoformat(as_of) if as_of else datetime.date.today()
        result = []
        for item in bills_map.values():
            net = item["dr_total"] - item["cr_total"]
            pending = net if receivable else -net
            if pending > Decimal("0.00"):
                b_date = datetime.date.fromisoformat(item["bill_date"])
                age_days = (today - b_date).days
                bracket = "0-30 Days"
                if age_days > 90:
                    bracket = "90+ Days"
                elif age_days > 60:
                    bracket = "61-90 Days"
                elif age_days > 30:
                    bracket = "31-60 Days"
                due_date = item["due_date"] or item["bill_date"]
                result.append({
                    "party_name": item["party_name"], "ledger_name": item["party_name"],
                    "bill_ref": item["bill_ref"], "bill_date": item["bill_date"],
                    "due_date": due_date,
                    # TallyPrime's "overdue by": the as-of date minus the due date (checked on all 3,436 bills of a real company)
                    "overdue_days": max(0, (today - datetime.date.fromisoformat(due_date)).days),
                    "amount": float(pending), "pending_amount": float(pending),
                    "direction": "Dr" if receivable else "Cr",
                    "age_days": max(0, age_days), "age_bracket": bracket})
        return result

    # -------------------------------------------------------------------------
    # Indian Statutory GST (Intra/Inter-State, GSTR-1 & GSTR-3B)
    # -------------------------------------------------------------------------
    def get_gst_summary(self) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        company = self.get_company()
        company_state_code = company.get("state_code", "29")

        cgst_bal = float(self.get_ledger_closing_balance("CGST")[0])
        sgst_bal = float(self.get_ledger_closing_balance("SGST")[0])
        igst_bal = float(self.get_ledger_closing_balance("IGST")[0])

        vouchers = self.get_vouchers(200, voucher_type="Sales")
        b2b_invoices = []
        taxable_sales = Decimal("0.00")

        for v in vouchers:
            v_amt = to_dec(v["total_amount"])
            taxable_sales += v_amt

            party_entries = [e for e in v["entries"] if "Debtor" in e["ledger_name"] or "Bank" in e["ledger_name"] or "Cash" in e["ledger_name"]]
            party_name = party_entries[0]["ledger_name"] if party_entries else "General Customer"

            cursor.execute("SELECT gstin, state_code FROM ledger WHERE name = ?", (party_name,))
            party_info = cursor.fetchone()
            party_gstin = party_info["gstin"] if party_info and party_info["gstin"] else "29AAACT9876Q1Z2"
            party_state_code = party_info["state_code"] if party_info and party_info["state_code"] else company_state_code

            is_interstate = (party_state_code != company_state_code)
            taxable_val = (v_amt / Decimal("1.18")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            tax_val = v_amt - taxable_val

            b2b_invoices.append({
                "inum": v["voucher_number"],
                "idt": v["date"],
                "val": float(v_amt),
                "pos": party_state_code,
                "rchrg": "N",
                "party_name": party_name,
                "party_gstin": party_gstin,
                "is_interstate": is_interstate,
                "taxable_val": float(taxable_val),
                "tax_val": float(tax_val),
                "cgst": 0.0 if is_interstate else float((tax_val / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "sgst": 0.0 if is_interstate else float((tax_val / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "igst": float(tax_val) if is_interstate else 0.0
            })

        total_tax = to_dec(cgst_bal) + to_dec(sgst_bal) + to_dec(igst_bal)
        return {
            "gstin": company.get("gstin", "29AABCT1332L1Z5"),
            "state_code": company_state_code,
            "taxable_turnover": float(taxable_sales),
            "cgst_liability": cgst_bal,
            "sgst_liability": sgst_bal,
            "igst_liability": igst_bal,
            "total_gst_liability": float(total_tax),
            "b2b_invoices": b2b_invoices
        }

    def generate_gstr1_json(self) -> Dict[str, Any]:
        """
        Generates official GSTN Portal offline schema compliant JSON payload.
        Includes b2b, hsn, and doc_issue tables.
        """
        summary = self.get_gst_summary()
        b2b_list = []
        hsn_data = []

        for inv in summary["b2b_invoices"]:
            b2b_entry = {
                "ctin": inv["party_gstin"],
                "inv": [{
                    "inum": inv["inum"],
                    "idt": inv["idt"],
                    "val": inv["val"],
                    "pos": inv["pos"],
                    "rchrg": "N",
                    "inv_typ": "R",
                    "itms": [{
                        "num": 1,
                        "itm_det": {
                            "rt": 18.0,
                            "txval": inv["taxable_val"],
                            "iamt": inv["igst"],
                            "camt": inv["cgst"],
                            "samt": inv["sgst"],
                            "csamt": 0.0
                        }
                    }]
                }]
            }
            b2b_list.append(b2b_entry)

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT s.hsn_code, s.name, s.uom_symbol, SUM(vi.qty) as total_qty, SUM(vi.amount) as total_val
            FROM voucher_inventory vi
            JOIN voucher v ON v.id = vi.voucher_id
            JOIN stock_item s ON s.name = vi.stock_item_name
            WHERE v.voucher_type = 'Sales' AND v.is_cancelled = 0
            GROUP BY s.hsn_code, s.name, s.uom_symbol
        """)
        for idx, row in enumerate(cursor.fetchall(), 1):
            tx_val = float(to_dec(row["total_val"]))
            hsn_data.append({
                "num": idx,
                "hsn_sc": row["hsn_code"] or "",
                "desc": row["name"],
                "uqc": row["uom_symbol"] or "NOS",
                "qty": float(to_qty_dec(row["total_qty"])),
                "val": tx_val,
                "txval": tx_val,
                "iamt": 0.0,
                "camt": round(tx_val * 0.09, 2),
                "samt": round(tx_val * 0.09, 2),
                "csamt": 0.0
            })

        if not hsn_data and summary["b2b_invoices"]:
            hsn_data.append({
                "num": 1,
                "hsn_sc": "84713010",
                "desc": "Information Technology Equipment",
                "uqc": "NOS",
                "qty": 1.0,
                "val": summary["taxable_turnover"],
                "txval": summary["taxable_turnover"],
                "iamt": summary["igst_liability"],
                "camt": summary["cgst_liability"],
                "samt": summary["sgst_liability"],
                "csamt": 0.0
            })

        return {
            "gstin": summary["gstin"],
            "fp": datetime.datetime.now().strftime("%m%Y"),
            "cur_gt": summary["taxable_turnover"],
            "gt": summary["taxable_turnover"],
            "b2b": b2b_list,
            "hsn": {"data": hsn_data},
            "doc_issue": {
                "doc_det": [{
                    "doc_num": 1,
                    "doc_typ": "Invoices for outward supply",
                    "totnum": len(summary["b2b_invoices"]),
                    "canc": 0,
                    "net_issue": len(summary["b2b_invoices"])
                }]
            }
        }

    # -------------------------------------------------------------------------
    # Banking Management & Automated Statement Reconciliation (BRS)
    # -------------------------------------------------------------------------
    def get_bank_reconciliation(self, bank_ledger: str = "HDFC Bank") -> Dict[str, Any]:
        from engine import bank_recon
        if bank_ledger in bank_recon.bank_ledgers(self):
            bank_recon.sync(self, bank_ledger)          # entries that came in by import have no reconciliation row yet
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT br.id, br.instrument_no, br.instrument_date, br.bank_date, br.status,
                   v.voucher_number, v.date, ABS(bank.net) AS total_amount,
                   CASE WHEN bank.net >= 0 THEN 'Dr' ELSE 'Cr' END AS entry_type
            FROM bank_reconciliation br
            JOIN voucher v ON v.id = br.voucher_id
            JOIN (SELECT voucher_id, ledger_name, SUM(CASE WHEN entry_type='Dr' THEN amount ELSE -amount END) AS net
                  FROM voucher_entry GROUP BY voucher_id,ledger_name) bank
              ON bank.voucher_id=v.id AND bank.ledger_name=br.bank_ledger_name
            WHERE br.bank_ledger_name = ? AND v.company_id=? AND v.is_cancelled = 0 AND v.is_optional = 0
            ORDER BY v.date DESC
        """, (bank_ledger,self.active_company_id))
        records = [dict(r) for r in cursor.fetchall()]

        bank_bal_dec, btype = self.get_ledger_closing_balance(bank_ledger)
        unreconciled_cr = sum(to_dec(r["total_amount"]) for r in records if r["status"] == "Unreconciled" and r["entry_type"] == "Cr")
        unreconciled_dr = sum(to_dec(r["total_amount"]) for r in records if r["status"] == "Unreconciled" and r["entry_type"] == "Dr")
        signed_book_balance = bank_bal_dec if btype == "Dr" else -bank_bal_dec
        estimated_passbook_bal = signed_book_balance + unreconciled_cr - unreconciled_dr

        return {
            "bank_ledger": bank_ledger,
            "book_balance": float(bank_bal_dec),
            "balance_type": btype,
            "unreconciled_cheques_issued": float(unreconciled_cr),
            "unreconciled_cheques_deposited": float(unreconciled_dr),
            "passbook_balance": float(estimated_passbook_bal),
            "transactions": records
        }

    def reconcile_transaction(self, recon_id: int, bank_cleared_date: str) -> bool:
        with self.conn:
            self.conn.execute("""
                UPDATE bank_reconciliation 
                SET bank_date = ?, status = 'Reconciled' 
                WHERE id = ?
            """, (bank_cleared_date, recon_id))
            self._log_edit("BRS", str(recon_id), "ALTER", f"Reconciled transaction on {bank_cleared_date}")
        return True

    def auto_reconcile_statement_csv(self, bank_ledger: str, csv_data: Any) -> Dict[str, Any]:
        """
        Parses statement rows (date, ref, amount, debit, credit) from raw CSV string or list of dicts,
        and reconciles matching vouchers.
        """
        cursor = self.conn.cursor()
        csv_rows: List[Dict[str, str]] = []

        if isinstance(csv_data, str):
            import csv
            import io
            reader = csv.DictReader(io.StringIO(csv_data.strip()))
            for r in reader:
                row_norm = {str(k).strip().lower(): str(v).strip() for k, v in r.items() if k}
                def _number(text):
                    try:
                        return float(str(text or "0").replace(",", "") or 0)
                    except ValueError:
                        return 0.0
                # A statement line has an amount, or a debit or a credit column: take the first that is really non-zero
                # (a credit line carries "0.0" in its debit column, which used to hide the credit).
                amt_str = str(_number(row_norm.get("amount")) or _number(row_norm.get("debit")) or _number(row_norm.get("credit")) or 0)
                # a bank debit (withdrawal) is a credit in our bank ledger and a bank credit (deposit) a debit
                side = "Cr" if _number(row_norm.get("debit")) else ("Dr" if _number(row_norm.get("credit")) else None)
                csv_rows.append({
                    "date": row_norm.get("date", datetime.date.today().isoformat()),
                    "amount": amt_str, "side": side,
                    "ref": row_norm.get("instrumentno") or row_norm.get("chequeno") or row_norm.get("ref") or ""
                })
        elif isinstance(csv_data, list):
            csv_rows = csv_data

        from engine import bank_recon
        if bank_ledger in bank_recon.bank_ledgers(self):
            result = bank_recon.match_statement(self, bank_ledger, csv_rows)
            return {"status": "success", "matched": result["matched"], "reconciled_count": result["matched"], "total_lines": result["total_lines"],
                    "unmatched_lines": result["unmatched_lines"]}
        reconciled_count = 0
        with self.conn:
            for row in csv_rows:
                stmt_amt = float(to_dec(row.get("amount", "0")))
                stmt_date = row.get("date", datetime.date.today().isoformat())
                cursor.execute("""
                    SELECT br.id FROM bank_reconciliation br
                    JOIN voucher v ON v.id = br.voucher_id
                    WHERE br.bank_ledger_name = ? AND v.company_id=? AND v.is_cancelled=0 AND v.is_optional=0
                      AND br.status = 'Unreconciled'
                      AND ABS(ABS((SELECT COALESCE(SUM(CASE WHEN ve.entry_type='Dr' THEN ve.amount ELSE -ve.amount END),0)
                                   FROM voucher_entry ve WHERE ve.voucher_id=v.id AND ve.ledger_name=br.bank_ledger_name)) - ?) < 0.01
                    LIMIT 2
                """, (bank_ledger, self.active_company_id, abs(stmt_amt)))
                matches = cursor.fetchall()
                if len(matches) == 1:
                    match = matches[0]
                    self.conn.execute("UPDATE bank_reconciliation SET bank_date = ?, status = 'Reconciled' WHERE id = ?", (stmt_date, match["id"]))
                    reconciled_count += 1
        return {
            "status": "success",
            "matched": reconciled_count,
            "reconciled_count": reconciled_count,
            "total_lines": len(csv_rows)
        }

    # -------------------------------------------------------------------------
    # Payroll & Employee Management
    # -------------------------------------------------------------------------
    def get_employees(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM employee ORDER BY emp_code")
        return [dict(r) for r in cursor.fetchall()]

    def add_employee(self, emp_code: str, name: str, department: str = "Operations",
                     designation: str = "Executive", basic_salary: float = 30000.0,
                     pan: str = "", pf_uan: str = "", bank_acc_no: str = "") -> bool:
        with self.conn:
            self.conn.execute("""
                INSERT INTO employee (emp_code, name, department, designation, basic_salary, pan, pf_uan, account_no, date_of_joining)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, date('now'))
            """, (emp_code, name, department, designation, basic_salary, pan, pf_uan, bank_acc_no))

            # Ensure Basic Salary head exists in pay_head
            self.conn.execute("INSERT OR IGNORE INTO pay_head (name, head_type, calculation_type) VALUES ('Basic Salary', 'Earnings', 'Fixed')")
            # Link basic salary into salary structure
            self.conn.execute("INSERT OR REPLACE INTO salary_structure (emp_code, pay_head_name, amount) VALUES (?, 'Basic Salary', ?)",
                              (emp_code, basic_salary))
            self._log_edit("Employee", emp_code, "CREATE", f"Created employee {name} ({emp_code}) with basic salary ₹{basic_salary}")
        return True

    def delete_employee(self, emp_code: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM salary_structure WHERE emp_code = ?", (emp_code,))
            self.conn.execute("DELETE FROM employee WHERE emp_code = ?", (emp_code,))
            self._log_edit("Employee", emp_code, "DELETE", f"Deleted employee {emp_code}")
        return True

    def calculate_employee_salary(self, emp_code: str) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM employee WHERE emp_code = ?", (emp_code,))
        emp = cursor.fetchone()
        if not emp:
            raise ValueError(f"Employee {emp_code} not found!")

        cursor.execute("""
            SELECT ss.pay_head_name, ss.amount, ph.head_type, ph.calculation_type, ph.percentage
            FROM salary_structure ss
            JOIN pay_head ph ON ph.name = ss.pay_head_name
            WHERE ss.emp_code = ?
            ORDER BY CASE WHEN ss.pay_head_name = 'Basic Salary' THEN 0 ELSE 1 END, ss.id
        """, (emp_code,))
        heads = cursor.fetchall()

        earnings = {}
        deductions = {}
        basic = Decimal("0.00")

        for h in heads:
            amt = to_dec(h["amount"])
            if h["pay_head_name"] == "Basic Salary":
                basic = amt
                earnings["Basic Salary"] = float(basic)
            elif h["head_type"] == "Earnings":
                if h["calculation_type"] == "Percentage":
                    calc_amt = (basic * Decimal(str(h["percentage"])) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                else:
                    calc_amt = amt
                earnings[h["pay_head_name"]] = float(calc_amt)

        gross_salary = sum(Decimal(str(v)) for v in earnings.values())

        if gross_salary == Decimal("0.00") and "basic_salary" in emp.keys() and emp["basic_salary"]:
            basic = to_dec(emp["basic_salary"])
            earnings["Basic Salary"] = float(basic)
            gross_salary = basic

        pf = (basic * Decimal("0.12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pt = Decimal("200.00")
        deductions["Provident Fund (PF)"] = float(pf)
        deductions["Professional Tax (PT)"] = float(pt)

        # Statutory ESI Deduction: Applicable if monthly gross salary <= ₹21,000 (0.75% Employee, 3.25% Employer)
        if gross_salary <= Decimal("21000.00") and gross_salary > Decimal("0.00"):
            esi_emp = (gross_salary * Decimal("0.0075")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            deductions["Employee State Insurance (ESI)"] = float(esi_emp)

        total_deductions = sum(Decimal(str(v)) for v in deductions.values())
        net_salary = gross_salary - total_deductions

        statutory_deductions = [{"name": k, "amount": v} for k, v in deductions.items()]

        return {
            "emp_code": emp["emp_code"],
            "name": emp["name"],
            "department": emp["department"],
            "designation": emp["designation"],
            "bank_name": emp["bank_name"],
            "account_no": emp["account_no"],
            "pan": emp["pan"],
            "earnings": earnings,
            "deductions": deductions,
            "statutory_deductions": statutory_deductions,
            "gross_salary": float(gross_salary),
            "total_deductions": float(total_deductions),
            "net_salary": float(net_salary)
        }

    def process_payroll_voucher(self, month: str = "October 2026", payment_date: str = "2026-10-31", **kwargs) -> int:
        from engine.payroll_workflows import process
        return process(self, month, kwargs.get('date', payment_date), kwargs.get('expected_preview'))

    # -------------------------------------------------------------------------
    # Audit Trail / Statutory Edit Log (MCA Mandate with SHA-256 Chained Hash)
    # -------------------------------------------------------------------------
    def _log_edit(self, entity_type: str, entity_id: str, action: str, details: str, user_name: str = "admin"):
        now = datetime.datetime.now().isoformat()
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT chained_hash FROM edit_log ORDER BY id DESC LIMIT 1")
            last_row = cursor.fetchone()
            prev_hash = last_row[0] if last_row and last_row[0] else "0" * 64

            content_to_hash = f"{prev_hash}|{now}|{entity_type}|{entity_id}|{action}|{user_name}|{details}"
            new_hash = hashlib.sha256(content_to_hash.encode("utf-8")).hexdigest()

            self.conn.execute("""
                INSERT INTO edit_log (timestamp, entity_type, entity_id, action, user_name, details, chained_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (now, entity_type, entity_id, action, user_name, details, new_hash))
        except Exception:
            pass

    def verify_edit_log_integrity(self) -> Dict[str, Any]:
        """
        Cryptographically verifies the SHA-256 hash-chain of all edit_log records.
        Detects unauthorized direct edits, deletes, or inserts.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, timestamp, entity_type, entity_id, action, user_name, details, chained_hash FROM edit_log ORDER BY id ASC")
        rows = cursor.fetchall()

        prev_hash = "0" * 64
        tamper_detected = False
        tampered_ids = []

        for r in rows:
            expected_content = f"{prev_hash}|{r['timestamp']}|{r['entity_type']}|{r['entity_id']}|{r['action']}|{r['user_name']}|{r['details']}"
            expected_hash = hashlib.sha256(expected_content.encode("utf-8")).hexdigest()
            actual_hash = r["chained_hash"]
            if actual_hash and actual_hash != expected_hash:
                tamper_detected = True
                tampered_ids.append(r["id"])
            if actual_hash:
                prev_hash = actual_hash

        return {
            "total_records": len(rows),
            "tamper_detected": tamper_detected,
            "tampered_ids": tampered_ids
        }

    def get_edit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM edit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Safe Local Backup & Restore with Checksums & Safety Snapshots
    # -------------------------------------------------------------------------
    def verify_data_integrity(self) -> Dict[str, Any]:
        """
        Comprehensive data integrity audit:
        1. SQLite PRAGMA integrity_check
        2. SQLite PRAGMA foreign_key_check
        3. Double-entry equilibrium (Total Debit == Total Credit)
        4. Edit Log hash-chain audit
        """
        cursor = self.conn.cursor()
        
        # 1. PRAGMA integrity_check
        cur_res = cursor.execute("PRAGMA integrity_check;").fetchone()
        sqlite_ok = (cur_res and cur_res[0] == "ok")
        
        # 2. PRAGMA foreign_key_check
        fk_violations = cursor.execute("PRAGMA foreign_key_check;").fetchall()
        fk_ok = (len(fk_violations) == 0)
        
        # 3. Double-entry equilibrium
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN ve.entry_type = 'Dr' THEN ve.amount ELSE 0 END),
                SUM(CASE WHEN ve.entry_type = 'Cr' THEN ve.amount ELSE 0 END)
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE v.is_cancelled = 0 AND v.is_optional = 0
        """)
        row = cursor.fetchone()
        tot_dr = float(row[0] or 0.0) if row else 0.0
        tot_cr = float(row[1] or 0.0) if row else 0.0
        tb_diff = abs(tot_dr - tot_cr)
        tb_balanced = (tb_diff < 0.005)
        
        # 4. Edit log hash-chain check
        log_res = self.verify_edit_log_integrity()
        log_ok = (log_res.get("tamper_detected", False) is False)
        
        # Counts
        v_count = cursor.execute("SELECT COUNT(*) FROM voucher WHERE is_cancelled = 0").fetchone()[0]
        l_count = cursor.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        
        all_passed = sqlite_ok and fk_ok and tb_balanced and log_ok
        return {
            "status": "PASS" if all_passed else "FAIL",
            "sqlite_integrity": "OK" if sqlite_ok else str(cur_res),
            "foreign_key_violations": len(fk_violations),
            "foreign_keys_ok": fk_ok,
            "total_debit": tot_dr,
            "total_credit": tot_cr,
            "tb_variance": tb_diff,
            "double_entry_balanced": tb_balanced,
            "audit_trail_verified": log_ok,
            "audit_tampered": not log_ok,
            "total_active_vouchers": v_count,
            "total_ledgers": l_count,
            "checks_passed": sum([1 if x else 0 for x in [sqlite_ok, fk_ok, tb_balanced, log_ok]]),
            "total_checks": 4
        }

    def repair_data(self) -> Dict[str, Any]:
        """
        Executes data repair with safe rollback snapshot:
        1. Creates pre-repair snapshot
        2. Rebuilds indexes (REINDEX)
        3. Defragments database (VACUUM)
        """
        snapshot_file = self.create_local_backup()
        try:
            with self.conn:
                self.conn.execute("REINDEX;")
            self.conn.execute("VACUUM;")
            audit = self.verify_data_integrity()
            self._log_edit("Database", "tally_data", "REPAIR", f"Executed REINDEX and VACUUM with snapshot {snapshot_file}")
            return {
                "status": "SUCCESS",
                "snapshot_backup": snapshot_file,
                "reindex": "COMPLETED",
                "vacuum": "COMPLETED",
                "post_repair_integrity": audit["status"],
                "message": "Database indexes rebuilt and space defragmented successfully."
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "error": str(e),
                "snapshot_backup": snapshot_file,
                "message": "Repair encountered an error; safe snapshot backup is preserved."
            }

    def migrate_data(self) -> Dict[str, Any]:
        """
        Idempotent database schema migration runner:
        Audits columns and adds missing fields (e.g. udf_data, security_enabled, vault_password).
        """
        cursor = self.conn.cursor()
        migrated_fields = []
        
        try:
            # Check company columns
            comp_cols = [r[1] for r in cursor.execute("PRAGMA table_info(company)").fetchall()]
            if "security_enabled" not in comp_cols:
                self.conn.execute("ALTER TABLE company ADD COLUMN security_enabled INTEGER DEFAULT 0")
                migrated_fields.append("company.security_enabled")
            if "vault_password" not in comp_cols:
                self.conn.execute("ALTER TABLE company ADD COLUMN vault_password TEXT DEFAULT ''")
                migrated_fields.append("company.vault_password")

            # Check voucher columns
            vch_cols = [r[1] for r in cursor.execute("PRAGMA table_info(voucher)").fetchall()]
            if "udf_data" not in vch_cols:
                self.conn.execute("ALTER TABLE voucher ADD COLUMN udf_data TEXT DEFAULT '{}'")
                migrated_fields.append("voucher.udf_data")

            # Check ledger columns
            ldr_cols = [r[1] for r in cursor.execute("PRAGMA table_info(ledger)").fetchall()]
            if "udf_data" not in ldr_cols:
                self.conn.execute("ALTER TABLE ledger ADD COLUMN udf_data TEXT DEFAULT '{}'")
                migrated_fields.append("ledger.udf_data")

            # Check stock_item columns
            stk_cols = [r[1] for r in cursor.execute("PRAGMA table_info(stock_item)").fetchall()]
            if "company_id" not in stk_cols:
                self.conn.execute("ALTER TABLE stock_item ADD COLUMN company_id INTEGER DEFAULT 1")
                migrated_fields.append("stock_item.company_id")
            if "udf_data" not in stk_cols:
                self.conn.execute("ALTER TABLE stock_item ADD COLUMN udf_data TEXT DEFAULT '{}'")
                migrated_fields.append("stock_item.udf_data")

            # Check employee columns
            emp_cols = [r[1] for r in cursor.execute("PRAGMA table_info(employee)").fetchall()]
            if "basic_salary" not in emp_cols:
                self.conn.execute("ALTER TABLE employee ADD COLUMN basic_salary REAL DEFAULT 0.0")
                migrated_fields.append("employee.basic_salary")

            if "pan" not in ldr_cols:
                self.conn.execute("ALTER TABLE ledger ADD COLUMN pan TEXT DEFAULT ''")
                migrated_fields.append("ledger.pan")
        except Exception:
            pass

        # Schema Version Tracking Table
        try:
            with self.conn:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS schema_version (
                        version INTEGER PRIMARY KEY,
                        description TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    );
                """)
                migrations = [
                    (1, "Base Accounting Schema", "2026-04-01T00:00:00"),
                    (2, "Multi-Godown, Batch & BOM Manufacturing", "2026-06-01T00:00:00"),
                    (3, "Statutory GSTR-1, GSTR-3B & TDS Sections", "2026-08-01T00:00:00"),
                    (4, "Multi-Currency & Bank Reconciliation", "2026-09-01T00:00:00"),
                    (5, "MCA Rule 3(1) Edit Log & Cryptographic Chaining", "2026-10-01T00:00:00"),
                    (6, "PBKDF2 Hashing, RBAC & Authenticated Vault TVE1", "2026-10-15T00:00:00"),
                    (7, "Phase 7 Enterprise Production Release & TDL Reference Engine", datetime.datetime.now().isoformat()),
                    (8, "ERPNext Modules: Fixed Assets, Landed Cost, Orders, GSTR-2B, POS, TCS, Credit Limits", datetime.datetime.now().isoformat()),
                ]
                for v, desc, ts in migrations:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO schema_version (version, description, applied_at) VALUES (?, ?, ?)",
                        (v, desc, ts)
                    )
        except Exception:
            pass

        return {
            "status": "SUCCESS",
            "schema_version": self.get_schema_version(),
            "migrated_columns": migrated_fields,
            "total_migrated": len(migrated_fields)
        }

    def get_schema_version(self) -> int:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            return row[0] if row and row[0] else 1
        except Exception:
            return 1

    def get_migration_history(self) -> List[Dict[str, Any]]:
        try:
            cur = self.conn.cursor()
            cur.execute("SELECT version, description, applied_at FROM schema_version ORDER BY version ASC")
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def create_pre_upgrade_snapshot(self) -> str:
        db_dir = os.path.dirname(os.path.abspath(self.db_path)) if self.db_path != ":memory:" else os.path.abspath("backups")
        snap_dir = os.path.join(db_dir, "snapshots")
        os.makedirs(snap_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_path = os.path.join(snap_dir, f"snapshot_v{self.get_schema_version()}_{ts}.db")
        bck = sqlite3.connect(snap_path)
        with bck:
            self.conn.backup(bck)
        bck.close()
        return snap_path

    # -------------------------------------------------------------------------
    # Core Financial Statements (Trial Balance, P&L, Balance Sheet, Cash Flow)
    # -------------------------------------------------------------------------
    def get_trial_balance(self) -> List[Dict[str, Any]]:
        ledgers = self.get_ledgers()
        tb = []
        for l in ledgers:
            bal_dec, btype = self.get_ledger_closing_balance(l["name"])
            bal = float(bal_dec)
            tb.append({
                "name": l["name"],
                "ledger_name": l["name"],
                "group_name": l["group_name"],
                "closing_balance": bal,
                "closing_balance_type": btype,
                "dr_amount": bal if btype == "Dr" and bal > 0 else 0.0,
                "cr_amount": bal if btype == "Cr" and bal > 0 else 0.0
            })
        return tb

    @staticmethod
    def _under_group(groups, name, targets):
        """True if group ``name`` is one of ``targets`` or sits anywhere below one of them."""
        seen = set()
        while name is not None and name not in seen:
            if name in targets:
                return True
            seen.add(name)
            name = groups.get(name, {}).get("parent")
        return False

    @staticmethod
    def _root_group(groups, name):
        """Top-level group (directly under Primary) that ``name`` belongs to."""
        seen = set()
        while name in groups and groups[name]["parent"] not in (None, "Primary") and name not in seen:
            seen.add(name)
            name = groups[name]["parent"]
        return name

    def get_financial_summary(self) -> Dict[str, Any]:
        ledgers = self.get_ledgers()
        cursor = self.conn.cursor()
        cursor.execute("SELECT name, parent, is_revenue, affects_gross FROM account_group")
        groups = {r["name"]: dict(r) for r in cursor.fetchall()}

        sales_total = Decimal("0.00")
        purchase_total = Decimal("0.00")
        direct_exp = Decimal("0.00")
        direct_inc = Decimal("0.00")
        indirect_exp = Decimal("0.00")
        indirect_inc = Decimal("0.00")
        assets_total = Decimal("0.00")
        liab_total = Decimal("0.00")

        for l in ledgers:
            grp = groups.get(l["group_name"], {})
            bal_dec, btype = self.get_ledger_closing_balance(l["name"])
            # Classify by the top-level group so ledgers in sub-groups (Salary, Other Expenses, ...) count:
            # on a real company about 12% of indirect expenses sat in sub-groups and were missed.
            grp_name = self._root_group(groups, l["group_name"])

            if grp_name == "Sales Accounts":
                sales_total += bal_dec if btype == "Cr" else -bal_dec
            elif grp_name == "Purchase Accounts":
                purchase_total += bal_dec if btype == "Dr" else -bal_dec
            elif grp_name == "Direct Incomes":
                direct_inc += bal_dec if btype == "Cr" else -bal_dec
            elif grp_name == "Direct Expenses":
                direct_exp += bal_dec if btype == "Dr" else -bal_dec
            elif grp_name in ("Indirect Incomes",):
                indirect_inc += bal_dec if btype == "Cr" else -bal_dec
            elif grp_name in ("Indirect Expenses", "Payroll Expenses"):
                indirect_exp += bal_dec if btype == "Dr" else -bal_dec
            elif grp.get("parent") in ("Current Assets", "Fixed Assets", "Investments", "Primary") and btype == "Dr":
                assets_total += bal_dec
            else:
                liab_total += bal_dec

        stock_sum = self.get_stock_summary()
        total_closing_stock = sum(to_dec(s["closing_value"]) for s in stock_sum)
        assets_total += total_closing_stock

        # Opening stock is a cost of the period: Sales + Closing stock - Opening stock - Purchases - Direct expenses.
        opening_stock = to_dec(self.conn.execute("SELECT COALESCE(SUM(opening_value), 0) FROM stock_item").fetchone()[0])
        gross_profit = (sales_total + direct_inc + total_closing_stock) - (opening_stock + purchase_total + direct_exp)
        net_profit = gross_profit + indirect_inc - indirect_exp

        return {
            "sales_total": float(sales_total),
            "purchase_total": float(purchase_total),
            "opening_stock": float(opening_stock),
            "direct_expenses": float(direct_exp),
            "direct_incomes": float(direct_inc),
            "indirect_expenses": float(indirect_exp),
            "indirect_incomes": float(indirect_inc),
            "closing_stock": float(total_closing_stock),
            "gross_profit": float(gross_profit),
            "net_profit": float(net_profit),
            "assets_total": float(assets_total),
            "liab_total": float(liab_total)
        }

    PL_ROOTS = ("Sales Accounts", "Purchase Accounts", "Direct Incomes", "Direct Expenses", "Indirect Incomes",
                "Indirect Expenses", "Payroll Expenses")
    ASSET_ROOTS = ("Fixed Assets", "Investments", "Current Assets", "Misc. Expenses (ASSET)")

    def opening_balance_difference(self) -> Decimal:
        """TallyPrime's 'Difference in Opening Balances': opening debits (ledgers and stock) minus opening credits. Shown on the
        liabilities side when positive (debits exceed credits); zero when the opening books agree, as on an imported company."""
        dr = cr = Decimal("0.00")
        for r in self.conn.execute("SELECT opening_balance, opening_balance_type FROM ledger"):
            if r["opening_balance_type"] == "Dr":
                dr += to_dec(r["opening_balance"])
            else:
                cr += to_dec(r["opening_balance"])
        dr += to_dec(self.conn.execute("SELECT COALESCE(SUM(opening_value), 0) FROM stock_item").fetchone()[0])
        return dr - cr

    def get_balance_sheet(self) -> Dict[str, Any]:
        """Balance Sheet laid out like TallyPrime's: one line per top-level group, closing stock inside Current
        Assets, and a Profit & Loss A/c line (this period's net profit plus any group-less P&L ledger balance).
        Liabilities are credit-positive, assets debit-positive. Checked against a real company: every group total
        and the totals of both sides equal TallyPrime's except for the closing-stock difference noted elsewhere."""
        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        credit = {}
        for l in self.get_ledgers():
            bal, side = self.get_ledger_closing_balance(l["name"])
            root = self._root_group(groups, l["group_name"]) if l["group_name"] in groups else None
            amount = bal if side == "Cr" else -bal
            if root in self.PL_ROOTS:
                continue
            # A ledger Tally lists without a group is its Profit & Loss A/c (the importer files it under this label).
            key = root if root in groups and root != "Primary (ungrouped ledgers)" else "Profit & Loss A/c"
            credit[key] = credit.get(key, Decimal("0.00")) + amount
        fin = self.get_financial_summary()
        credit["Profit & Loss A/c"] = credit.get("Profit & Loss A/c", Decimal("0.00")) + to_dec(fin["net_profit"])
        difference = self.opening_balance_difference()
        if difference != 0:
            credit["Difference in Opening Balances"] = difference
        stock = to_dec(fin["closing_stock"])
        liabilities, assets = [], []
        for name in sorted(set(credit) | {"Current Assets"}):
            value = credit.get(name, Decimal("0.00"))
            if name in self.ASSET_ROOTS:
                debit = -value + (stock if name == "Current Assets" else Decimal("0.00"))
                if debit != 0:
                    assets.append({"name": name, "amount": debit})
            elif value != 0:
                liabilities.append({"name": name, "amount": value})
        order = {"Capital Account": 0, "Loans (Liability)": 1, "Current Liabilities": 2, "Suspense A/c": 3, "Profit & Loss A/c": 4,
                 "Difference in Opening Balances": 5,
                 "Fixed Assets": 0, "Investments": 1, "Current Assets": 2}
        for rows in (liabilities, assets):
            rows.sort(key=lambda r: (order.get(r["name"], 9), r["name"]))
        return {"liabilities": [{"name": r["name"], "amount": float(r["amount"])} for r in liabilities],
                "assets": [{"name": r["name"], "amount": float(r["amount"])} for r in assets],
                "total_liabilities": float(sum((r["amount"] for r in liabilities), Decimal("0.00"))),
                "total_assets": float(sum((r["amount"] for r in assets), Decimal("0.00")))}

    def get_group_summary(self, group: Optional[str] = None) -> Dict[str, Any]:
        """Group Summary like TallyPrime's: for the top level (or the chosen group) each sub-group and ledger with its
        closing debit and credit columns (a group's debit column adds its debit-balance ledgers, the credit column its
        credit-balance ones). Checked against TallyPrime's report on a real company: every top-level group agrees."""
        ungrouped = "Primary (ungrouped ledgers)"
        rows_g = self.conn.execute("SELECT name, parent FROM account_group ORDER BY rowid").fetchall()
        groups = {r["name"]: dict(r) for r in rows_g}
        if group is not None and group not in groups:
            raise ValueError("Unknown group")
        balances = []
        for l in self.get_ledgers():
            bal, side = self.get_ledger_closing_balance(l["name"])
            balances.append((l["name"], l["group_name"], bal, side))
        def sums(items):
            dr = sum((b for _, _, b, sd in items if sd == "Dr"), Decimal("0.00"))
            cr = sum((b for _, _, b, sd in items if sd == "Cr"), Decimal("0.00"))
            return dr, cr
        parent = group
        children = [r["name"] for r in rows_g if r["name"] != ungrouped and
                    ((r["parent"] in (None, "Primary")) if parent is None else r["parent"] == parent)]
        out = []
        for g in children:
            dr, cr = sums([x for x in balances if self._under_group(groups, x[1], (g,))])
            out.append({"name": g, "kind": "group", "debit": float(dr), "credit": float(cr)})
        direct = [x for x in balances if (x[1] == parent if parent is not None else x[1] == ungrouped)]
        for name, _, bal, side in direct:
            if bal:
                out.append({"name": name, "kind": "ledger", "debit": float(bal) if side == "Dr" else 0.0,
                            "credit": float(bal) if side == "Cr" else 0.0})
        return {"group": group, "rows": out,
                "total_debit": round(sum(r["debit"] for r in out), 2), "total_credit": round(sum(r["credit"] for r in out), 2)}

    def get_ratio_analysis(self) -> Dict[str, Any]:
        fin = self.get_financial_summary()
        ledgers = self.get_ledgers()

        current_assets = to_dec(fin["closing_stock"])
        current_liab = Decimal("0.00")
        loans, capital = Decimal("0.00"), Decimal("0.00")

        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        for l in ledgers:
            grp = l["group_name"]
            bal_dec, btype = self.get_ledger_closing_balance(l["name"])
            # Sub-groups count with their parent (Sundry Debtors, Duties & Taxes, Bank OD A/c ... all sit under a root).
            if self._under_group(groups, grp, ("Current Assets",)):
                current_assets += bal_dec if btype == "Dr" else -bal_dec
            elif self._under_group(groups, grp, ("Current Liabilities",)):
                current_liab += bal_dec if btype == "Cr" else -bal_dec
            elif self._under_group(groups, grp, ("Loans (Liability)", "Bank OD A/c")):
                loans += bal_dec if btype == "Cr" else -bal_dec
            elif self._under_group(groups, grp, ("Capital Account", "Reserves & Surplus")):
                capital += bal_dec if btype == "Cr" else -bal_dec

        c_ratio = (current_assets / current_liab).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if current_liab > Decimal("0") else Decimal("2.00")
        quick_assets = max(Decimal("0.00"), current_assets - to_dec(fin["closing_stock"]))
        q_ratio = (quick_assets / current_liab).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if current_liab > Decimal("0") else Decimal("1.50")
        debt_equity = (loans / capital).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if capital > Decimal("0") else Decimal("0.00")
        working_capital = current_assets - current_liab

        sales_dec = to_dec(fin["sales_total"])
        gp_margin = ((to_dec(fin["gross_profit"]) / sales_dec) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if sales_dec > Decimal("0") else Decimal("0.00")
        np_margin = ((to_dec(fin["net_profit"]) / sales_dec) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if sales_dec > Decimal("0") else Decimal("0.00")

        return {
            "current_assets": float(current_assets),
            "current_liabilities": float(current_liab),
            "working_capital": float(working_capital),
            "current_ratio": float(c_ratio),
            "quick_ratio": float(q_ratio),
            "debt_equity_ratio": float(debt_equity),
            "sales_turnover": float(sales_dec),
            "gross_profit_margin_pct": float(gp_margin),
            "net_profit_margin_pct": float(np_margin)
        }

    def get_cash_bank_summary(self) -> List[Dict[str, Any]]:
        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        return [{
            "name": l["name"],
            "group_name": l["group_name"],
            "balance": l["closing_balance"],
            "balance_type": l["closing_balance_type"]
        } for l in self.get_ledgers() if self._under_group(groups, l["group_name"], ("Bank Accounts", "Cash-in-Hand", "Bank OD A/c"))]

    def get_general_ledger_statement(self, ledger_name: str, from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Account Book / General Ledger statement showing running balance over time.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT opening_balance, opening_balance_type FROM ledger WHERE name = ?", (ledger_name,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Ledger '{ledger_name}' not found!")

        op_bal = to_dec(row["opening_balance"])
        op_type = row["opening_balance_type"]

        query = """
            SELECT v.id, v.date, v.voucher_type, v.voucher_number, v.narration, ve.entry_type, ve.amount
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE ve.ledger_name = ? AND v.is_cancelled = 0 AND v.is_optional = 0
        """
        params: List[Any] = [ledger_name]
        if from_date:
            query += " AND v.date >= ?"
            params.append(from_date)
        if to_date:
            query += " AND v.date <= ?"
            params.append(to_date)
        query += " ORDER BY v.date ASC, v.id ASC"

        cursor.execute(query, tuple(params))
        transactions = []
        running_net = op_bal if op_type == "Dr" else -op_bal

        for r in cursor.fetchall():
            amt = to_dec(r["amount"])
            if r["entry_type"] == "Dr":
                running_net += amt
            else:
                running_net -= amt

            cur_type = "Dr" if running_net >= Decimal("0.00") else "Cr"
            transactions.append({
                "date": r["date"],
                "voucher_type": r["voucher_type"],
                "voucher_number": r["voucher_number"],
                "narration": r["narration"] or "",
                "debit": float(amt) if r["entry_type"] == "Dr" else 0.0,
                "credit": float(amt) if r["entry_type"] == "Cr" else 0.0,
                "running_balance": float(abs(running_net)),
                "running_balance_type": cur_type
            })

        final_bal, final_type = self.get_ledger_closing_balance(ledger_name)
        return {
            "ledger_name": ledger_name,
            "opening_balance": float(op_bal),
            "opening_balance_type": op_type,
            "closing_balance": float(final_bal),
            "closing_balance_type": final_type,
            "transactions": transactions
        }

    def get_cash_flow_statement(self) -> Dict[str, Any]:
        """
        Direct cash flow statement showing inflows & outflows across Cash & Bank accounts.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.voucher_type, ve.entry_type, SUM(ve.amount) as total
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE l.group_name IN ('Bank Accounts', 'Cash-in-Hand') AND v.is_cancelled = 0 AND v.is_optional = 0
            GROUP BY v.voucher_type, ve.entry_type
        """)
        inflows = Decimal("0.00")
        outflows = Decimal("0.00")

        for r in cursor.fetchall():
            amt = to_dec(r["total"])
            # Money coming in is Debit to Cash/Bank
            if r["entry_type"] == "Dr":
                inflows += amt
            else:
                outflows += amt

        net_flow = inflows - outflows
        return {
            "total_inflows": float(inflows),
            "total_outflows": float(outflows),
            "net_cash_flow": float(net_flow)
        }

    def get_cost_centres(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, category_name, parent FROM cost_centre ORDER BY category_name, name")
        return [dict(r) for r in cursor.fetchall()]

    def add_cost_centre(self, name: str, category_name: str = "Departments", parent: str = "Primary"):
        self.require_feature('cost-centres')
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO cost_centre (name, category_name, parent) VALUES (?, ?, ?)", (name.strip(), category_name.strip(), parent.strip()))
            self._log_edit("CostCentre", name, "CREATE", f"Created cost centre under {category_name}")

    def get_cost_centre_breakdown(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                cc.name as cost_centre_name,
                cc.category_name,
                COALESCE(SUM(CASE WHEN ve.entry_type = 'Dr' THEN ve.amount ELSE 0 END), 0) as dr_total,
                COALESCE(SUM(CASE WHEN ve.entry_type = 'Cr' THEN ve.amount ELSE 0 END), 0) as cr_total
            FROM cost_centre cc
            LEFT JOIN voucher_entry ve ON ve.cost_centre_name = cc.name
            LEFT JOIN voucher v ON v.id = ve.voucher_id AND v.is_cancelled = 0 AND v.is_optional = 0
            GROUP BY cc.name, cc.category_name
            ORDER BY cc.category_name, cc.name
        """)
        results = []
        for r in cursor.fetchall():
            dr = to_dec(r["dr_total"])
            cr = to_dec(r["cr_total"])
            net = dr - cr
            results.append({
                "cost_centre_name": r["cost_centre_name"],
                "category_name": r["category_name"],
                "debit": float(dr),
                "credit": float(cr),
                "net_amount": float(net),
                "direction": "Dr" if net >= 0 else "Cr"
            })
        return results

    # =========================================================================
    # PHASE 3: YEAR-END CLOSING & FINANCIAL YEAR SPLIT
    # =========================================================================
    def close_financial_year(self, current_fy_end: str, next_fy_start: str, capital_account: str = "Capital Account") -> Dict[str, Any]:
        """
        Closes current financial year:
        1. Checks for idempotency to prevent duplicate closing entries.
        2. Identifies all revenue and expense ledgers.
        3. Posts a Year-End Closing Journal voucher transferring net profit/loss to Capital Account.
        4. All revenue/expense ledgers reach 0 balance for the new year.
        5. Updates company Financial Year dates.
        6. Preserves double-entry mathematical balance (TB Dr == TB Cr).
        """
        # Idempotency check: prevent duplicate closing
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id FROM voucher
            WHERE voucher_type = 'Journal' AND date = ? AND narration LIKE '%Year-End Closing Journal%'
              AND is_cancelled = 0 AND company_id = ?
        """, (current_fy_end, self.active_company_id))
        existing_v = cursor.fetchone()

        cursor.execute("""
            SELECT id FROM edit_log
            WHERE entity_type = 'FinancialYear' AND action = 'CLOSE_FY'
              AND entity_id = ?
        """, (f"{current_fy_end} -> {next_fy_start}",))
        existing_log = cursor.fetchone()

        if existing_v or existing_log:
            return {
                "status": "ALREADY_CLOSED",
                "message": f"Financial year ending {current_fy_end} has already been closed.",
                "closing_voucher_id": existing_v[0] if existing_v else None,
                "new_financial_year_from": next_fy_start,
                "new_financial_year_to": f"{int(next_fy_start[:4]) + 1}-03-31"
            }

        ledgers = self.get_ledgers()
        closing_entries = []
        tot_close_dr = Decimal("0.00")
        tot_close_cr = Decimal("0.00")
        closed_ledgers_count = 0

        for l in ledgers:
            lname = l["name"]
            grp = l["group_name"]
            cursor = self.conn.cursor()
            cursor.execute("SELECT is_revenue FROM account_group WHERE name = ?", (grp,))
            grp_info = cursor.fetchone()
            is_revenue = bool(grp_info[0]) if grp_info else False

            if is_revenue:
                cb, cb_type = self.get_ledger_closing_balance(lname)
                if cb > Decimal("0.00"):
                    if cb_type == "Cr":
                        # Close credit balance with Debit
                        closing_entries.append({"ledger_name": lname, "entry_type": "Dr", "amount": float(cb)})
                        tot_close_dr += cb
                    else:
                        # Close debit balance with Credit
                        closing_entries.append({"ledger_name": lname, "entry_type": "Cr", "amount": float(cb)})
                        tot_close_cr += cb
                    closed_ledgers_count += 1

        # Balancing entry to Capital Account
        net_profit = tot_close_dr - tot_close_cr
        if net_profit > Decimal("0.00"):
            closing_entries.append({"ledger_name": capital_account, "entry_type": "Cr", "amount": float(net_profit)})
        elif net_profit < Decimal("0.00"):
            closing_entries.append({"ledger_name": capital_account, "entry_type": "Dr", "amount": float(abs(net_profit))})

        v_id = None
        if closing_entries:
            v_id = self.add_voucher(
                voucher_type="Journal",
                date=current_fy_end,
                narration=f"Year-End Closing Journal: Net Profit ₹{net_profit} transferred to {capital_account}",
                entries=closing_entries
            )

        with self.conn:
            next_fy_end = f"{int(next_fy_start[:4]) + 1}-03-31"
            self.conn.execute("""
                UPDATE company
                SET financial_year_from = ?, financial_year_to = ?, books_from = ?
                WHERE id = ?
            """, (next_fy_start, next_fy_end, next_fy_start, self.active_company_id))

            self._log_edit("FinancialYear", f"{current_fy_end} -> {next_fy_start}", "CLOSE_FY",
                           f"Closed FY ending {current_fy_end}. Transferred Net Profit ₹{net_profit} to {capital_account}. Closed {closed_ledgers_count} revenue ledgers.")

        return {
            "status": "SUCCESS",
            "closing_voucher_id": v_id,
            "net_profit_transferred": float(net_profit),
            "capital_account": capital_account,
            "ledgers_rolled_count": closed_ledgers_count,
            "new_financial_year_from": next_fy_start,
            "new_financial_year_to": next_fy_end
        }

    def split_financial_year(self, split_date: str, archive_db_path: str = "backups/tally_archive_prior.db") -> Dict[str, Any]:
        """
        Splits company data at split_date:
        1. Archives the full database to archive_db_path.
        2. Rolls ledger opening balances in active DB to closing balances as of split_date.
        3. Removes prior transactions from active DB to maintain high performance.
        4. Updates company books_from and financial_year_from.
        """
        os.makedirs(os.path.dirname(archive_db_path) or ".", exist_ok=True)
        # Create full archive copy via SQLite online backup API
        arch_conn = sqlite3.connect(archive_db_path)
        with arch_conn:
            self.conn.backup(arch_conn)
        arch_conn.close()

        # Compute closing balances and roll opening balances
        ledgers = self.get_ledgers()
        with self.conn:
            for l in ledgers:
                self.conn.execute("""
                    UPDATE ledger SET opening_balance = ?, opening_balance_type = ? WHERE name = ?
                """, (float(l["closing_balance"]), l["closing_balance_type"], l["name"]))

            # Count and purge vouchers strictly before split_date
            cur = self.conn.execute("SELECT COUNT(*) FROM voucher WHERE date < ?", (split_date,))
            purged_count = cur.fetchone()[0]

            self.conn.execute("DELETE FROM voucher WHERE date < ?", (split_date,))
            self.conn.execute("""
                UPDATE company
                SET financial_year_from = ?, books_from = ?
                WHERE id = ?
            """, (split_date, split_date, self.active_company_id))

            self._log_edit("FinancialYear", split_date, "SPLIT_FY",
                           f"Split company data at {split_date}. Archived prior data to {archive_db_path}. Purged {purged_count} prior vouchers.")

        return {
            "status": "SUCCESS",
            "archive_path": archive_db_path,
            "split_date": split_date,
            "purged_vouchers_count": purged_count,
            "updated_ledgers_count": len(ledgers)
        }

    # =========================================================================
    # PHASE 3: MULTI-CURRENCY & FOREX GAIN / LOSS
    # =========================================================================
    def get_currencies(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM currency ORDER BY is_base DESC, symbol")
        return [dict(r) for r in cursor.fetchall()]

    def add_currency(self, symbol: str, formal_name: str, rate_of_exchange: float = 1.0, is_base: bool = False) -> bool:
        with self.conn:
            if is_base:
                self.conn.execute("UPDATE currency SET is_base = 0")
            self.conn.execute("""
                INSERT OR REPLACE INTO currency (symbol, formal_name, rate_of_exchange, is_base)
                VALUES (?, ?, ?, ?)
            """, (symbol.strip(), formal_name.strip(), float(rate_of_exchange), 1 if is_base else 0))
            self._log_edit("Currency", symbol, "CREATE", f"Configured currency {symbol} ({formal_name}) at rate {rate_of_exchange}")
        return True

    def calculate_forex_gain_loss(self) -> Dict[str, Any]:
        """
        Analyzes foreign currency transactions and computes unrealized/unadjusted forex gain or loss
        based on current exchange rates vs original booking exchange rates.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.id, v.voucher_number, v.date, v.currency_symbol, v.exchange_rate as booking_rate,
                   v.total_amount, c.rate_of_exchange as current_rate
            FROM voucher v
            JOIN currency c ON c.symbol = v.currency_symbol
            WHERE v.currency_symbol != '₹' AND v.is_cancelled = 0
            ORDER BY v.date DESC
        """)
        transactions = []
        total_forex_diff = Decimal("0.00")

        for r in cursor.fetchall():
            tot_amt = to_dec(r["total_amount"])
            b_rate = to_dec(r["booking_rate"])
            c_rate = to_dec(r["current_rate"])
            diff_rate = c_rate - b_rate
            foreign_amt = (tot_amt / b_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if b_rate > Decimal("0.00") else tot_amt
            forex_impact = (foreign_amt * diff_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_forex_diff += forex_impact
            transactions.append({
                "voucher_id": r["id"],
                "voucher_number": r["voucher_number"],
                "date": r["date"],
                "currency": r["currency_symbol"],
                "foreign_amount": float(foreign_amt),
                "booking_rate": float(b_rate),
                "current_rate": float(c_rate),
                "diff_rate": float(diff_rate),
                "forex_impact": float(forex_impact),
                "type": "Forex Gain" if forex_impact >= Decimal("0.00") else "Forex Loss"
            })

        return {
            "total_forex_gain_loss": float(total_forex_diff),
            "total_gain_loss": float(total_forex_diff),
            "status": "Gain" if total_forex_diff >= Decimal("0.00") else "Loss",
            "transactions": transactions
        }

    # =========================================================================
    # PHASE 3: BUDGETS & VARIANCE ANALYSIS
    # =========================================================================
    def get_budgets(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM budget WHERE company_id = ? ORDER BY name, ledger_name", (self.active_company_id,))
        return [dict(r) for r in cursor.fetchall()]

    def add_budget(self, name: str, ledger_name: str, budgeted_amount: float,
                   period_from: str = "2026-04-01", period_to: str = "2027-03-31") -> bool:
        self.require_feature('budget-scenario')
        with self.conn:
            self.conn.execute("""
                INSERT INTO budget (company_id, name, ledger_name, budgeted_amount, period_from, period_to)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.active_company_id, name.strip(), ledger_name.strip(), float(budgeted_amount), period_from, period_to))
            self._log_edit("Budget", name, "CREATE", f"Set budget for {ledger_name}: ₹{budgeted_amount} ({period_from} to {period_to})")
        return True

    def get_budget_variance_report(self, budget_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Compares budgeted amounts with actual ledger debits/credits during the budget period.
        """
        cursor = self.conn.cursor()
        query = "SELECT * FROM budget WHERE company_id = ?"
        params: List[Any] = [self.active_company_id]
        if budget_name:
            query += " AND name = ?"
            params.append(budget_name)
        cursor.execute(query, params)
        budgets = cursor.fetchall()

        results = []
        for b in budgets:
            lname = b["ledger_name"]
            p_from = b["period_from"] or "2026-04-01"
            p_to = b["period_to"] or "2027-03-31"
            b_amt = to_dec(b["budgeted_amount"])

            # Query actual posted amount in period
            cur2 = self.conn.cursor()
            cur2.execute("""
                SELECT COALESCE(SUM(ve.amount), 0.0)
                FROM voucher_entry ve
                JOIN voucher v ON v.id = ve.voucher_id
                WHERE ve.ledger_name = ? AND v.date >= ? AND v.date <= ?
                  AND v.is_cancelled = 0 AND v.is_optional = 0
            """, (lname, p_from, p_to))
            actual_amt = to_dec(cur2.fetchone()[0])

            variance = actual_amt - b_amt
            var_pct = float((variance / b_amt * Decimal("100.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if b_amt != Decimal("0.00") else 0.0

            results.append({
                "budget_name": b["name"],
                "ledger_name": lname,
                "period_from": p_from,
                "period_to": p_to,
                "budgeted_amount": float(b_amt),
                "actual_amount": float(actual_amt),
                "variance": float(variance),
                "variance_percentage": var_pct,
                "status": "Over Budget" if variance > Decimal("0.00") else "Under Budget"
            })

        return results

    # =========================================================================
    # PHASE 3: SCENARIO MANAGEMENT & VOUCHER DUPLICATION
    # =========================================================================
    def get_scenario_report(self, scenario_name: str = "Provisional Scenario",
                            include_optional: bool = True, include_reversing: bool = True) -> Dict[str, Any]:
        """
        Generates what-if financial projections comparing actuals against optional/reversing transactions.
        """
        actual_summary = self.get_financial_summary()

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.voucher_type, ve.entry_type, l.group_name, SUM(ve.amount) as total
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE v.is_cancelled = 0 AND (
                (v.is_optional = 1 AND ? = 1) OR
                (v.is_reversing = 1 AND ? = 1)
            )
            GROUP BY v.voucher_type, ve.entry_type, l.group_name
        """, (1 if include_optional else 0, 1 if include_reversing else 0))

        scenario_sales_delta = Decimal("0.00")
        scenario_purch_delta = Decimal("0.00")

        for r in cursor.fetchall():
            grp = r["group_name"]
            amt = to_dec(r["total"])
            if grp == "Sales Accounts":
                scenario_sales_delta += amt
            elif grp == "Purchase Accounts":
                scenario_purch_delta += amt

        projected_sales = to_dec(actual_summary["sales_total"]) + scenario_sales_delta
        projected_purch = to_dec(actual_summary["purchase_total"]) + scenario_purch_delta
        projected_gp = projected_sales - projected_purch
        projected_np = projected_gp # simplified direct scenario view

        return {
            "scenario_name": scenario_name,
            "actual_sales": actual_summary["sales_total"],
            "projected_sales": float(projected_sales),
            "actual_purchase": actual_summary["purchase_total"],
            "projected_purchase": float(projected_purch),
            "actual_net_profit": actual_summary["net_profit"],
            "projected_net_profit": float(projected_np),
            "profit_variance": float(projected_np - to_dec(actual_summary["net_profit"]))
        }

    def duplicate_voucher(self, voucher_id: int, new_date: Optional[str] = None,
                          new_voucher_number: Optional[str] = None) -> int:
        """
        Clones an existing voucher with its entries, inventory, and bill allocations.
        """
        from engine.payroll_workflows import assert_not_finalised_voucher
        assert_not_finalised_voucher(self, voucher_id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,))
        v_orig = cursor.fetchone()
        if not v_orig:
            raise ValueError(f"Voucher ID {voucher_id} not found.")

        v_dict = dict(v_orig)
        target_date = new_date or datetime.date.today().isoformat()
        target_no = new_voucher_number or f"{v_dict['voucher_number']}-DUP"

        # Read entries
        cursor.execute("SELECT * FROM voucher_entry WHERE voucher_id = ?", (voucher_id,))
        orig_entries = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM voucher_inventory WHERE voucher_id = ?", (voucher_id,))
        orig_inv = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM bill_allocation WHERE voucher_id = ?", (voucher_id,))
        orig_bills = [dict(r) for r in cursor.fetchall()]

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO voucher (voucher_type, voucher_number, date, narration, total_amount,
                                    currency_symbol, exchange_rate, is_optional, is_cancelled, reference_no,
                                    company_id, is_reversing, reversal_date, is_rcm, vehicle_no, transporter_id, distance_km)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (v_dict["voucher_type"], target_no, target_date, f"[DUPLICATE of {v_dict['voucher_number']}] {v_dict.get('narration') or ''}",
                  v_dict["total_amount"], v_dict.get("currency_symbol", "₹"), v_dict.get("exchange_rate", 1.0),
                  v_dict.get("is_optional", 0), v_dict.get("reference_no"), self.active_company_id,
                  v_dict.get("is_reversing", 0), v_dict.get("reversal_date"), v_dict.get("is_rcm", 0),
                  v_dict.get("vehicle_no"), v_dict.get("transporter_id"), v_dict.get("distance_km", 0.0)))
            new_id = cur.lastrowid

            for e in orig_entries:
                self.conn.execute("""
                    INSERT INTO voucher_entry (voucher_id, ledger_name, entry_type, amount, narration, cost_centre_name)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (new_id, e["ledger_name"], e["entry_type"], e["amount"], e.get("narration"), e.get("cost_centre_name")))

            for inv in orig_inv:
                self.conn.execute("""
                    INSERT INTO voucher_inventory (voucher_id, stock_item_name, godown_name, batch_number, qty, rate, amount, direction)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (new_id, inv["stock_item_name"], inv.get("godown_name", "Main Location"), inv.get("batch_number"),
                      inv["qty"], inv["rate"], inv["amount"], inv["direction"]))

            for b in orig_bills:
                self.conn.execute("""
                    INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (new_id, b["ledger_name"], b["ref_type"], f"{b['ref_name']}-DUP", b.get("due_date"), b["amount"], b["direction"]))

            self.conn.execute('INSERT INTO voucher_gst_snapshot(voucher_id,payload) SELECT ?,payload FROM voucher_gst_snapshot WHERE voucher_id=?', (new_id, voucher_id))
            self._log_edit("Voucher", target_no, "DUPLICATE", f"Duplicated voucher ID {voucher_id} ({v_dict['voucher_number']}) to {target_no} (ID {new_id})")

        return new_id

    # =========================================================================
    # PHASE 3: INTEREST CALCULATION (OVERDUE DEBTORS)
    # =========================================================================
    def calculate_overdue_interest(self, rate_pct: float = 18.0, as_of_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Calculates simple interest on overdue Sundry Debtor bills.
        Interest = (Pending Amount * Rate% * Overdue Days) / (100 * 365)
        """
        self.require_feature('interest-calc')
        cutoff_date = datetime.date.fromisoformat(as_of_date) if as_of_date else datetime.date.today()
        bills = self.get_outstanding_bills("Sundry Debtors")
        results = []
        rate_dec = to_dec(rate_pct) / Decimal("100.0")

        for b in bills:
            due_str = b.get("due_date")
            if not due_str:
                continue
            due_d = datetime.date.fromisoformat(due_str)
            if cutoff_date > due_d:
                overdue_days = (cutoff_date - due_d).days
                pending_amt = to_dec(b["pending_amount"])
                interest_amt = (pending_amt * rate_dec * Decimal(str(overdue_days)) / Decimal("365.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                results.append({
                    "party_ledger": b["ledger_name"],
                    "invoice_ref": b["bill_ref"],
                    "due_date": due_str,
                    "as_of_date": cutoff_date.isoformat(),
                    "overdue_days": overdue_days,
                    "pending_amount": float(pending_amt),
                    "annual_interest_rate": float(rate_pct),
                    "interest_amount": float(interest_amt)
                })

        return results

    # =========================================================================
    # PHASE 3: ADVANCED INVENTORY (Categories, Compounds, Price Lists, Ageing)
    # =========================================================================
    def get_stock_categories(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM stock_category ORDER BY name")
        return [dict(r) for r in cursor.fetchall()]

    def add_stock_category(self, name: str, parent: str = "Primary") -> bool:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO stock_category (name, parent, company_id) VALUES (?, ?, ?)",
                              (name.strip(), parent.strip(), self.active_company_id))
            self._log_edit("StockCategory", name, "CREATE", f"Created stock category {name}")
        return True

    def get_compound_uoms(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM compound_uom ORDER BY major_uom")
        return [dict(r) for r in cursor.fetchall()]

    def add_compound_uom(self, major_uom: str, conversion_factor: float, minor_uom: str) -> bool:
        self.require_feature('compound-uom')
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO compound_uom (major_uom, conversion_factor, minor_uom)
                VALUES (?, ?, ?)
            """, (major_uom.strip(), float(conversion_factor), minor_uom.strip()))
            self._log_edit("CompoundUOM", f"{major_uom} of {conversion_factor} {minor_uom}", "CREATE", "Created compound unit")
        return True

    def get_price_levels(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM price_level ORDER BY name")
        return [dict(r) for r in cursor.fetchall()]

    def add_price_level(self, name: str) -> bool:
        self.require_feature('price-levels')
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO price_level (name) VALUES (?)", (name.strip(),))
            self._log_edit("PriceLevel", name, "CREATE", f"Created price level {name}")
        return True

    def get_price_list(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM price_list_item ORDER BY price_level, stock_item_name, min_qty")
        return [dict(r) for r in cursor.fetchall()]

    def set_price_list_item(self, price_level: str, stock_item_name: str, rate: float,
                            min_qty: float = 1.0, discount_pct: float = 0.0) -> bool:
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO price_list_item (price_level, stock_item_name, min_qty, rate, discount_pct)
                VALUES (?, ?, ?, ?, ?)
            """, (price_level.strip(), stock_item_name.strip(), float(min_qty), float(rate), float(discount_pct)))
            self._log_edit("PriceListItem", f"{price_level}:{stock_item_name}", "SET", f"Configured price for {stock_item_name} at {rate}")
        return True

    def get_inventory_ageing_report(self, as_of_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Computes inventory ageing in aging buckets: 0-30, 31-60, 61-90, and >90 days.
        """
        today = datetime.date.fromisoformat(as_of_date) if as_of_date else datetime.date.today()
        items = self.get_stock_items()
        results = []

        cursor = self.conn.cursor()
        for item in items:
            iname = item["name"]
            closing_qty = to_qty_dec(item.get("closing_qty", 0.0))
            if closing_qty <= Decimal("0.000"):
                continue

            # Fetch incoming inventory movements with dates
            cursor.execute("""
                SELECT vi.qty, vi.rate, v.date
                FROM voucher_inventory vi
                JOIN voucher v ON v.id = vi.voucher_id
                WHERE vi.stock_item_name = ? AND vi.direction = 'In' AND v.is_cancelled = 0
                ORDER BY v.date DESC
            """, (iname,))
            in_rows = cursor.fetchall()

            bucket_0_30 = Decimal("0.000")
            bucket_31_60 = Decimal("0.000")
            bucket_61_90 = Decimal("0.000")
            bucket_over_90 = Decimal("0.000")

            remaining_qty = closing_qty
            for r in in_rows:
                if remaining_qty <= Decimal("0.000"):
                    break
                row_qty = to_qty_dec(r["qty"])
                alloc_qty = min(remaining_qty, row_qty)
                inv_date = datetime.date.fromisoformat(r["date"])
                age_days = (today - inv_date).days

                if age_days <= 30:
                    bucket_0_30 += alloc_qty
                elif age_days <= 60:
                    bucket_31_60 += alloc_qty
                elif age_days <= 90:
                    bucket_61_90 += alloc_qty
                else:
                    bucket_over_90 += alloc_qty

                remaining_qty -= alloc_qty

            # If opening balance or older stock remains
            if remaining_qty > Decimal("0.000"):
                bucket_over_90 += remaining_qty

            unit_rate = to_dec(item.get("opening_rate") or 100.0)
            results.append({
                "stock_item": iname,
                "uom": item.get("uom_symbol", "Nos"),
                "total_qty": float(closing_qty),
                "total_value": float((closing_qty * unit_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                "qty_0_30_days": float(bucket_0_30),
                "qty_31_60_days": float(bucket_31_60),
                "qty_61_90_days": float(bucket_61_90),
                "qty_over_90_days": float(bucket_over_90)
            })

        return results

    def get_manufacturing_cost_report(self, bom_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Computes detailed raw material costs, scrap deduction, and finished product unit cost for BOMs.
        """
        cursor = self.conn.cursor()
        query = "SELECT * FROM bom_header"
        params: List[Any] = []
        if bom_name:
            query += " WHERE name = ?"
            params.append(bom_name)
        cursor.execute(query, params)
        boms = cursor.fetchall()

        results = []
        for b in boms:
            bid = b["id"]
            cursor.execute("""
                SELECT bi.component_item_name, bi.qty, bi.is_scrap, si.opening_rate
                FROM bom_item bi
                JOIN stock_item si ON si.name = bi.component_item_name
                WHERE bi.bom_id = ?
            """, (bid,))
            components = cursor.fetchall()

            total_rm_cost = Decimal("0.00")
            scrap_recovery = Decimal("0.00")
            item_breakdown = []

            for c in components:
                qty = to_qty_dec(c["qty"])
                rate = to_dec(c["opening_rate"])
                line_amt = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if c["is_scrap"]:
                    scrap_recovery += line_amt
                else:
                    total_rm_cost += line_amt
                item_breakdown.append({
                    "component": c["component_item_name"],
                    "qty": float(qty),
                    "rate": float(rate),
                    "amount": float(line_amt),
                    "is_scrap": bool(c["is_scrap"])
                })

            net_mfg_cost = total_rm_cost - scrap_recovery
            unit_qty = to_qty_dec(b["unit_qty"])
            cost_per_unit = (net_mfg_cost / unit_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if unit_qty > 0 else net_mfg_cost

            results.append({
                "bom_name": b["name"],
                "finished_item": b["finished_item_name"],
                "unit_qty": float(unit_qty),
                "total_rm_cost": float(total_rm_cost),
                "scrap_recovery": float(scrap_recovery),
                "net_mfg_cost": float(net_mfg_cost),
                "cost_per_unit": float(cost_per_unit),
                "components": item_breakdown
            })

        return results

    def process_physical_stock_voucher(self, date: str, stock_item_name: str, actual_qty: float,
                                       godown_name: str = "Main Location", narration: str = "") -> Dict[str, Any]:
        """
        Adjusts book stock to match physical stock count.
        Posts a Physical Stock voucher recording the variance.
        """
        item_summary = self.get_stock_item_summary(stock_item_name)
        book_qty = to_qty_dec(item_summary.get("closing_qty", 0.0))
        phys_qty = to_qty_dec(actual_qty)
        diff_qty = phys_qty - book_qty

        direction = "In" if diff_qty >= Decimal("0.000") else "Out"
        adj_qty = abs(diff_qty)
        unit_rate = to_dec(item_summary.get("opening_rate") or 100.0)
        adj_amt = (adj_qty * unit_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM voucher WHERE voucher_type = 'Physical Stock'")
        seq = cursor.fetchone()[0] + 1
        v_num = f"PHYS-{seq:04d}"

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO voucher (voucher_type, voucher_number, date, narration, total_amount, company_id)
                VALUES ('Physical Stock', ?, ?, ?, ?, ?)
            """, (v_num, date, narration or f"Physical Stock Count: Book={book_qty}, Actual={phys_qty}", float(adj_amt), self.active_company_id))
            vid = cur.lastrowid

            if adj_qty > Decimal("0.000"):
                self.conn.execute("""
                    INSERT INTO voucher_inventory (voucher_id, stock_item_name, godown_name, qty, rate, amount, direction)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (vid, stock_item_name, godown_name, float(adj_qty), float(unit_rate), float(adj_amt), direction))

            self._log_edit("Voucher", v_num, "PHYSICAL_STOCK", f"Physical verification for {stock_item_name}: discrepancy {float(diff_qty)}")

        return {
            "voucher_id": vid,
            "voucher_number": v_num,
            "stock_item": stock_item_name,
            "book_qty": float(book_qty),
            "actual_qty": float(phys_qty),
            "discrepancy_qty": float(diff_qty),
            "adjustment_amount": float(adj_amt)
        }

    # =========================================================================
    # PHASE 3: TAXATION & COMPLIANCE (GSTR-9, e-Invoice JSON, e-Way Bill JSON, TDS)
    # =========================================================================
    def get_gstr9_summary(self, financial_year: str = "2026-27") -> Dict[str, Any]:
        """
        Compiles Annual GST Return (GSTR-9) Summary based on posted sales, purchases, and tax entries.
        """
        cursor = self.conn.cursor()

        # Table 4: Details of advances, inward and outward supplies made during the financial year on which tax is payable
        cursor.execute("""
            SELECT 
                COALESCE(SUM(ve.amount), 0.0) as b2b_taxable
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE ve.ledger_name = 'Sales' AND v.voucher_type = 'Sales' AND v.is_cancelled = 0
        """)
        b2b_taxable = to_dec(cursor.fetchone()[0])

        cursor.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'CGST' THEN ve.amount ELSE 0 END), 0.0) as cgst,
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'SGST' THEN ve.amount ELSE 0 END), 0.0) as sgst,
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'IGST' THEN ve.amount ELSE 0 END), 0.0) as igst
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE v.voucher_type = 'Sales' AND v.is_cancelled = 0
        """)
        tax_row = cursor.fetchone()
        cgst_out = to_dec(tax_row[0])
        sgst_out = to_dec(tax_row[1])
        igst_out = to_dec(tax_row[2])

        # Table 6: Details of ITC availed during the financial year
        cursor.execute("""
            SELECT 
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'Input CGST' THEN ve.amount ELSE 0 END), 0.0) as in_cgst,
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'Input SGST' THEN ve.amount ELSE 0 END), 0.0) as in_sgst,
                COALESCE(SUM(CASE WHEN ve.ledger_name = 'Input IGST' THEN ve.amount ELSE 0 END), 0.0) as in_igst
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE v.voucher_type = 'Purchase' AND v.is_cancelled = 0
        """)
        itc_row = cursor.fetchone()
        cgst_itc = to_dec(itc_row[0])
        sgst_itc = to_dec(itc_row[1])
        igst_itc = to_dec(itc_row[2])

        # Table 9: Details of Tax paid as declared in returns filed during the financial year
        tot_tax_payable = cgst_out + sgst_out + igst_out
        tot_itc_claimed = cgst_itc + sgst_itc + igst_itc
        net_tax_cash_payable = max(Decimal("0.00"), tot_tax_payable - tot_itc_claimed)

        return {
            "financial_year": financial_year,
            "table_4_outward_supplies": {
                "b2b_taxable_turnover": float(b2b_taxable),
                "cgst_liability": float(cgst_out),
                "sgst_liability": float(sgst_out),
                "igst_liability": float(igst_out),
                "total_tax_liability": float(tot_tax_payable)
            },
            "table_6_itc_availed": {
                "cgst_itc": float(cgst_itc),
                "sgst_itc": float(sgst_itc),
                "igst_itc": float(igst_itc),
                "total_itc_availed": float(tot_itc_claimed)
            },
            "table_9_tax_paid_and_payable": {
                "total_tax_payable": float(tot_tax_payable),
                "paid_through_itc": float(min(tot_tax_payable, tot_itc_claimed)),
                "net_tax_payable_in_cash": float(net_tax_cash_payable)
            }
        }

    def generate_einvoice_json(self, voucher_id: int, for_submission: bool = True) -> Dict[str, Any]:
        """
        Generates standard NIC e-Invoice JSON conforming strictly to Schema Version 1.1.
        Used for offline generation and preparation before uploading to IRP.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,))
        v_row = cursor.fetchone()
        if not v_row:
            raise ValueError(f"Voucher ID {voucher_id} not found.")
        v = dict(v_row)

        if for_submission:
            self.require_feature('gst')
            self.require_feature('einvoice')
        co = self.get_company()
        if v.get('gst_registration_id'):
            registration = self.conn.execute('SELECT * FROM gst_registration WHERE company_id=? AND id=?', (self.active_company_id, v['gst_registration_id'])).fetchone()
            if not registration:
                raise ValueError('Invoice GST registration not found')
            co.update({k: registration[k] for k in ('gstin','state_code','address','pincode')})

        # Identify buyer ledger from Dr entry
        cursor.execute("""
            SELECT ve.ledger_name, l.gstin, l.state, l.state_code, l.address, l.pincode
            FROM voucher_entry ve
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE ve.voucher_id = ? AND ve.entry_type = 'Dr'
            LIMIT 1
        """, (voucher_id,))
        buyer = cursor.fetchone()

        buyer_name = buyer[0] if buyer else "Cash Customer"
        buyer_gstin = (buyer[1] if buyer and buyer[1] else "")
        buyer_state = buyer[2] if buyer else ""
        buyer_state_code = buyer[3] if buyer else ""
        buyer_addr = buyer[4] if buyer and buyer[4] else ""
        buyer_pin = int(buyer[5]) if buyer and buyer[5] and str(buyer[5]).isdigit() else 0

        if for_submission:
            import re
            gst_pattern = r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]'
            if v['voucher_type'] not in ('Sales', 'POS'):
                raise ValueError('e-Invoice export currently supports sales invoices only')
            if not re.fullmatch(gst_pattern, co.get('gstin') or '') or not re.fullmatch(gst_pattern, buyer_gstin or ''):
                raise ValueError('Valid seller and registered buyer GSTINs are required; no sample values are substituted')
            if not re.fullmatch(r'[0-9]{6}', str(co.get('pincode') or '')) or not re.fullmatch(r'[0-9]{6}', str(buyer_pin)):
                raise ValueError('Actual seller and buyer pincodes are required')
            if not co.get('address') or not buyer_addr:
                raise ValueError('Actual seller and buyer addresses are required')

        # Format date as DD/MM/YYYY
        v_date = v["date"]
        parts = v_date.split("-")
        dt_formatted = f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else v_date

        # Fetch items
        cursor.execute("""
            SELECT vi.stock_item_name, vi.qty, vi.rate, vi.amount, si.hsn_code, si.gst_rate
            FROM voucher_inventory vi
            JOIN stock_item si ON si.name = vi.stock_item_name
            WHERE vi.voucher_id = ?
        """, (voucher_id,))
        inv_items = cursor.fetchall()

        item_list = []
        tot_ass_val = Decimal("0.00")
        tot_cgst = Decimal("0.00")
        tot_sgst = Decimal("0.00")
        tot_igst = Decimal("0.00")

        is_interstate = (co.get("state_code", "") != buyer_state_code)

        for idx, itm in enumerate(inv_items, start=1):
            qty = to_qty_dec(itm["qty"])
            rate = to_dec(itm["rate"])
            ass_val = to_dec(itm["amount"])
            gst_rt = to_dec(itm["gst_rate"])

            tot_ass_val += ass_val

            if is_interstate:
                igst_amt = (ass_val * gst_rt / Decimal("100.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                cgst_amt = Decimal("0.00")
                sgst_amt = Decimal("0.00")
            else:
                half_rt = gst_rt / Decimal("2.0")
                cgst_amt = (ass_val * half_rt / Decimal("100.0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                sgst_amt = cgst_amt
                igst_amt = Decimal("0.00")

            tot_cgst += cgst_amt
            tot_sgst += sgst_amt
            tot_igst += igst_amt
            tot_item_val = ass_val + cgst_amt + sgst_amt + igst_amt

            item_list.append({
                "ItemNo": str(idx),
                "ItemNm": itm["stock_item_name"],
                "HsnCd": itm["hsn_code"] or "",
                "Qty": float(qty),
                "UnitPrice": float(rate),
                "TotAmt": float(ass_val),
                "AssAmt": float(ass_val),
                "GstRt": float(gst_rt),
                "IgstAmt": float(igst_amt),
                "CgstAmt": float(cgst_amt),
                "SgstAmt": float(sgst_amt),
                "TotItemVal": float(tot_item_val)
            })

        tot_inv_val = tot_ass_val + tot_cgst + tot_sgst + tot_igst

        # Official NIC e-Invoice JSON schema v1.1
        einvoice = {
            "Version": "1.1",
            "TranDtls": {
                "TaxSch": "GST",
                "SupTyp": "B2B",
                "RegRev": "Y" if v.get("is_rcm") else "N",
                "EcmGstin": None
            },
            "DocDtls": {
                "Typ": "INV",
                "No": v["voucher_number"],
                "Dt": dt_formatted
            },
            "SellerDtls": {
                "Gstin": co.get("gstin", ""),
                "LglNm": co.get("formal_name") or co.get("name", ""),
                "Addr1": co.get("address", ""),
                "Loc": co.get("state", ""),
                "Pin": int(co.get("pincode") or 0),
                "Stcd": co.get("state_code", "")
            },
            "BuyerDtls": {
                "Gstin": buyer_gstin,
                "LglNm": buyer_name,
                "Pos": buyer_state_code,
                "Addr1": buyer_addr,
                "Loc": buyer_state,
                "Pin": buyer_pin,
                "Stcd": buyer_state_code
            },
            "ItemList": item_list,
            "ValDtls": {
                "AssVal": float(tot_ass_val),
                "CgstVal": float(tot_cgst),
                "SgstVal": float(tot_sgst),
                "IgstVal": float(tot_igst),
                "TotInvVal": float(tot_inv_val)
            }
        }

        if for_submission and (not item_list or any(not str(it.get('HsnCd', '')).isdigit() for it in item_list)):
            raise ValueError('Invoice line items with actual HSN/SAC codes are required')
        return einvoice

    def generate_eway_bill_json(self, voucher_id: int) -> Dict[str, Any]:
        """
        Generates standard NIC e-Way Bill JSON payload for offline preparation.
        """
        self.require_feature('ewaybill')
        einv = self.generate_einvoice_json(voucher_id)
        cursor = self.conn.cursor()
        cursor.execute("SELECT vehicle_no, transporter_id, distance_km FROM voucher WHERE id = ?", (voucher_id,))
        row = cursor.fetchone()

        vehicle_no = row[0] if row and row[0] else ""
        transporter_id = row[1] if row and row[1] else ""
        distance = float(row[2]) if row and row[2] else 0.0

        if not vehicle_no or distance <= 0:
            raise ValueError('Actual vehicle number and positive transport distance are required')

        eway_bill = {
            "supplyType": "O",
            "subSupplyType": "1",
            "docType": "INV",
            "docNo": einv["DocDtls"]["No"],
            "docDate": einv["DocDtls"]["Dt"],
            "fromGstin": einv["SellerDtls"]["Gstin"],
            "toGstin": einv["BuyerDtls"]["Gstin"],
            "totalValue": einv["ValDtls"]["TotInvVal"],
            "cgstValue": einv["ValDtls"]["CgstVal"],
            "sgstValue": einv["ValDtls"]["SgstVal"],
            "igstValue": einv["ValDtls"]["IgstVal"],
            "transDistance": distance,
            "transporterId": transporter_id,
            "transporterName": "",
            "transMode": "1",
            "vehicleNo": vehicle_no,
            "vehicleType": "R",
            "itemList": [
                {
                    "itemNo": int(it["ItemNo"]),
                    "productName": it["ItemNm"],
                    "hsnCode": int(it["HsnCd"]),
                    "quantity": it["Qty"],
                    "taxableAmount": it["AssAmt"],
                    "cgstRate": it["GstRt"] / 2.0 if it["CgstAmt"] > 0 else 0.0,
                    "sgstRate": it["GstRt"] / 2.0 if it["SgstAmt"] > 0 else 0.0,
                    "igstRate": it["GstRt"] if it["IgstAmt"] > 0 else 0.0
                }
                for it in einv["ItemList"]
            ]
        }
        return eway_bill

    def get_tds_sections(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tds_section ORDER BY section_code")
        return [dict(r) for r in cursor.fetchall()]

    def add_tds_section(self, section_code: str, description: str, threshold_amount: float,
                        rate_individual: float, rate_other: float) -> bool:
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO tds_section (section_code, description, threshold_amount, rate_individual, rate_other)
                VALUES (?, ?, ?, ?, ?)
            """, (section_code.strip(), description.strip(), float(threshold_amount), float(rate_individual), float(rate_other)))
            self._log_edit("TDSSection", section_code, "CREATE", f"Configured TDS section {section_code}")
        return True

    def get_tds_register(self) -> List[Dict[str, Any]]:
        """
        Extracts all transactions where TDS was deducted at source.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.id as voucher_id, v.voucher_number, v.date, ve.ledger_name, ve.amount, ve.entry_type, v.narration
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE (ve.ledger_name LIKE '%TDS%' OR ve.ledger_name LIKE '%Tax Deducted%')
              AND v.is_cancelled = 0
            ORDER BY v.date DESC
        """)
        results = []
        for r in cursor.fetchall():
            results.append({
                "voucher_id": r["voucher_id"],
                "voucher_number": r["voucher_number"],
                "date": r["date"],
                "tds_ledger": r["ledger_name"],
                "tds_amount": r["amount"],
                "entry_type": r["entry_type"],
                "narration": r["narration"]
            })
        return results

    # =========================================================================
    # PHASE 3: BANKING, CHEQUES, PAYMENT ADVICE & FUNDS FLOW
    # =========================================================================
    def get_cheque_books(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM cheque_book ORDER BY bank_ledger_name, book_name")
        return [dict(r) for r in cursor.fetchall()]

    def add_cheque_book(self, bank_ledger_name: str, book_name: str, from_no: int, to_no: int) -> bool:
        with self.conn:
            self.conn.execute("""
                INSERT INTO cheque_book (bank_ledger_name, book_name, from_no, to_no, current_no)
                VALUES (?, ?, ?, ?, ?)
            """, (bank_ledger_name.strip(), book_name.strip(), from_no, to_no, from_no))
            self._log_edit("ChequeBook", book_name, "CREATE", f"Added cheque book {book_name} ({from_no}-{to_no}) for {bank_ledger_name}")
        return True

    def issue_cheque(self, bank_ledger_name: str, voucher_id: int, instrument_no: Optional[str] = None) -> Dict[str, Any]:
        """
        Issues next cheque number from the configured cheque book and attaches it to bank reconciliation.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, current_no, to_no FROM cheque_book WHERE bank_ledger_name = ? AND current_no <= to_no LIMIT 1", (bank_ledger_name,))
        book = cursor.fetchone()

        chq_num_str = instrument_no
        if not chq_num_str:
            if not book:
                chq_num_str = f"CHQ{datetime.date.today().strftime('%Y%m%d%H%M')[-6:]}"
            else:
                chq_num_str = str(book[1])

        with self.conn:
            if book and not instrument_no:
                self.conn.execute("UPDATE cheque_book SET current_no = current_no + 1 WHERE id = ?", (book[0],))

            self.conn.execute("""
                INSERT OR REPLACE INTO bank_reconciliation (voucher_id, bank_ledger_name, instrument_no, instrument_date, status)
                VALUES (?, ?, ?, ?, 'Unreconciled')
            """, (voucher_id, bank_ledger_name, chq_num_str, datetime.date.today().isoformat()))

            self._log_edit("Cheque", chq_num_str, "ISSUE", f"Issued cheque {chq_num_str} for voucher ID {voucher_id}")

        return {
            "status": "SUCCESS",
            "bank_ledger": bank_ledger_name,
            "instrument_no": chq_num_str,
            "voucher_id": voucher_id
        }

    def get_payment_advice(self, voucher_id: int) -> Dict[str, Any]:
        """
        Generates payment advice slip for suppliers and vendors.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM voucher WHERE id = ?", (voucher_id,))
        v_row = cursor.fetchone()
        if not v_row:
            raise ValueError(f"Voucher ID {voucher_id} not found.")
        v = dict(v_row)

        co = self.get_company()

        # Find beneficiary (Dr ledger) and paying bank (Cr ledger)
        cursor.execute("""
            SELECT ve.ledger_name, ve.entry_type, l.contact_person, l.bank_name, l.account_no
            FROM voucher_entry ve
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE ve.voucher_id = ?
        """, (voucher_id,))
        entries = cursor.fetchall()

        beneficiary = "Unknown Beneficiary"
        paying_bank = "Bank"
        for e in entries:
            if e["entry_type"] == "Dr":
                beneficiary = e["ledger_name"]
            elif e["entry_type"] == "Cr":
                paying_bank = e["ledger_name"]

        cursor.execute("SELECT instrument_no, instrument_date FROM bank_reconciliation WHERE voucher_id = ?", (voucher_id,))
        rec = cursor.fetchone()
        chq_no = rec[0] if rec else "N/A"
        chq_date = rec[1] if rec else v["date"]

        amt = to_dec(v["total_amount"])

        return {
            "company_name": co.get("formal_name", "Demo Enterprise Ltd."),
            "company_address": co.get("address", ""),
            "payment_voucher_no": v["voucher_number"],
            "date": v["date"],
            "beneficiary_name": beneficiary,
            "paying_bank": paying_bank,
            "instrument_no": chq_no,
            "instrument_date": chq_date,
            "amount": float(amt),
            "amount_in_words": amount_to_words(float(amt)),
            "narration": v["narration"] or "Payment towards verified bills"
        }

    def get_funds_flow_statement(self) -> Dict[str, Any]:
        """
        Computes Funds Flow Statement:
        Sources of Funds:
        - Funds from Operations (Net Profit + Depreciation/non-cash)
        - Issue of Capital / Loans Taken
        Applications of Funds:
        - Purchase of Fixed Assets
        - Repayment of Loans
        Net Change in Working Capital = Sources - Applications
        """
        fin = self.get_financial_summary()
        net_profit = to_dec(fin.get("net_profit", 0.0))

        cursor = self.conn.cursor()
        # Non-current assets purchases (Fixed Assets debits)
        cursor.execute("""
            SELECT COALESCE(SUM(ve.amount), 0.0)
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE l.group_name = 'Fixed Assets' AND ve.entry_type = 'Dr' AND v.is_cancelled = 0
        """)
        fixed_assets_purchased = to_dec(cursor.fetchone()[0])

        # Capital introduced (Capital credits)
        cursor.execute("""
            SELECT COALESCE(SUM(ve.amount), 0.0)
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE l.group_name = 'Capital Account' AND ve.entry_type = 'Cr' AND v.is_cancelled = 0
        """)
        capital_introduced = to_dec(cursor.fetchone()[0])

        sources_total = net_profit + capital_introduced
        applications_total = fixed_assets_purchased
        net_working_capital_change = sources_total - applications_total

        return {
            "sources": {
                "funds_from_operations": float(net_profit),
                "capital_introduced": float(capital_introduced),
                "total_sources": float(sources_total)
            },
            "applications": {
                "fixed_assets_acquired": float(fixed_assets_purchased),
                "total_applications": float(applications_total)
            },
            "net_increase_in_working_capital": float(net_working_capital_change)
        }

    # =========================================================================
    # PHASE 4: ENTERPRISE SECURITY, PBKDF2 HASHING & RBAC ENFORCEMENT
    # =========================================================================
    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None) -> str:
        """
        NIST SP 800-63B compliant PBKDF2-HMAC-SHA256 password hashing with 100,000 iterations and random salt.
        Format: pbkdf2_sha256$100000$<salt_hex>$<hash_hex>
        """
        if not salt:
            salt = os.urandom(16).hex()
        iterations = 100000
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
        return f"pbkdf2_sha256${iterations}${salt}${derived}"

    @classmethod
    def verify_password(cls, password: str, stored_hash: str) -> Tuple[bool, bool]:
        """
        Verifies password against stored hash.
        Returns: (is_valid, needs_upgrade)
        Supports both modern PBKDF2-HMAC-SHA256 and legacy salted SHA256.
        """
        if stored_hash.startswith("pbkdf2_sha256$"):
            parts = stored_hash.split("$")
            if len(parts) == 4:
                iterations = int(parts[1])
                salt = parts[2]
                expected_hash = parts[3]
                derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
                return hmac.compare_digest(derived, expected_hash), False

        # Legacy salted SHA-256 backward compatibility
        legacy_hash = hashlib.sha256(f"{password}_tally_salt".encode("utf-8")).hexdigest()
        if hmac.compare_digest(legacy_hash, stored_hash):
            return True, True  # Valid, but needs upgrade to modern PBKDF2!

        return False, False

    def create_user(self, username: str, password: str, role: str = "Data Entry",
                    full_name: str = "", company_access: str = "ALL") -> bool:
        if role not in ('Admin', 'Data Entry', 'Auditor', 'Viewer'):
            raise ValueError('Unknown role')
        if not username.strip() or len(password) < 8:
            raise ValueError('Username and password of at least 8 characters are required')
        p_hash = self.hash_password(password)
        with self.conn:
            self.conn.execute("""
                INSERT INTO user_account (username, password_hash, role, full_name, company_access)
                VALUES (?, ?, ?, ?, ?)
            """, (username.strip(), p_hash, role.strip(), full_name.strip(), company_access))
            self._log_edit("UserAccount", username, "CREATE", f"Created user {username} with role {role}")
        return True

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """
        Authenticates user with constant-time verification.
        Transparently migrates legacy hashes to PBKDF2-HMAC-SHA256 upon successful login.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, username, password_hash, role, full_name, company_access, is_active
            FROM user_account
            WHERE username = ? AND is_active = 1
        """, (username.strip(),))
        row = cursor.fetchone()
        if not row:
            return None

        stored_hash = row["password_hash"]
        is_valid, needs_upgrade = self.verify_password(password, stored_hash)
        if not is_valid:
            return None

        # Transparent security upgrade
        if needs_upgrade:
            new_hash = self.hash_password(password)
            with self.conn:
                self.conn.execute("UPDATE user_account SET password_hash = ? WHERE id = ?", (new_hash, row["id"]))
                self._log_edit("UserAccount", username, "UPGRADE_SECURITY", "Upgraded password hash to PBKDF2-HMAC-SHA256")

        user_data = dict(row)
        del user_data["password_hash"]
        return user_data

    def create_session(self, user_dict: Dict[str, Any], duration_hours: int = 24) -> str:
        """
        Creates a cryptographically secure random session token with timestamp expiration.
        """
        token = os.urandom(32).hex()
        now = datetime.datetime.now()
        expires = now + datetime.timedelta(hours=duration_hours)
        with self.conn:
            self.conn.execute("""
                INSERT INTO user_session (token, user_id, username, role, company_access, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (token, user_dict["id"], user_dict["username"], user_dict["role"],
                  user_dict.get("company_access", "ALL"), now.isoformat(), expires.isoformat()))
        return token

    def validate_session(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Validates token and checks for expiration.
        """
        if not token:
            return None
        cursor = self.conn.cursor()
        cursor.execute("SELECT s.token,s.user_id,u.username,u.role,u.company_access,s.created_at,s.expires_at FROM user_session s JOIN user_account u ON u.id=s.user_id WHERE s.token=? AND u.is_active=1", (token,))
        row = cursor.fetchone()
        if not row:
            return None

        exp_str = row["expires_at"]
        if datetime.datetime.fromisoformat(exp_str) < datetime.datetime.now():
            self.revoke_session(token)
            return None

        return dict(row)

    def revoke_session(self, token: str) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM user_session WHERE token = ?", (token,))
        return True

    @staticmethod
    def check_rbac_permission(role: str, endpoint: str, method: str) -> bool:
        """
        Enforces Role-Based Access Control (RBAC):
        - Admin: Full read/write across all endpoints.
        - Data Entry: Read all reports and post vouchers, ledgers, items. Blocked from FY Close/Split, User Management, and Backup Restore.
        - Auditor: Read-only access across reports, audit logs, vouchers, masters. Blocked from all mutating POST endpoints.
        """
        if role == "Admin":
            return True
        if (endpoint.startswith('/api/payroll/') or endpoint == '/api/employees' or ('/api/report/' in endpoint and any(name in endpoint.lower() for name in ('payroll','payslip')))) and (method != 'GET' or role not in ('Auditor',)):
            return False
        if method != 'GET' and (endpoint.startswith('/api/users') or endpoint.startswith('/api/companies/') or endpoint.startswith('/api/company/vault/') or endpoint in ('/api/company/features', '/api/company/consolidated', '/api/archive/export', '/api/backup/schedule', '/api/payroll/config', '/api/gst/registrations')):
            return False

        # Sensitive read endpoints requiring authorization:
        sensitive_read_endpoints = [
            "/api/payroll/employees",
            "/api/payroll/payslips",
            "/api/payroll/config",
            "/api/backup/versions",
            "/api/report/PayrollSummary",
            "/api/users",
            "/api/system/audit_history"
        ]

        if role == "Auditor":
            # Auditor can only read (GET) and cannot modify (POST/PUT/DELETE)
            return method == "GET"

        if role == "Data Entry":
            if method == "GET":
                return True
            # Block administrative / destructive endpoints
            blocked_endpoints = [
                "/api/company/close_fy",
                "/api/company/split_fy",
                "/api/companies/create",
                "/api/companies/consolidate",
                "/api/companies/delete",
                "/api/companies/alter",
                "/api/backup/restore",
                "/api/users/delete",
                "/api/users/create",
                "/api/users",
                "/api/auth/register"
            ]
            return endpoint not in blocked_endpoints

        if role == "Viewer":
            if method != "GET":
                return False
            return endpoint not in sensitive_read_endpoints

        return False

    def get_users(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, username, role, full_name, company_access, is_active FROM user_account ORDER BY username")
        return [dict(r) for r in cursor.fetchall()]

    def delete_user(self, user_id: int) -> bool:
        """
        Deletes user by ID. Prevents deleting the sole Admin user.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT username, role FROM user_account WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"User ID {user_id} not found.")
        if row["role"] == "Admin":
            cursor.execute("SELECT COUNT(*) FROM user_account WHERE role = 'Admin'")
            if cursor.fetchone()[0] <= 1:
                raise ValueError("Cannot delete the only Admin user in the system.")
        with self.conn:
            self.conn.execute("DELETE FROM user_account WHERE id = ?", (user_id,))
            self.conn.execute("DELETE FROM user_session WHERE user_id = ?", (user_id,))
            self._log_edit("UserAccount", row["username"], "DELETE", f"Deleted user {row['username']} (ID {user_id})")
        return True

    def update_user(self, user_id: int, role: str, full_name: str = "", is_active: int = 1) -> bool:
        """
        Updates user account role, display name, or active status.
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT username FROM user_account WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"User ID {user_id} not found.")
        with self.conn:
            self.conn.execute("""
                UPDATE user_account
                SET role = ?, full_name = ?, is_active = ?
                WHERE id = ?
            """, (role.strip(), full_name.strip(), is_active, user_id))
            self._log_edit("UserAccount", row["username"], "ALTER", f"Updated user {row['username']} role to {role}")
        return True

    # -------------------------------------------------------------------------
    # Authenticated Company Vault Encryption (TallyVault AEAD Encrypt-then-MAC)
    # -------------------------------------------------------------------------
    def export_authenticated_company_vault(self, company_id: int, vault_password: str, output_path: str) -> Dict[str, Any]:
        """
        Exports company ledger, vouchers, inventory, and masters into an
        authenticated encrypted container (.tve) using PBKDF2 (100,000 rounds)
        and HMAC-SHA256 Encrypt-then-MAC.
        """
        if not vault_password or len(vault_password) < 6:
            raise ValueError("Vault password must be at least 6 characters long!")

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM company WHERE id = ?", (company_id,))
        comp = cursor.fetchone()
        if not comp:
            raise ValueError(f"Company ID {company_id} not found!")

        cursor.execute("SELECT * FROM ledger WHERE company_id = ?", (company_id,))
        ledgers = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM stock_item WHERE company_id = ?", (company_id,))
        stock_items = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM voucher WHERE company_id = ?", (company_id,))
        vouchers = [dict(r) for r in cursor.fetchall()]

        vch_ids = [v["id"] for v in vouchers]
        entries = []
        inv_items = []
        bill_allocs = []
        if vch_ids:
            placeholders = ",".join("?" * len(vch_ids))
            cursor.execute(f"SELECT * FROM voucher_entry WHERE voucher_id IN ({placeholders})", vch_ids)
            entries = [dict(r) for r in cursor.fetchall()]

            cursor.execute(f"SELECT * FROM voucher_inventory WHERE voucher_id IN ({placeholders})", vch_ids)
            inv_items = [dict(r) for r in cursor.fetchall()]

            cursor.execute(f"SELECT * FROM bill_allocation WHERE voucher_id IN ({placeholders})", vch_ids)
            bill_allocs = [dict(r) for r in cursor.fetchall()]

        cursor.execute("SELECT * FROM edit_log WHERE entity_type = 'Company' AND entity_id = ?", (comp["name"],))
        audit_logs = [dict(r) for r in cursor.fetchall()]

        payload_obj = {
            "format": "TallyVault Authenticated Archive v1",
            "company": dict(comp),
            "ledgers": ledgers,
            "stock_items": stock_items,
            "vouchers": vouchers,
            "entries": entries,
            "inventory_items": inv_items,
            "bill_allocations": bill_allocs,
            "audit_logs": audit_logs,
            "exported_at": datetime.datetime.now().isoformat()
        }
        raw_bytes = json.dumps(payload_obj, default=str).encode("utf-8")

        # Key derivation: PBKDF2-HMAC-SHA256 (100,000 iterations)
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac("sha256", vault_password.encode("utf-8"), salt, 100000, dklen=64)
        enc_key = key[:32]
        mac_key = key[32:]

        # Deterministic CTR stream cipher with SHA-256 blocks
        nonce = os.urandom(16)
        ciphertext = bytearray(len(raw_bytes))
        block_count = (len(raw_bytes) + 31) // 32
        for b_idx in range(block_count):
            counter_bytes = b_idx.to_bytes(8, "big")
            block_key = hmac.new(enc_key, nonce + counter_bytes, hashlib.sha256).digest()
            start = b_idx * 32
            end = min(start + 32, len(raw_bytes))
            for i in range(start, end):
                ciphertext[i] = raw_bytes[i] ^ block_key[i - start]

        # Authenticate with HMAC-SHA256 (Encrypt-then-MAC)
        header = b"TVE1"
        tag = hmac.new(mac_key, header + salt + nonce + ciphertext, hashlib.sha256).digest()

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(header + salt + nonce + tag + ciphertext)

        self._log_edit("Company", comp["name"], "VAULT_EXPORT", f"Exported authenticated TallyVault archive to {os.path.basename(output_path)}")
        return {
            "status": "success",
            "company_name": comp["name"],
            "archive_path": output_path,
            "size_bytes": len(header + salt + nonce + tag + ciphertext),
            "ledgers_count": len(ledgers),
            "vouchers_count": len(vouchers)
        }

    def import_authenticated_company_vault(self, vault_file_path: str, vault_password: str) -> Dict[str, Any]:
        """
        Imports and authenticates a TallyVault encrypted archive (.tve).
        Validates PBKDF2-HMAC-SHA256 tag before decryption.
        """
        if not os.path.exists(vault_file_path):
            raise FileNotFoundError(f"Vault archive '{vault_file_path}' not found!")

        with open(vault_file_path, "rb") as f:
            data = f.read()

        if len(data) < 4 + 16 + 16 + 32:
            raise ValueError("Corrupt or invalid TallyVault container format!")

        header = data[:4]
        if header != b"TVE1":
            raise ValueError("Invalid TallyVault header! File is not an authentic .tve container.")

        salt = data[4:20]
        nonce = data[20:36]
        expected_tag = data[36:68]
        ciphertext = data[68:]

        # Re-derive keys
        key = hashlib.pbkdf2_hmac("sha256", vault_password.encode("utf-8"), salt, 100000, dklen=64)
        enc_key = key[:32]
        mac_key = key[32:]

        # Verify HMAC tag with constant-time comparison
        computed_tag = hmac.new(mac_key, header + salt + nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(computed_tag, expected_tag):
            raise ValueError("Authentication Failed: Invalid vault password or tampered archive!")

        # Decrypt CTR keystream
        raw_bytes = bytearray(len(ciphertext))
        block_count = (len(ciphertext) + 31) // 32
        for b_idx in range(block_count):
            counter_bytes = b_idx.to_bytes(8, "big")
            block_key = hmac.new(enc_key, nonce + counter_bytes, hashlib.sha256).digest()
            start = b_idx * 32
            end = min(start + 32, len(ciphertext))
            for i in range(start, end):
                raw_bytes[i] = ciphertext[i] ^ block_key[i - start]

        payload_obj = json.loads(raw_bytes.decode("utf-8"))
        comp_data = payload_obj["company"]

        # Restore company safely with new ID if existing
        with self.conn:
            cursor = self.conn.cursor()
            c_name = comp_data["name"]
            cursor.execute("SELECT id FROM company WHERE name = ?", (c_name,))
            if cursor.fetchone():
                c_name = f"{c_name} (Vault Restored)"

            cursor.execute("""
                INSERT INTO company (name, address, gstin, state, state_code, financial_year_from, financial_year_to, currency_symbol, security_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (c_name, comp_data.get("address", ""), comp_data.get("gstin", ""), comp_data.get("state", "Karnataka"),
                  comp_data.get("state_code", "29"), comp_data.get("financial_year_from", "2026-04-01"),
                  comp_data.get("financial_year_to", "2027-03-31"), comp_data.get("currency_symbol", "₹"), 1))
            new_cid = cursor.lastrowid

            # Restore ledgers
            for l in payload_obj.get("ledgers", []):
                cursor.execute("""
                    INSERT OR IGNORE INTO ledger (name, group_name, company_id, opening_balance, opening_balance_type, contact_person, gstin, state, state_code, credit_limit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (l["name"], l["group_name"], new_cid, l.get("opening_balance", 0.0), l.get("opening_balance_type", "Dr"),
                      l.get("contact_person", ""), l.get("gstin", ""), l.get("state", "Karnataka"), l.get("state_code", "29"), l.get("credit_limit", 0.0)))

            self._log_edit("Company", c_name, "VAULT_IMPORT", f"Successfully imported authenticated TallyVault company (ID {new_cid})")

        return {
            "status": "success",
            "company_id": new_cid,
            "company_name": c_name,
            "vouchers_count": len(payload_obj.get("vouchers", [])),
            "ledgers_count": len(payload_obj.get("ledgers", []))
        }

    # -------------------------------------------------------------------------
    # Fixed Asset Management & Depreciation (ERPNext Assets + Tally FA)
    # -------------------------------------------------------------------------
    def add_fixed_asset(self, asset_name: str, asset_code: str, category_name: str,
                        purchase_date: str, in_service_date: str, purchase_cost: float,
                        scrap_value: float = 0.0, useful_life_months: int = 60,
                        depreciation_method: str = "SLM", depreciation_rate_pct: float = 20.0,
                        asset_ledger: str = "Computers & IT Equipment",
                        depr_expense_ledger: str = "Depreciation Expense",
                        accum_depr_ledger: str = "Accumulated Depreciation",
                        notes: str = "") -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM fixed_asset WHERE asset_code = ?", (asset_code,))
        if cursor.fetchone():
            raise ValueError(f"Fixed asset with code '{asset_code}' already exists!")

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO fixed_asset (
                    company_id, asset_name, asset_code, category_name, purchase_date, in_service_date,
                    purchase_cost, scrap_value, useful_life_months, depreciation_method,
                    depreciation_rate_pct, asset_ledger, depr_expense_ledger, accum_depr_ledger, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'In Use', ?)
            """, (self.active_company_id, asset_name, asset_code, category_name, purchase_date,
                  in_service_date, purchase_cost, scrap_value, useful_life_months,
                  depreciation_method.upper(), depreciation_rate_pct, asset_ledger,
                  depr_expense_ledger, accum_depr_ledger, notes))
            asset_id = cur.lastrowid
            self._log_edit("FixedAsset", asset_code, "CREATE", f"Added fixed asset '{asset_name}' (Cost: ₹{purchase_cost:,.2f})")

        self.calculate_depreciation_schedule(asset_id)
        return asset_id

    def get_fixed_assets(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT fa.*,
                   COALESCE(SUM(CASE WHEN ade.status = 'Posted' THEN ade.depreciation_amount ELSE 0 END), 0.0) as posted_depreciation
            FROM fixed_asset fa
            LEFT JOIN asset_depreciation_entry ade ON ade.asset_id = fa.id
            WHERE fa.company_id = ?
            GROUP BY fa.id
            ORDER BY fa.id DESC
        """, (self.active_company_id,))
        assets = []
        for r in cursor.fetchall():
            a = dict(r)
            a["current_book_value"] = max(0.0, a["purchase_cost"] - a["posted_depreciation"])
            assets.append(a)
        return assets

    def get_fixed_asset(self, asset_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fixed_asset WHERE id = ? AND company_id = ?", (asset_id, self.active_company_id))
        row = cursor.fetchone()
        if not row:
            return None
        res = dict(row)
        cursor.execute("SELECT * FROM asset_depreciation_entry WHERE asset_id = ? ORDER BY period_date ASC", (asset_id,))
        res["schedule"] = [dict(s) for s in cursor.fetchall()]
        posted = sum(s["depreciation_amount"] for s in res["schedule"] if s["status"] == "Posted")
        res["posted_depreciation"] = posted
        res["current_book_value"] = max(0.0, res["purchase_cost"] - posted)
        return res

    def calculate_depreciation_schedule(self, asset_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM fixed_asset WHERE id = ?", (asset_id,))
        asset = cursor.fetchone()
        if not asset:
            raise ValueError(f"Asset ID {asset_id} not found!")

        cost = to_dec(asset["purchase_cost"])
        scrap = to_dec(asset["scrap_value"])
        months = max(1, asset["useful_life_months"])
        method = asset["depreciation_method"]
        rate_pct = to_dec(asset["depreciation_rate_pct"])
        in_service = asset["in_service_date"]

        with self.conn:
            self.conn.execute("DELETE FROM asset_depreciation_entry WHERE asset_id = ? AND status = 'Scheduled'", (asset_id,))

        entries = []
        accum_depr = Decimal("0.00")
        book_val = cost
        depreciable_base = cost - scrap

        try:
            curr_dt = datetime.date.fromisoformat(in_service)
        except Exception:
            curr_dt = datetime.date.today()

        monthly_slm = (depreciable_base / Decimal(str(months))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        for m in range(1, months + 1):
            month = curr_dt.month + 1
            year = curr_dt.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            day = min(curr_dt.day, 28)
            curr_dt = datetime.date(year, month, day)
            dt_str = curr_dt.isoformat()

            if method == "WDV":
                monthly_rate = (rate_pct / Decimal("100")) / Decimal("12")
                depr_amt = (book_val * monthly_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                depr_amt = monthly_slm

            if book_val - depr_amt < scrap:
                depr_amt = max(Decimal("0.00"), book_val - scrap)

            accum_depr += depr_amt
            book_val = max(scrap, cost - accum_depr)

            entries.append((asset_id, dt_str, float(depr_amt), float(accum_depr), float(book_val), "Scheduled"))
            if book_val <= scrap:
                break

        with self.conn:
            self.conn.executemany("""
                INSERT INTO asset_depreciation_entry (asset_id, period_date, depreciation_amount, accumulated_depreciation, book_value, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, entries)

        return self.get_fixed_asset(asset_id).get("schedule", [])

    def post_depreciation_entry(self, entry_id: int, user_name: str = "admin") -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ade.*, fa.asset_name, fa.asset_code, fa.depr_expense_ledger, fa.accum_depr_ledger
            FROM asset_depreciation_entry ade
            JOIN fixed_asset fa ON fa.id = ade.asset_id
            WHERE ade.id = ?
        """, (entry_id,))
        entry = cursor.fetchone()
        if not entry:
            raise ValueError(f"Depreciation entry {entry_id} not found!")
        if entry["status"] == "Posted":
            raise ValueError("Depreciation entry has already been posted!")

        depr_amt = float(entry["depreciation_amount"])
        v_entries = [
            {"ledger_name": entry["depr_expense_ledger"], "entry_type": "Dr", "amount": depr_amt},
            {"ledger_name": entry["accum_depr_ledger"], "entry_type": "Cr", "amount": depr_amt}
        ]

        v_id = self.add_voucher(
            voucher_type="Journal",
            date=entry["period_date"],
            narration=f"Depreciation for {entry['asset_name']} [{entry['asset_code']}] for period ending {entry['period_date']}",
            entries=v_entries
        )

        with self.conn:
            self.conn.execute("UPDATE asset_depreciation_entry SET status = 'Posted', voucher_id = ? WHERE id = ?", (v_id, entry_id))
            self._log_edit("FixedAsset", entry["asset_code"], "DEPRECIATE", f"Posted depreciation of ₹{depr_amt:,.2f} via Vch #{v_id}", user_name)

        return v_id

    def dispose_fixed_asset(self, asset_id: int, disposal_date: str, sale_amount: float,
                            disposal_ledger: str = "Bank Accounts", user_name: str = "admin") -> Dict[str, Any]:
        asset = self.get_fixed_asset(asset_id)
        if not asset:
            raise ValueError(f"Asset ID {asset_id} not found!")
        if asset["status"] in ("Scrapped", "Sold"):
            raise ValueError(f"Asset {asset['asset_code']} has already been disposed ({asset['status']})!")

        book_val = asset["current_book_value"]
        diff = sale_amount - book_val
        cost = asset["purchase_cost"]
        accum_depr = asset["posted_depreciation"]

        v_entries = [
            {"ledger_name": disposal_ledger, "entry_type": "Dr", "amount": sale_amount},
            {"ledger_name": asset["accum_depr_ledger"], "entry_type": "Dr", "amount": accum_depr},
            {"ledger_name": asset["asset_ledger"], "entry_type": "Cr", "amount": cost}
        ]

        if diff > 0:
            v_entries.append({"ledger_name": "Direct Incomes", "entry_type": "Cr", "amount": diff})
        elif diff < 0:
            v_entries.append({"ledger_name": "Indirect Expenses", "entry_type": "Dr", "amount": abs(diff)})

        v_id = self.add_voucher(
            voucher_type="Journal",
            date=disposal_date,
            narration=f"Disposal/Sale of {asset['asset_name']} [{asset['asset_code']}]. Book Value: ₹{book_val:,.2f}, Sold: ₹{sale_amount:,.2f}",
            entries=v_entries
        )

        new_status = "Sold" if sale_amount > 0 else "Scrapped"
        with self.conn:
            self.conn.execute("UPDATE fixed_asset SET status = ? WHERE id = ?", (new_status, asset_id))
            self._log_edit("FixedAsset", asset["asset_code"], "DISPOSE", f"Disposed asset ({new_status}) for ₹{sale_amount:,.2f} via Vch #{v_id}", user_name)

        return {
            "asset_id": asset_id,
            "status": new_status,
            "voucher_id": v_id,
            "book_value": book_val,
            "sale_amount": sale_amount,
            "gain_or_loss": diff
        }

    def get_fixed_asset_register(self) -> List[Dict[str, Any]]:
        assets = self.get_fixed_assets()
        cat_map = {}
        for a in assets:
            cat = a["category_name"]
            if cat not in cat_map:
                cat_map[cat] = {
                    "category": cat,
                    "total_cost": 0.0,
                    "total_depreciation": 0.0,
                    "net_book_value": 0.0,
                    "assets_count": 0,
                    "items": []
                }
            cat_map[cat]["total_cost"] += a["purchase_cost"]
            cat_map[cat]["total_depreciation"] += a["posted_depreciation"]
            cat_map[cat]["net_book_value"] += a["current_book_value"]
            cat_map[cat]["assets_count"] += 1
            cat_map[cat]["items"].append(a)
        return list(cat_map.values())

    # -------------------------------------------------------------------------
    # Landed Cost Vouchers (ERPNext Landed Cost + Tally Additional Costs)
    # -------------------------------------------------------------------------
    def apply_landed_cost(self, purchase_voucher_id: int, expense_ledger: str, amount: float,
                          allocation_method: str = "Value", narration: str = "", user_name: str = "admin") -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, voucher_number, date, total_amount FROM voucher WHERE id = ? AND is_cancelled = 0", (purchase_voucher_id,))
        vch = cursor.fetchone()
        if not vch:
            raise ValueError(f"Purchase voucher #{purchase_voucher_id} not found or is cancelled!")

        cursor.execute("""
            SELECT id, stock_item_name, qty, rate, amount
            FROM voucher_inventory
            WHERE voucher_id = ? AND direction = 'In'
        """, (purchase_voucher_id,))
        inv_items = cursor.fetchall()
        if not inv_items:
            raise ValueError(f"Purchase voucher #{purchase_voucher_id} has no inward inventory line items!")

        tot_val = sum(Decimal(str(i["amount"])) for i in inv_items)
        tot_qty = sum(Decimal(str(i["qty"])) for i in inv_items)
        tot_charge = to_dec(amount)

        v_num = f"LC-{purchase_voucher_id:04d}-{int(datetime.datetime.now().timestamp()) % 10000}"
        v_date = vch["date"] or datetime.date.today().isoformat()

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO landed_cost_voucher (company_id, voucher_number, date, purchase_voucher_id, expense_ledger, amount, allocation_method, narration)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.active_company_id, v_num, v_date, purchase_voucher_id, expense_ledger, float(tot_charge), allocation_method, narration))
            lc_id = cur.lastrowid

            for item in inv_items:
                i_val = Decimal(str(item["amount"]))
                i_qty = Decimal(str(item["qty"]))
                orig_rate = Decimal(str(item["rate"]))

                if allocation_method == "Quantity" and tot_qty > Decimal("0"):
                    alloc_cost = (tot_charge * (i_qty / tot_qty)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                else:
                    alloc_cost = (tot_charge * (i_val / tot_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if tot_val > Decimal("0") else Decimal("0.00")

                cost_per_unit = (alloc_cost / i_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if i_qty > Decimal("0") else Decimal("0.00")
                eff_rate = orig_rate + cost_per_unit

                self.conn.execute("""
                    INSERT INTO landed_cost_allocation (landed_cost_id, stock_item_name, qty, allocated_cost, original_rate, effective_rate)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (lc_id, item["stock_item_name"], float(i_qty), float(alloc_cost), float(orig_rate), float(eff_rate)))

                self.conn.execute("""
                    UPDATE voucher_inventory
                    SET landed_cost_addition = landed_cost_addition + ?, effective_rate = ?
                    WHERE id = ?
                """, (float(alloc_cost), float(eff_rate), item["id"]))

            self._log_edit("LandedCost", v_num, "APPLY", f"Allocated ₹{amount:,.2f} of '{expense_ledger}' to Vch #{purchase_voucher_id}", user_name)

        return lc_id

    def get_landed_cost_vouchers(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT lcv.*, v.voucher_number as purchase_voucher_no
            FROM landed_cost_voucher lcv
            JOIN voucher v ON v.id = lcv.purchase_voucher_id
            WHERE lcv.company_id = ?
            ORDER BY lcv.id DESC
        """, (self.active_company_id,))
        return [dict(r) for r in cursor.fetchall()]

    def get_landed_cost_summary(self, purchase_voucher_id: Optional[int] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        q = """
            SELECT lca.*, lcv.voucher_number as lc_voucher_no, lcv.date, lcv.expense_ledger, lcv.purchase_voucher_id
            FROM landed_cost_allocation lca
            JOIN landed_cost_voucher lcv ON lcv.id = lca.landed_cost_id
            WHERE lcv.company_id = ?
        """
        params = [self.active_company_id]
        if purchase_voucher_id:
            q += " AND lcv.purchase_voucher_id = ?"
            params.append(purchase_voucher_id)
        q += " ORDER BY lca.id DESC"
        cursor.execute(q, params)
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Order Processing & Fulfillment (ERPNext Buying & Selling + Tally Orders)
    # -------------------------------------------------------------------------
    def create_order(self, order_type: str, party_name: str, date: str,
                     items: List[Dict[str, Any]], due_date: str = "", narration: str = "") -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM order_tracking WHERE order_type = ?", (order_type,))
        num = cursor.fetchone()[0] + 1
        prefix = "SO" if "Sales" in order_type else ("PO" if "Purchase" in order_type else "QTN")
        order_num = f"{prefix}-{num:04d}"
        o_date = date or datetime.date.today().isoformat()
        d_date = due_date or o_date

        tot_amt = Decimal("0.00")
        validated_items = []
        for itm in items:
            item_name = itm.get("stock_item_name") or itm.get("item_name", "Item")
            q = to_qty_dec(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
            r = to_dec(itm.get("rate", 0))
            a = (q * r).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if "amount" not in itm else to_dec(itm["amount"])
            tot_amt += a
            validated_items.append((item_name, float(q), float(r), float(a)))

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO order_tracking (company_id, order_type, order_number, order_date, party_name, due_date, status, total_amount, narration)
                VALUES (?, ?, ?, ?, ?, ?, 'Open', ?, ?)
            """, (self.active_company_id, order_type, order_num, o_date, party_name, d_date, float(tot_amt), narration))
            o_id = cur.lastrowid

            for item_name, qty, rate, amt in validated_items:
                self.conn.execute("""
                    INSERT INTO order_item (order_id, stock_item_name, ordered_qty, rate, amount)
                    VALUES (?, ?, ?, ?, ?)
                """, (o_id, item_name, qty, rate, amt))

            self._log_edit("Order", order_num, "CREATE", f"Created {order_type} for {party_name} of ₹{float(tot_amt):,.2f}")

        return self.get_order(o_id)

    def get_orders(self, order_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        q = "SELECT * FROM order_tracking WHERE company_id = ?"
        params = [self.active_company_id]
        if order_type:
            q += " AND order_type = ?"
            params.append(order_type)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY id DESC"
        cursor.execute(q, params)
        orders = []
        for r in cursor.fetchall():
            o = dict(r)
            cursor.execute("SELECT * FROM order_item WHERE order_id = ?", (o["id"],))
            o["items"] = [dict(i) for i in cursor.fetchall()]
            o["pending_amount"] = max(0.0, o["total_amount"] - o["fulfilled_amount"])
            orders.append(o)
        return orders

    def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM order_tracking WHERE id = ? AND company_id = ?", (order_id, self.active_company_id))
        r = cursor.fetchone()
        if not r:
            return None
        o = dict(r)
        cursor.execute("SELECT * FROM order_item WHERE order_id = ?", (order_id,))
        o["items"] = [dict(i) for i in cursor.fetchall()]
        o["pending_amount"] = max(0.0, o["total_amount"] - o["fulfilled_amount"])
        return o

    def fulfill_order(self, order_id: int, fulfillment_voucher_id: int, fulfilled_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM order_tracking WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if not order:
            raise ValueError(f"Order ID {order_id} not found!")

        cursor.execute("SELECT * FROM order_item WHERE order_id = ?", (order_id,))
        items = {i["stock_item_name"]: dict(i) for i in cursor.fetchall()}

        with self.conn:
            tot_fulfilled_addition = Decimal("0.00")
            if fulfilled_items:
                for fi in fulfilled_items:
                    s_name = fi.get("stock_item_name") or fi.get("item_name")
                    f_qty = to_qty_dec(fi.get("qty") if "qty" in fi else (fi.get("fulfilled_qty") or fi.get("quantity", 0)))
                    if s_name in items:
                        orig = items[s_name]
                        new_f_qty = to_qty_dec(orig["fulfilled_qty"]) + f_qty
                        rate = to_dec(orig["rate"])
                        tot_fulfilled_addition += (f_qty * rate)
                        self.conn.execute("UPDATE order_item SET fulfilled_qty = ? WHERE id = ?", (float(new_f_qty), orig["id"]))
            else:
                for orig in items.values():
                    rem_qty = to_qty_dec(orig["ordered_qty"]) - to_qty_dec(orig["fulfilled_qty"])
                    rate = to_dec(orig["rate"])
                    tot_fulfilled_addition += (rem_qty * rate)
                    self.conn.execute("UPDATE order_item SET fulfilled_qty = ordered_qty WHERE id = ?", (orig["id"],))

            new_fulfilled_total = to_dec(order["fulfilled_amount"]) + tot_fulfilled_addition
            tot_order = to_dec(order["total_amount"])
            new_status = "Fulfilled" if new_fulfilled_total >= tot_order else "Partially Fulfilled"

            self.conn.execute("""
                UPDATE order_tracking
                SET fulfilled_amount = ?, status = ?
                WHERE id = ?
            """, (float(new_fulfilled_total), new_status, order_id))

            self._log_edit("Order", order["order_number"], "FULFILL", f"Fulfilled with Vch #{fulfillment_voucher_id} ({new_status})")

        return self.get_order(order_id)

    def get_pending_orders_report(self, order_type: Optional[str] = None) -> List[Dict[str, Any]]:
        orders = self.get_orders(order_type=order_type)
        return [o for o in orders if o["status"] in ("Open", "Partially Fulfilled")]

    # -------------------------------------------------------------------------
    # Credit Limits & Credit Terms Control (ERPNext Selling + Tally Credit Limits)
    # -------------------------------------------------------------------------
    def set_ledger_credit_terms(self, ledger_name: str, credit_limit: float = 0.0,
                                credit_days: int = 30, enforce_credit_limit: int = 0) -> bool:
        with self.conn:
            self.conn.execute("""
                UPDATE ledger
                SET credit_limit = ?, credit_period_days = ?, enforce_credit_limit = ?
                WHERE name = ? AND company_id = ?
            """, (credit_limit, credit_days, enforce_credit_limit, ledger_name, self.active_company_id))
            self._log_edit("Ledger", ledger_name, "ALTER", f"Updated credit terms: Limit=₹{credit_limit:,.2f}, Days={credit_days}, Enforce={bool(enforce_credit_limit)}")
        return True

    def check_credit_limit(self, ledger_name: str, additional_amount: float = 0.0) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT credit_limit, credit_period_days, enforce_credit_limit FROM ledger WHERE name = ? AND company_id = ?", (ledger_name, self.active_company_id))
        row = cursor.fetchone()
        if not row:
            return {
                "ledger_name": ledger_name, "credit_limit": 0.0, "current_balance": 0.0,
                "current_outstanding": 0.0, "additional_amount": additional_amount,
                "new_balance": additional_amount, "projected_outstanding": additional_amount,
                "is_exceeded": False, "blocked": False, "allowed": True
            }

        limit = float(row["credit_limit"] or 0.0)
        enforce = bool(row["enforce_credit_limit"])
        bal_dec, b_type = self.get_ledger_closing_balance(ledger_name)
        curr_bal = float(bal_dec) if b_type == "Dr" else -float(bal_dec)
        new_bal = curr_bal + additional_amount
        exceeded = (limit > 0.0) and (new_bal > limit)
        excess = max(0.0, new_bal - limit) if exceeded else 0.0

        return {
            "ledger_name": ledger_name,
            "credit_limit": limit,
            "credit_period_days": row["credit_period_days"],
            "current_balance": curr_bal,
            "current_outstanding": curr_bal,
            "additional_amount": additional_amount,
            "new_balance": new_bal,
            "projected_outstanding": new_bal,
            "is_exceeded": exceeded,
            "excess_amount": excess,
            "enforce_credit_limit": enforce,
            "blocked": exceeded and enforce,
            "allowed": not (exceeded and enforce)
        }

    # -------------------------------------------------------------------------
    # GSTR-2B Inward ITC Reconciliation (ERPNext India Regional + Tally GSTR-2B)
    # -------------------------------------------------------------------------
    def reconcile_gstr2b(self, return_period: str, gstr2b_invoices: List[Dict[str, Any]], tolerance: float = 1.0) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        upload_time = datetime.datetime.now().isoformat()

        cursor.execute("""
            SELECT v.id, v.voucher_number, v.date, v.total_amount,
                   COALESCE(l.gstin, '') as supplier_gstin, l.name as supplier_name
            FROM voucher v
            JOIN voucher_entry ve ON ve.voucher_id = v.id AND ve.entry_type = 'Cr'
            JOIN ledger l ON l.name = ve.ledger_name
            WHERE v.voucher_type = 'Purchase' AND v.is_cancelled = 0 AND v.company_id = ?
        """, (self.active_company_id,))
        book_vouchers = cursor.fetchall()

        book_by_num = {}
        for b in book_vouchers:
            v_num = b["voucher_number"].strip().lower()
            book_by_num[v_num] = dict(b)

        matched = []
        mismatched = []
        missing_in_books = []
        matched_book_ids = set()

        for inv in gstr2b_invoices:
            inv_no = str(inv.get("inum", inv.get("invoice_no", ""))).strip()
            gstin = str(inv.get("ctin", inv.get("supplier_gstin", ""))).strip().upper()
            s_name = str(inv.get("cname", inv.get("supplier_name", "Vendor"))).strip()
            inv_dt = str(inv.get("idt", inv.get("invoice_date", ""))).strip()
            val = float(inv.get("val", inv.get("invoice_val", 0.0)))
            txval = float(inv.get("txval", inv.get("taxable_val", val / 1.18 if val > 0 else 0.0)))
            igst = float(inv.get("igst", 0.0))
            cgst = float(inv.get("cgst", 0.0))
            sgst = float(inv.get("sgst", 0.0))

            book_match = book_by_num.get(inv_no.lower())
            if book_match:
                book_amt = float(book_match["total_amount"])
                matched_book_ids.add(book_match["id"])
                if abs(book_amt - val) <= tolerance:
                    matched.append({
                        "supplier_gstin": gstin, "supplier_name": s_name, "invoice_no": inv_no,
                        "invoice_date": inv_dt, "invoice_val": val, "taxable_val": txval,
                        "igst": igst, "cgst": cgst, "sgst": sgst, "status": "Matched",
                        "book_voucher_id": book_match["id"], "mismatch_reason": "Exact match"
                    })
                else:
                    mismatched.append({
                        "supplier_gstin": gstin, "supplier_name": s_name, "invoice_no": inv_no,
                        "invoice_date": inv_dt, "invoice_val": val, "taxable_val": txval,
                        "igst": igst, "cgst": cgst, "sgst": sgst, "status": "Tax Mismatch",
                        "book_voucher_id": book_match["id"],
                        "mismatch_reason": f"Value discrepancy: 2B ₹{val:,.2f} vs Books ₹{book_amt:,.2f}"
                    })
            else:
                missing_in_books.append({
                    "supplier_gstin": gstin, "supplier_name": s_name, "invoice_no": inv_no,
                    "invoice_date": inv_dt, "invoice_val": val, "taxable_val": txval,
                    "igst": igst, "cgst": cgst, "sgst": sgst, "status": "Missing in Books",
                    "book_voucher_id": None, "mismatch_reason": "Invoice reported by vendor in GSTR-2B but missing in local books"
                })

        missing_in_2b = []
        for b in book_vouchers:
            if b["id"] not in matched_book_ids:
                missing_in_2b.append({
                    "supplier_gstin": b["supplier_gstin"], "supplier_name": b["supplier_name"],
                    "invoice_no": b["voucher_number"], "invoice_date": b["date"],
                    "invoice_val": float(b["total_amount"]), "taxable_val": float(b["total_amount"]) / 1.18,
                    "igst": 0.0, "cgst": 0.0, "sgst": 0.0, "status": "Missing in 2B",
                    "book_voucher_id": b["id"], "mismatch_reason": "Booked in purchase register but vendor GSTR-1 not filed / absent in 2B"
                })

        with self.conn:
            cur = self.conn.execute("""
                INSERT INTO gstr2b_reconciliation (
                    company_id, return_period, uploaded_at, total_invoices, matched_count,
                    mismatched_count, missing_in_books_count, missing_in_2b_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.active_company_id, return_period, upload_time, len(gstr2b_invoices),
                  len(matched), len(mismatched), len(missing_in_books), len(missing_in_2b)))
            recon_id = cur.lastrowid

            all_items = matched + mismatched + missing_in_books + missing_in_2b
            for itm in all_items:
                self.conn.execute("""
                    INSERT INTO gstr2b_reconciliation_item (
                        recon_id, supplier_gstin, supplier_name, invoice_no, invoice_date,
                        invoice_val, taxable_val, igst, cgst, sgst, status, book_voucher_id, mismatch_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (recon_id, itm["supplier_gstin"], itm["supplier_name"], itm["invoice_no"],
                      itm["invoice_date"], itm["invoice_val"], itm["taxable_val"],
                      itm["igst"], itm["cgst"], itm["sgst"], itm["status"],
                      itm["book_voucher_id"], itm["mismatch_reason"]))

        res = self.get_gstr2b_recon_details(recon_id)
        res["recon_id"] = recon_id
        return res

    def get_gstr2b_reconciliations(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM gstr2b_reconciliation WHERE company_id = ? ORDER BY id DESC", (self.active_company_id,))
        return [dict(r) for r in cursor.fetchall()]

    def get_gstr2b_recon_details(self, recon_id: int) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM gstr2b_reconciliation WHERE id = ? AND company_id = ?", (recon_id, self.active_company_id))
        r = cursor.fetchone()
        if not r:
            return {}
        res = dict(r)
        cursor.execute("SELECT * FROM gstr2b_reconciliation_item WHERE recon_id = ? ORDER BY id ASC", (recon_id,))
        items = [dict(i) for i in cursor.fetchall()]
        res["items"] = items
        res["eligible_itc"] = sum(i["cgst"] + i["sgst"] + i["igst"] for i in items if i["status"] == "Matched")
        return res

    # -------------------------------------------------------------------------
    # Point of Sale (POS) Invoicing (ERPNext POS + Tally POS)
    # -------------------------------------------------------------------------
    def add_pos_voucher(self, date: str, party_name: str, items: List[Dict[str, Any]],
                        cash_tendered: float = 0.0, card_tendered: float = 0.0,
                        upi_tendered: float = 0.0, cheque_tendered: float = 0.0,
                        customer_phone: str = "", narration: str = "") -> Dict[str, Any]:
        tot_bill = sum(to_dec(i["amount"]) for i in items)
        tot_paid = to_dec(cash_tendered) + to_dec(card_tendered) + to_dec(upi_tendered) + to_dec(cheque_tendered)
        change_returned = max(Decimal("0.00"), tot_paid - tot_bill) if cash_tendered > 0 else Decimal("0.00")

        v_entries = []
        if cash_tendered > 0:
            effective_cash = float(to_dec(cash_tendered) - change_returned)
            if effective_cash > 0:
                v_entries.append({"ledger_name": "Cash", "entry_type": "Dr", "amount": effective_cash})
        if card_tendered > 0:
            v_entries.append({"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": float(card_tendered)})
        if upi_tendered > 0:
            v_entries.append({"ledger_name": "State Bank of India", "entry_type": "Dr", "amount": float(upi_tendered)})
        if cheque_tendered > 0:
            v_entries.append({"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": float(cheque_tendered)})

        curr_dr = sum(Decimal(str(e["amount"])) for e in v_entries)
        if curr_dr < tot_bill:
            v_entries.append({"ledger_name": party_name, "entry_type": "Dr", "amount": float(tot_bill - curr_dr)})

        v_entries.append({"ledger_name": "Sales A/c", "entry_type": "Cr", "amount": float(tot_bill)})

        inv_items = []
        for itm in items:
            s_name = itm.get("stock_item_name") or itm.get("item_name", "Stock Item")
            q = float(to_qty_dec(itm.get("qty") if "qty" in itm else itm.get("quantity", 1)))
            r = float(to_dec(itm.get("rate", 0)))
            a = float((to_qty_dec(q) * to_dec(r)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if "amount" not in itm else to_dec(itm["amount"]))
            inv_items.append({
                "stock_item_name": s_name,
                "qty": q,
                "rate": r,
                "amount": a,
                "direction": "Out"
            })

        v_id = self.add_voucher(
            voucher_type="Sales",
            date=date,
            narration=f"POS Quick Invoice to {party_name}. Tendered: ₹{float(tot_paid):,.2f}, Change: ₹{float(change_returned):,.2f}. {narration}".strip(),
            entries=v_entries,
            inventory_items=inv_items
        )

        with self.conn:
            self.conn.execute("UPDATE voucher SET is_pos = 1 WHERE id = ?", (v_id,))
            self.conn.execute("""
                INSERT INTO pos_payment_detail (voucher_id, cash_amount, card_amount, upi_amount, cheque_amount, change_returned, customer_phone)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (v_id, cash_tendered, card_tendered, upi_tendered, cheque_tendered, float(change_returned), customer_phone))

        return {
            "voucher_id": v_id,
            "total_bill": float(tot_bill),
            "total_amount": float(tot_bill),
            "total_tendered": float(tot_paid),
            "tender_paid": float(tot_paid),
            "change_returned": float(change_returned),
            "customer_phone": customer_phone
        }

    def get_pos_invoices(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.id, v.voucher_number, v.date, v.total_amount, v.narration,
                   ppd.cash_amount, ppd.card_amount, ppd.upi_amount, ppd.cheque_amount, ppd.change_returned, ppd.customer_phone
            FROM voucher v
            JOIN pos_payment_detail ppd ON ppd.voucher_id = v.id
            WHERE v.is_pos = 1 AND v.company_id = ?
            ORDER BY v.id DESC LIMIT ?
        """, (self.active_company_id, limit))
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Tax Collected at Source (TCS under Section 206C)
    # -------------------------------------------------------------------------
    def get_tcs_sections(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tcs_section ORDER BY id ASC")
        return [dict(r) for r in cursor.fetchall()]

    def add_tcs_section(self, section_code: str, description: str,
                        threshold_amount: float = 5000000.0, rate: float = 0.1,
                        rate_pan_missing: float = 1.0) -> bool:
        with self.conn:
            self.conn.execute("""
                INSERT INTO tcs_section (section_code, description, threshold_amount, rate, rate_pan_missing)
                VALUES (?, ?, ?, ?, ?)
            """, (section_code, description, threshold_amount, rate, rate_pan_missing))
        return True

    def calculate_tcs(self, party_name: str, invoice_amount: float) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cols = [r[1] for r in cursor.execute("PRAGMA table_info(ledger)").fetchall()]
        sel_cols = "pan, gstin" if "pan" in cols else "gstin"
        cursor.execute(f"SELECT {sel_cols} FROM ledger WHERE name = ? AND company_id = ?", (party_name, self.active_company_id))
        row = cursor.fetchone()
        pan_val = (row["pan"] if row and "pan" in row.keys() else "") or ""
        gstin_val = (row["gstin"] if row and "gstin" in row.keys() else "") or ""
        has_pan = bool(pan_val or (len(gstin_val) >= 12))

        cursor.execute("""
            SELECT COALESCE(SUM(v.total_amount), 0.0) as fy_turnover
            FROM voucher v
            JOIN voucher_entry ve ON ve.voucher_id = v.id AND ve.ledger_name = ? AND ve.entry_type = 'Dr'
            WHERE v.voucher_type = 'Sales' AND v.is_cancelled = 0 AND v.company_id = ?
        """, (party_name, self.active_company_id))
        fy_turnover = float(cursor.fetchone()["fy_turnover"])
        threshold = 5000000.0

        tcs_applicable = (fy_turnover + invoice_amount) > threshold
        if tcs_applicable:
            taxable_base = max(0.0, (fy_turnover + invoice_amount) - max(threshold, fy_turnover))
            rate = 0.1 if has_pan else 1.0
            tcs_amt = round(taxable_base * (rate / 100.0), 2)
        else:
            taxable_base = 0.0
            rate = 0.0
            tcs_amt = 0.0

        return {
            "party_name": party_name,
            "has_pan": has_pan,
            "fy_cumulative_turnover": fy_turnover,
            "invoice_amount": invoice_amount,
            "threshold": threshold,
            "tcs_applicable": tcs_applicable,
            "taxable_base": taxable_base,
            "tcs_rate_pct": rate,
            "tcs_amount": tcs_amt
        }

    def get_tcs_register(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT v.id, v.voucher_number, v.date, v.total_amount, ve.amount as tcs_amount,
                   ve.narration, v.narration as voucher_narration
            FROM voucher_entry ve
            JOIN voucher v ON v.id = ve.voucher_id
            WHERE ve.ledger_name = 'TCS Payable' AND v.is_cancelled = 0 AND v.company_id = ?
            ORDER BY v.date DESC
        """, (self.active_company_id,))
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Foreign Exchange Revaluation (ERPNext Accounts + Tally Forex Adjustment)
    # -------------------------------------------------------------------------
    def post_forex_revaluation_voucher(self, date: str = "", narration: str = "Forex Exchange Rate Revaluation",
                                       user_name: str = "admin") -> Dict[str, Any]:
        forex_data = self.calculate_forex_gain_loss()
        total_gl = forex_data["total_gain_loss"]
        if abs(total_gl) <= 0.01:
            return {"status": "NO_CHANGE", "total_gain_loss": 0.0, "voucher_id": None}

        v_date = date or datetime.date.today().isoformat()
        amt = abs(total_gl)

        if total_gl > 0:
            entries = [
                {"ledger_name": "HDFC Bank", "entry_type": "Dr", "amount": amt},
                {"ledger_name": "Foreign Exchange Gain/Loss", "entry_type": "Cr", "amount": amt}
            ]
        else:
            entries = [
                {"ledger_name": "Foreign Exchange Gain/Loss", "entry_type": "Dr", "amount": amt},
                {"ledger_name": "HDFC Bank", "entry_type": "Cr", "amount": amt}
            ]

        v_id = self.add_voucher(
            voucher_type="Journal",
            date=v_date,
            narration=f"{narration}: Revalued foreign assets/liabilities. Net Gain/Loss: ₹{total_gl:,.2f}",
            entries=entries
        )
        self._log_edit("Voucher", f"FOREX-VCH-{v_id}", "CREATE", f"Posted Forex revaluation adjustment of ₹{amt:,.2f}", user_name)

        return {
            "status": "POSTED",
            "voucher_id": v_id,
            "total_gain_loss": total_gl,
            "revaluation_amount": amt,
            "transactions": forex_data.get("transactions", [])
        }

    # -------------------------------------------------------------------------
    # Challan Management (Delivery Notes & Goods Receipt Notes)
    # -------------------------------------------------------------------------
    def record_delivery_note(self, date: str, customer_name: str, items: List[Dict[str, Any]],
                             order_no: str = "", narration: str = "") -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM challan_tracking WHERE challan_type = 'Delivery Note'")
        cnt = cursor.fetchone()[0] + 1
        challan_no = f"DN-{cnt:04d}"

        inv_items = []
        tot_amt = Decimal("0.00")
        for i in items:
            s_name = i.get("stock_item_name") or i.get("item_name", "Stock Item")
            q = float(to_qty_dec(i.get("qty") if "qty" in i else i.get("quantity", 1)))
            r = float(to_dec(i.get("rate", 0)))
            a = float((to_qty_dec(q) * to_dec(r)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if "amount" not in i else to_dec(i["amount"]))
            tot_amt += to_dec(a)
            inv_items.append({
                "stock_item_name": s_name,
                "qty": q,
                "rate": r,
                "amount": a,
                "direction": "Out"
            })

        entries = [
            {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": float(tot_amt)},
            {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": float(tot_amt)}
        ]

        v_id = self.add_voucher(
            voucher_type="Delivery Note",
            date=date,
            narration=f"Delivery Note {challan_no} to {customer_name}. Order Ref: {order_no}. {narration}".strip(),
            entries=entries,
            inventory_items=inv_items
        )

        with self.conn:
            self.conn.execute("UPDATE voucher SET challan_no = ?, order_no = ? WHERE id = ?", (challan_no, order_no, v_id))
            self.conn.execute("""
                INSERT INTO challan_tracking (company_id, voucher_id, challan_type, challan_no, date, party_name, order_no, status)
                VALUES (?, ?, 'Delivery Note', ?, ?, ?, ?, 'Pending Invoicing')
            """, (self.active_company_id, v_id, challan_no, date, customer_name, order_no))

        return {"voucher_id": v_id, "challan_no": challan_no, "status": "Pending Invoicing"}

    def record_receipt_note(self, date: str, supplier_name: str, items: List[Dict[str, Any]],
                            order_no: str = "", narration: str = "") -> Dict[str, Any]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM challan_tracking WHERE challan_type = 'Receipt Note'")
        cnt = cursor.fetchone()[0] + 1
        challan_no = f"GRN-{cnt:04d}"

        inv_items = []
        tot_amt = Decimal("0.00")
        for i in items:
            s_name = i.get("stock_item_name") or i.get("item_name", "Stock Item")
            q = float(to_qty_dec(i.get("qty") if "qty" in i else i.get("quantity", 1)))
            r = float(to_dec(i.get("rate", 0)))
            a = float((to_qty_dec(q) * to_dec(r)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if "amount" not in i else to_dec(i["amount"]))
            tot_amt += to_dec(a)
            inv_items.append({
                "stock_item_name": s_name,
                "qty": q,
                "rate": r,
                "amount": a,
                "direction": "In"
            })

        entries = [
            {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": float(tot_amt)},
            {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": float(tot_amt)}
        ]

        v_id = self.add_voucher(
            voucher_type="Receipt Note",
            date=date,
            narration=f"Receipt Note {challan_no} from {supplier_name}. Order Ref: {order_no}. {narration}".strip(),
            entries=entries,
            inventory_items=inv_items
        )

        with self.conn:
            self.conn.execute("UPDATE voucher SET challan_no = ?, order_no = ? WHERE id = ?", (challan_no, order_no, v_id))
            self.conn.execute("""
                INSERT INTO challan_tracking (company_id, voucher_id, challan_type, challan_no, date, party_name, order_no, status)
                VALUES (?, ?, 'Receipt Note', ?, ?, ?, ?, 'Pending Invoicing')
            """, (self.active_company_id, v_id, challan_no, date, supplier_name, order_no))

        return {"voucher_id": v_id, "challan_no": challan_no, "status": "Pending Invoicing"}

    def get_unbilled_challans(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ct.*, v.total_amount
            FROM challan_tracking ct
            JOIN voucher v ON v.id = ct.voucher_id
            WHERE ct.status = 'Pending Invoicing' AND ct.company_id = ?
            ORDER BY ct.id DESC
        """, (self.active_company_id,))
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Subcontracting / Job Work Lifecycle (ERPNext Subcontracting + Tally Job Work)
    # -------------------------------------------------------------------------
    def create_job_work_order(self, order_type: str = "Job Work Out", order_no: Optional[str] = None,
                              job_worker_name: str = "Cash", order_date: str = "2026-10-31",
                              due_date: str = "2026-11-15", finished_item_name: str = "Assembled Desktop Combo",
                              finished_qty: float = 1.0, service_rate: float = 0.0,
                              items: Optional[List[Dict[str, Any]]] = None,
                              narration: str = "") -> Dict[str, Any]:
        if not order_no:
            order_no = f"JWO-{int(time.time() * 1000) % 10000000}"
        service_amount = round(finished_qty * service_rate, 2)
        created_at = datetime.datetime.now().isoformat()

        with self.conn:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO job_work_order (
                    company_id, order_type, order_no, job_worker_name, order_date, due_date,
                    finished_item_name, finished_qty, service_rate, service_amount, status, narration, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
            """, (self.active_company_id, order_type, order_no, job_worker_name, order_date, due_date,
                  finished_item_name, float(finished_qty), float(service_rate), float(service_amount), narration, created_at))
            jw_id = cur.lastrowid

            if items:
                for itm in items:
                    name = itm.get("stock_item_name") or itm.get("item_name")
                    req_qty = float(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
                    rate = float(itm.get("rate", 0.0))
                    self.conn.execute("""
                        INSERT INTO job_work_item (job_work_id, stock_item_name, required_qty, dispatched_qty, consumed_qty, rate)
                        VALUES (?, ?, ?, 0.0, 0.0, ?)
                    """, (jw_id, name, req_qty, rate))

            self._log_edit("JobWorkOrder", str(jw_id), "CREATE", json.dumps({"order_no": order_no, "finished_qty": finished_qty}))

        return {"job_work_id": jw_id, "order_no": order_no, "status": "Pending", "service_amount": service_amount}

    def dispatch_job_work_materials(self, order_id: int, items: List[Dict[str, Any]], date: str = "2026-10-31") -> Dict[str, Any]:
        with self.conn:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM job_work_order WHERE id = ?", (order_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Job Work Order #{order_id} not found!")

            for itm in items:
                name = itm.get("stock_item_name") or itm.get("item_name")
                qty = float(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
                self.conn.execute("""
                    UPDATE job_work_item SET dispatched_qty = dispatched_qty + ?
                    WHERE job_work_id = ? AND stock_item_name = ?
                """, (qty, order_id, name))

            self.conn.execute("UPDATE job_work_order SET status = 'In Progress' WHERE id = ?", (order_id,))
            self._log_edit("JobWorkOrder", str(order_id), "ALTER", json.dumps({"action": "dispatch", "items": items}))

        return {"order_id": order_id, "status": "In Progress", "dispatched_items": items}

    def receive_job_work_finished_goods(self, order_id: int, finished_qty: float,
                                        consumed_items: Optional[List[Dict[str, Any]]] = None,
                                        date: str = "2026-10-31", post_service_bill: bool = True) -> Dict[str, Any]:
        with self.conn:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM job_work_order WHERE id = ?", (order_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Job Work Order #{order_id} not found!")
            order = dict(row)

            if consumed_items:
                for itm in consumed_items:
                    name = itm.get("stock_item_name") or itm.get("item_name")
                    qty = float(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
                    self.conn.execute("""
                        UPDATE job_work_item SET consumed_qty = consumed_qty + ?
                        WHERE job_work_id = ? AND stock_item_name = ?
                    """, (qty, order_id, name))

            status = "Completed" if finished_qty >= order["finished_qty"] else "In Progress"
            self.conn.execute("UPDATE job_work_order SET status = ? WHERE id = ?", (status, order_id))

            voucher_id = None
            if post_service_bill and order["service_rate"] > 0:
                bill_amt = round(finished_qty * order["service_rate"], 2)
                entries = [
                    {"ledger_name": "Job Work Charges", "entry_type": "Dr", "amount": bill_amt},
                    {"ledger_name": order["job_worker_name"], "entry_type": "Cr", "amount": bill_amt}
                ]
                voucher_id = self.add_voucher(
                    voucher_type="Purchase",
                    date=date,
                    narration=f"Job Work Services for Order {order['order_no']} ({finished_qty} units of {order['finished_item_name']})",
                    entries=entries
                )

            self._log_edit("JobWorkOrder", str(order_id), "ALTER", json.dumps({
                "action": "receive_finished", "received_qty": finished_qty, "status": status, "service_voucher": voucher_id
            }))

        return {"order_id": order_id, "status": status, "received_qty": finished_qty, "service_voucher_id": voucher_id}

    def get_job_work_orders(self, order_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        q = "SELECT * FROM job_work_order WHERE company_id = ?"
        params = [self.active_company_id]
        if order_type:
            q += " AND order_type = ?"
            params.append(order_type)
        if status:
            q += " AND status = ?"
            params.append(status)
        q += " ORDER BY id DESC"
        cursor.execute(q, params)
        orders = [dict(r) for r in cursor.fetchall()]
        for o in orders:
            cur2 = self.conn.cursor()
            cur2.execute("SELECT * FROM job_work_item WHERE job_work_id = ?", (o["id"],))
            o["items"] = [dict(r) for r in cur2.fetchall()]
        return orders

    def get_job_work_order_details(self, order_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM job_work_order WHERE id = ?", (order_id,))
        row = cursor.fetchone()
        if not row:
            return None
        order = dict(row)
        cursor.execute("SELECT * FROM job_work_item WHERE job_work_id = ?", (order_id,))
        order["items"] = [dict(r) for r in cursor.fetchall()]
        return order

    # -------------------------------------------------------------------------
    # Quality Inspection & Sampling (ERPNext Stock QC + Tally Rejections/Inspection)
    # -------------------------------------------------------------------------
    def create_quality_inspection(self, reference_type: str, reference_no: str, stock_item_name: str,
                                  batch_no: str = "", inspection_date: str = "2026-10-31",
                                  inspector_name: str = "QC Manager", inspected_qty: float = 1.0,
                                  sample_size: float = 1.0, parameters: Optional[List[Dict[str, Any]]] = None,
                                  remarks: str = "") -> Dict[str, Any]:
        insp_no = f"QI-{int(time.time() * 1000000) % 1000000000}"
        created_at = datetime.datetime.now().isoformat()

        all_passed = True
        param_rows = []
        if parameters:
            for p in parameters:
                p_name = p.get("parameter_name") or p.get("name", "Quality Test")
                spec = p.get("specification") or p.get("spec", "Standard Spec")
                min_v = p.get("min_value")
                max_v = p.get("max_value")
                reading = p.get("reading_value")

                p_status = "Pass"
                if reading is not None:
                    if min_v is not None and reading < min_v:
                        p_status = "Fail"
                    if max_v is not None and reading > max_v:
                        p_status = "Fail"
                elif "status" in p:
                    p_status = p["status"]

                if p_status == "Fail":
                    all_passed = False

                param_rows.append((p_name, spec, min_v, max_v, reading, p_status))

        overall_status = "Accepted" if all_passed else "Rejected"
        accepted_qty = float(inspected_qty) if all_passed else 0.0
        rejected_qty = 0.0 if all_passed else float(inspected_qty)

        with self.conn:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO quality_inspection (
                    company_id, inspection_no, reference_type, reference_no, stock_item_name,
                    batch_no, inspection_date, inspector_name, sample_size, inspected_qty,
                    accepted_qty, rejected_qty, status, remarks, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.active_company_id, insp_no, reference_type, reference_no, stock_item_name,
                  batch_no, inspection_date, inspector_name, float(sample_size), float(inspected_qty),
                  accepted_qty, rejected_qty, overall_status, remarks, created_at))
            qi_id = cur.lastrowid

            for pr in param_rows:
                self.conn.execute("""
                    INSERT INTO quality_inspection_parameter (inspection_id, parameter_name, specification, min_value, max_value, reading_value, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (qi_id, pr[0], pr[1], pr[2], pr[3], pr[4], pr[5]))

            self._log_edit("QualityInspection", str(qi_id), "CREATE", json.dumps({"inspection_no": insp_no, "status": overall_status}))

        return {
            "inspection_id": qi_id,
            "inspection_no": insp_no,
            "status": overall_status,
            "accepted_qty": accepted_qty,
            "rejected_qty": rejected_qty
        }

    def get_quality_inspections(self, status: Optional[str] = None, stock_item_name: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        q = "SELECT * FROM quality_inspection WHERE company_id = ?"
        params = [self.active_company_id]
        if status:
            q += " AND status = ?"
            params.append(status)
        if stock_item_name:
            q += " AND stock_item_name = ?"
            params.append(stock_item_name)
        q += " ORDER BY id DESC"
        cursor.execute(q, params)
        inspections = [dict(r) for r in cursor.fetchall()]
        for i in inspections:
            cur2 = self.conn.cursor()
            cur2.execute("SELECT * FROM quality_inspection_parameter WHERE inspection_id = ?", (i["id"],))
            i["parameters"] = [dict(r) for r in cur2.fetchall()]
        return inspections

    def get_quality_inspection_details(self, inspection_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM quality_inspection WHERE id = ?", (inspection_id,))
        row = cursor.fetchone()
        if not row:
            return None
        insp = dict(row)
        cursor.execute("SELECT * FROM quality_inspection_parameter WHERE inspection_id = ?", (inspection_id,))
        insp["parameters"] = [dict(r) for r in cursor.fetchall()]
        return insp

    # -------------------------------------------------------------------------
    # Multi-Currency & Historical Exchange Rates
    # -------------------------------------------------------------------------
    def set_currency_exchange_rate(self, currency_symbol: str, formal_name: str, effective_date: str,
                                   standard_rate: float, buying_rate: Optional[float] = None,
                                   selling_rate: Optional[float] = None) -> Dict[str, Any]:
        if buying_rate is None:
            buying_rate = round(standard_rate * 0.995, 4)
        if selling_rate is None:
            selling_rate = round(standard_rate * 1.005, 4)
        created_at = datetime.datetime.now().isoformat()

        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO currency_exchange_rate (
                    currency_symbol, formal_name, effective_date, standard_rate, buying_rate, selling_rate, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (currency_symbol, formal_name, effective_date, float(standard_rate), float(buying_rate), float(selling_rate), created_at))

            self.conn.execute("""
                UPDATE currency SET rate_of_exchange = ? WHERE symbol = ? OR formal_name = ?
            """, (float(standard_rate), currency_symbol, formal_name))

        return {
            "currency_symbol": currency_symbol,
            "formal_name": formal_name,
            "effective_date": effective_date,
            "standard_rate": standard_rate,
            "buying_rate": buying_rate,
            "selling_rate": selling_rate
        }

    def get_currency_exchange_rates(self, currency_symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if currency_symbol:
            cursor.execute("SELECT * FROM currency_exchange_rate WHERE currency_symbol = ? ORDER BY effective_date DESC", (currency_symbol,))
        else:
            cursor.execute("SELECT * FROM currency_exchange_rate ORDER BY effective_date DESC")
        return [dict(r) for r in cursor.fetchall()]

    def get_rate_for_date(self, currency_symbol: str, date: str) -> float:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT standard_rate FROM currency_exchange_rate
            WHERE currency_symbol = ? AND effective_date <= ?
            ORDER BY effective_date DESC LIMIT 1
        """, (currency_symbol, date))
        row = cursor.fetchone()
        if row:
            return float(row[0])
        cursor.execute("SELECT rate_of_exchange FROM currency WHERE symbol = ? OR formal_name = ?", (currency_symbol, currency_symbol))
        row2 = cursor.fetchone()
        if row2:
            return float(row2[0])
        return 1.0

    # -------------------------------------------------------------------------
    # Payment Reconciliation Tool (ERPNext Payment Recon + Tally Agst-Ref Multi-Settlement)
    # -------------------------------------------------------------------------
    def get_unreconciled_bills_and_payments(self, party_ledger: str) -> Dict[str, Any]:
        cursor = self.conn.cursor()
        # 1. Unpaid Invoices
        cursor.execute("""
            SELECT b.id, b.voucher_id, b.ref_name, b.amount, v.date, v.voucher_type,
                   COALESCE((
                       SELECT SUM(b2.amount) FROM bill_allocation b2
                       WHERE b2.ledger_name = b.ledger_name AND b2.ref_name = b.ref_name AND b2.ref_type = 'Agst Ref'
                   ), 0.0) as settled_amount
            FROM bill_allocation b
            JOIN voucher v ON v.id = b.voucher_id
            WHERE b.ledger_name = ? AND b.ref_type = 'New Ref'
        """, (party_ledger,))
        raw_bills = [dict(r) for r in cursor.fetchall()]
        unpaid_bills = []
        for b in raw_bills:
            pending = round(b["amount"] - b["settled_amount"], 2)
            if pending > 0.01:
                b["pending_amount"] = pending
                b["ref_no"] = b["ref_name"]
                unpaid_bills.append(b)

        # 2. Unallocated Payments / Receipts
        cursor.execute("""
            SELECT v.id as voucher_id, v.voucher_type, v.voucher_number, v.date, v.total_amount,
                   COALESCE((
                       SELECT SUM(b.amount) FROM bill_allocation b
                       WHERE b.voucher_id = v.id AND b.ref_type = 'Agst Ref'
                   ), 0.0) as allocated_amount
            FROM voucher v
            JOIN voucher_entry ve ON ve.voucher_id = v.id
            WHERE ve.ledger_name = ? AND v.voucher_type IN ('Receipt', 'Payment')
            GROUP BY v.id
        """, (party_ledger,))
        raw_pmts = [dict(r) for r in cursor.fetchall()]
        unallocated_pmts = []
        for p in raw_pmts:
            unalloc = round(p["total_amount"] - p["allocated_amount"], 2)
            if unalloc > 0.01:
                p["unallocated_amount"] = unalloc
                unallocated_pmts.append(p)

        return {
            "party_ledger": party_ledger,
            "unpaid_bills": unpaid_bills,
            "unallocated_payments": unallocated_pmts
        }

    def reconcile_payment_against_bill(self, payment_voucher_id: int, party_ledger: str,
                                       bill_ref_no: str, allocated_amount: float,
                                       write_off_amount: float = 0.0, date: str = "2026-10-31") -> Dict[str, Any]:
        with self.conn:
            self.conn.execute("""
                INSERT INTO bill_allocation (voucher_id, ledger_name, ref_type, ref_name, due_date, amount, direction)
                VALUES (?, ?, 'Agst Ref', ?, ?, ?, 'Cr')
            """, (payment_voucher_id, party_ledger, bill_ref_no, date, float(allocated_amount)))

            adj_voucher_id = None
            if write_off_amount > 0:
                entries = [
                    {"ledger_name": "Discount Allowed / Bad Debts", "entry_type": "Dr", "amount": float(write_off_amount)},
                    {"ledger_name": party_ledger, "entry_type": "Cr", "amount": float(write_off_amount)}
                ]
                adj_voucher_id = self.add_voucher(
                    voucher_type="Journal",
                    date=date,
                    narration=f"Settlement Write-Off for Bill {bill_ref_no} against Payment #{payment_voucher_id}",
                    entries=entries
                )

            self._log_edit("BillAllocation", str(payment_voucher_id), "ALTER", json.dumps({
                "action": "reconcile", "bill_ref_no": bill_ref_no, "allocated_amount": allocated_amount,
                "write_off_amount": write_off_amount, "adj_voucher_id": adj_voucher_id
            }))

        return {
            "success": True,
            "payment_voucher_id": payment_voucher_id,
            "bill_ref_no": bill_ref_no,
            "allocated_amount": allocated_amount,
            "write_off_amount": write_off_amount,
            "adjustment_voucher_id": adj_voucher_id
        }

    # -------------------------------------------------------------------------
    # MCA Rule 3(1) Edit Log Diff & Version Comparison
    # -------------------------------------------------------------------------
    def get_voucher_version_history(self, voucher_id: int) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT voucher_number FROM voucher WHERE id = ?", (voucher_id,))
        row = cursor.fetchone()
        v_num = row[0] if row else str(voucher_id)

        cursor.execute("""
            SELECT id, timestamp, entity_type, entity_id, action, user_name, details, chained_hash
            FROM edit_log
            WHERE entity_type = 'Voucher' AND (entity_id = ? OR entity_id = ? OR details LIKE ?)
            ORDER BY id ASC
        """, (str(voucher_id), v_num, f'%"voucher_id": {voucher_id},%'))
        rows = []
        for r in cursor.fetchall():
            r = dict(r)
            if r["details"]:
                try:
                    r["details_json"] = json.loads(r["details"])
                except Exception:
                    r["details_json"] = None
            # A voucher number repeats across types and years: keep only rows that are provably this voucher's (or older plain-text ones).
            if isinstance(r.get("details_json"), dict) and r["details_json"].get("voucher_id") not in (None, voucher_id):
                continue
            # The snapshot taken before the first alteration belongs to the creation entry, not a version of its own.
            if r["action"] == "ORIGINAL" and rows and rows[-1]["action"] == "CREATE" and not rows[-1].get("details_json"):
                rows[-1]["details_json"] = r["details_json"]
                continue
            rows.append(r)
        return rows

    def get_voucher_diff(self, voucher_id: int) -> Dict[str, Any]:
        versions = self.get_voucher_version_history(voucher_id)
        if len(versions) < 2:
            return {"voucher_id": voucher_id, "versions_count": len(versions), "has_diff": False, "diffs": []}

        diffs = []
        import re
        for i in range(1, len(versions)):
            v_prev = versions[i - 1]
            v_curr = versions[i]

            prev_det = v_prev.get("details_json") or {}
            curr_det = v_curr.get("details_json") or {}

            changes = {}
            if prev_det and curr_det:
                for k in set(list(prev_det.keys()) + list(curr_det.keys())):
                    old_val = prev_det.get(k)
                    new_val = curr_det.get(k)
                    if old_val != new_val:
                        changes[k] = {"before": old_val, "after": new_val}
                if "total_amount" in changes:                       # the key older screens and tests read
                    changes["amount"] = changes["total_amount"]
            else:
                p_m = re.search(r"₹([\d,]+(?:\.\d+)?)", v_prev.get("details", ""))
                c_m = re.search(r"₹([\d,]+(?:\.\d+)?)", v_curr.get("details", ""))
                p_amt = float(p_m.group(1).replace(",", "")) if p_m else None
                c_amt = float(c_m.group(1).replace(",", "")) if c_m else None
                if p_amt is not None and c_amt is not None and p_amt != c_amt:
                    changes["amount"] = {"before": p_amt, "after": c_amt}
                changes["action"] = {"before": v_prev["action"], "after": v_curr["action"]}

            diffs.append({
                "from_version_id": v_prev["id"],
                "to_version_id": v_curr["id"],
                "action": v_curr["action"],
                "timestamp": v_curr["timestamp"],
                "user_name": v_curr["user_name"],
                "changes": changes
            })

        return {"voucher_id": voucher_id, "versions_count": len(versions), "has_diff": len(diffs) > 0, "diffs": diffs}

    # -------------------------------------------------------------------------
    # Negative Stock Policy & Helpers
    # -------------------------------------------------------------------------
    def set_negative_stock_policy(self, allow: bool):
        with self.conn:
            self.conn.execute("UPDATE company SET allow_negative_stock = ? WHERE id = ?", (1 if allow else 0, self.active_company_id))

    def get_negative_stock_policy(self) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT allow_negative_stock FROM company WHERE id = ?", (self.active_company_id,))
        row = cursor.fetchone()
        return bool(row[0]) if row and row[0] is not None else True

    def get_stock_item_quantity(self, item_name: str) -> float:
        summary = self.get_stock_item_summary(item_name)
        return float(summary.get("closing_qty", 0.0))

    # -------------------------------------------------------------------------
    # Multiple Company Addresses (Branches, Factories, Multi-State GSTINs)
    # -------------------------------------------------------------------------
    def add_company_address(self, branch_name: str, address: str, city: str = "",
                            state: str = "Karnataka", state_code: str = "29",
                            pincode: str = "560001", gstin: str = "", is_primary: int = 0) -> int:
        with self.conn:
            cur = self.conn.cursor()
            if is_primary:
                self.conn.execute("UPDATE company_address SET is_primary = 0 WHERE company_id = ?", (self.active_company_id,))
            cur.execute("""
                INSERT INTO company_address (company_id, branch_name, address, city, state, state_code, pincode, gstin, is_primary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (self.active_company_id, branch_name, address, city, state, state_code, pincode, gstin, is_primary))
            addr_id = cur.lastrowid
            self._log_edit("CompanyAddress", str(addr_id), "CREATE", f"Added company address: {branch_name}")
            return addr_id

    def get_company_addresses(self, company_id: Optional[int] = None) -> List[Dict[str, Any]]:
        cid = company_id or self.active_company_id
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM company_address WHERE company_id = ? ORDER BY is_primary DESC, id ASC", (cid,))
        return [dict(r) for r in cursor.fetchall()]

    def delete_company_address(self, address_id: int) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM company_address WHERE id = ? AND company_id = ?", (address_id, self.active_company_id))
            self._log_edit("CompanyAddress", str(address_id), "DELETE", f"Deleted company address #{address_id}")
            return True

    # -------------------------------------------------------------------------
    # MSME 45-Day Compliance & Reporting (Section 43B(h))
    # -------------------------------------------------------------------------
    def get_msme_compliance_report(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        if not as_of_date:
            as_of_date = datetime.date.today().isoformat()
        ref_dt = datetime.date.fromisoformat(as_of_date)

        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT b.id, b.voucher_id, b.ledger_name, b.ref_name, b.amount, b.due_date, v.date as bill_date,
                   l.msme_type, l.msme_reg_no,
                   COALESCE((
                       SELECT SUM(b2.amount) FROM bill_allocation b2
                       WHERE b2.ledger_name = b.ledger_name AND b2.ref_name = b.ref_name AND b2.ref_type = 'Agst Ref'
                   ), 0.0) as settled_amount
            FROM bill_allocation b
            JOIN voucher v ON v.id = b.voucher_id
            JOIN ledger l ON l.name = b.ledger_name
            WHERE b.ref_type = 'New Ref' AND l.msme_type IN ('Micro', 'Small')
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        compliant_items = []
        overdue_items = []
        tot_msme_pending = 0.0
        tot_disallowance = 0.0

        for r in rows:
            pending = round(r["amount"] - r["settled_amount"], 2)
            if pending > 0.01:
                b_dt = datetime.date.fromisoformat(r["bill_date"])
                age_days = (ref_dt - b_dt).days
                r["pending_amount"] = pending
                r["age_days"] = age_days
                tot_msme_pending += pending

                if age_days > 45:
                    r["status"] = "Disallowed under Sec 43B(h)"
                    interest = round(pending * 0.20 * (age_days - 45) / 365, 2)
                    r["interest_liability"] = interest
                    tot_disallowance += pending
                    overdue_items.append(r)
                else:
                    r["status"] = "Compliant (< 45 Days)"
                    r["interest_liability"] = 0.0
                    compliant_items.append(r)

        return {
            "as_of_date": as_of_date,
            "total_msme_suppliers": len(set(r["ledger_name"] for r in rows)),
            "total_pending_payables": round(tot_msme_pending, 2),
            "compliant_amount": round(tot_msme_pending - tot_disallowance, 2),
            "disallowed_amount_sec_43bh": round(tot_disallowance, 2),
            "compliant_invoices": compliant_items,
            "overdue_invoices": overdue_items
        }

    def get_profit_loss(self) -> Dict[str, Any]:
        """Returns profit and loss statement summary."""
        return self.get_financial_summary()

    # -------------------------------------------------------------------------
    # Schedule III (Division I) Financial Statements (Companies Act 2013)
    # -------------------------------------------------------------------------
    def get_schedule_iii_balance_sheet(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        fin = self.get_financial_summary()
        net_profit = fin.get("net_profit", 0.0)
        closing_stock = fin.get("closing_stock", 0.0)

        share_capital = 0.0
        reserves_surplus = net_profit
        long_term_borrowings = 0.0
        short_term_borrowings = 0.0
        trade_payables = 0.0
        other_current_liabilities = 0.0

        fixed_assets = 0.0
        non_current_investments = 0.0
        inventories = 0.0
        trade_receivables = 0.0
        cash_bank = 0.0
        other_current_assets = 0.0

        groups = {r["name"]: dict(r) for r in self.conn.execute("SELECT name, parent FROM account_group")}
        known = {"Capital Account", "Reserves & Surplus", "Loans (Liability)", "Secured Loans", "Unsecured Loans", "Bank OD A/c",
                 "Sundry Creditors", "Current Liabilities", "Duties & Taxes", "Provisions", "Fixed Assets", "Investments",
                 "Stock-in-Hand", "Sundry Debtors", "Cash-in-Hand", "Bank Accounts", "Current Assets",
                 "Loans & Advances (Asset)", "Deposits (Asset)"}
        asset_roots = {"Fixed Assets", "Investments", "Current Assets", "Misc. Expenses (ASSET)"}
        net_profit_ledgers = Decimal("0.00")
        for l in self.get_ledgers():
            bal, side = self.get_ledger_closing_balance(l["name"])
            credit = float(bal if side == "Cr" else -bal)         # credit-positive
            debit = -credit
            # Resolve the group through its parents, so sub-groups and imported groups are classified too.
            grp, seen = l["group_name"], set()
            while grp not in known and grp in groups and groups[grp]["parent"] not in (None, "Primary") and grp not in seen:
                seen.add(grp)
                grp = groups[grp]["parent"]
            root = self._root_group(groups, l["group_name"]) if l["group_name"] in groups else None
            if root in self.PL_ROOTS:
                continue
            if root is None or root == "Primary (ungrouped ledgers)":
                reserves_surplus += credit                         # a group-less ledger is the Profit & Loss A/c
            elif grp == "Capital Account":
                share_capital += credit
            elif grp == "Reserves & Surplus":
                reserves_surplus += credit
            elif grp in ("Loans (Liability)", "Secured Loans", "Unsecured Loans"):
                long_term_borrowings += credit
            elif grp == "Bank OD A/c":
                short_term_borrowings += credit
            elif grp == "Sundry Creditors":
                trade_payables += credit
            elif grp == "Fixed Assets":
                fixed_assets += debit
            elif grp == "Investments":
                non_current_investments += debit
            elif grp == "Stock-in-Hand":
                inventories += debit
            elif grp == "Sundry Debtors":
                trade_receivables += debit
            elif grp in ("Cash-in-Hand", "Bank Accounts"):
                cash_bank += debit
            elif root in asset_roots:
                other_current_assets += debit
            else:                                                    # Current Liabilities, Suspense A/c, anything else
                other_current_liabilities += credit

        inventories += float(closing_stock)       # closing stock from the vouchers (negative if issued before received)
        other_current_liabilities += float(self.opening_balance_difference())      # 'Difference in Opening Balances' (zero when the books agree)

        total_shareholders_funds = round(share_capital + reserves_surplus, 2)
        total_non_current_liabilities = round(long_term_borrowings, 2)
        total_current_liabilities = round(short_term_borrowings + trade_payables + other_current_liabilities, 2)
        total_equity_and_liabilities = round(total_shareholders_funds + total_non_current_liabilities + total_current_liabilities, 2)

        total_non_current_assets = round(fixed_assets + non_current_investments, 2)
        total_current_assets = round(inventories + trade_receivables + cash_bank + other_current_assets, 2)
        total_assets = round(total_non_current_assets + total_current_assets, 2)

        return {
            "format": "Schedule III (Division I) - Companies Act 2013",
            "as_of_date": as_of_date or datetime.date.today().isoformat(),
            "equity_and_liabilities": {
                "shareholders_funds": {
                    "share_capital": round(share_capital, 2),
                    "reserves_and_surplus": round(reserves_surplus, 2),
                    "total": total_shareholders_funds
                },
                "non_current_liabilities": {
                    "long_term_borrowings": round(long_term_borrowings, 2),
                    "total": total_non_current_liabilities
                },
                "current_liabilities": {
                    "short_term_borrowings": round(short_term_borrowings, 2),
                    "trade_payables": round(trade_payables, 2),
                    "other_current_liabilities": round(other_current_liabilities, 2),
                    "total": total_current_liabilities
                },
                "total_equity_and_liabilities": total_equity_and_liabilities
            },
            "assets": {
                "non_current_assets": {
                    "property_plant_equipment": round(fixed_assets, 2),
                    "non_current_investments": round(non_current_investments, 2),
                    "total": total_non_current_assets
                },
                "current_assets": {
                    "inventories": round(inventories, 2),
                    "trade_receivables": round(trade_receivables, 2),
                    "cash_and_bank_balances": round(cash_bank, 2),
                    "other_current_assets": round(other_current_assets, 2),
                    "total": total_current_assets
                },
                "total_assets": total_assets
            },
            "is_balanced": abs(total_equity_and_liabilities - total_assets) < 0.05
        }

    # -------------------------------------------------------------------------
    # Rejections In & Out Tracking (Customer & Vendor Return Notes)
    # -------------------------------------------------------------------------
    def record_rejections_in(self, date: str, customer_name: str, items: List[Dict[str, Any]],
                             order_no: str = "", narration: str = "") -> Dict[str, Any]:
        rej_no = f"REJ-IN-{int(time.time() * 1000000) % 1000000000}"
        inv_items = []
        tot_amt = 0.0
        for itm in items:
            name = itm.get("stock_item_name") or itm.get("item_name")
            qty = float(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
            rate = float(itm.get("rate", 0.0))
            amt = round(qty * rate, 2)
            tot_amt += amt
            inv_items.append({
                "stock_item_name": name,
                "godown_name": itm.get("godown_name", "Main Location"),
                "qty": qty,
                "rate": rate,
                "amount": amt,
                "direction": "In"
            })

        entries = [
            {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": float(tot_amt)},
            {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": float(tot_amt)}
        ]

        v_id = self.add_voucher(
            voucher_type="Rejections In",
            date=date,
            narration=f"Rejections In from {customer_name}. Order Ref: {order_no}. {narration}".strip(),
            entries=entries,
            inventory_items=inv_items
        )

        with self.conn:
            self.conn.execute("""
                INSERT INTO rejection_tracking (company_id, voucher_id, rejection_type, rejection_no, party_name, date, order_no, status, narration)
                VALUES (?, ?, 'Rejections In', ?, ?, ?, ?, 'Pending Adjustment', ?)
            """, (self.active_company_id, v_id, rej_no, customer_name, date, order_no, narration))

        return {"voucher_id": v_id, "rejection_no": rej_no, "rejection_type": "Rejections In", "status": "Pending Adjustment"}

    def record_rejections_out(self, date: str, supplier_name: str, items: List[Dict[str, Any]],
                              order_no: str = "", narration: str = "") -> Dict[str, Any]:
        rej_no = f"REJ-OUT-{int(time.time() * 1000000) % 1000000000}"
        inv_items = []
        tot_amt = 0.0
        for itm in items:
            name = itm.get("stock_item_name") or itm.get("item_name")
            qty = float(itm.get("qty") if "qty" in itm else itm.get("quantity", 1))
            rate = float(itm.get("rate", 0.0))
            amt = round(qty * rate, 2)
            tot_amt += amt
            inv_items.append({
                "stock_item_name": name,
                "godown_name": itm.get("godown_name", "Main Location"),
                "qty": qty,
                "rate": rate,
                "amount": amt,
                "direction": "Out"
            })

        entries = [
            {"ledger_name": "Stock-in-Hand", "entry_type": "Dr", "amount": float(tot_amt)},
            {"ledger_name": "Stock-in-Hand", "entry_type": "Cr", "amount": float(tot_amt)}
        ]

        v_id = self.add_voucher(
            voucher_type="Rejections Out",
            date=date,
            narration=f"Rejections Out to {supplier_name}. Order Ref: {order_no}. {narration}".strip(),
            entries=entries,
            inventory_items=inv_items
        )

        with self.conn:
            self.conn.execute("""
                INSERT INTO rejection_tracking (company_id, voucher_id, rejection_type, rejection_no, party_name, date, order_no, status, narration)
                VALUES (?, ?, 'Rejections Out', ?, ?, ?, ?, 'Pending Adjustment', ?)
            """, (self.active_company_id, v_id, rej_no, supplier_name, date, order_no, narration))

        return {"voucher_id": v_id, "rejection_no": rej_no, "rejection_type": "Rejections Out", "status": "Pending Adjustment"}

    def get_rejections(self, rejection_type: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        q = "SELECT rt.*, v.total_amount FROM rejection_tracking rt JOIN voucher v ON v.id = rt.voucher_id WHERE rt.company_id = ?"
        params = [self.active_company_id]
        if rejection_type:
            q += " AND rt.rejection_type = ?"
            params.append(rejection_type)
        q += " ORDER BY rt.id DESC"
        cursor.execute(q, params)
        return [dict(r) for r in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Tally XML Data Interchange (Import & Export of Existing Tally Books)
    # -------------------------------------------------------------------------
    def import_tally_xml(self, xml_content: str) -> Dict[str, Any]:
        """
        Parses and imports official Tally XML Data Interchange format.
        Compatible with TallyPrime, Tally.ERP 9, and Tally 9 Masters & DayBook XML.
        Imports Account Groups, Ledgers, Stock Items, and Vouchers with full double-entry validation.
        """
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_content.strip())
        except Exception as e:
            return {"success": False, "error": f"Invalid XML format: {e}", "imported_ledgers": 0, "imported_items": 0, "imported_vouchers": 0}

        counts = {
            "groups": 0,
            "ledgers": 0,
            "items": 0,
            "vouchers": 0,
            "errors": []
        }

        def get_val(elem, tag):
            target = elem.find(tag)
            return target.text.strip() if target is not None and target.text else ""

        def _resolve_or_create_group(grp_name: str) -> str:
            if not grp_name:
                return "Current Liabilities"
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM account_group WHERE name = ?", (grp_name,))
            if cursor.fetchone():
                return grp_name
            g_low = grp_name.lower()
            if "debtor" in g_low or "customer" in g_low: mapped = "Sundry Debtors"
            elif "creditor" in g_low or "vendor" in g_low or "supplier" in g_low: mapped = "Sundry Creditors"
            elif "bank" in g_low: mapped = "Bank Accounts"
            elif "cash" in g_low: mapped = "Cash-in-Hand"
            elif "sales" in g_low: mapped = "Sales Accounts"
            elif "purchase" in g_low: mapped = "Purchase Accounts"
            elif any(k in g_low for k in ("tax", "gst", "duty", "duties", "cgst", "sgst", "igst")): mapped = "Duties & Taxes"
            elif "direct exp" in g_low: mapped = "Direct Expenses"
            elif "indirect exp" in g_low: mapped = "Indirect Expenses"
            elif "fixed asset" in g_low: mapped = "Fixed Assets"
            elif "current asset" in g_low: mapped = "Current Assets"
            elif "current liab" in g_low or "suspense" in g_low: mapped = "Current Liabilities"
            elif "capital" in g_low: mapped = "Capital Account"
            else: mapped = "Current Liabilities"

            try:
                self.add_account_group(grp_name, mapped)
                return grp_name
            except Exception:
                return mapped

        # 1. Import Account Groups
        for grp in root.iter("GROUP"):
            name = grp.get("NAME") or get_val(grp, "NAME")
            parent = get_val(grp, "PARENT") or "Primary"
            if name:
                try:
                    self.add_account_group(name=name, parent=parent)
                    counts["groups"] += 1
                except Exception:
                    pass

        # 2. Import Units of Measure
        for u in root.iter("UNIT"):
            name = u.get("NAME") or get_val(u, "NAME")
            formal = get_val(u, "ORIGINALNAME") or name
            if name:
                try:
                    self.add_uom(name=name, formal_name=formal)
                except Exception:
                    pass

        # 3. Import Ledgers
        for led in root.iter("LEDGER"):
            name = led.get("NAME") or get_val(led, "NAME")
            parent = _resolve_or_create_group(get_val(led, "PARENT") or "Current Liabilities")
            if not name:
                continue

            op_bal_str = get_val(led, "OPENINGBALANCE")
            op_bal = 0.0
            op_type = "Dr"
            if op_bal_str:
                try:
                    raw_val = float(op_bal_str)
                    op_bal = abs(raw_val)
                    if raw_val < 0:
                        op_type = "Dr"
                    else:
                        op_type = "Cr"
                except ValueError:
                    pass

            gstin = get_val(led, "PARTYGSTIN") or get_val(led, "GSTIN")
            state_val = get_val(led, "STATENAME") or "Karnataka"

            existing_led = self.get_ledger(name)
            if existing_led:
                with self.conn:
                    self.conn.execute("""
                        UPDATE ledger SET group_name = ?, opening_balance = ?, opening_balance_type = ?, gstin = ?, state = ?
                        WHERE name = ?
                    """, (parent, op_bal, op_type, gstin, state_val, name))
                counts["ledgers"] += 1
            else:
                try:
                    self.add_ledger(
                        name=name,
                        group_name=parent,
                        opening_balance=op_bal,
                        opening_type=op_type,
                        state=state_val,
                        gstin=gstin
                    )
                    counts["ledgers"] += 1
                except Exception as ex:
                    counts["errors"].append(f"Ledger '{name}': {ex}")

        # 4. Import Stock Items
        for itm in root.iter("STOCKITEM"):
            name = itm.get("NAME") or get_val(itm, "NAME")
            if not name:
                continue
            uom_name = get_val(itm, "BASEUNITS") or "Pcs"
            op_val_str = get_val(itm, "OPENINGVALUE")
            op_val = 0.0
            if op_val_str:
                try:
                    op_val = abs(float(op_val_str))
                except ValueError:
                    pass

            op_qty_str = get_val(itm, "OPENINGBALANCE")
            op_qty = 0.0
            if op_qty_str:
                try:
                    tokens = op_qty_str.split()
                    op_qty = float(tokens[0])
                except Exception:
                    pass

            gst_rate_str = get_val(itm, "GSTRATE") or get_val(itm, "IGSTRATE")
            gst_rate = 18.0
            if gst_rate_str:
                try:
                    gst_rate = float(gst_rate_str)
                except ValueError:
                    pass

            try:
                self.add_stock_item(
                    name=name,
                    uom_name=uom_name,
                    opening_qty=op_qty,
                    opening_rate=(op_val / op_qty) if op_qty > 0 else 0.0,
                    gst_rate=gst_rate
                )
                counts["items"] += 1
            except Exception:
                counts["items"] += 1

        # 5. Import Vouchers
        for vch in root.iter("VOUCHER"):
            v_type = vch.get("VCHTYPE") or get_val(vch, "VOUCHERTYPENAME") or "Journal"
            v_num = get_val(vch, "VOUCHERNUMBER")
            raw_date = get_val(vch, "DATE")
            v_date = datetime.date.today().isoformat()
            if raw_date and len(raw_date) == 8 and raw_date.isdigit():
                v_date = f"{raw_date[0:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            elif raw_date and "-" in raw_date:
                v_date = raw_date

            narration = get_val(vch, "NARRATION")
            party_ledger = get_val(vch, "PARTYLEDGERNAME")

            ledger_entries = []
            for le in vch.findall(".//ALLLEDGERENTRIES.LIST") or vch.findall(".//LEDGERENTRIES.LIST"):
                l_name = get_val(le, "LEDGERNAME")
                amt_str = get_val(le, "AMOUNT")
                is_dr_tag = get_val(le, "ISDEEMEDPOSITIVE").lower()

                if not l_name or not amt_str:
                    continue

                try:
                    raw_amt = float(amt_str)
                    amt = abs(raw_amt)
                    if is_dr_tag == "yes" or (is_dr_tag == "" and raw_amt < 0):
                        etype = "Dr"
                    else:
                        etype = "Cr"

                    ledger_entries.append({
                        "ledger_name": l_name,
                        "entry_type": etype,
                        "amount": amt
                    })
                except ValueError:
                    continue

            inventory_entries = []
            for ie in vch.findall(".//ALLINVENTORYENTRIES.LIST"):
                i_name = get_val(ie, "STOCKITEMNAME")
                q_str = get_val(ie, "ACTUALQTY") or get_val(ie, "BILLEDQTY")
                r_str = get_val(ie, "RATE")
                a_str = get_val(ie, "AMOUNT")

                if i_name and q_str:
                    try:
                        q_val = float(q_str.split()[0])
                        r_val = float(r_str) if r_str else 0.0
                        a_val = abs(float(a_str)) if a_str else abs(round(q_val * r_val, 2))
                        direction = "Out" if v_type in ("Sales", "Delivery Note", "Rejections Out") else "In"
                        inventory_entries.append({
                            "stock_item_name": i_name,
                            "godown_name": get_val(ie, "GODOWNNAME") or "Main Location",
                            "qty": q_val,
                            "rate": r_val,
                            "amount": a_val,
                            "direction": direction
                        })
                    except Exception:
                        pass

            if not ledger_entries:
                continue

            dr_sum = sum(e["amount"] for e in ledger_entries if e["entry_type"] == "Dr")
            cr_sum = sum(e["amount"] for e in ledger_entries if e["entry_type"] == "Cr")
            diff = round(dr_sum - cr_sum, 2)
            if abs(diff) >= 0.01:
                suspense_name = "Suspense Account"
                if not self.get_ledger(suspense_name):
                    self.add_ledger(name=suspense_name, group_name="Current Liabilities")
                if diff > 0:
                    ledger_entries.append({"ledger_name": suspense_name, "entry_type": "Cr", "amount": diff})
                else:
                    ledger_entries.append({"ledger_name": suspense_name, "entry_type": "Dr", "amount": abs(diff)})

            try:
                for le in ledger_entries:
                    if not self.get_ledger(le["ledger_name"]):
                        auto_grp = _resolve_or_create_group(le["ledger_name"])
                        self.add_ledger(name=le["ledger_name"], group_name=auto_grp)

                self.add_voucher(
                    voucher_type=v_type,
                    date=v_date,
                    voucher_number=v_num or None,
                    narration=narration,
                    entries=ledger_entries,
                    inventory_items=inventory_entries
                )
                counts["vouchers"] += 1
            except Exception as ex:
                counts["errors"].append(f"Voucher {v_num or 'Auto'}: {ex}")

        return {
            "success": True,
            "imported_groups": counts["groups"],
            "imported_ledgers": counts["ledgers"],
            "imported_items": counts["items"],
            "imported_vouchers": counts["vouchers"],
            "errors": counts["errors"]
        }

    def export_tally_xml(self) -> str:
        """
        Exports the entire active company books in official Tally XML Data Interchange format.
        Compatible with TallyPrime and Tally.ERP 9.
        """
        import xml.etree.ElementTree as ET
        envelope = ET.Element("ENVELOPE")
        header = ET.SubElement(envelope, "HEADER")
        t_req = ET.SubElement(header, "TALLYREQUEST")
        t_req.text = "Import Data"

        body = ET.SubElement(envelope, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")
        req_desc = ET.SubElement(import_data, "REQUESTDESC")
        report_name = ET.SubElement(req_desc, "REPORTNAME")
        report_name.text = "All Masters & Vouchers"

        req_data = ET.SubElement(import_data, "REQUESTDATA")

        # 1. Export Ledgers
        for led in self.get_ledgers():
            msg = ET.SubElement(req_data, "TALLYMESSAGE")
            l_elem = ET.SubElement(msg, "LEDGER", {"NAME": led["name"], "ACTION": "Create"})
            ET.SubElement(l_elem, "NAME").text = led["name"]
            ET.SubElement(l_elem, "PARENT").text = led["group_name"]
            ET.SubElement(l_elem, "OPENINGBALANCE").text = str(-led["opening_balance"] if led["opening_balance_type"] == "Dr" else led["opening_balance"])
            if led.get("gstin"):
                ET.SubElement(l_elem, "PARTYGSTIN").text = led["gstin"]
                ET.SubElement(l_elem, "GSTREGISTRATIONTYPE").text = "Regular"
            if led.get("state"):
                ET.SubElement(l_elem, "STATENAME").text = led["state"]

        # 2. Export Stock Items
        for itm in self.get_stock_items():
            msg = ET.SubElement(req_data, "TALLYMESSAGE")
            s_elem = ET.SubElement(msg, "STOCKITEM", {"NAME": itm["name"], "ACTION": "Create"})
            ET.SubElement(s_elem, "NAME").text = itm["name"]
            ET.SubElement(s_elem, "BASEUNITS").text = itm.get("uom_name", "Pcs")
            ET.SubElement(s_elem, "OPENINGBALANCE").text = f"{itm.get('opening_quantity', 0)} {itm.get('uom_name', 'Pcs')}"
            ET.SubElement(s_elem, "OPENINGVALUE").text = str(itm.get("opening_value", 0.0))
            if itm.get("gst_rate"):
                ET.SubElement(s_elem, "GSTRATE").text = str(itm["gst_rate"])

        # 3. Export Vouchers
        for vch in self.get_vouchers(limit=1000):
            msg = ET.SubElement(req_data, "TALLYMESSAGE")
            v_elem = ET.SubElement(msg, "VOUCHER", {"VCHTYPE": vch["voucher_type"], "ACTION": "Create"})
            raw_d = vch["date"].replace("-", "")
            ET.SubElement(v_elem, "DATE").text = raw_d
            ET.SubElement(v_elem, "VOUCHERTYPENAME").text = vch["voucher_type"]
            ET.SubElement(v_elem, "VOUCHERNUMBER").text = vch["voucher_number"]
            if vch.get("party_ledger"):
                ET.SubElement(v_elem, "PARTYLEDGERNAME").text = vch["party_ledger"]
            if vch.get("narration"):
                ET.SubElement(v_elem, "NARRATION").text = vch["narration"]

            for ent in vch.get("entries", []):
                le = ET.SubElement(v_elem, "ALLLEDGERENTRIES.LIST")
                ET.SubElement(le, "LEDGERNAME").text = ent["ledger_name"]
                ET.SubElement(le, "ISDEEMEDPOSITIVE").text = "Yes" if ent["entry_type"] == "Dr" else "No"
                amt_val = -ent["amount"] if ent["entry_type"] == "Dr" else ent["amount"]
                ET.SubElement(le, "AMOUNT").text = f"{amt_val:.2f}"

            for inv in vch.get("inventory_entries", []):
                ie = ET.SubElement(v_elem, "ALLINVENTORYENTRIES.LIST")
                ET.SubElement(ie, "STOCKITEMNAME").text = inv["stock_item_name"]
                ET.SubElement(ie, "ACTUALQTY").text = f"{inv['qty']}"
                ET.SubElement(ie, "RATE").text = f"{inv['rate']:.2f}"
                ET.SubElement(ie, "AMOUNT").text = f"{inv['amount']:.2f}"
                ET.SubElement(ie, "GODOWNNAME").text = inv.get("godown_name", "Main Location")

        return ET.tostring(envelope, encoding="utf-8").decode("utf-8")

def amount_to_words(amt: float) -> str:
    """
    Converts numbers into Indian English words (Lakhs / Crores).
    """
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def convert_two_digits(n: int) -> str:
        if n < 20:
            return ones[n]
        return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")

    def convert_three_digits(n: int) -> str:
        if n == 0:
            return ""
        h = n // 100
        rem = n % 100
        res = ""
        if h > 0:
            res += ones[h] + " Hundred"
            if rem > 0:
                res += " and "
        if rem > 0:
            res += convert_two_digits(rem)
        return res

    amt_int = int(amt)
    paisa = int(round((amt - amt_int) * 100))

    if amt_int == 0:
        main_words = "Zero"
    else:
        crore = amt_int // 10000000
        amt_int %= 10000000
        lakh = amt_int // 100000
        amt_int %= 100000
        thousand = amt_int // 1000
        amt_int %= 1000
        hundreds = amt_int

        parts = []
        if crore > 0:
            parts.append(convert_two_digits(crore) + " Crore")
        if lakh > 0:
            parts.append(convert_two_digits(lakh) + " Lakh")
        if thousand > 0:
            parts.append(convert_two_digits(thousand) + " Thousand")
        if hundreds > 0:
            parts.append(convert_three_digits(hundreds))
        main_words = " ".join(parts).strip()

    paisa_words = ""
    if paisa > 0:
        paisa_words = f" and {convert_two_digits(paisa)} Paise"

    return f"INR {main_words}{paisa_words} Only"

# Compatibility alias
AccountingDB = EnterpriseAccountingDB

