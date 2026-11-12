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

## Everyday work
| To do this | Use |
| --- | --- |
| Enter vouchers | F4 Contra, F5 Payment, F6 Receipt, F7 Journal, F8 Sales, F9 Purchase, Alt+F5 Debit Note, Alt+F6 Credit Note |
| Save a voucher | Ctrl+A |
| Open or alter a voucher | Click it in the Day Book or in a register |
| Print an invoice or voucher | Alt+P (from an open voucher it prints that voucher) |
| Add GST to an invoice | the *Add GST* button on the multi-line voucher screen |
| Reports | Gateway menu: Balance Sheet, Profit & Loss, Trial Balance, Group Summary, Stock Summary, the registers, Day Book |
| Drill down | Click a row (a month in a register, a group in Trial Balance or Group Summary, an item in Stock Summary) |
| Search anything | Ctrl+K or Alt+G |
| Backup and restore | Alt+Y |

Voucher numbers continue your series automatically (for example 5490 becomes 5491) and restart each April. The entry
screen shows the number the voucher will get (for example "No. 5491"). For a company imported from TallyPrime, Payment offers
only cash and bank ledgers to credit, Receipt only cash and bank ledgers to debit, and Contra only cash and bank ledgers.
Ledgers, stock items and godowns must exist before a voucher can use them; create them first.

## Export
**Import / Export Tally data** also writes the company as Tally XML. This file has been checked by re-importing it
into TallyForge only. Whether TallyPrime accepts it has not been tested, so try it on a copy company first.

## Before you rely on it
- Take a backup (Alt+Y, or copy the company folder) before big imports and at month end.
- Closing stock and profit agree with TallyPrime to within paise on the books it was tested on; compare the
  Balance Sheet and Profit & Loss with TallyPrime after each import and tell me about any difference.
- Not checked against TallyPrime: GST and TDS returns, e-invoice and e-way bills, payroll, bank reconciliation,
  cost centres, godown transfers and batches with expiry (your books do not use these).
- Item GST rates and HSN codes are not available from TallyPrime, so enter a rate on an item before expecting GST on it.
