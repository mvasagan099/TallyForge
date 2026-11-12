# Direct native-company reader

This is an experimental read-only record preview. It runs in Python on macOS,
Windows or Linux without a running TallyPrime engine. A reference-calibrated Sales
preview now decodes dates and ledger allocations. It does not yet open a complete
accounting company or produce verified company financial reports.

## Use

1. Fully restart TallyForge after updating Python files, then reload its page.
2. In F3, select either the native company folder or its parent folder.
3. Select the native entry. Under **Open native records directly · Experimental**,
   choose **Read master names**, **Read voucher search keys** or **Read transaction records**.
4. Search text or use Previous/Next. Inspect a sequence for physical source-page
   fields, unscaled numeric candidates and the SHA-256 of the source file.
5. In transaction details, use **Find related LinkMgr records by exact GUID** when
   exactly one GUID candidate is available. This reads related reference records
   without a running TallyPrime process. Raw numeric and layout sections are
   collapsed so the lookup remains visible.
6. Use **Verify transaction directory** to check the file's tree, sibling links
   and node counts. This reports structural membership, not financial active status.
7. With `Master.xml`, `Transactions.xml` and/or `DayBook.xml` in the same company
   folder, use **Compare XML references**. No XML is imported or changed.
8. Inspect a Sales transaction and choose **Read calibrated Sales preview** to
   read its date and balanced ledger allocations directly from native files.
   This preview does not read XML at runtime. Other voucher types are rejected.

The file inventory and optional live TallyPrime connection are collapsed so they
do not obscure the direct-reader controls.

## Implemented format observations

- Fixed 512-byte pages in `.1800` files. All source pages, including page zero,
  must pass the observed dual CRC-16/CCITT checks using their physical page number
  as seed. An invalid checksum rejects the request rather than returning partial
  apparent success.
- Strict length-prefixed, null-terminated UTF-16LE string fields. Invalid Unicode,
  odd lengths and unsupported control characters are rejected.
- The uint32 at byte 8 is the declared linked-page count, **not a page index or
  record subtype**. Root records have a positive count; continuation pages have
  zero. The declared count must equal the actual chain length. This interpretation
  matched all 9,478 roots in the initial cross-file check and is enforced on every
  reconstructed record. The previous index-based selection omitted some masters.
- Non-adjacent continuation pages reached through the pointer at byte 12. Joins
  require a valid checksum, matching sequence/type/marker, zero continuation
  count, valid range and no cycles. Chains are limited to 1,024 pages. Continuation
  content begins at byte 28. Physical byte spans are retained for split strings.
- Master-name candidates: root object type `0x000B`, string field 2.
  The names may include groups, ledgers, aliases or other masters. Their semantics
  and active status are not established by this filter.
- Voucher search-key candidates: root object type `0x0003`, string field
  2 containing four ENQ-separated tokens: year token, type sequence, type name,
  number. The year token is not treated as the voucher date. No type is guessed
  from party groups, number prefixes or company names.
- Transaction records: type `0x000B` roots in TranMgr.1800 with header words
  `(1, 3)` at offsets 32 and 36. Two explicit page references at offsets 40 and 44
  must resolve to checksum-valid `0x0042` roots of the same sequence. Each target
  chain is reconstructed and checked against its declared page count. The browser
  shows the actual references and text; it does not infer posting/active status.
- Detail view reconstructs root records carrying the selected sequence. Cross-root
  association is provisional unless established by explicit header references.
- Numeric evidence scanning recognizes candidate type codes `0x06`, `0x08`, `0x09`
  and `0x0D` outside valid string fields. It preserves raw little-endian bytes and
  signed/unsigned integers as decimal **strings**, including zero and 64-bit values
  beyond JavaScript's exact integer range. No amount divisor, date epoch, account
  mapping or currency sign is assigned. Candidate scanning is not a typed schema
  parser and can still match bytes in unsupported non-text structures.
- A separate sequential decoder (`engine/native_tlv.py`) now walks observed
  serialized field boundaries rather than scanning for markers. It handles bounded
  nested containers, opaque reference words, length prefixes, strict UTF-16 and
  fixed-width payloads. Unknown encodings stop the decode at an explicit offset;
  there is no search-and-resume fallback. Detail views distinguish complete and
  partial layout decoding. A complete layout still has unverified financial/date
  meanings. Zero-width and compound encodings remain opaque.
- LinkMgr.1800 has a **different layout**: a common 16-byte header, with continuation
  payload beginning at byte 16. Bytes 16–27 are data, not the Manager/TranMgr type
  and marker header. Its reader validates checksums, sequence identity, chain bounds
  and declared counts without applying the wrong header profile. String field
  `0x235B` is matched exactly to the selected transaction GUID candidate (field 10).
  File sequence numbers are independent and are never joined across these files.
- The LinkMgr sequential field profile starts at logical byte 38, after its
  opaque record prefix. Observed internal encoding words `0x0021`, `0x0022`,
  `0x0041`, `0x0042` and `0x0044` have bounded opaque payload widths. Their contents
  are not treated as ledger allocations or amounts. Extra flags, including `0x80`,
  remain visible and are not applied as a guessed currency sign. Supported
  sequential layouts must contain a matching GUID field at real field boundaries;
  a GUID-looking marker inside an opaque payload is excluded. Partial layouts
  remain explicitly labelled literal candidates, not confirmed field matches.
- The TranMgr physical directory uses its header's uint32 fields at offsets 16,
  20, 24 and 28 for tree root, first sibling, last sibling and node count. Root
  nodes have a layout word `L` at byte 28 (observed values 24, 34, 40); four page
  links at `L+34` represent previous, left, right and next. Validation requires
  matching count, valid targets, unique tree ownership, full reachability,
  reciprocal sibling links, coherent endpoints, matching in-order/tree and
  sibling order, and strictly increasing sequence numbers. Corrupt, cyclic,
  orphaned or unsupported directories are rejected. This establishes physical
  directory membership only; posted/cancelled/deleted semantics remain unknown.

These observations are checked against the supplied company and synthetic
fixtures, not a published Tally format specification. Research provenance and
the upstream MIT notice are in `THIRD_PARTY_NOTICES.md`. No upstream executable
or native writer is used.

## Supplied-company results

The supplied `100124` copy yielded:

| Source | Pages checked | Decoded preview candidates |
| --- | ---: | ---: |
| Manager.1800 | 8,114 | 2,277 master-name roots |
| TranMgr.1800 | 287,823 | 131 voucher search-key pages |
| TranMgr.1800 | 287,823 | 40,348 transaction headers with two validated metadata references each |
| LinkMgr.1800 | 41,741 | 20,673 root chains, 86,037 complete string fields |

All pages in these files passed checksum validation. The selected scans
reported zero rejected string candidates after continuation reconstruction.
The transaction scan validated 80,696 header-to-metadata references. These counts
are not active ledger/voucher totals. Historical/deleted or index records
can be present. SHA-256 comparison of all 24 original supplied files confirmed
that the reader did not modify them.

The production sequential decoder consumed all 40,348 primary transaction
metadata roots with no unsupported-field diagnostic. This establishes coverage
of that serialized layout in this copy, not the meanings of its values or the
other native record types.

The cross-file investigation found 16,863 LinkMgr GUID fields: 16,855 matched
exactly one TranMgr sequence, while 8 were unmatched or ambiguous. One TranMgr
field-10 string occurred under multiple sequences. Accordingly, the UI labels
these as exact string-reference matches, not verified active accounting
allocations. The real detail-to-link flow for transaction sequence 1 returned
LinkMgr root page 1, independent sequence 12769, chain `[1, 1188]`. A split GUID
field spans physical bytes `1022+2` and `608272+32`. No private GUID value is
required to document or test the structural observation.

The newer profile fully parses 30,450 of 40,348 secondary metadata layouts;
9,897 stop at unknown encodings and one reaches the 8,192-node resource bound.
It fully parses all 16,863 GUID-bearing LinkMgr layouts. The remaining 3,810
LinkMgr roots stop at unsupported encodings. Unknown structures are retained as
unknowns, not consumed with guessed lengths. The real related-record lookup for
sequence 1 now confirms the GUID through sequential field boundaries.

The directory verifier checks all 287,823 TranMgr pages and reaches the same
40,348 nodes through both graph paths, with tree root page 98312, first sibling
page 1 and last sibling page 287785. There are no orphan directory nodes in this
copy. Independent inspection also found that the 80,696 header metadata
references are distinct and cover the observed metadata-root set. This still
does not identify which records contribute to posted financial balances.

## Remaining requirements before opening complete books

- Interpret posting/deletion/cancellation state and pending updates. The physical
  directory is now verified, but it does not establish the active accounting dataset.
- Decode complete voucher identities, dates, amounts, signs/scales, ledger and
  stock allocations, and the links between the native files.
- Decode opening balances and reconcile closing balances and financial reports
  against independently known results. Conflicting upstream numeric scaling
  assumptions are deliberately not adopted.
- Validate multiple companies, custom voucher types, years, revisions and data
  versions. `.900`, `.500` and protected/encrypted data are not supported by this
  record preview.

No unknown balance is presented as zero, no candidate is imported into local
books, and no dummy accounting company is activated for a native selection.

## XML reference calibration

The supplied exports are UTF-16. The bounded reference parser preserves numeric
control references such as `&#4;` and literal control characters in memory using
collision-free placeholders, then restores their exact parsed values. It never
changes the files. Literal entity-like text in CDATA is preserved. DTD/entity
declarations are rejected, input is capped at 64 MB per XML, and element counts
are bounded. Only the fixed reference filenames in a discovered company folder
are accepted by the comparison endpoint. Company names must match.

| Reference | Exported records | Verified voucher dates |
| --- | ---: | --- |
| Master.xml | 1,981 ledgers and 32 groups | Not applicable |
| Transactions.xml | 30 Sales vouchers | 1–29 October 2026 |
| DayBook.xml | 49 Sales vouchers | 1–28 April 2026 |

All 79 vouchers matched exactly one native record through `VOUCHERKEY`: the high
and low 32-bit words correspond to the first and third hex components of native
metadata text field 3. The middle component remains uninterpreted. XML MASTERID
must **not** be used as a native sequence: none of these 79 identities had equal
values. For example, reference master ID 40529 corresponds to native sequence
50595. The decoded primary fields also match reference MASTERID (integer 3003),
ALTERID (integer 3004) and DATE (date field 103) for all 79. The date representation
matches days since **1899-12-31**, not the previously suggested Excel epoch.

All 1,981 reference ledgers fully decode and match primary name, company GUID,
parent group and alteration ID. All 32 reference groups match primary name,
company GUID and alteration ID. The field start is the root layout word at byte
28 plus 50: observed group layout 16 starts at byte 66, while ledger layout 20
starts at byte 70. A fixed byte-70 start incorrectly marked 23 ledgers and 9
groups unresolved (including ledgers whose parent group was unresolved). The
comparison now uses each record's declared layout, as the Sales preview already
did. Canonical names use the first text child on path `[3,2]`, not aliases,
arbitrary field-2 occurrences or mailing names. A synthetic regression covers
the shorter group prefix, dependent ledger parent and alias rejection.

The complete comparison reports no unresolved references across the supplied
three XML files. This establishes coverage of those exports only. SHA-256 checks
before and after comparison confirm all 27 company-folder files are unchanged.

Both metadata blocks decode completely for all 79 reference vouchers. The exact
allocation path is `[10003,2,2,2]`: the immediate parent has one field-2/type0300
ledger reference and one field-2/type0900 signed int64 amount. Dividing the signed
integer by **100000** gives all **188 of 188** reference ledger/amount pairs,
including inventory accounting allocations. All 79 allocation multisets match
exactly and balance to zero. Flag `0x80` accompanies the negative sample amounts;
the int64 already carries the sign, so the flag must not invert it again. Nested
bill/inventory duplicate paths are excluded. Using a top-level amount field or
the first/largest amount was disproved by reference counterexamples.

`engine/native_voucher.py` implements only that precise path, preserving raw bytes,
flags and physical provenance. Unknown ledger names, ambiguous entry shapes,
partial layouts and empty entry sets do not become zero-valued records. The
`native_sales.py` preview resolves its Sales class through native master names,
checks cross-file company GUIDs, requires balanced allocations and the calibrated
flag pattern, and preserves decimal amounts without floating-point conversion.

The corpus is **Sales only**, all exported as uncancelled/non-optional. It does
not validate posting/cancellation flags, other voucher classes, other currencies,
opening balances or complete reports. No opening-balance values were provided in
the master export; missing values are not treated as zero. The application still
reports `standalone_books_readable: false` and never activates local books from
these previews.

## Resource and integrity bounds

Requests are restricted to the existing local-admin storage API. Paths are
resolved exclusively through discovered company keys. Source files open read-only
with symlink rejection. Sources must be 512-byte aligned and between 1 KB and
512 MB. Page numbers, filters, limits and offsets are bounded. A changed file
identity, size or modification time invalidates the result. This detects normal
concurrent changes, but is not a multi-file transactional snapshot: use a stable
company copy for research.

## Validation

`tests/test_native_records.py` covers independent checksum references, declared
chain counts, explicit metadata references and sequence mismatches, non-adjacent
split Unicode strings with byte provenance, bad/cyclic chains, corruption,
truncation, source changes, traversal, symlinks, search/paging and unchanged source
hashes, unscaled signed 64-bit integers, zero values and source-byte spans.
Storage API and frontend contracts exercise the route and safe text
rendering. No live TallyPrime reconciliation or complete financial decoding is
claimed.

The current targeted run passed 79 Python tests (71 native access, record,
link, directory, layout, allocation and reference tests and 8 storage API tests), plus the JavaScript
UI contract checks. `test_native_links.py` covers the separate 16-byte profile,
exact GUID matching, split fields, corruption, identity changes and unchanged
sources. `test_native_tlv.py` covers nested bounds, no resynchronization, Unicode,
limits and lossless integers. A real-data detail
read of transaction sequence 1 reconstructed three roots across six pages and
extracted 14 complete text fields. Raw numeric candidates retained exact integer
strings. A 50-row transaction response was about 215 KB. The running browser
backend must be restarted to load reader protocol version 5; this session did not
restart it or validate the new controls in the live browser.

## REA availability

The installed `reverse-engineer-anything` skill was read and used for its staged
evidence/unknowns workflow. REA MCP's session request stalled and the tools became
unavailable. The installed public CLI reported version 6.7.0; its read-only
doctor failed during Ghidra ownership-helper compilation because Swift attempted
to write a module cache outside the permitted workspace. No configuration repair,
installation or security-setting change was made. The format findings above are
from local read-only Python analysis, not REA decompilation Evidence or execution
of TallyPrime. The existing setup.exe is an installer, not a substitute reference
execution of the TallyPrime accounting engine.

A subsequent CLI retry redirected the compiler cache environment variables into
permitted `/tmp` folders. The helper still attempted the same denied cache path,
so the retry did not repair native REA analysis. No permissions were broadened.


## Company books preview

Selecting a native company with reference XML files now opens a read-only books
browser. The `POST /api/storage/native/books` endpoint uses the same checksum,
identity, date and exact allocation comparison as the reference verifier. It
fails closed if any supplied reference is unresolved. It does not activate the
native company as a SQLite accounting company or import records.

The browser offers searchable Ledgers and Groups, a Sales register with date and
ledger filters, pagination and voucher allocation drill-downs. The current real
company yields 2,013 masters, 79 unique Sales vouchers and 188 allocation entries.
Overlapping identical exports are deduplicated by GUID. Displayed names, parent
references, dates and allocations come from matched native layouts; voucher-number
labels come from the XML exports. XML files select and validate this subset, so
this browser requires them. The separate single-voucher Sales decoder continues
to work without XML. Opening/closing balances and posting status remain unavailable,
not zero. The browser does not claim complete company coverage, support native
writes or enable reports requiring unknown opening balances.

Tests cover duplicate exports, mismatched amounts, unchanged source bytes,
nullable balances/status, the API route and traversal rejection, plus frontend
search/date filtering, ledger-to-register navigation, escaped text and exact
signed decimal voucher drill-downs. The desktop launcher uses the server's
threaded HTTP implementation so an idle browser connection cannot monopolize
its sole request worker.
