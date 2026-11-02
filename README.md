# TallyForge — Reverse Engineered. Rebuilt. Reimagined.

> **An independently rebuilt accounting and business management application inspired by TallyPrime's functionality.**

A **100% fully offline**, keyboard-driven enterprise accounting system and **TDL (Tally Definition Language)** runtime engine replicating the authentic placements, screen layouts, workflows, financial math, and shortcut keys of TallyPrime (Release 7.2 enterprise feature set).

- **Application:** TallyForge
- **Executable:** `TallyForge.exe` (Windows) / `TallyForge.app` (macOS)
- **GitHub Repository:** [`mvasagan099/TallyForge`](https://github.com/mvasagan099/TallyForge)
- **License:** MIT / Open Source Enterprise Architecture

---

## 1. 100% Offline Architecture (Zero Cloud / Internet Dependencies)

- **Strictly Offline**: Zero external network calls, zero CDNs, zero remote tracking/telemetry. Socket-isolated verification confirmed.
- **Local Embedded Database**: Stores company masters, 28 Indian accounting groups, ledgers, inventory stock items, units of measure, compound units, godowns, batches, BOMs, employees, price levels, cheque books, and double-entry voucher transactions locally in `tally_data.db` (enforcing `PRAGMA foreign_keys = ON;`).
- **Mathematical Correctness**: Strict `Decimal` arithmetic with `ROUND_HALF_UP` quantization across all accounting, tax, inventory, and payroll computations ($\sum\text{Dr} = \sum\text{Cr}$).
- **Zero Configuration**: Ready to run out of the box on macOS, Linux, or Windows.

---

## 2. Placements & Layout Fidelity (Exact Tally Architecture)

The interface strictly matches the dual-pane split layout and button placements of TallyPrime:

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Top Menu Strip: K:Company | Y:Data | Z:Inventory | G:Go To (Alt+G) | B:Backup | R:Restore | P:Print
├───────────────────────────────────┬───────────────────────────────────┬─────────────────────────┤
│ LEFT PANE (Company & Period)      │ CENTER WORKPLACE (Gateway)        │ RIGHT SIDEBAR           │
│                                   │                                   │ (Sticky Function Keys)  │
│ [ CURRENT PERIOD ]                │ ┌───────────────────────────────┐ │ F2: Date                │
│ 1-Apr-2026 to 31-Mar-2027         │ │     Gateway of Tally          │ │ F3: Company             │
│                                   │ ├───────────────────────────────┤ │ F4: Contra              │
│ [ CURRENT DATE ]                  │ │ MASTERS                       │ │ F5: Payment             │
│ Saturday, 31 Oct 2026             │ │  Create | Alter | Chart of Acc│ │ F6: Receipt             │
│                                   │ │ TRANSACTIONS                  │ │ F7: Journal             │
│ [ SELECTED COMPANIES ]            │ │  Vouchers | Day Book          │ │ F8: Sales               │
│ • Demo Enterprise Ltd.            │ │ UTILITIES & STATUTORY         │ │ F9: Purchase            │
│   GSTIN: 29AABCT1332L1Z5          │ │  Banking & BRS | GSTR-1/3B/9  │ │ F10: Stock Item         │
│   Date of Last Entry: Dynamic     │ │  Payroll Pay Sheet            │ │ Alt+F5: Dr Note         │
│                                   │ │ REPORTS                       │ │ Alt+F6: Cr Note         │
│ [ SYSTEM & COMPLIANCE METRICS ]   │ │  Balance Sheet | P&L | Stock  │ │ Alt+F7: Mfg BOM         │
│ 100% Offline | TDL Active         │ │  Receivables | Payables       │ │ Ctrl+F4: Payroll        │
│ Statutory Edit Log: Compliant     │ │  Funds Flow | Budget Variance │ │ F11: Features           │
│ Multi-Godown & Batch: Enabled     │ │  Inventory Ageing | TDS Reg   │ │ F12: TDL Studio         │
│ 31 Tables | Foreign Keys ON       │ │  Ratio Analysis | Trial Bal   │ │ Esc: Gateway            │
│                                   │ └───────────────────────────────┘ │                         │
├───────────────────────────────────┴───────────────────────────────────┴─────────────────────────┤
│ BOTTOM CALCULATOR DRAWER: Press Ctrl+N to open, Ctrl+M / Esc to return                          │
│ CALC > (15000 * 1.18) + 450                                                                    │
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ BOTTOM STATUS BAR: Mode: 100% Offline Standalone | TallyPrime 2026 Engine // Release 7.2.0      │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Complete Module Coverage

### 1. Multi-Company & Consolidation
- Create, Alter, Select, Shut, and Delete companies.
- Multi-company database with active company switching (`/api/companies/switch`).
- Group company consolidation across member company IDs with cross-company elimination.
- Consolidated Balance Sheet, Consolidated P&L, and Consolidated Trial Balance.

### 2. Year-End Closing & Financial Year Split
- **Year-End Closing**: Automatically computes Net Profit/Loss, posts balanced closing journal entries, transfers profit to Capital Account, resets revenue/expense ledgers to 0.00, and carries forward asset/liability balances into the new financial year.
- **Financial Year Split**: Creates an online backup archive of historical data, resets active database opening balances, purges prior transactions, and advances the books beginning date.

### 3. Accounting & Financial Integrity
- Enforced $\sum\text{Dr} = \sum\text{Cr}$ for every single posted transaction.
- 28 Standard Indian Accounting Groups (Capital, Current Assets, Current Liabilities, Bank Accounts, Sundry Debtors, Sundry Creditors, Fixed Assets, Sales, Purchase, Direct/Indirect Expenses, etc.).
- Multi-currency transactions (INR, USD, EUR) with exchange rate tracking and automated Forex Gain/Loss computation.
- Cost Categories & Cost Centres with allocation and breakdown reporting.
- Budgets & Variance Analysis (Budgeted vs Actual with variance percentage).
- Scenario Management (comparing standard financials with provisional/optional/reversing transactions).
- Overdue interest calculation (18% p.a. simple interest on overdue debtor bills).
- Voucher operations: Creation, Alteration, Cancellation, Deletion, and 1-click Duplication.

### 4. Advanced Inventory & Manufacturing
- Stock items with HSN/SAC codes, GST rates, reorder levels, and default godowns.
- Stock categories & Compound units of measure (e.g. `1 Box = 10 Nos`).
- Multi-godown tracking (Main Location, Warehouse A, Warehouse B) with inter-warehouse stock transfer vouchers.
- Batch-wise inventory with Manufacturing & Expiry dates.
- Price Levels & Price Lists (Retail, Wholesale, Dealer) with quantity breaks and discount percentages.
- Bill of Materials (BOM) & Manufacturing Journals (consumes raw material components, produces finished items, deducts scrap value).
- Inventory Ageing Analysis (0-30, 31-60, 61-90, >90 days).
- Manufacturing & BOM Cost Breakdown reports.
- Physical stock audit count verification and discrepancy adjustment vouchers.

### 5. Indian Statutory & GST Compliance
- CGST, SGST, IGST intra-state and inter-state tax split based on 2-digit state codes.
- Input Tax Credit (ITC) tracking on purchases and Capital Goods.
- GSTR-1 Outward Return JSON export strictly conforming to the official GSTN schema (`b2b`, `b2cl`, `b2cs`, `hsn`).
- GSTR-3B Monthly Return calculation.
- GSTR-9 Annual Return Summary (Table 4 Outward Supplies, Table 6 ITC Availed, Table 9 Tax Paid & Payable).
- **e-Invoice JSON**: Prepared offline conforming strictly to NIC Schema Version 1.1 (`TranDtls`, `DocDtls`, `SellerDtls`, `BuyerDtls`, `ItemList`, `ValDtls`).
- **e-Way Bill JSON**: Prepared offline conforming strictly to NIC standard with transporter ID, vehicle number, and distance parameters.
- TDS / Withholding Tax: Sections 194C, 194J, 194I, 194Q with threshold tracking, deductions, and TDS Register.

### 6. Banking & Funds Flow
- Bank Reconciliation (BRS): Manual entry of bank clearance dates.
- Automated Bank Statement CSV upload and statement matching.
- Cheque book management: Number range configuration, current cheque tracking, and automated cheque issuance.
- Formal Payment Advice slip generation with amount-in-words conversion.
- Funds Flow Statement: Sources of Funds vs Applications of Funds with Working Capital variation.
- Direct Cash Flow Statement: Operating inflows and outflows across Cash and Bank ledgers.

### 7. Payroll & Employee Management
- Employee Master with PF UAN, ESI numbers, PAN, bank details, and employee groups.
- Configurable Pay Heads: Basic Salary, House Rent Allowance (HRA), Special Allowance, Provident Fund (PF 12%), Professional Tax (PT).
- Monthly attendance tracking and automated salary slip generation.

### 8. Statutory Audit Trail & Backups
- Section 135 MCA Compliant Edit Log: Logs every CREATE, ALTER, CANCEL, DELETE action with timestamp, user name, and chained cryptographic hash.
- Full local JSON database backup with SHA-256 integrity hash.
- Safe restore with pre-restore snapshots and tampering detection.

### 9. TDL (Tally Definition Language) Parser & Runtime Engine
- Lexer, AST Parser, Symbol Table, and Expression Evaluator.
- Supports definitions: `[Report]`, `[Form]`, `[Part]`, `[Line]`, `[Field]`, `[Collection]`, `[Menu]`, `[Button]`, `[Key]`, `[UDF]`, `[System: Formula]`.
- Modifiers: `#` (override/extend definition), `!` (conditional definition), `*` (replace).
- Math & String expressions: Arithmetic (`$Amount * 1.18`), built-in functions (`$$LocaleString`, `$$Number`, `$$Sum`, `$$If`, `$$IsEmpty`).
- Conditional TDL expressions: `If <cond> Then <val1> Else <val2>`.
- In-browser TDL Studio with line-by-line syntax checking and live hot-reloading.

### 10. Packaging & Desktop Distribution
- Standalone macOS `.app` bundle builder (`create_macos_app.sh`).
- PyInstaller specification (`pyinstaller.spec`) for single-executable distribution.
- Interactive launcher (`start_tally.command`) with automatic database integrity checks and browser opening.

---

## 4. Verification Test Results (37 Tests - 100% Pass)

Run all three test suites:

```bash
python3 tests/test_enterprise_suite.py
python3 tests/test_full_year_simulation.py
python3 tests/test_offline_isolation.py
```

### 1. `tests/test_enterprise_suite.py` (14/14 Passed)
- Test A: Company Management & Alterations
- Test B: Accounting Core & Mathematical Integrity ($\sum\text{Dr} = \sum\text{Cr}$)
- Test C: Sales Workflow & Inventory Deductions
- Test D: Purchase Workflow & Input Tax Credit
- Test E: Bill-wise Allocations & Ageing Analysis
- Test F: Inventory & Multi-Godown Transfers
- Test G: Manufacturing Journal & BOM Processing
- Test H: Payroll & Employee Statutory Deductions
- Test I: Banking & BRS Reconciliation (CSV Matching)
- Test J: Statutory GST Returns (GSTR-1 JSON Schema)
- Test K: TDL Parser AST, Modifiers & Math Expressions
- Test L: Local Backup & SHA-256 Tamper Rejection
- Test M: Offline Verification (Zero Remote Telemetry)
- Test N: Reports & Navigation Coverage (16 Reports)

### 2. `tests/test_full_year_simulation.py` (All 20 Steps Passed)
- Full 20-step Indian enterprise lifecycle from capital injection to B2B purchases, manufacturing, sales, e-Invoice/e-Way bill generation, foreign currency export, payment advice, TDS deductions, payroll, BRS, physical stock count, budgets, annual GSTR-9, funds flow, and year-end closing.
- Mathematical Proof: Final Trial Balance Debit (₹15,97,000.00) == Credit (₹15,97,000.00).

### 3. `tests/test_offline_isolation.py` (2/2 Passed)
- Socket-level interception: Blocks any non-loopback connection.
- Hits 25 API endpoints under strict socket isolation; 0 non-local calls made.
- Scans all HTML, CSS, JS assets; 0 remote CDN URLs or tracking scripts detected.

---

## 5. Keyboard Navigation & Shortcuts

| Shortcut | Action |
| :--- | :--- |
| **Alt+G** | Go To Smart Search (reports, vouchers, ledgers, items) |
| **F2** | Change Current Date |
| **F3 / Alt+K** | Company Selection & Management |
| **F4** | Contra Voucher Entry |
| **F5** | Payment Voucher Entry |
| **F6** | Receipt Voucher Entry |
| **F7** | Journal Voucher Entry |
| **F8** | Sales Voucher Entry |
| **F9** | Purchase Voucher Entry |
| **F10** | Stock Items Master |
| **Alt+F5** | Debit Note Voucher Entry |
| **Alt+F6** | Credit Note Voucher Entry |
| **Alt+F7** | Manufacturing Journal (BOM Production) |
| **Ctrl+F4** | Monthly Payroll Processing |
| **F11** | Company Features Overview |
| **F12** | TDL Studio (Live Code & Diagnostics) |
| **Ctrl+N** | Open Bottom Calculator Drawer |
| **Ctrl+M / Esc** | Close Calculator / Return to Workplace |
| **Ctrl+A** | Accept / Save Voucher |
| **Esc** | Return to Gateway of Tally |

---

## 6. How to Run

### Option 1: macOS Application Bundle
```bash
./create_macos_app.sh
open dist/TallyPrime.app
```

### Option 2: Command Launcher
```bash
./start_tally.command
```

### Option 3: Terminal Server
```bash
python3 server.py
# Open your browser to http://127.0.0.1:8080
```
