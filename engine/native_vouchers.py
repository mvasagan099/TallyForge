"""Read-only batch reader for every voucher in a native TallyPrime ``TranMgr.1800``.

One pass decodes, per transaction record: voucher type, date, master ID, alter ID, whether the record
carries the company-GUID field, and the ledger postings (the same 10003/2/2 allocation path as the Sales
preview, without its Sales-only restriction).

What was verified, and against what: on one company (22,343 live vouchers in a running TallyPrime, eight
voucher types, 40,348 native transaction records) type, date and alter ID matched for every live voucher,
and the per-ledger net of every decodable voucher matched TallyPrime exactly. See PARITY_IMPLEMENTATION_STATUS.md
for the numbers and the limits. Nothing here is a published Tally format specification.

Known limits:
* Line-by-line entries can differ from TallyPrime's (the native allocation list holds item-level pieces where
  TallyPrime shows one aggregate line); the per-ledger net is what matches, so use ``ledger_nets``.
* Such a difference still looks balanced natively, so balance cannot detect it.
* "Active" is an inference (see ``select_active``) that was wrong for 2 of 17,988 non-live records.
* Voucher number (field 2005, else 203) and narration (field 205) are decoded. Number was exact for every voucher
  where TallyPrime supplies one (20,281 of 20,281); for 2,062 Sales vouchers TallyPrime's export returns it blank while
  the native file holds one, so those cannot be checked. Narration keeps trailing whitespace that Tally's export trims.
* Cancelled / optional flags and opening balances are NOT decoded.
"""
import datetime
from collections import Counter, defaultdict
from decimal import Decimal

from engine.native_reference import _scan, _values, canonical_name
from engine.native_tally import detect, folders
from engine.native_records import checksum, chain_data
from engine.native_tlv import decode_fields
from engine.native_voucher import decode_allocation_evidence

MAX_NODES = 16384
HEADER_WORDS = b'\x01\0\0\0\x03\0\0\0'


def _kind(nodes):
    values = _values(nodes, 203, 0x300, True)
    return int(values[0]) if values else None


def _date(nodes):
    values = _values(nodes, 103, 0x0d00, True)
    if not values:
        return None
    try:
        return (datetime.date(1899, 12, 31) + datetime.timedelta(days=int(values[0]))).isoformat()
    except (ValueError, OverflowError):
        return None


def _first_text(nodes, *fields):
    for field in fields:
        values = _values(nodes, field, 0x0f00, True)
        if values:
            return str(values[0])
    return ''


def read_native_vouchers(root, key):
    """Decode every transaction record. Never raises for a single bad record: the reason is stored on it."""
    folder = next((p for identity, p in folders(root) if identity == key), None)
    if folder is None:
        raise ValueError('Native company not found')
    version, files = detect(folder)
    if version != '1800':
        raise ValueError('The native voucher reader supports .1800 only')
    names, company_guids = {}, set()

    def master(stream, page, number, pages):
        data, spans, _ = chain_data(stream, page, number, pages)
        layout = decode_fields(data, spans, start=int.from_bytes(page[28:32], 'little') + 50)
        name = canonical_name(layout)
        if name:
            names[int.from_bytes(page[4:8], 'little')] = name
        company_guids.update(_values(layout['nodes'], 507, 0x0f00, True))

    sources = {'Manager.1800': _scan(files['manager.1800'], master)}
    if len(company_guids) != 1:
        raise ValueError('Native masters do not identify exactly one company GUID')
    records = []

    def voucher(stream, page, number, pages):
        if page[32:40] != HEADER_WORDS:
            return
        record = {'sequence': int.from_bytes(page[4:8], 'little'), 'page': number}
        position = stream.tell()
        try:
            layouts = []
            for at in (40, 44):
                target = int.from_bytes(page[at:at + 4], 'little')
                if not 0 < target < pages:
                    raise ValueError('invalid metadata reference')
                stream.seek(target * 512)
                metadata = stream.read(512)
                if (len(metadata) != 512 or metadata[4:8] != page[4:8] or int.from_bytes(metadata[16:18], 'little') != 66
                        or int.from_bytes(metadata[:4], 'little') != checksum(metadata, target)):
                    raise ValueError('metadata identity or checksum differs')
                data, spans, _ = chain_data(stream, metadata, target, pages)
                layouts.append(decode_fields(data, spans, max_nodes=MAX_NODES))
            nodes = layouts[0]['nodes']
            kind = _kind(nodes)
            master_ids = _values(nodes, 3003, 0x600, True)
            alter_ids = _values(nodes, 3004, 0x600, True)
            record.update(type=names.get(kind), master_id=int(master_ids[0]) if master_ids else None,
                          alter_id=int(alter_ids[0]) if alter_ids else None, date=_date(nodes),
                          has_company_guid=bool(_values(nodes, 3004, 0x0f00, True)),
                          number=_first_text(nodes, 2005, 203), narration=_first_text(nodes, 205),
                          layout_complete=bool(layouts[0]['complete']))
            if not layouts[1]['complete']:
                record['error'] = 'postings layout incomplete: ' + str(layouts[1]['diagnostic'])[:80]
            else:
                try:
                    allocations = decode_allocation_evidence(layouts[1], names)
                    record['entries'] = [(e['ledger_name'], e['amount_decimal']) for e in allocations['entries']]
                    record['balanced'] = bool(allocations['balanced'])
                except ValueError as exc:
                    record['error'] = str(exc)[:120]
        except ValueError as exc:
            record['error'] = str(exc)[:120]
        finally:
            stream.seek(position)
        records.append(record)

    sources['TranMgr.1800'] = _scan(files['tranmgr.1800'], voucher)
    company_guid = next(iter(company_guids))
    for r in records:
        if r.get('master_id') is not None:
            r['guid'] = company_guid + '-' + format(r['master_id'], '08x')
    return {'company_guid': company_guid, 'records': records, 'sources': sources, 'ledger_count': len(names),
            'source_files_written': False}


def select_active(records):
    """One record per voucher: the company-GUID-bearing record with the highest alter ID.

    Inference, not a documented flag: on the verified company every live voucher carried that field and
    all but 2 of 17,988 other records did not (those 2 belong to masters that have two records each).
    Records without it are treated as deleted/superseded history."""
    best = {}
    for r in records:
        if not r.get('has_company_guid') or r.get('master_id') is None:
            continue
        rank = (r.get('alter_id') or 0, r['sequence'])
        current = best.get(r['master_id'])
        if current is None or rank > (current.get('alter_id') or 0, current['sequence']):
            best[r['master_id']] = r
    return sorted(best.values(), key=lambda r: (r.get('date') or '', r['master_id']))


def ledger_nets(active, non_posting=frozenset()):
    """Net per ledger over decodable, posting vouchers; debit positive (TallyPrime's AMOUNT is negative for debit).

    Returns (nets, skipped) where skipped counts vouchers whose postings could not be decoded or are non-posting."""
    nets, skipped = defaultdict(Decimal), Counter()
    for r in active:
        if r.get('type') in non_posting:
            skipped['non-posting type'] += 1
            continue
        if 'entries' not in r:
            skipped['postings not decoded'] += 1
            continue
        for name, amount in r['entries']:
            nets[name] -= Decimal(amount)
    return {k: v for k, v in nets.items() if v != 0}, dict(skipped)


def compare_with_bundle(active, bundle):
    """Compare decoded native vouchers with an XML/live bundle (tally_xml_import.bundle_from_raw).

    Returns counts only; a voucher is matched through its GUID."""
    truth = bundle['vouchers']
    native = {r['guid']: r for r in active if 'guid' in r}
    out = Counter()
    mismatches = defaultdict(list)
    for guid, v in truth.items():
        r = native.get(guid)
        if r is None:
            out['truth voucher missing from native'] += 1
            continue
        out['matched by GUID'] += 1
        if v.get('number'):
            equal = r.get('number') == v['number']
            out['number equal' if equal else 'number DIFFERS'] += 1
            if not equal and len(mismatches['number']) < 3:
                mismatches['number'].append(guid)
        elif r.get('number'):
            out['number held natively but blank in truth (unverifiable)'] += 1
        want, got = (v.get('narration') or '').strip(), (r.get('narration') or '').strip()
        out['narration equal' if want == got else 'narration DIFFERS'] += 1
        for label, ok in (('type', r.get('type') == v['type']), ('date', r.get('date') == v['date']),
                          ('alter id', str(r.get('alter_id')) == str(v['alter_id']))):
            out[label + ' equal'] += ok
            if not ok and len(mismatches[label]) < 3:
                mismatches[label].append(guid)
        if 'entries' not in r:
            out['postings not decoded'] += 1
            continue
        a, b = defaultdict(Decimal), defaultdict(Decimal)
        for name, amount in r['entries']:
            a[name.replace('\r\n', '\n')] -= Decimal(amount)
        for e in v['entries']:
            b[e['ledger'].replace('\r\n', '\n')] += -e['amount']
        equal = {k: x for k, x in a.items() if x} == {k: x for k, x in b.items() if x}
        out['per-ledger net equal' if equal else 'per-ledger net DIFFERS'] += 1
        if not equal and len(mismatches['net']) < 3:
            mismatches['net'].append(guid)
    out['native active but not in truth'] = len(set(native) - set(truth))
    return dict(out), dict(mismatches)
