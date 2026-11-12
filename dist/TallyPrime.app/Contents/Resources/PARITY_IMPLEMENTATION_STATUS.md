# TallyPrime gap implementation status

Updated 2026-11-02. This is the current evidence-based status; earlier Phase 9/10 claims of complete parity and statutory certification are superseded. Passing repository tests does not certify GST schemas, tax calculations, native Tally compatibility or production deployment.

| Audited area | Delivered in this change | Remaining work / dependency |
| --- | --- | --- |
| Backup / restore | Complete SQLite snapshots, checksum metadata, pre-restore snapshots, integrity/foreign-key checks, preservation of IDs and extension tables. No 5,000-voucher limit. | Old JSON backups are rejected for full restore because they omit data. Historical JSON recovery needs a separate, reviewed import workflow. Checksums detect corruption, not a malicious party who can replace both files. |
| Company deletion | Numbered companies can be removed from selection with a full recovery snapshot and an archive marker; other open contexts for that company are invalidated. | Physical removal of recovery copies remains an explicit manual operation. |
| Independent company windows | Per-window context header and sessionStorage credentials; each request binds its own database under a lock. | Legacy shared-database company scoping remains incomplete; this is not a high-concurrency server replacement. |
| Login / permissions | Secured companies require credentials. Invalid tokens return 401. Account disabling/current role changes take effect for sessions. Company access lists and administrative write restrictions are enforced. | Unsecured companies intentionally allow local owner access. Native company files are not encrypted; OS filesystem permissions remain necessary. |
| F11 configuration | Saved per company; real checks on inventory entry, bill allocations, currency, cost centres, batches, godowns, budgets, pricing masters and e-document export. | Not every historic API or TDL report condition has been audited for every feature switch. |
| Inventory valuation | Average Cost, FIFO, LIFO, Last Purchase Cost, Standard Cost and Zero Cost selections. | Period-specific costing, monthly average and other Tally valuation variants remain incomplete. FIFO/LIFO reject negative chronological stock rather than invent costs. |
| Payroll | Reviewed monthly paid-day proration, overtime, arrears, bonus, manual deductions, employer references, preview fingerprints, canonical month handling, atomic/idempotent posting, immutable employee/employer snapshots, printable saved payslips and JSON export. Finalised vouchers cannot be altered/deleted/duplicated. Bank reconciliation uses actual net bank entries and skips ambiguous same-amount matches. | Automatic statutory income tax/PF/PT/ESI validation, Form 16, statutory file exports, automatic loan amortisation and a full reversal/reissue workflow remain incomplete. Unsaved employee inputs use clearly labelled legacy deduction estimates. Legacy shared-company employee data is rejected for this workflow. |
| Excel | Bounded XLSX reader, first-sheet column mapping, preview, saved mapping reuse, atomic staged ledger/stock imports, XLSX export. | Complex multi-sheet/voucher import mappings and Excel charts/styles are outside this implementation. Formula cells are rejected; exports use literal text. |
| Email | Explicitly confirmed XLSX delivery over SMTP TLS, configured via environment variables. | SMTP credentials and a verified account are required. No real email was sent during development. Integrated WhatsApp templates/provider transport remain missing. |
| GST registrations | Registration masters and numbering; month/registration-scoped book review using posted tax amounts; posting-time party/rate/HSN snapshots preserved across master changes; amendment refresh and duplication handling; warnings/exclusions and downloadable review JSON. | GSTN-format returns, credit/debit-note classification, foreign-currency tax basis, ITC eligibility and current schema validation remain incomplete. These reviews are not portal filings; existing legacy return code still includes simplified calculations. |
| IMS | Local normalized JSON invoice import, accept/reject/pending decisions, remarks and accepted-tax summary. | This is not the GST portal JSON schema or a GSTR-2B recomputation engine. Portal download/upload/reset and ITC amendment workflows require provider integration and validation. |
| E-documents | NIC preparation rejects missing GSTIN/address/pincode/HSN and missing road transport details. Standard invoice preview can work without an e-invoice GSTIN. | No IRP-issued IRN, signed QR, acknowledgement, cancellation or e-Way Bill provider lifecycle. Current preparation schema must be validated against the selected provider. |
| Company search | Active voucher search no longer has a latest-100 cutoff. Company Tools can search selectable unsecured books and the authenticated active secured company. | Cross-company protected searches require separate authorisation. Typo-tolerant SmartFind semantics and full Tally report search remain incomplete. |
| Consolidation | Saved group definitions; same-currency and financial-year checks; protected-member session authorisation; explicit receivable/payable and income/expense eliminations with references, remaining-balance validation, balanced report journals, and before/after totals. Source vouchers stay unchanged. | Reports use all retained balances, without a date filter. Investment/equity elimination, unrealised inventory profit, minority interests, currency translation and full statutory consolidated statements remain incomplete. Legacy multi-company files must be split before file-based group reporting. |
| Scheduled backup | Persistent interval/retention settings, app background worker, version listing, standalone backup_worker.py. | Running while the app is closed requires installation into an OS scheduler. No OS scheduled task was installed in this session. TallyDrive is not integrated. |
| Encryption | Optional new AES-256-GCM snapshot module (.tfvault), with scrypt key derivation and a separate format marker. | cryptography is not installed in this environment and installation was unavailable. Runtime capability reports it unavailable. Encryption/decryption was not exercised here. Live database encryption/SQLCipher migration and native TallyVault remain unimplemented. Legacy .tve functions are this app's older custom format, not native TallyVault. |
| Native data / TDL | Native .1800/.900/.500 folder recognition; bounded .1800 company-name hints; read-only file inventory; parent-folder or direct company-folder selection. Added export-only XML/HTTP connection for loaded companies, ledger balances and DayBook vouchers through a running TallyPrime server. Supplied native files/ZIP remain unchanged. | Standalone native ledger/voucher decoding is not implemented or verified. The connection requires a running, reachable TallyPrime with the company loaded; tests use XML fixtures, not a live engine. Compiled TCP, full TDL and COM/ODBC compatibility remain incomplete. |
| Connected banking / GST | Capability endpoint reports real setup blockers; unsupported integration calls return an explicit unavailable error. | Bank/provider onboarding, API contracts, sandbox credentials, consent and successful end-to-end testing are still required. No financial or government connection is claimed. |
| Distribution | Source and macOS source-resource bundles updated without replacing company databases. | Windows binaries have not been rebuilt. Live browser and packaged-app execution were not possible because this workspace blocks server sockets. |

## Use the new tools

Launch the project with `python3 desktop_app.py`, select a company, press F3 and choose **Company Tools**. Backups, valuation, payroll accounts, GST registrations, local IMS, XLSX import/export, SMTP delivery and capability requirements are available there. F11 loads and saves company feature choices.

To protect a company, open Company Management → Security. Create the first administrator using the masked form and enable **Require company login**. Store credentials securely; unsecured companies intentionally retain local owner mode. Credentials are stored in browser sessionStorage rather than shared localStorage.

## Group reporting and monthly GST book review

In **F3 → Company Tools → Consolidated company reports**, select members and enter a group name. Protected members can be authorised with the masked member-login form without changing the active company. Select debit/credit member ledgers, amount and reference to add a reporting elimination. Save the group or run a preview. Its before/after totals, trial balance and balanced elimination journal are rendered as tables; adjustments are never posted into source books. Over-elimination, wrong ledger sides, mixed currencies/financial years, unauthorised members and legacy multi-company files are rejected. Group definitions store no credentials.

In **GST books by registration and month**, select a month and registration, then review posted GST or download the review JSON. Main, additional and all registrations are separate scopes. Newly posted sales/purchase/POS vouchers preserve party GSTIN and item-rate/HSN snapshots. Old unsnapshotted vouchers are marked as using current masters. Cancelled/optional vouchers are omitted; unclassified credit/debit notes and foreign-currency documents are explicitly excluded. Warning tables identify missing details and mismatches. These book totals are not a GSTN schema, eligible ITC calculation or tax-payable determination.

## Access an existing native Tally company

The company chooser now detects native company folders instead of showing them as empty. Choose either their parent directory or the company folder itself. Select the native entry to inspect its format, detected name hint and file inventory. Metadata preview does **not** open financial books in the local accounting engine. Creating local books inside a native company folder is blocked.

After updating source files, fully stop the old Python backend before launching `start_tally.command` again. Browser refresh alone can load the new interface while the old backend remains in memory. This mismatch was confirmed in Safari: the live `/api/storage` response omitted `native_companies`, while a fresh process detected company `100124` directly. The chooser now identifies this outdated response explicitly and explains how to restart instead of reporting that the folder is empty. Catalogue requests bypass the browser cache. The running backend could not be restarted in this session because the execution sandbox blocks local server binding.

The **Connect to TallyPrime (read-only)** view can request actual data from a running TallyPrime engine without manual exports:

1. On the Windows computer that runs TallyPrime, load the original company and configure TallyPrime as Server/Both on its desired port (commonly 9000).
2. In TallyForge, enter `http://127.0.0.1:9000` if TallyPrime runs on the same computer. Otherwise enter the Windows computer's numeric private LAN IP and port, for example `http://192.168.1.10:9000`. The default localhost URL does not reach another computer or VM.
3. Choose **List loaded companies**, select the company, then **Read ledgers** or **Read vouchers**. A voucher date range is optional; without one, TallyPrime's current reporting period applies.

All bridge requests are fixed export requests. There are no import, alter or delete operations. Response sizes are bounded; redirects, public/metadata endpoints, and XML entity declarations are rejected. Raw Tally amount strings retain their formatting/signs and are never silently converted into local accounting entries. Paging re-requests the source and does not provide a transactionally frozen cross-page snapshot.

**Direct-reader progress:** an experimental `.1800` preview reads master-name fields, voucher search keys and linked transaction metadata without Windows or a running TallyPrime engine. It validates page checksums, declared chain lengths and bounded continuation links, retains byte provenance, and exposes search, pagination and reconstructed root-record details. The supplied company yielded 2,277 master-name roots, 131 voucher search-key candidates and 40,348 transaction headers with 80,696 validated metadata references. Numeric candidates retain exact raw bytes and signed/unsigned decimal strings; no currency scaling or date meaning is assigned. These are physical candidates, not active ledger/voucher totals. Inventory and live-connection controls are collapsed. See [docs/native-reader.md](docs/native-reader.md) for format observations, attribution, tests and limitations.

**Current limit:** no standalone financial decoder for `.1800`, `.900` or `.500` is implemented. Active-record selection, balances, amounts and allocations remain incomplete. Candidates are never imported as posted entries, and no financial amount or report is invented. This session did not have a running TallyPrime endpoint or reference exports, so no live financial reconciliation claim is made.

**Related-record and layout decoding:** protocol version 3 adds exact voucher-GUID lookup in LinkMgr.1800 using its distinct 16-byte continuation layout. All 41,741 pages and 20,673 chains passed structural checks; 86,037 complete strings were read. Across the investigated copy, 16,855 of 16,863 LinkMgr GUID fields resolved to exactly one TranMgr sequence; 8 were unmatched/ambiguous. Sequence IDs are not joined across files. A sequential, bounded field decoder consumed all 40,348 primary metadata layouts, retaining nested boundaries, raw integers and explicit unknown-format stops. This validates layout coverage, not monetary semantics. Related lookup and technical layout sections are available in native transaction details. The targeted 48 Python tests and frontend contracts pass. REA native analysis remained unavailable because its ownership-helper compilation hit a sandbox cache-write denial; the findings come from local read-only file analysis.

**Directory verification and extended layouts (protocol 4):** the native dialog now validates the TranMgr physical directory: tree root 98312 and sibling chain from page 1 to 287785 both reach 40,348 nodes, with reciprocal links, strict sequence order and no orphans. This is directory membership, not active/posted accounting status. Opaque internal encoding widths allow complete sequential layout reads for 30,450 secondary metadata records and all 16,863 GUID-bearing LinkMgr records; unsupported layouts and resource bounds stay explicit. Matching GUID markers inside opaque payloads are excluded when the layout is supported. Raw flags/references remain visible without assigning currency meanings. The targeted 60 Python tests and UI contracts pass; the REA cache-redirection retry remained blocked.

**XML-calibrated Sales preview (protocol 5):** the supplied UTF-16 reference exports contain 1,981 ledgers, 32 groups and 79 Sales vouchers (30 October / 49 April 2026). All 79 voucher identities/dates match through VOUCHERKEY, not native-sequence equality. Exact ledger/amount paths yield 188/188 matching allocations and 79/79 balanced allocation sets, using signed int64 / 100000 without a second flag-based sign inversion. Both metadata blocks fully decode for these references. The app can compare the fixed XML reference files and show an independent native Sales preview without reading XML at preview time. All 1,981 reference ledgers match primary names, company GUIDs, parent groups and alteration IDs, and all 32 groups match primary names, GUIDs and alteration IDs. The XML comparison now uses the per-record layout offset (layout word + 50), fixing shorter group prefixes and dependent parent checks; aliases cannot substitute for primary names. No references remain unresolved in the supplied exports. Missing opening balances are not zero, and other voucher classes/currencies and posting-state flags remain unverified. The targeted 80 Python tests plus frontend contracts pass. This remains read-only subset compatibility, not complete company opening.

Official interface references: [Integration prerequisites](https://help.tallysolutions.com/pre-requisites-for-integrations/), [XML integration](https://help.tallysolutions.com/xml-integration/), [Sample XML](https://help.tallysolutions.com/sample-xml/).

## Monthly payroll

Use **F3 → Company Tools → Payroll accounts** to choose separate expense, bank/cash/payable and deduction-liability ledgers. Then open **Monthly payroll and payslips**, choose a month and posting date, and load employees. Review scheduled/paid days, overtime hours/rate, arrears, bonus and named deduction amounts for each employee. A saved attendance/payroll reference is required. Manual deduction amounts are used exactly as entered; they are not statutory rate calculations.

Regular earnings are prorated by paid days / scheduled days; overtime, arrears and bonus are added afterwards. Without saved inputs, full regular earnings and explicitly labelled legacy estimates are retained for compatibility. Older attendance rows are shown for review but are never silently applied. Preview the full month before posting. Changes to saved inputs, salary masters, accounts or the selected company invalidate that preview. Posting creates the voucher and payroll snapshots in one transaction, and month aliases such as `April 2026` and `2026-04` cannot post twice.

Finalised inputs and payroll vouchers cannot be edited/deleted/duplicated. A cancelled payroll remains visible but cannot be silently reposted; reversal/reissue still requires a reviewed correction workflow. Saved payslips survive employee/master changes and employee deletion. They can be viewed, printed with masked bank numbers or downloaded as JSON. The JSON contains payroll information, including stored account details. Data-entry/viewer roles cannot read these payroll endpoints; only administrators can change/post payroll and auditors can review it.

## Background backups

Enable the schedule in Company Tools. The application checks due backups while it is running. To continue when it is closed, configure your OS scheduler to run this command at least every five minutes, substituting real absolute paths:

```sh
/path/to/python3 /path/to/TallyForge/backup_worker.py --data-dir /path/to/CompanyData
```

Keep the scheduler's data path the same as the selected company folder. Do not schedule two overlapping workers for the same folder. Each worker checks the persisted interval, so a five-minute OS schedule does not force a backup every five minutes.

## Optional SMTP delivery

Configure `TALLY_SMTP_HOST`, `TALLY_SMTP_PORT` (465 or 587), `TALLY_SMTP_SENDER`, `TALLY_SMTP_USER`, and `TALLY_SMTP_PASSWORD` in the application environment. Secrets are never stored in company feature settings. The Send Export action asks the operator to confirm the recipient and reports SMTP acceptance, not guaranteed mailbox delivery.

## Optional AES-GCM dependency

Install `requirements-optional.txt` in the Python environment used to launch the app. No home-grown fallback is used for the new AES-GCM export. This does not enable encryption of the live SQLite file and does not make the archive compatible with native TallyVault.

## Validation

123 distinct selected Python tests were exercised. The native-access/parser/API/UI checks passed; the offline asset test was corrected to distinguish a configurable LAN endpoint from an automatically loaded external asset, then its 14-test enterprise suite passed on rerun. Frontend contract checks passed with `node tests/test_company_ui.js`, including scoped GST reports, masked payroll rendering and wrong-month posting prevention. No live TallyPrime engine, browser, SMTP delivery, encryption execution, bank connection or GST portal submission was tested. SHA-256 comparison confirmed all 24 supplied native company files were unchanged after inspection.

See tests/test_reliability_workflows.py, tests/test_company_storage_api.py, tests/test_company_store.py and tests/test_company_ui.js. The existing accounting, enterprise, security and offline statutory suites are also used. Statutory fixture updates explicitly provide buyer and transport details instead of relying on invented defaults. These tests check the local implementation, not certification by Tally, GSTN, a bank or a tax authority.


**Read-only company books browser:** native company selection can open searchable
ledger/group lists and a Sales register, with date filters, ledger entry navigation,
pagination and voucher allocations. The reference-gated native dataset contains
2,013 masters and 79 deduplicated Sales vouchers / 188 allocations. Unresolved
references reject the books request. Voucher numbers remain XML labels; native
names, parents, dates and amounts are compared before display. Opening/closing
balances and posting status are unavailable. No import, native writes or complete
book activation occurs. The desktop HTTP launcher also uses the threaded server.
Targeted validation: 81 Python tests and frontend contracts.
