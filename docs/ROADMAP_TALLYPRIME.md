# Recreating TallyPrime in TallyForge: what is missing and in what order

Written 2026-11-13 from the code and the status notes, not from a TallyPrime feature checklist alone. Every phase ends with a
check against real TallyPrime data (read-only, through its HTTP interface) or against its published behaviour, and a feature
is only called done when that check passes. Anything TallyForge cannot do honestly is listed at the end instead of faked.

## Where it stands (done and checked)
Books and reports that agree with TallyPrime on the real company: Trial Balance, Balance Sheet, Profit & Loss, stock valuation,
bills, registers, orders. Sales / purchase invoices, orders, delivery / receipt / rejection notes, GST on invoices, TCS / TDS,
price lists, bank reconciliation, GST rate summary, import from a live TallyPrime, TallyPrime-style Gateway, shortcuts, F2 period,
F12 options per screen, report keys, alter / delete of masters.

## Phase 1: Account books and the report engine (started)
Why first: reports are what an accountant opens all day, and they can be checked against TallyPrime number for number.
1. Ledger report as TallyPrime shows it: ledger list, monthly summary (opening, debit, credit, closing per month), month to
   vouchers, voucher to alteration; particulars show the other ledger; the period from F2 applies. **(first slice, in this change)**
2. Cash / Bank Books: every cash and bank ledger with opening, debit, credit and closing for the period, drilling to the ledger. **(in this change)**
3. Group Summary and Trial Balance drill all the way to vouchers.
4. Day Book filters (voucher type, ledger), Journal / Payment / Receipt registers with the same drill.
5. A real report column and view model so that Alt+F1 (detailed / condensed), Ctrl+B (basis of values), Ctrl+H (change view),
   Alt+C / Alt+A / Alt+D (columns), Alt+N (auto repeat) and Ctrl+J (exceptions) have something to act on.
6. Cost centre and cost category reports, interest calculation on outstandings, ageing with user-set bands, negative stock and
   other exception reports, Stock Query and movement analysis.
Check: every ledger's closing balance and every month's totals against TallyPrime's own reports.

## Phase 2: Voucher entry the way TallyPrime does it
1. Voucher Types: create, alter, numbering method, prefix / suffix per type, voucher classes, optional or post-dated defaults.
2. The accounting voucher screen: By / To lines, current balance under the ledger, narration per line and per voucher,
   Page Up / Page Down through earlier vouchers, Alt+I insert, Alt+2 duplicate, Ctrl+Enter alter a master from inside a voucher,
   Alt+C create a master from inside a voucher.
3. Bill-wise screen (New Ref, Agst Ref, Advance, On Account) with allocation checks; cost centre allocation; stat adjustments.
4. Post-dated vouchers and the post-dated summary; reversing and memorandum vouchers in their reports.
5. Multi-currency vouchers with forex gain / loss.
Check: re-enter a sample of real vouchers and compare ledger effects; compare numbering with TallyPrime's series.

## Phase 3: Inventory depth
Stock groups and categories, compound units, price levels per party, discounts, godown transfers, batch and expiry with reports,
reorder levels and the reorder report, additional cost of purchase, job costing, tracking numbers on all note types,
actual versus billed quantity, stock ageing and movement analysis.
Check: stock summary and item monthly figures against TallyPrime (already within 20 paise on the real company).

## Phase 4: Statutory
1. GST: HSN / SAC and rate masters (TallyPrime does not expose them over HTTP, so they must be entered or imported from a file),
   reverse charge, input credit, the full GSTR-1 tables (B2B, B2C, credit notes, HSN summary, documents issued), GSTR-3B with
   ITC, GSTR-2A / 2B matching, e-invoice and e-way bill generation through a provider (needs the user's own credentials).
2. TDS and TCS: nature-of-payment masters, deduction at payment or booking, challans, Form 26Q / 27EQ data, certificates.
3. Payroll: statutory PF, ESI, professional tax, gratuity, Form 16 data.
Check: against the user's own returns; no figure is shown as a return until it agrees with a filed one.

## Phase 5: Data and platform
Excel import and export of masters and vouchers, report export to PDF / Excel / JSON, share by mail, company repair and
rewrite, split and close year with opening carry-forward checks, finer user rights, audit of every master and voucher edit,
scheduled backup with restore test.

## Phase 6: TDL
Broader TDL language coverage (collections, formulae, interface objects, menus, forms, validations), so that add-ons written for
TallyPrime run; a conformance suite built from the TDL reference. Compiled add-ons (`.tcp`) are out of scope.

## Cannot be recreated, or only with the user's own accounts
- Reading TallyPrime's native `.1800` / `.900` / `.500` financial data without TallyPrime running: the format is not documented
  and no ground truth for balances exists here. TallyPrime running and read-only HTTP is the supported route.
- Tally.NET, TallyDrive, remote access and licensing, Connected Banking and online payments, e-filing of returns, WhatsApp
  sending: these are services of Tally Solutions, banks or the government and need accounts and approvals.
- Multi-user work on one company over a network: TallyForge is a local single-user application today. A server mode is a
  separate project and needs its own locking and security review.

## Working rules
Read-only toward TallyPrime. Real company data stays out of the repository and out of public text. Each slice ships with tests
and a status note stating what was checked and what was not.
