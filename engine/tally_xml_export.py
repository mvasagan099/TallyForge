"""Export a TallyForge company as Tally XML (Import Data envelope), and prove it by round trip.

Everything is written from the database with exact decimals and Tally's sign convention (AMOUNT negative =
debit). All vouchers are exported (the older exporter stopped at 1,000). Each voucher carries a GUID: the one
it was imported with, or a stable one derived from the company and voucher id, so re-importing the file into
the same or a fresh company is idempotent.

Round-trip verified with this project's own importer (``round_trip``): export, import into a fresh company,
compare every voucher, ledger, group and bill. NOT verified: that a running TallyPrime accepts the file;
TallyForge never writes to TallyPrime, so that has not been tested. Data this project does not store (bank
details, cost centres, ...) is not exported. Buyer / consignee / GST details on vouchers and ledger addresses are.
"""
import hashlib
import io
import json
import re
import sqlite3
import uuid
from decimal import Decimal
from xml.etree import ElementTree as ET

from engine.tally_xml_import import _INVOICE_FIELDS, NON_POSTING_KINDS, UNGROUPED

NAMESPACE = uuid.UUID('6f1c9e1e-5a52-4b6e-9a7d-2d9a6f6f7a10')
CENT = Decimal('0.01')
RESERVED_GROUPS = {'Primary'}
INVOICE_FIELDS = _INVOICE_FIELDS


def _d(value):
    return Decimal(repr(value)).quantize(CENT)


def _txt(parent, tag, value):
    node = ET.SubElement(parent, tag)
    node.text = '' if value is None else str(value)
    return node


def _json_dict(text):
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _invoice_details_by_voucher(conn):
    """{voucher id: invoice header details} (buyer, GSTINs, place of supply ...); empty for a company never imported."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='voucher_invoice'").fetchone():
        return {}
    return {r[0]: _json_dict(r[1]) for r in conn.execute('SELECT voucher_id, details_json FROM voucher_invoice')}


def voucher_guid(imported_guid, company_guid, voucher_id):
    """The GUID a voucher is exported under: its imported Tally GUID, else a stable derived one."""
    if imported_guid:
        return imported_guid
    return str(uuid.uuid5(NAMESPACE, f'{company_guid}:{voucher_id}')) + '-' + format(voucher_id, '08x')


def _company(conn):
    return conn.execute('SELECT * FROM company ORDER BY id LIMIT 1').fetchone()


def export_company_xml(db, include_vouchers=True):
    """Return the Tally XML (utf-8 bytes with an XML declaration) for the active company."""
    conn = db.conn
    conn.row_factory = sqlite3.Row
    company = _company(conn)
    cid = company['id']
    company_guid = str(uuid.uuid5(NAMESPACE, f'company:{company["name"]}'))
    imported = {r['local_id']: r['guid'] for r in conn.execute(
        "SELECT local_id, guid FROM tally_import_record WHERE kind='voucher'")} \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='tally_import_record'").fetchone() else {}

    env = ET.Element('ENVELOPE')
    header = ET.SubElement(env, 'HEADER')
    _txt(header, 'TALLYREQUEST', 'Import Data')
    desc = ET.SubElement(ET.SubElement(ET.SubElement(env, 'BODY'), 'IMPORTDATA'), 'REQUESTDESC')
    _txt(desc, 'REPORTNAME', 'All Masters' if not include_vouchers else 'Vouchers')
    sv = ET.SubElement(desc, 'STATICVARIABLES')
    _txt(sv, 'SVCURRENTCOMPANY', company['name'])
    data = ET.SubElement(env.find('BODY/IMPORTDATA'), 'REQUESTDATA')

    def message():
        return ET.SubElement(data, 'TALLYMESSAGE', {'xmlns:UDF': 'TallyUDF'})

    # ---- masters -------------------------------------------------------------------------------
    for u in conn.execute('SELECT * FROM uom ORDER BY symbol'):
        n = ET.SubElement(message(), 'UNIT', {'NAME': u['symbol'], 'ACTION': 'Create'})
        _txt(n, 'NAME', u['symbol'])
        _txt(n, 'ORIGINALNAME', u['formal_name'] or u['symbol'])
    groups = list(conn.execute('SELECT * FROM account_group ORDER BY id'))
    for g in groups:
        n = ET.SubElement(message(), 'GROUP', {'NAME': g['name'], 'ACTION': 'Create'})
        _txt(n, 'NAME', g['name'])
        _txt(n, 'PARENT', '' if g['parent'] == 'Primary' else g['parent'])
        _txt(n, 'ISREVENUE', 'Yes' if g['is_revenue'] else 'No')
        _txt(n, 'AFFECTSGROSSPROFIT', 'Yes' if g['affects_gross'] else 'No')
    for l in conn.execute('SELECT * FROM ledger ORDER BY id'):
        n = ET.SubElement(message(), 'LEDGER', {'NAME': l['name'], 'ACTION': 'Create'})
        _txt(n, 'NAME', l['name'])
        _txt(n, 'PARENT', '' if l['group_name'] == UNGROUPED else l['group_name'])
        opening = _d(l['opening_balance'])
        _txt(n, 'OPENINGBALANCE', f'{-opening if l["opening_balance_type"] == "Dr" else opening:.2f}')
        _txt(n, 'ISBILLWISEON', 'Yes' if l['maintain_bill_by_bill'] else 'No')
        if l['gstin']:
            _txt(n, 'PARTYGSTIN', l['gstin'])
        if l['state']:
            _txt(n, 'LEDSTATENAME', l['state'])
        if l['pan']:
            _txt(n, 'INCOMETAXNUMBER', l['pan'])
        extra = _json_dict(l['udf_data'])
        if extra.get('mailing'):
            _txt(n, 'MAILINGNAME', extra['mailing'])
        if l['address']:
            al = ET.SubElement(n, 'ADDRESS.LIST')
            for line in l['address'].split('\n'):
                _txt(al, 'ADDRESS', line)
        if extra.get('country'):
            _txt(n, 'COUNTRYNAME', extra['country'])
    for s in conn.execute('SELECT * FROM stock_item ORDER BY id'):
        n = ET.SubElement(message(), 'STOCKITEM', {'NAME': s['name'], 'ACTION': 'Create'})
        _txt(n, 'NAME', s['name'])
        _txt(n, 'BASEUNITS', s['uom_symbol'])
        if s['gst_rate']:
            _txt(n, 'GSTRATE', s['gst_rate'])
        if s['hsn_code']:
            _txt(n, 'HSNCODE', s['hsn_code'])

    if include_vouchers:
        # ---- vouchers: set-based reads, grouped in Python (22,000 vouchers export in seconds) ----
        entries, bills, inventory = {}, {}, {}
        for e in conn.execute('SELECT * FROM voucher_entry ORDER BY id'):
            entries.setdefault(e['voucher_id'], []).append(e)
        for b in conn.execute('SELECT * FROM bill_allocation ORDER BY id'):
            bills.setdefault((b['voucher_id'], b['ledger_name']), []).append(b)
        for i in conn.execute('SELECT * FROM voucher_inventory ORDER BY id'):
            inventory.setdefault(i['voucher_id'], []).append(i)
        units = {r['name']: r['uom_symbol'] for r in conn.execute('SELECT name, uom_symbol FROM stock_item')}
        invoice_details = _invoice_details_by_voucher(conn)
        for v in conn.execute('SELECT * FROM voucher ORDER BY date, id'):
            vid = v['id']
            n = ET.SubElement(message(), 'VOUCHER', {'VCHTYPE': v['voucher_type'], 'ACTION': 'Create'})
            _txt(n, 'DATE', v['date'].replace('-', ''))
            _txt(n, 'GUID', voucher_guid(imported.get(vid), company_guid, vid))
            _txt(n, 'VOUCHERTYPENAME', v['voucher_type'])
            _txt(n, 'VOUCHERNUMBER', v['voucher_number'])
            if v['reference_no']:
                _txt(n, 'REFERENCE', v['reference_no'])
            if v['narration']:
                _txt(n, 'NARRATION', v['narration'])
            for key, tag in INVOICE_FIELDS:
                if invoice_details.get(vid, {}).get(key):
                    _txt(n, tag, invoice_details[vid][key])
            if invoice_details.get(vid, {}).get('address'):
                al = ET.SubElement(n, 'ADDRESS.LIST')
                for line in invoice_details[vid]['address'].split('\n'):
                    _txt(al, 'ADDRESS', line)
            non_posting =v['voucher_type'] in NON_POSTING_KINDS
            _txt(n, 'ISCANCELLED', 'Yes' if v['is_cancelled'] else 'No')
            # Non-posting kinds are stored as optional internally; exported as what they are.
            _txt(n, 'ISOPTIONAL', 'Yes' if v['is_optional'] and not non_posting else 'No')
            used = set()
            for e in entries.get(vid, []):
                le = ET.SubElement(n, 'ALLLEDGERENTRIES.LIST')
                _txt(le, 'LEDGERNAME', e['ledger_name'])
                debit = e['entry_type'] == 'Dr'
                _txt(le, 'ISDEEMEDPOSITIVE', 'Yes' if debit else 'No')
                amount = _d(e['amount'])
                _txt(le, 'AMOUNT', f'{-amount if debit else amount:.2f}')
                if e['ledger_name'] not in used:       # bill allocations belong to the first entry of that ledger
                    used.add(e['ledger_name'])
                    for b in bills.get((vid, e['ledger_name']), []):
                        ba = ET.SubElement(le, 'BILLALLOCATIONS.LIST')
                        _txt(ba, 'NAME', b['ref_name'])
                        _txt(ba, 'BILLTYPE', b['ref_type'])
                        bamount = _d(b['amount'])
                        _txt(ba, 'AMOUNT', f'{-bamount if b["direction"] == "Dr" else bamount:.2f}')
            for i in inventory.get(vid, []):
                unit = units.get(i['stock_item_name'], 'Nos')
                ie = ET.SubElement(n, 'ALLINVENTORYENTRIES.LIST')
                _txt(ie, 'STOCKITEMNAME', i['stock_item_name'])
                _txt(ie, 'ISDEEMEDPOSITIVE', 'Yes' if i['direction'] == 'In' else 'No')
                qty = format(Decimal(repr(i['qty'])).normalize(), 'f')
                _txt(ie, 'RATE', f'{_d(i["rate"]):.2f}/{unit}')
                _txt(ie, 'AMOUNT', f'{_d(i["amount"]):.2f}')
                _txt(ie, 'ACTUALQTY', f' {qty} {unit}')
                _txt(ie, 'BILLEDQTY', f' {qty} {unit}')
                batch = ET.SubElement(ie, 'BATCHALLOCATIONS.LIST')
                _txt(batch, 'GODOWNNAME', i['godown_name'] or 'Main Location')
                if i['batch_number']:
                    _txt(batch, 'BATCHNAME', i['batch_number'])
                _txt(batch, 'AMOUNT', f'{_d(i["amount"]):.2f}')
                _txt(batch, 'ACTUALQTY', f' {qty} {unit}')
    buffer = io.StringIO()
    ET.ElementTree(env).write(buffer, encoding='unicode', xml_declaration=False)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + _escape_controls(buffer.getvalue())).encode('utf-8')


_CONTROLS = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\r]')


def _escape_controls(text):
    """XML parsers turn a raw CR into LF (breaking names that contain a line break, which Tally allows) and
    reject raw C0 controls, which Tally names can contain. Write both as numeric references, the way
    TallyPrime does; the importer restores them exactly."""
    return _CONTROLS.sub(lambda m: f'&#{ord(m[0])};', text)


# ---------------------------------------------------------------------------------------------------
# Round-trip proof
# ---------------------------------------------------------------------------------------------------
def snapshot(db):
    """Canonical, comparable content of a company (no ids): used to compare an original and a re-import."""
    conn = db.conn
    conn.row_factory = sqlite3.Row
    company_guid = str(uuid.uuid5(NAMESPACE, f'company:{_company(conn)["name"]}'))
    imported = {r['local_id']: r['guid'] for r in conn.execute(
        "SELECT local_id, guid FROM tally_import_record WHERE kind='voucher'")} \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='tally_import_record'").fetchone() else {}
    out = {'groups': {}, 'ledgers': {}, 'vouchers': {}}
    for g in conn.execute('SELECT * FROM account_group'):
        out['groups'][g['name']] = (g['parent'], int(g['is_revenue']), int(g['affects_gross']))
    for l in conn.execute('SELECT * FROM ledger'):
        out['ledgers'][l['name']] = (l['group_name'], str(_d(l['opening_balance'])), l['opening_balance_type'] if _d(l['opening_balance']) else '',
                                     l['gstin'] or '', int(l['maintain_bill_by_bill']), l['address'] or '',
                                     json.dumps(_json_dict(l['udf_data']), sort_keys=True))
    entries, bills, inventory = {}, {}, {}
    invoice_details = _invoice_details_by_voucher(conn)
    for e in conn.execute('SELECT * FROM voucher_entry ORDER BY id'):
        entries.setdefault(e['voucher_id'], []).append((e['ledger_name'], e['entry_type'], str(_d(e['amount']))))
    for b in conn.execute('SELECT * FROM bill_allocation ORDER BY id'):
        bills.setdefault(b['voucher_id'], []).append((b['ledger_name'], b['ref_type'], b['ref_name'], b['direction'], str(_d(b['amount']))))
    for i in conn.execute('SELECT * FROM voucher_inventory ORDER BY id'):
        inventory.setdefault(i['voucher_id'], []).append((i['stock_item_name'], i['godown_name'], i['batch_number'] or '',
                                                          format(Decimal(repr(i['qty'])).normalize(), 'f'), str(_d(i['rate'])), str(_d(i['amount'])), i['direction']))
    for v in conn.execute('SELECT * FROM voucher'):
        guid = voucher_guid(imported.get(v['id']), company_guid, v['id'])
        out['vouchers'][guid] = {
            'type': v['voucher_type'], 'date': v['date'], 'number': v['voucher_number'], 'narration': (v['narration'] or '').strip(),
            'reference': v['reference_no'] or '', 'cancelled': int(v['is_cancelled']), 'optional': int(v['is_optional']),
            'invoice': json.dumps(invoice_details.get(v['id'], {}), sort_keys=True),
            'entries': sorted(entries.get(v['id'], [])), 'bills': sorted(bills.get(v['id'], [])), 'inventory': sorted(inventory.get(v['id'], []))}
    return out


def compare_snapshots(a, b, limit=5):
    """Return {'ok': bool, 'differences': {...}}; keys that differ are listed (first ``limit`` examples)."""
    diffs = {}
    for part in ('groups', 'ledgers', 'vouchers'):
        only_a = sorted(set(a[part]) - set(b[part]))
        only_b = sorted(set(b[part]) - set(a[part]))
        changed = sorted(k for k in set(a[part]) & set(b[part]) if a[part][k] != b[part][k])
        if only_a or only_b or changed:
            diffs[part] = {'only_in_original': only_a[:limit], 'only_in_reimport': only_b[:limit], 'changed': changed[:limit],
                           'counts': [len(only_a), len(only_b), len(changed)]}
    return {'ok': not diffs, 'differences': diffs,
            'counts': {p: len(a[p]) for p in ('groups', 'ledgers', 'vouchers')}}


def round_trip(db, workdir):
    """Export ``db``, import the file into a fresh company under ``workdir``, compare. Returns the comparison."""
    import pathlib
    from engine.accounting_db import EnterpriseAccountingDB
    from engine.tally_xml_import import import_company
    workdir = pathlib.Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / 'export'
    source.mkdir(exist_ok=True)
    data = export_company_xml(db)
    (source / 'Master.xml').write_bytes(data)
    target = workdir / 'data'
    target.mkdir(exist_ok=True)
    report = import_company(target, [source / 'Master.xml'])
    fresh = EnterpriseAccountingDB(str(pathlib.Path(report['folder']) / 'company.db'), seed_demo=False)
    try:
        fresh.select_company(int(report['storage_key'].split(':')[1]))
        result = compare_snapshots(snapshot(db), snapshot(fresh))
    finally:
        fresh.close()
    result['export_bytes'] = len(data)
    result['sha256'] = hashlib.sha256(data).hexdigest()
    result['reimport_verification_ok'] = report['verification']['ok']
    return result
