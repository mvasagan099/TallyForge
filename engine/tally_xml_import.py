"""Import TallyPrime XML exports (masters + vouchers) into a TallyForge company.

Design rules:
* Source files are opened read-only and hashed before parsing; nothing is written to them.
* All money is parsed and summed with Decimal. Amounts that are not plain decimal
  strings (for example foreign-currency expressions) are rejected, never guessed.
* Tally sign convention: a negative AMOUNT is a debit, a positive AMOUNT is a credit. The
  ISDEEMEDPOSITIVE flag is informational (it disagrees on real data, e.g. Round Off) and only counted.
* Unbalanced vouchers are rejected and reported. No suspense balancing entries are invented.
* Re-running is idempotent: records are keyed by Tally GUID. Unchanged records are skipped,
  changed ones are replaced, all inside one SQLite transaction.
* Opening balances that are absent from the export are recorded as *unknown*, not zero.
"""
import hashlib
import io
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_XML_BYTES = 160 * 1024 * 1024  # live monthly voucher responses reach ~72 MB
MAX_ELEMENTS = 30_000_000
PLAIN_AMOUNT = re.compile(r'^[+-]?\d+(?:\.\d+)?$')
QTY = re.compile(r'^\s*([+-]?\d+(?:\.\d+)?)\s*(.*?)\s*$')
CENT = Decimal('0.01')
IMPORTER_VERSION = 1
# Voucher kinds that do not post to ledgers in TallyPrime. Verified against a real company: Purchase Order
# (its postings net exactly to the gap between our books and TallyPrime's Trial Balance). The rest follow
# Tally's documented behaviour and are not verified on that data.
NON_POSTING_KINDS = frozenset({'Purchase Order', 'Sales Order', 'Job Work In Order', 'Job Work Out Order', 'Memorandum',
                               'Delivery Note', 'Receipt Note', 'Rejections In', 'Rejections Out', 'Material In',
                               'Material Out', 'Stock Journal', 'Physical Stock', 'Attendance'})
UNGROUPED = 'Primary (ungrouped ledgers)'

ENTRY_TAGS = ('LEDGERENTRIES.LIST', 'ALLLEDGERENTRIES.LIST')
INVENTORY_TAGS = ('ALLINVENTORYENTRIES.LIST', 'INVENTORYENTRIES.LIST')


class TallyImportError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Reading and parsing
# ---------------------------------------------------------------------------
def read_source(path):
    """Read a stable snapshot of a file without modifying it; return (bytes, sha256)."""
    path = Path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not 0 < before.st_size <= MAX_XML_BYTES:
            raise TallyImportError(f'{path.name}: file is empty or larger than {MAX_XML_BYTES} bytes')
        raw = stream.read(MAX_XML_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(raw) != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise TallyImportError(f'{path.name}: file changed while being read')
    return raw, hashlib.sha256(raw).hexdigest()


def _decode(raw):
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        return raw.decode('utf-16')
    if raw[:4] in (b'<\0E\0', b'<\0?\0'):
        return raw.decode('utf-16-le')
    return raw.decode('utf-8-sig')


def _sanitize(text):
    """XML 1.0 forbids most C0 controls, which Tally emits (e.g. &#4; and raw \\x05).

    Map them to private-use placeholders that do not occur in the text, parse, then
    restore exact values. References inside CDATA/comments are left untouched."""
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise TallyImportError('XML DTD/entity declarations are not accepted')
    bad = [v for v in range(32) if v not in (9, 10, 13)]
    base = next((b for b in range(0xF0000, 0xF1000, 32) if all(chr(b + v) not in text for v in bad)), None)
    if base is None:
        raise TallyImportError('Cannot reserve control-character placeholders')

    def numeric(match):
        token = match[1]
        if len(token) > 10:
            raise TallyImportError('Invalid numeric character reference')
        value = int(token[1:], 16) if token.lower().startswith('x') else int(token)
        return chr(base + value) if value in bad else match[0]

    parts = re.split(r'(<!\[CDATA\[.*?\]\]>|<!--.*?-->)', text, flags=re.S)
    for i, part in enumerate(parts):
        if not part.startswith(('<![CDATA[', '<!--')):
            part = re.sub(r'&#(x[0-9a-fA-F]+|[0-9]+);', numeric, part)
        parts[i] = re.sub('[\x00-\x08\x0b\x0c\x0e-\x1f]', lambda m: chr(base + ord(m[0])), part)
    restore = {base + v: v for v in bad}
    return ''.join(parts), (lambda value: (value or '').translate(restore))


def _amount(raw, where):
    raw = (raw or '').strip()
    if not raw:
        return None
    if len(raw) > 40 or not PLAIN_AMOUNT.match(raw):
        raise TallyImportError(f'{where}: unsupported amount format {raw!r} (foreign-currency or formula amounts are not imported)')
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise TallyImportError(f'{where}: invalid amount {raw!r}') from exc


def _yes(value):
    return (value or '').strip().lower() == 'yes'


def _date(raw, where):
    raw = (raw or '').strip()
    if not re.fullmatch(r'\d{8}', raw):
        raise TallyImportError(f'{where}: unsupported date {raw!r}')
    return f'{raw[:4]}-{raw[4:6]}-{raw[6:]}'


def _quantity(raw):
    """'100 Kg' -> (Decimal('100'), 'Kg'). Returns (None, '') when absent/unparseable."""
    m = QTY.match(raw or '')
    if not m or not (raw or '').strip():
        return None, ''
    return Decimal(m[1]), m[2]


def parse_export(raw, source_name='export.xml'):
    """Parse one Tally XML export into plain dicts. Only records directly under
    TALLYMESSAGE are considered."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_XML_BYTES:
        raise TallyImportError(f'{source_name}: invalid or oversized XML')
    text, fix = _sanitize(_decode(bytes(raw)))
    out = {'companies': set(), 'groups': [], 'ledgers': [], 'currencies': [], 'vouchers': [], 'voucher_types': [],
           'other': Counter()}
    stack, elements = [], 0
    try:
        for event, node in ET.iterparse(io.StringIO(text), events=('start', 'end')):
            if event == 'start':
                stack.append(node.tag)
                elements += 1
                if elements > MAX_ELEMENTS:
                    raise TallyImportError(f'{source_name}: XML element limit exceeded')
                continue
            stack.pop()
            if node.tag == 'SVCURRENTCOMPANY' and fix(node.text).strip():
                out['companies'].add(fix(node.text).strip())
            if stack and stack[-1] in ('TALLYMESSAGE', 'COLLECTION'):
                handler = HANDLERS.get(node.tag)
                if handler:
                    out[handler[0]].append(handler[1](node, fix, source_name))
                else:
                    out['other'][node.tag] += 1
                node.clear()
    except ET.ParseError as exc:
        raise TallyImportError(f'{source_name}: malformed XML: {exc}') from exc
    out['companies'] = sorted(out['companies'])
    out['other'] = dict(out['other'])
    return out


def _text(node, tag, fix):
    return fix(node.findtext(tag)).strip()


def _group(node, fix, src):
    return {'name': fix(node.get('NAME')), 'guid': _text(node, 'GUID', fix), 'alter_id': _text(node, 'ALTERID', fix),
            'parent': _text(node, 'PARENT', fix), 'is_revenue': _yes(node.findtext('ISREVENUE')),
            'affects_gross': _yes(node.findtext('AFFECTSGROSSPROFIT')),
            'is_deemed_positive': _yes(node.findtext('ISDEEMEDPOSITIVE')),
            'deleted': _yes(node.findtext('ISDELETED'))}


def _ledger(node, fix, src):
    name = fix(node.get('NAME'))
    opening_raw = node.findtext('OPENINGBALANCE')
    return {'name': name, 'guid': _text(node, 'GUID', fix), 'alter_id': _text(node, 'ALTERID', fix),
            'parent': _text(node, 'PARENT', fix), 'currency': _text(node, 'CURRENCYNAME', fix),
            'opening': None if opening_raw is None else _amount(fix(opening_raw), f'{src} ledger {name!r} opening'),
            'opening_raw': None if opening_raw is None else fix(opening_raw).strip(),
            'gstin': _text(node, 'PARTYGSTIN', fix) or _text(node, 'GSTIN', fix),
            'state': _text(node, 'LEDSTATENAME', fix) or _text(node, 'STATENAME', fix),
            'pan': _text(node, 'INCOMETAXNUMBER', fix),
            'billwise': _yes(node.findtext('ISBILLWISEON')),
            'deleted': _yes(node.findtext('ISDELETED'))}


def _currency(node, fix, src):
    return {'name': fix(node.get('NAME')), 'guid': _text(node, 'GUID', fix), 'alter_id': _text(node, 'ALTERID', fix),
            'formal_name': _text(node, 'MAILINGNAME', fix) or _text(node, 'EXPANDEDSYMBOL', fix),
            'decimal_places': _text(node, 'DECIMALPLACES', fix)}


def _signed_entry(entry, fix, where, ledger_tag='LEDGERNAME'):
    ledger = fix(entry.findtext(ledger_tag))
    amount = _amount(fix(entry.findtext('AMOUNT')), f'{where} ledger {ledger!r}')
    if not ledger or amount is None:
        return None
    # The sign of AMOUNT is authoritative (negative = debit). ISDEEMEDPOSITIVE describes the ledger's
    # nature and legitimately disagrees with the sign on real data (e.g. Round Off), so it is only
    # counted; the voucher balance check is the real safeguard.
    deemed = (entry.findtext('ISDEEMEDPOSITIVE') or '').strip().lower()
    flag_mismatch = amount != 0 and deemed in ('yes', 'no') and (deemed == 'yes') != (amount < 0)
    bills = []
    for bill in entry.findall('BILLALLOCATIONS.LIST'):
        bname = fix(bill.findtext('NAME')).strip()
        bamt = _amount(fix(bill.findtext('AMOUNT')), f'{where} bill {bname!r}')
        if bname and bamt is not None:
            bills.append({'name': bname, 'type': fix(bill.findtext('BILLTYPE')).strip() or 'New Ref', 'amount': bamt})
    return {'ledger': ledger, 'amount': amount, 'bills': bills, 'flag_mismatch': bool(flag_mismatch)}


def _voucher(node, fix, src):
    guid = _text(node, 'GUID', fix)
    where = f'{src} voucher {guid or "?"}'
    v = {'guid': guid, 'alter_id': _text(node, 'ALTERID', fix), 'master_id': _text(node, 'MASTERID', fix),
         'voucher_key': _text(node, 'VOUCHERKEY', fix),
         'type': _text(node, 'VOUCHERTYPENAME', fix) or fix(node.get('VCHTYPE')),
         'date': _date(node.findtext('DATE'), where), 'number': _text(node, 'VOUCHERNUMBER', fix),
         'reference': _text(node, 'REFERENCE', fix), 'narration': fix(node.findtext('NARRATION')).strip(),
         'party': _text(node, 'PARTYLEDGERNAME', fix),
         'cancelled': _yes(node.findtext('ISCANCELLED')), 'optional': _yes(node.findtext('ISOPTIONAL')),
         'deleted': _yes(node.findtext('ISDELETED')), 'source': src,
         'entries': [], 'inventory': [], 'zero_entries': 0, 'deduplicated_allocations': 0}
    sources = {}  # list tag -> entries, merged below
    if not guid:
        raise TallyImportError(f'{src}: voucher without GUID on {v["date"]}')
    for child in node:
        if child.tag in ENTRY_TAGS:
            e = _signed_entry(child, fix, where)
            if e is None:
                continue
            if e['amount'] == 0:
                v['zero_entries'] += 1
                continue
            sources.setdefault(child.tag, []).append(e)
        elif child.tag in INVENTORY_TAGS:
            item = fix(child.findtext('STOCKITEMNAME'))
            if not item:
                continue
            qty, unit = _quantity(fix(child.findtext('ACTUALQTY')) or fix(child.findtext('BILLEDQTY')))
            rate_raw = fix(child.findtext('RATE')).strip()
            rate = _amount(rate_raw.split('/')[0], f'{where} item {item!r} rate') if rate_raw else None
            amount = _amount(fix(child.findtext('AMOUNT')), f'{where} item {item!r}') or Decimal('0')
            v['inventory'].append({'item': item, 'qty': qty, 'unit': unit, 'rate': rate, 'amount': amount,
                                   'godown': fix(child.findtext('.//GODOWNNAME')).strip(),
                                   'batch': fix(child.findtext('.//BATCHNAME')).strip(),
                                   'direction': 'In' if _yes(child.findtext('ISDEEMEDPOSITIVE')) else 'Out'})
            for alloc in child.findall('ACCOUNTINGALLOCATIONS.LIST'):
                e = _signed_entry(alloc, fix, where)
                if e is None:
                    continue
                if e['amount'] == 0:
                    v['zero_entries'] += 1
                    continue
                sources.setdefault('allocation', []).append(e)
    # The same postings can be listed by several sources: a live collection fetch returns the complete
    # ALLLEDGERENTRIES.LIST, the LEDGERENTRIES.LIST subset and the inventory allocations together, and the
    # allocation can hold item-level pieces of an aggregate that the complete list shows as one line
    # (observed on a real TallyPrime). File exports instead split postings across them. Try the possible
    # readings in order of preference and use the first that balances; if none does the voucher is
    # reported as unbalanced and rejected, never patched.
    a = sources.get('ALLLEDGERENTRIES.LIST', [])
    b = sources.get('LEDGERENTRIES.LIST', [])
    c = sources.get('allocation', [])
    union, best = [], {}
    for entries in (a, b, c):
        for key, n in Counter((e['ledger'], e['amount']) for e in entries).items():
            best[key] = max(best.get(key, 0), n)
    seen = Counter()
    for e in a + b + c:
        key = (e['ledger'], e['amount'])
        if seen[key] < best[key]:
            seen[key] += 1
            union.append(e)
    candidates = [x for x in (a, b + c, a + c, union) if x]
    chosen = next((x for x in candidates if sum((e['amount'] for e in x), Decimal('0')) == 0), candidates[0] if candidates else [])
    v['entries'] = list(chosen)
    v['deduplicated_allocations'] = len(a) + len(b) + len(c) - len(chosen)
    return v


def _voucher_type(node, fix, src):
    return {'name': fix(node.get('NAME')), 'guid': _text(node, 'GUID', fix), 'parent': _text(node, 'PARENT', fix),
            'deleted': _yes(node.findtext('ISDELETED'))}


HANDLERS = {'GROUP': ('groups', _group), 'LEDGER': ('ledgers', _ledger), 'VOUCHERTYPE': ('voucher_types', _voucher_type),
            'CURRENCY': ('currencies', _currency), 'VOUCHER': ('vouchers', _voucher)}


def voucher_fingerprint(v):
    """Stable content hash used for idempotent re-imports."""
    payload = {k: v.get(k) for k in ('type', 'date', 'number', 'reference', 'narration', 'cancelled', 'optional', 'non_posting')}
    payload['entries'] = [[e['ledger'], str(e['amount']), [[b['name'], b['type'], str(b['amount'])] for b in e['bills']]] for e in v['entries']]
    payload['inventory'] = [[i['item'], str(i['qty']), i['unit'], str(i['rate']), str(i['amount']), i['godown'], i['batch'], i['direction']] for i in v['inventory']]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_bundle(paths):
    """Read and merge several exports from files. Duplicate GUIDs across files must be identical."""
    items = []
    for path in paths:
        raw, digest = read_source(path)
        items.append((Path(path).name, str(Path(path).resolve()), raw, digest))
    return bundle_from_raw(items)


def bundle_from_raw(items):
    """Merge exports given as (name, origin, raw_bytes, sha256) tuples (files or live responses)."""
    bundle = {'sources': [], 'companies': set(), 'groups': {}, 'ledgers': {}, 'currencies': {}, 'vouchers': {},
              'voucher_types': {}, 'duplicates': Counter(), 'deleted_skipped': Counter(), 'other': Counter()}
    for name, origin, raw, digest in items:
        parsed = parse_export(raw, name)
        for vt in parsed['voucher_types']:
            if vt['name'] and not vt['deleted']:
                bundle['voucher_types'][vt['name']] = vt['parent'] or vt['name']
        bundle['sources'].append({'file': name, 'path': origin, 'sha256': digest, 'bytes': len(raw),
                                  'counts': {k: len(parsed[k]) for k in ('groups', 'ledgers', 'currencies', 'vouchers')}})
        bundle['companies'].update(parsed['companies'])
        bundle['other'].update(parsed['other'])
        for kind in ('groups', 'ledgers', 'currencies', 'vouchers'):
            for rec in parsed[kind]:
                if rec.get('deleted'):
                    bundle['deleted_skipped'][kind] += 1
                    continue
                key = rec['guid'] or rec.get('name')
                if key in bundle[kind]:
                    previous = bundle[kind][key]
                    same = (voucher_fingerprint(previous) == voucher_fingerprint(rec)) if kind == 'vouchers' else                         {k: v for k, v in previous.items() if k != 'source'} == {k: v for k, v in rec.items() if k != 'source'}
                    if not same:
                        raise TallyImportError(f'Conflicting duplicate {kind[:-1]} {key} across exports')
                    bundle['duplicates'][kind] += 1
                    continue
                bundle[kind][key] = rec
    if len(bundle['companies']) > 1:
        raise TallyImportError(f'Exports belong to different companies: {sorted(bundle["companies"])}')
    bundle['companies'] = sorted(bundle['companies'])
    bundle['non_posting_types'] = Counter()
    for v in bundle['vouchers'].values():
        kind = bundle['voucher_types'].get(v['type'], v['type'])
        v['non_posting'] = kind in NON_POSTING_KINDS
        if v['non_posting']:
            bundle['non_posting_types'][v['type']] += 1
    return bundle


# ---------------------------------------------------------------------------
# Independent reference figures computed from XML alone
# ---------------------------------------------------------------------------
def reference_figures(bundle):
    per_ledger = defaultdict(Decimal)  # debit-positive net
    unbalanced, by_type = [], Counter()
    for v in bundle['vouchers'].values():
        total = sum((e['amount'] for e in v['entries']), Decimal('0'))
        if not v.get('non_posting') and (total != 0 or not v['entries']):
            unbalanced.append({'guid': v['guid'], 'date': v['date'], 'difference': str(total)})
            continue
        if v['cancelled'] or v['optional'] or v.get('non_posting'):
            by_type[v['type'] + ' (cancelled/optional)'] += 1
            continue
        by_type[v['type']] += 1
        for e in v['entries']:
            per_ledger[e['ledger']] -= e['amount']  # negative amount = debit
    for led in bundle['ledgers'].values():
        if led['opening'] is not None:
            per_ledger[led['name']] -= led['opening']
    dr = sum((x for x in per_ledger.values() if x > 0), Decimal('0'))
    cr = -sum((x for x in per_ledger.values() if x < 0), Decimal('0'))
    return {'per_ledger': {k: v for k, v in per_ledger.items() if v != 0}, 'vouchers_by_type': dict(by_type),
            'unbalanced': unbalanced, 'trial_balance': {'debit': dr, 'credit': cr}}


# ---------------------------------------------------------------------------
# Writing into a TallyForge company database
# ---------------------------------------------------------------------------
META_SCHEMA = """
CREATE TABLE IF NOT EXISTS tally_import_record (
    company_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    guid TEXT NOT NULL,
    alter_id TEXT,
    fingerprint TEXT,
    local_id INTEGER,
    local_name TEXT,
    source_file TEXT,
    opening_known INTEGER,
    PRIMARY KEY (company_id, kind, guid)
);
CREATE TABLE IF NOT EXISTS tally_import_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    importer_version INTEGER NOT NULL,
    tally_company TEXT,
    sources_json TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
"""


def _q(value):
    return float(value.quantize(CENT)) if value is not None else 0.0


def _record(conn, cid, kind, guid):
    row = conn.execute('SELECT * FROM tally_import_record WHERE company_id=? AND kind=? AND guid=?', (cid, kind, guid)).fetchone()
    return dict(row) if row else None


def _put_record(conn, cid, kind, guid, alter_id, fingerprint, local_id, local_name, source, opening_known=None):
    conn.execute("""INSERT INTO tally_import_record(company_id,kind,guid,alter_id,fingerprint,local_id,local_name,source_file,opening_known)
                    VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(company_id,kind,guid) DO UPDATE SET alter_id=excluded.alter_id, fingerprint=excluded.fingerprint,
                    local_id=excluded.local_id, local_name=excluded.local_name, source_file=excluded.source_file,
                    opening_known=excluded.opening_known""",
                 (cid, kind, guid, alter_id, fingerprint, local_id, local_name, source, opening_known))


def _ordered_groups(groups):
    """Parents before children; unknown parents are reported by the caller."""
    by_name = {g['name']: g for g in groups}
    done, order = set(), []

    def visit(name, trail=()):
        if name in done or name not in by_name:
            return
        if name in trail:
            raise TallyImportError(f'Group parent cycle at {name!r}')
        parent = by_name[name]['parent']
        if parent:
            visit(parent, trail + (name,))
        done.add(name)
        order.append(by_name[name])
    for g in groups:
        visit(g['name'])
    return order


def import_bundle(db, bundle):
    """Write a parsed bundle into db (an EnterpriseAccountingDB with its company selected).

    Runs as one transaction; any error rolls back everything."""
    import datetime
    conn = db.conn
    cid = db.active_company_id
    conn.executescript(META_SCHEMA)
    ref = reference_figures(bundle)
    summary = Counter()
    skipped, warnings = [], []
    summary['entries_flag_disagrees_with_sign'] = sum(
        1 for v in bundle['vouchers'].values() for e in v['entries'] if e.get('flag_mismatch'))
    if not summary['entries_flag_disagrees_with_sign']:
        del summary['entries_flag_disagrees_with_sign']
    rejected = {u['guid'] for u in ref['unbalanced']}
    with conn:
        # Currencies (base currency only; the supplied exports have one, listed twice).
        for cur in bundle['currencies'].values():
            if not conn.execute('SELECT 1 FROM currency WHERE symbol=?', (cur['name'],)).fetchone():
                is_base = 0 if conn.execute('SELECT 1 FROM currency WHERE is_base=1').fetchone() else 1
                conn.execute('INSERT INTO currency(symbol, formal_name, rate_of_exchange, is_base) VALUES(?,?,1.0,?)',
                             (cur['name'], cur['formal_name'] or cur['name'], is_base))
                summary['currencies_created'] += 1
            _put_record(conn, cid, 'currency', cur['guid'] or cur['name'], cur['alter_id'], None, None, cur['name'], None)

        # Groups
        for g in _ordered_groups(list(bundle['groups'].values())):
            parent = g['parent'] or 'Primary'
            if parent != 'Primary' and not conn.execute('SELECT 1 FROM account_group WHERE name=?', (parent,)).fetchone():
                raise TallyImportError(f'Group {g["name"]!r} has unknown parent {parent!r}')
            row = conn.execute('SELECT parent,is_revenue,affects_gross FROM account_group WHERE name=?', (g['name'],)).fetchone()
            values = (parent, int(g['is_revenue']), int(g['affects_gross']))
            if row is None:
                conn.execute('INSERT INTO account_group(name,parent,is_revenue,affects_gross) VALUES(?,?,?,?)', (g['name'],) + values)
                summary['groups_created'] += 1
            elif tuple(row) != values:
                conn.execute('UPDATE account_group SET parent=?,is_revenue=?,affects_gross=? WHERE name=?', values + (g['name'],))
                summary['groups_updated'] += 1
            else:
                summary['groups_unchanged'] += 1
            _put_record(conn, cid, 'group', g['guid'] or g['name'], g['alter_id'], None, None, g['name'], None)

        # Ledgers
        posted = {e['ledger'] for v in bundle['vouchers'].values() for e in v['entries']}
        for led in bundle['ledgers'].values():
            parent = led['parent']
            if not parent:
                # Tally's built-in Primary-level ledgers (e.g. Profit & Loss A/c) have no group, but TallyForge
                # ledgers require one. Keep them, in an explicit group, instead of dropping real postings.
                parent = UNGROUPED
                if not conn.execute('SELECT 1 FROM account_group WHERE name=?', (UNGROUPED,)).fetchone():
                    conn.execute('INSERT INTO account_group(name,parent,is_revenue,affects_gross) VALUES(?,?,0,0)', (UNGROUPED, 'Primary'))
                    summary['groups_created'] += 1
                summary['ledgers_placed_in_ungrouped_group'] += 1
            if not conn.execute('SELECT 1 FROM account_group WHERE name=?', (parent,)).fetchone():
                raise TallyImportError(f'Ledger {led["name"]!r} references unknown group {parent!r}')
            opening = led['opening'] or Decimal('0')
            op_type = 'Dr' if opening < 0 else 'Cr' if opening > 0 else 'Dr'
            values = dict(group_name=parent, opening_balance=_q(abs(opening)), opening_balance_type=op_type,
                          gstin=led['gstin'], state=led['state'], pan=led['pan'], maintain_bill_by_bill=int(led['billwise']))
            existing = conn.execute('SELECT id,' + ','.join(values) + ' FROM ledger WHERE name=?', (led['name'],)).fetchone()
            if existing:
                lid = existing['id']
                if tuple(existing[k] for k in values) == tuple(values.values()):
                    summary['ledgers_unchanged'] += 1
                else:
                    conn.execute('UPDATE ledger SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                                 tuple(values.values()) + (lid,))
                    summary['ledgers_updated'] += 1
            else:
                lid = conn.execute('INSERT INTO ledger(name,company_id,' + ','.join(values) + ') VALUES(?,?,' +
                                   ','.join('?' * len(values)) + ')', (led['name'], cid) + tuple(values.values())).lastrowid
                summary['ledgers_created'] += 1
            _put_record(conn, cid, 'ledger', led['guid'] or led['name'], led['alter_id'], None, lid, led['name'], None,
                        opening_known=0 if led['opening'] is None else 1)
            if led['opening'] is None:
                summary['ledgers_opening_unknown'] += 1

        missing = sorted(posted - {l['name'] for l in bundle['ledgers'].values()})
        if missing:
            raise TallyImportError(f'Vouchers reference ledgers absent from the master export: {missing[:10]}')

        # Vouchers
        for v in sorted(bundle['vouchers'].values(), key=lambda x: (x['date'], x['guid'])):
            if v['guid'] in rejected:
                summary['vouchers_rejected_unbalanced'] += 1
                continue
            fp = voucher_fingerprint(v)
            prior = _record(conn, cid, 'voucher', v['guid'])
            if prior and prior['fingerprint'] == fp and conn.execute('SELECT 1 FROM voucher WHERE id=?', (prior['local_id'],)).fetchone():
                summary['vouchers_unchanged'] += 1
                continue
            if prior:
                conn.execute('DELETE FROM voucher WHERE id=?', (prior['local_id'],))
                summary['vouchers_replaced'] += 1
            else:
                summary['vouchers_created'] += 1
            for inv in v['inventory']:
                _ensure_stock_item(conn, cid, inv, summary)
            total = sum((-e['amount'] for e in v['entries'] if e['amount'] < 0), Decimal('0'))
            vid = conn.execute("""INSERT INTO voucher(voucher_type,voucher_number,date,narration,total_amount,currency_symbol,
                                  exchange_rate,is_optional,is_cancelled,reference_no,company_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                               (v['type'], v['number'], v['date'], v['narration'], _q(total),
                                _base_symbol(conn), 1.0, int(v['optional'] or v.get('non_posting', False)), int(v['cancelled']),
                                v['reference'] or None, cid)).lastrowid
            for e in v['entries']:
                conn.execute('INSERT INTO voucher_entry(voucher_id,ledger_name,entry_type,amount,narration,cost_centre_name) VALUES(?,?,?,?,?,?)',
                             (vid, e['ledger'], 'Dr' if e['amount'] < 0 else 'Cr', _q(abs(e['amount'])), '', ''))
                for b in e['bills']:
                    conn.execute('INSERT INTO bill_allocation(voucher_id,ledger_name,ref_type,ref_name,amount,direction) VALUES(?,?,?,?,?,?)',
                                 (vid, e['ledger'], b['type'], b['name'], _q(abs(b['amount'])), 'Dr' if b['amount'] < 0 else 'Cr'))
            for inv in v['inventory']:
                conn.execute('INSERT INTO voucher_inventory(voucher_id,stock_item_name,godown_name,batch_number,qty,rate,amount,direction) VALUES(?,?,?,?,?,?,?,?)',
                             (vid, inv['item'], inv['godown'] or 'Main Location', inv['batch'],
                              float(abs(inv['qty'])) if inv['qty'] is not None else 0.0,
                              _q(inv['rate']) if inv['rate'] is not None else 0.0, _q(abs(inv['amount'])), inv['direction']))
            if not v['number']:
                summary['vouchers_without_number'] += 1
            _put_record(conn, cid, 'voucher', v['guid'], v['alter_id'], fp, vid, v['number'], v['source'])

        run_summary = {'counts': dict(summary), 'skipped': skipped, 'warnings': warnings,
                       'unbalanced_rejected': ref['unbalanced'], 'duplicates_across_files': dict(bundle['duplicates']),
                       'deleted_records_skipped': dict(bundle['deleted_skipped']), 'ignored_record_types': dict(bundle['other']),
                       'non_posting_vouchers_kept_out_of_balances': dict(bundle.get('non_posting_types', {}))}
        conn.execute('INSERT INTO tally_import_run(company_id,started_at,importer_version,tally_company,sources_json,summary_json) VALUES(?,?,?,?,?,?)',
                     (cid, datetime.datetime.now().isoformat(timespec='seconds'), IMPORTER_VERSION,
                      bundle['companies'][0] if bundle['companies'] else None,
                      json.dumps(bundle['sources'], ensure_ascii=False), json.dumps(run_summary, ensure_ascii=False, default=str)))
    try:
        db._log_edit('Company', str(cid), 'IMPORT', f'Tally XML import: {dict(summary)}')
        conn.commit()
    except Exception:
        pass
    return run_summary


def _base_symbol(conn):
    row = conn.execute('SELECT currency_symbol FROM company WHERE id=1').fetchone()
    return row[0] if row else '₹'


def _ensure_stock_item(conn, cid, inv, summary):
    if conn.execute('SELECT 1 FROM stock_item WHERE name=?', (inv['item'],)).fetchone():
        return
    unit = inv['unit'] or 'Nos'
    if not conn.execute('SELECT 1 FROM uom WHERE symbol=?', (unit,)).fetchone():
        conn.execute('INSERT INTO uom(symbol, formal_name, decimal_places) VALUES(?,?,2)', (unit, unit))
        summary['units_created'] += 1
    # Stock masters were not part of the export; only name and unit are known.
    conn.execute("""INSERT INTO stock_item(name, group_name, uom_symbol, hsn_code, gst_rate, opening_qty, opening_rate,
                    opening_value, company_id) VALUES(?, 'Primary', ?, '', 0.0, 0, 0, 0, ?)""", (inv['item'], unit, cid))
    summary['stock_items_created_from_vouchers'] += 1


# ---------------------------------------------------------------------------
# Verification of the imported database against the XML reference
# ---------------------------------------------------------------------------
def verify(db, bundle):
    """Compare the database with figures computed independently from the XML."""
    ref = reference_figures(bundle)
    conn = db.conn
    cid = db.active_company_id
    checks = []

    def check(name, ok, expected, actual):
        checks.append({'check': name, 'ok': bool(ok), 'expected': str(expected), 'actual': str(actual)})

    imported = {r['guid']: r['local_id'] for r in conn.execute(
        "SELECT guid, local_id FROM tally_import_record WHERE company_id=? AND kind='voucher'", (cid,))}
    expected_guids = {g for g in bundle['vouchers'] if g not in {u['guid'] for u in ref['unbalanced']}}
    check('voucher GUIDs imported', set(imported) >= expected_guids, len(expected_guids), len(set(imported) & expected_guids))
    ids = [imported[g] for g in expected_guids if g in imported]
    by_type = Counter()
    unbalanced = 0
    per_ledger = defaultdict(Decimal)
    for vid in ids:
        v = conn.execute('SELECT voucher_type,is_cancelled,is_optional FROM voucher WHERE id=?', (vid,)).fetchone()
        rows = conn.execute('SELECT ledger_name,entry_type,amount FROM voucher_entry WHERE voucher_id=?', (vid,)).fetchall()
        dr = sum((Decimal(repr(r['amount'])).quantize(CENT) for r in rows if r['entry_type'] == 'Dr'), Decimal('0'))
        cr = sum((Decimal(repr(r['amount'])).quantize(CENT) for r in rows if r['entry_type'] == 'Cr'), Decimal('0'))
        if (dr != cr or dr == 0) and not (v['is_cancelled'] or v['is_optional']):
            unbalanced += 1
        if v['is_cancelled'] or v['is_optional']:
            by_type[v['voucher_type'] + ' (cancelled/optional)'] += 1
            continue
        by_type[v['voucher_type']] += 1
        for r in rows:
            amt = Decimal(repr(r['amount'])).quantize(CENT)
            per_ledger[r['ledger_name']] += amt if r['entry_type'] == 'Dr' else -amt
    check('voucher counts by type', dict(by_type) == ref['vouchers_by_type'], ref['vouchers_by_type'], dict(by_type))
    check('every imported voucher balanced (Dr = Cr)', unbalanced == 0, 0, unbalanced)
    for led in conn.execute('SELECT name, opening_balance, opening_balance_type FROM ledger'):
        ob = Decimal(repr(led['opening_balance'])).quantize(CENT)
        if ob:
            per_ledger[led['name']] += ob if led['opening_balance_type'] == 'Dr' else -ob
    db_net = {k: v for k, v in per_ledger.items() if v != 0}
    ref_net = {k: v.quantize(CENT) for k, v in ref['per_ledger'].items()}
    mismatched = sorted(k for k in set(db_net) | set(ref_net) if db_net.get(k, Decimal('0')) != ref_net.get(k, Decimal('0')))
    check('per-ledger closing balances match XML', not mismatched, len(ref_net), f'{len(mismatched)} mismatched: {mismatched[:5]}')
    dr = sum((x for x in db_net.values() if x > 0), Decimal('0'))
    cr = -sum((x for x in db_net.values() if x < 0), Decimal('0'))
    tb = ref['trial_balance']
    check('trial balance debit total', dr == tb['debit'].quantize(CENT), tb['debit'], dr)
    check('trial balance credit total', cr == tb['credit'].quantize(CENT), tb['credit'], cr)
    check('trial balance Dr = Cr', dr == cr, dr, cr)
    # Also exercise the application's own trial-balance report path.
    app_tb = db.get_trial_balance()
    app_dr = sum((Decimal(repr(r['dr_amount'])).quantize(CENT) for r in app_tb), Decimal('0'))
    app_cr = sum((Decimal(repr(r['cr_amount'])).quantize(CENT) for r in app_tb), Decimal('0'))
    check('application trial balance report matches', (app_dr, app_cr) == (dr, cr), (str(dr), str(cr)), (str(app_dr), str(app_cr)))
    ledgers_expected = {l['name'] for l in bundle['ledgers'].values() if l['parent']}
    ledgers_present = {r[0] for r in conn.execute('SELECT name FROM ledger')}
    check('ledgers present', ledgers_expected <= ledgers_present, len(ledgers_expected), len(ledgers_expected & ledgers_present))
    groups_present = {r[0] for r in conn.execute('SELECT name FROM account_group')}
    groups_expected = {g['name'] for g in bundle['groups'].values()}
    check('groups present', groups_expected <= groups_present, len(groups_expected), len(groups_expected & groups_present))
    return {'ok': all(c['ok'] for c in checks), 'checks': checks,
            'reference': {'vouchers_by_type': ref['vouchers_by_type'], 'unbalanced_in_source': len(ref['unbalanced']),
                          'trial_balance': {k: str(v) for k, v in tb.items()}}}


# ---------------------------------------------------------------------------
# Company-level entry point
# ---------------------------------------------------------------------------
def default_sources(folder):
    folder = Path(folder)
    found = [folder / n for n in ('Master.xml', 'DayBook.xml', 'Transactions.xml') if (folder / n).is_file()]
    if not found or found[0].name != 'Master.xml':
        raise TallyImportError(f'{folder}: Master.xml is required')
    return found


def find_imported_company(data_root, tally_company):
    data_root = Path(data_root)
    for folder in sorted(p for p in data_root.iterdir() if p.is_dir() and p.name.isdigit()):
        path = folder / 'company.db'
        if not path.is_file() or (folder / 'company.archived').exists():
            continue
        conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            row = conn.execute('SELECT company_id FROM tally_import_run WHERE tally_company=? ORDER BY id DESC LIMIT 1', (tally_company,)).fetchone()
        except sqlite3.Error:
            row = None
        finally:
            conn.close()
        if row:
            return folder, row[0]
    return None, None


def import_company(data_root, source_paths, company_name=None):
    """Create (first run) or re-sync a numbered TallyForge company folder from export files."""
    return import_company_bundle(data_root, load_bundle(source_paths), company_name)


def import_company_bundle(data_root, bundle, company_name=None, extra_verify=None):
    """Same as import_company for an already-built bundle. extra_verify(db, bundle) may return
    additional verification checks (e.g. against TallyPrime's own reports)."""
    from engine.accounting_db import EnterpriseAccountingDB
    tally_company = bundle['companies'][0] if bundle['companies'] else None
    if not tally_company and not company_name:
        raise TallyImportError('Company name not found in exports; pass company_name')
    data_root = Path(data_root).resolve()
    folder, cid = find_imported_company(data_root, tally_company or company_name)
    created = False
    if folder is None:
        number = 10000
        while True:
            folder = data_root / str(number)
            try:
                folder.mkdir()
                break
            except FileExistsError:
                number += 1
        created = True
    db = None
    try:
        db = EnterpriseAccountingDB(str(folder / 'company.db'), seed_demo=False)
        if created:
            dates = sorted(v['date'] for v in bundle['vouchers'].values())
            start_year = int(dates[0][:4]) - (1 if dates and int(dates[0][5:7]) < 4 else 0) if dates else 2026
            cid = db.create_company(name=company_name or tally_company, state='', state_code='', address='',
                                    financial_year_from=f'{start_year}-04-01', financial_year_to=f'{start_year + 1}-03-31')
            db.conn.execute("UPDATE company SET gstin='', pincode='' WHERE id=?", (cid,))
            db.conn.commit()
        db.select_company(cid)
        summary = import_bundle(db, bundle)
        result = verify(db, bundle)
        if extra_verify:
            extra = extra_verify(db, bundle)
            result['checks'] += extra['checks']
            result['tally_reference'] = extra.get('reference')
            result['ok'] = all(c['ok'] for c in result['checks'])
        db.checkpoint()
        return {'folder': str(folder), 'storage_key': f'{folder.name}:{cid}', 'created': created,
                'tally_company': tally_company, 'sources': bundle['sources'], 'import': summary, 'verification': result}
    except Exception:
        if db:
            db.close()
            db = None
        if created:
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        if db:
            db.close()
