# TallyForge day to day

## Start it
From the project folder:

```
python server.py
```

Then open http://127.0.0.1:8080 in a browser (everything runs on your computer; nothing is sent anywhere).
Your companies live in the data folder next to the program as numbered folders (for example `10000/company.db`).
Never delete or move those folders; copy them for backups.

## Open your company
Press **Alt+K** (or F3), check the *Company Data Path* shows your data folder, click your company and press **Enter**.

## Bring in fresh data from TallyPrime (read-only)
TallyForge only ever *reads* from TallyPrime. It never writes to it.

1. In TallyPrime, open the company and make sure it is the only one loaded. Enable the server
   (Server or Both mode, port 9000). Save your work and avoid using TallyPrime while the import runs.
2. In TallyForge: Alt+K, then **Import / Export Tally data**, then the live section.
   List the loaded companies, choose yours, tick the confirmation box and start. A company with a few years of
   history takes several minutes; the screen shows progress and a verification result when it ends.
3. The result must say *verified*. If any check fails, do not rely on the numbers; keep the message and ask.

Good to know (tested):
- Re-importing is safe and repeatable. Unchanged vouchers stay as they are.
- A voucher you entered only in TallyForge is **kept** by a re-import (it does not exist in TallyPrime).
- If you edit an imported voucher in TallyForge, a re-import keeps your edit until that voucher changes in TallyPrime,
  then TallyPrime's version wins.
- Nothing you do in TallyForge appears in TallyPrime. Treat TallyPrime as the master for the books, or use TallyForge
  on its own; avoid keeping the same books in both by hand.

You can also import from an export folder (`Master.xml`, `DayBook.xml`) or replay a saved capture with no
TallyPrime running; both are on the same screen.

## The screen
TallyForge opens on the Gateway of Tally, laid out like TallyPrime: use the arrow keys and Enter, or press the underlined
letter of a menu item. Esc goes back one level. The path at the bottom shows where you are. F12 opens Configuration
(interface style, panels, button bar, invoice mode, the "Accept?" question). Choose *Dashboard* under Reports for the
overview cards, or set the interface style to Modern in F12 to get the old layout back.

Keys that match TallyPrime: F2 period, F3 or Alt+F3 company, F4 Contra, F5 Payment, F6 Receipt, F7 Journal, F8 Sales,
F9 Purchase, F10 Other Vouchers, F11 Features, F12 Configure, Alt+F5 Debit Note, Alt+F6 Credit Note, Alt+F7 Stock Journal,
Alt+F8 Delivery Note, Alt+F9 Receipt Note, Ctrl+F5 Rejection Out, Ctrl+F6 Rejection In, Ctrl+F7 Physical Stock,
Ctrl+F8 Sales Order, Ctrl+F9 Purchase Order, Ctrl+A accept, Ctrl+Q back, Alt+G or Ctrl+K go to, Alt+X cancel and Alt+D delete
an open voucher, Ctrl+H switch between invoice and voucher mode, Ctrl+E export, Alt+M share.

## Everyday work
| To do this | Use |
| --- | --- |
| Enter vouchers | F4 Contra, F5 Payment, F6 Receipt, F7 Journal, F8 Sales, F9 Purchase, Alt+F5 Debit Note, Alt+F6 Credit Note |
| Enter a sales or purchase invoice | F8 / F9 (also Alt+F5/F6 for notes): choose the party and the sales or purchase ledger, add item lines (quantity, rate, discount %), add freight or other ledgers if needed, tick GST or round off, then Ctrl+A |
| Enter a sales or purchase order | choose Sales Order or Purchase Order as the voucher type (or search "Order" with Ctrl+K): party, items with quantity, rate and a due date, then Ctrl+A |
| Receive or deliver against an order | on the invoice, pick the order for each item line ("Against order"); the pending quantity falls |
| Record goods sent or received before the bill | choose Delivery Note or Receipt Note; invoice it later with "Against order / note" on the Sales or Purchase invoice |
| Record goods sent back | choose Rejection Note (In) for a customer return or Rejection Note (Out) for goods returned to a supplier, allocated to the note it reverses |
| Collect TCS or deduct TDS | on the Sales or Purchase invoice pick the tax ledger and a percentage (TCS adds to the bill, TDS reduces it) |
| Use a price list | create one through the price-list screen or API, give a party that list, and leave the rate blank on the invoice line |
| See what is still pending | Gateway menu: Purchase Orders (pending), Sales Orders (pending) |
| Save a voucher | Ctrl+A |
| Open or alter a voucher | Click it in the Day Book or in a register |
| Print an invoice or voucher | Alt+P (from an open voucher it prints that voucher) |
| Add GST to an invoice | the *Add GST* button on the multi-line voucher screen |
| Reconcile a bank | Gateway menu: Banking & BRS. Click a pending line to record the date the bank cleared it; upload a statement CSV to match lines automatically. Imported companies start with everything unreconciled |
| Price lists | Gateway menu: Price Lists (or Ctrl+K) |
| GST summary | Gateway menu: GST Summary (by rate): outward and inward tax by rate for the financial year, with a check that it closes against the tax ledgers. A summary of your books, not a filed return |
| Reports | Gateway menu: Balance Sheet, Profit & Loss, Trial Balance, Group Summary, Stock Summary, the registers, Day Book |
| Drill down | Click a row (a month in a register, a group in Trial Balance or Group Summary, an item in Stock Summary) |
| Search anything | Ctrl+K or Alt+G |
| Backup and restore | Alt+Y |

Voucher numbers continue your series automatically (for example 5490 becomes 5491) and restart each April. The entry
screen shows the number the voucher will get (for example "No. 5491"). For a company imported from TallyPrime, Payment offers
only cash and bank ledgers to credit, Receipt only cash and bank ledgers to debit, and Contra only cash and bank ledgers.
Ledgers, stock items and godowns must exist before a voucher can use them; create them first. The godown list on the
entry screen is your company's own. A sales invoice with items gets its bill reference automatically, and its print carries the
buyer's GSTIN and state from that party's latest earlier invoice.

## Export
To try the file in TallyPrime safely, follow `docs/TALLYPRIME_IMPORT_TEST.md` (a test company only).

**Import / Export Tally data** also writes the company as Tally XML. This file has been checked by re-importing it
into TallyForge only. Whether TallyPrime accepts it has not been tested, so try it on a copy company first.

## Before you rely on it
- Take a backup (Alt+Y, or copy the company folder) before big imports and at month end.
- Closing stock and profit agree with TallyPrime to within paise on the books it was tested on; compare the
  Balance Sheet and Profit & Loss with TallyPrime after each import and tell me about any difference.
- Not checked against TallyPrime: GST and TDS returns, e-invoice and e-way bills, payroll, bank reconciliation,
  cost centres, godown transfers and batches with expiry (your books do not use these).
- Item GST rates and HSN codes are not available from TallyPrime, so enter a rate on an item before expecting GST on it.
