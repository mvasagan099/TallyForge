"""Import a company from a running TallyPrime through the read-only bridge, or replay a capture.

Safety rules (learned the hard way):
* Requests go through engine.tally_bridge, which refuses anything but Export requests and
  refuses wildcard fetches. A wildcard Voucher fetch overloaded a live TallyPrime and crashed it.
* Requests are strictly sequential. The first timeout/failure aborts the whole run: no retry,
  because TallyPrime may still be busy with the previous request.
* Voucher history is requested one calendar month at a time with explicitly named methods.

Offline: ``capture_dir`` saves every raw response plus a manifest; ``replay_dir`` re-runs the
same import later from that capture without any network access.
"""
import datetime
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from engine import tally_bridge
from engine.tally_xml_import import (CENT, TallyImportError, _amount, _sanitize, _decode, bundle_from_raw,
                                     import_company_bundle, reference_figures)

LIVE_MAX_BYTES = 160 * 1024 * 1024
LIVE_TIMEOUT = 180
STOCK_METHODS = ('Name', 'BaseUnits', 'CostingMethod', 'ValuationMethod', 'OpeningBalance', 'OpeningValue')

VOUCHER_METHODS = ('GUID', 'MasterID', 'AlterID', 'Date', 'VoucherNumber', 'VoucherTypeName', 'PartyLedgerName',
                   # Invoice header details (names verified accepted by a live TallyPrime):
                   'PartyMailingName', 'BasicBuyerName', 'PartyGSTIN', 'StateName', 'PlaceOfSupply', 'CmpGSTIN', 'CmpGSTState',
                   'ConsigneeGSTIN', 'ConsigneeMailingName', 'ConsigneeStateName', 'BasicOrderRef', 'BasicShippedBy',
                   'BasicFinalDestination',
                   'Narration', 'Reference', 'IsCancelled', 'IsOptional', 'IsDeleted',
                   'AllLedgerEntries.LedgerName', 'AllLedgerEntries.Amount', 'AllLedgerEntries.IsDeemedPositive',
                   'AllInventoryEntries.StockItemName', 'AllInventoryEntries.ActualQty',
                   'AllInventoryEntries.Rate', 'AllInventoryEntries.Amount',
                   # Bill references: verified on one real month (30/30 vouchers identical to the file export).
                   'AllLedgerEntries.BillAllocations.Name', 'AllLedgerEntries.BillAllocations.BillType',
                   'AllLedgerEntries.BillAllocations.Amount')
CENSUS_METHODS = ('GUID', 'Date', 'VoucherTypeName', 'IsCancelled', 'IsOptional', 'IsDeleted', 'AlterID')
# MailingName / Address / CountryName / LedStateName: ledger details the masters report leaves out (verified live).
LEDGER_METHODS = ('Name', 'Parent', 'OpeningBalance', 'ClosingBalance', 'GUID', 'MailingName', 'Address', 'CountryName', 'LedStateName')
COMPANY_METHODS = ('Name', 'GUID', 'StartingFrom', 'BooksFrom', 'EndingAt', 'LastVoucherDate')


# ---------------------------------------------------------------------------
# Response parsers (reference figures from TallyPrime itself)
# ---------------------------------------------------------------------------
def _tree(raw):
    text, fix = _sanitize(_decode(raw))
    try:
        return ET.fromstring(text), fix
    except ET.ParseError as exc:
        raise TallyImportError(f'Malformed Tally response: {exc}') from exc


def parse_company_info(raw):
    root, fix = _tree(raw)
    for node in root.iter('COMPANY'):
        name = fix(node.get('NAME')).strip()
        if not name:
            continue
        get = lambda tag: fix(node.findtext(tag)).strip()
        return {'name': name, 'guid': get('GUID'), 'starting_from': get('STARTINGFROM'),
                'books_from': get('BOOKSFROM') or get('STARTINGFROM'), 'ending_at': get('ENDINGAT'),
                'last_voucher_date': get('LASTVOUCHERDATE')}
    raise TallyImportError('TallyPrime did not report the requested company; load it in TallyPrime first')


def parse_census(raw):
    """Voucher headers only: returns list of dicts (guid, date, type, flags)."""
    root, fix = _tree(raw)
    rows = []
    for v in root.iter('VOUCHER'):
        guid = fix(v.findtext('GUID')).strip()
        if not guid:
            continue
        rows.append({'guid': guid, 'date': fix(v.findtext('DATE')).strip(), 'type': fix(v.findtext('VOUCHERTYPENAME')).strip(),
                     'cancelled': fix(v.findtext('ISCANCELLED')).strip() == 'Yes',
                     'optional': fix(v.findtext('ISOPTIONAL')).strip() == 'Yes',
                     'deleted': fix(v.findtext('ISDELETED')).strip() == 'Yes'})
    return rows


def parse_ledger_balances(raw):
    """Ledger collection -> {name: {'opening','closing','parent'}} with Tally's sign (credit +, debit -)."""
    root, fix = _tree(raw)
    out = {}
    for node in root.iter('LEDGER'):
        name = fix(node.get('NAME')).strip()
        if not name:
            continue
        get = lambda tag: fix(node.findtext(tag)).strip()
        out[name] = {'mailing': get('MAILINGNAME'), 'country': get('COUNTRYNAME'), 'state': get('LEDSTATENAME'),
                     'address': '\n'.join(a for a in (fix(x.text).strip() for x in node.findall('ADDRESS.LIST/ADDRESS')) if a),
                     'parent': get('PARENT'),
                     'opening': _amount(get('OPENINGBALANCE'), f'ledger {name!r} opening') or Decimal('0'),
                     'closing': _amount(get('CLOSINGBALANCE'), f'ledger {name!r} closing') or Decimal('0')}
    return out


def parse_trial_balance(raw):
    """Trial Balance report -> {display name: net debit (Decimal)}. An empty envelope means no balances."""
    root, fix = _tree(raw)
    names = [fix(n.findtext('DSPDISPNAME')).strip() for n in root.findall('DSPACCNAME')]
    infos = root.findall('DSPACCINFO')
    if len(names) != len(infos):
        raise TallyImportError('Trial Balance response has mismatched name/amount rows')
    rows = {}
    for name, info in zip(names, infos):
        dr = _amount(fix(info.findtext('DSPCLDRAMT/DSPCLDRAMTA')), f'TB {name!r} debit') or Decimal('0')
        cr = _amount(fix(info.findtext('DSPCLCRAMT/DSPCLCRAMTA')), f'TB {name!r} credit') or Decimal('0')
        if name in rows:
            raise TallyImportError(f'Trial Balance lists {name!r} twice')
        if dr > 0 or cr < 0:
            raise TallyImportError(f'TB {name!r}: unexpected sign (debit column {dr}, credit column {cr}); TallyPrime shows debits as negatives')
        rows[name] = -dr - cr  # debit amounts arrive as negatives in the debit column
    return rows


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def month_ranges(first, last):
    first, last = datetime.date.fromisoformat(first), datetime.date.fromisoformat(last)
    cur = first.replace(day=1)
    while cur <= last:
        nxt = (cur.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        yield max(cur, first).isoformat(), min(nxt - datetime.timedelta(days=1), last).isoformat()
        cur = nxt


def _d(value):  # Tally YYYYMMDD -> ISO
    if not re.fullmatch(r'\d{8}', value or ''):
        raise TallyImportError(f'Unexpected Tally date {value!r}')
    return f'{value[:4]}-{value[4:6]}-{value[6:]}'


class Source:
    """Where raw responses come from: a live server, or a previously captured directory."""

    def __init__(self, endpoint=None, transport=None, capture_dir=None, replay_dir=None):
        if bool(replay_dir) == bool(endpoint or transport):
            raise TallyImportError('Choose exactly one of a live endpoint or a replay directory')
        self.endpoint, self.transport = endpoint, transport
        self.capture_dir = Path(capture_dir) if capture_dir else None
        self.replay_dir = Path(replay_dir) if replay_dir else None
        self.log = []
        self.manifest = {}
        if self.replay_dir:
            self.manifest = json.loads((self.replay_dir / 'manifest.json').read_text(encoding='utf-8'))
        if self.capture_dir:
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            if any(self.capture_dir.iterdir()):
                raise TallyImportError('Capture directory must be empty (captures are never overwritten)')

    @property
    def live(self):
        return self.replay_dir is None

    def get(self, label, body_factory, *, large=False):
        if self.replay_dir:
            entry = self.manifest.get(label)
            if not entry:
                raise TallyImportError(f'Capture has no response for {label!r}')
            raw = (self.replay_dir / entry['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise TallyImportError(f'Capture file for {label!r} was modified')
            return raw
        body = body_factory()
        tally_bridge.assert_read_only(body)  # also protects injected transports
        send = self.transport or tally_bridge.send_xml
        try:
            raw = send(self.endpoint, body, max_bytes=LIVE_MAX_BYTES, timeout=LIVE_TIMEOUT) if self.transport is None \
                else send(self.endpoint, body)
        except Exception as exc:
            raise TallyImportError(f'Live request {label!r} failed ({exc}). Stopping without retry: TallyPrime may still be '
                                   'processing it. Check TallyPrime before running again.') from exc
        self.log.append({'label': label, 'bytes': len(raw)})
        if self.capture_dir:
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', label) + '.xml'
            (self.capture_dir / name).write_bytes(raw)
            self.manifest[label] = {'file': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
            (self.capture_dir / 'manifest.json').write_text(json.dumps(self.manifest, indent=1), encoding='utf-8')
        return raw


def parse_stock_items(raw):
    """Stock item masters from TallyPrime's own collection: unit, costing method, opening quantity and value.

    Tally writes opening stock value as a debit (negative); the app stores it positive."""
    out = {}
    root, fix = _tree(raw)
    clean = lambda v: fix(v).strip()
    for node in root.iter('STOCKITEM'):
        name = clean(node.get('NAME'))
        if not name:
            continue
        qty_text = (node.findtext('OPENINGBALANCE') or '').strip()
        m = re.match(r'-?\d+(?:\.\d+)?', qty_text)
        value_text = (node.findtext('OPENINGVALUE') or '').strip()
        value = abs(Decimal(value_text)) if value_text else Decimal('0')
        qty = abs(Decimal(m.group())) if m else Decimal('0')
        out[name] = {'name': name, 'unit': clean(node.findtext('BASEUNITS')) or None, 'opening_qty': qty,
                     'opening_value': value, 'costing': clean(node.findtext('COSTINGMETHOD')) or None,
                     'valuation': clean(node.findtext('VALUATIONMETHOD')) or None}
    return out


def fetch_all(source, company, as_at=None):
    """Fetch everything needed for a full import and reconciliation. Returns a dict of raw responses."""
    info = parse_company_info(source.get('company', lambda: tally_bridge.collection_request(
        'TFCompany', 'Company', COMPANY_METHODS, company)))
    if info['name'] != company:
        raise TallyImportError(f'TallyPrime answered for {info["name"]!r}, not {company!r}')
    books_from = _d(info['books_from'])
    end = as_at or _d(info['last_voucher_date'] or info['ending_at'])
    out = {'info': info, 'books_from': books_from, 'as_at': end, 'raw': {}}
    raw = out['raw']
    raw['masters'] = source.get('masters', lambda: tally_bridge.report_request('List of Accounts', company, {'ACCOUNTTYPE': 'All Masters'}), large=True)
    raw['ledgers'] = source.get('ledgers', lambda: tally_bridge.collection_request('TFLedgers', 'Ledger', LEDGER_METHODS, company, books_from, end))
    raw['census'] = source.get('census', lambda: tally_bridge.collection_request('TFCensus', 'Voucher', CENSUS_METHODS, company, books_from, end), large=True)
    raw['tb_open'] = source.get('tb_open', lambda: tally_bridge.report_request('Trial Balance', company, {
        'SVFROMDATE': books_from.replace('-', ''), 'SVTODATE': books_from.replace('-', ''), 'EXPLODEFLAG': 'Yes'}))
    raw['tb_close'] = source.get('tb_close', lambda: tally_bridge.report_request('Trial Balance', company, {
        'SVFROMDATE': books_from.replace('-', ''), 'SVTODATE': end.replace('-', ''), 'EXPLODEFLAG': 'Yes'}))
    try:
        raw['stock_items'] = source.get('stock_items', lambda: tally_bridge.collection_request(
            # Opening stock is relative to the period asked for: without a period it is the opening of the
            # current financial year (a carry-forward), which the vouchers already include.
            'TFStockItems', 'StockItem', STOCK_METHODS, company, books_from, books_from))
    except TallyImportError:
        if source.live:
            raise            # an older capture simply has no stock masters; a live read must have them
    census = parse_census(raw['census'])
    months = sorted({c['date'][:6] for c in census if c['date']})
    out['months'] = []
    for month in months:
        first = _d(month + '01')
        last = (datetime.date.fromisoformat(first).replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)
        label = f'vouchers_{month}'
        raw[label] = source.get(label, lambda f=first, l=last.isoformat(): tally_bridge.collection_request(
            'TFVouchers', 'Voucher', VOUCHER_METHODS, company, f, l))
        out['months'].append(label)
    return out


# ---------------------------------------------------------------------------
# Bundle building and reconciliation against Tally's reports
# ---------------------------------------------------------------------------
def build_bundle(fetched):
    raw = fetched['raw']
    items = [('masters', 'tally-live', raw['masters'], hashlib.sha256(raw['masters']).hexdigest())]
    items += [(label, 'tally-live', raw[label], hashlib.sha256(raw[label]).hexdigest()) for label in fetched['months']]
    bundle = bundle_from_raw(items)
    bundle['stock_items'] = parse_stock_items(raw['stock_items']) if raw.get('stock_items') else {}
    balances = parse_ledger_balances(raw['ledgers'])
    census = parse_census(raw['census'])
    # Opening balances: TallyPrime's own ledger collection is authoritative; masters omit them.
    filled = unknown = 0
    for led in bundle['ledgers'].values():
        ref = balances.get(led['name'])
        if ref is not None:
            for key in ('mailing', 'country', 'state', 'address'):
                if not led.get(key) and ref.get(key):
                    led[key] = ref[key]
        if led['opening'] is None and ref is not None:
            led['opening'], led['opening_raw'] = ref['opening'], f'{ref["opening"]}'
            filled += 1
        elif led['opening'] is None:
            unknown += 1
    bundle['live'] = {'opening_filled_from_ledger_collection': filled, 'opening_still_unknown': unknown,
                      'census': Counter(), 'books_from': fetched['books_from'], 'as_at': fetched['as_at']}
    live_ids = {c['guid'] for c in census if not c['deleted']}
    fetched_ids = set(bundle['vouchers'])
    bundle['live']['census_vouchers'] = len(live_ids)
    bundle['live']['fetched_vouchers'] = len(fetched_ids)
    bundle['live']['missing_from_fetch'] = sorted(live_ids - fetched_ids)
    bundle['live']['unexpected_in_fetch'] = sorted(fetched_ids - live_ids)
    bundle['live']['census'] = dict(Counter(c['type'] + ('/cancelled' if c['cancelled'] else '') +
                                            ('/optional' if c['optional'] else '') for c in census if not c['deleted']))
    return bundle, balances, census


def _tbkey(name):
    """Tally shows a line break inside a ledger name as '- ' in reports (observed on real data:
    'management <CRLF>consultancy' is listed as 'management - consultancy'); XML text also
    collapses CR LF. Compare on that form."""
    return name.replace(chr(13) + chr(10), '- ').replace(chr(13), '- ').replace(chr(10), '- ')


def _keyed(mapping, what):
    """Re-key a report's rows by comparable name. Two different rows that collapse to one name are refused."""
    out = {}
    for key, value in mapping.items():
        k = _tbkey(key)
        if k in out:
            raise TallyImportError(f'{what}: two names collide after line-break normalisation ({k!r}); cannot compare safely')
        out[k] = value
    return out


def _summed(mapping):
    """Numeric rows by comparable name. Names that collapse together are summed (the report cannot tell
    them apart); returns (rows, merged display names)."""
    out, merged = defaultdict(Decimal), set()
    seen = set()
    for key, value in mapping.items():
        k = _tbkey(key)
        if k in seen:
            merged.add(k)
        seen.add(k)
        out[k] += value
    return dict(out), sorted(merged)


PL_NAME = 'Profit & Loss A/c'


def verify_against_tally(fetched, balances, census):
    """Return an extra_verify callback comparing our books with TallyPrime's own reports, paisa for paisa.

    Tally's Trial Balance lists group totals for most groups and individual ledgers only for some, so
    group nets are the main comparison; ledger rows are compared where listed, and TallyPrime's ledger
    collection (every ledger) is a second, independent reference. Anything Tally reports that our books
    cannot explain (e.g. a Profit & Loss A/c balancing row) is reported as a failure, never absorbed."""
    tb_close = _keyed(parse_trial_balance(fetched['raw']['tb_close']), 'closing trial balance')
    tb_open = _keyed(parse_trial_balance(fetched['raw']['tb_open']), 'opening trial balance')
    open_empty = not tb_open

    def run(db, bundle):
        checks = []

        def check(name, ok, expected, actual):
            checks.append({'check': name, 'ok': bool(ok), 'expected': str(expected), 'actual': str(actual)})

        live = bundle['live']
        check('every voucher in TallyPrime census was fetched', not live['missing_from_fetch'], 0, len(live['missing_from_fetch']))
        check('no fetched voucher absent from census', not live['unexpected_in_fetch'], 0, len(live['unexpected_in_fetch']))
        conn = db.conn
        opening = defaultdict(Decimal)
        net = defaultdict(Decimal)
        for r in conn.execute('SELECT name, opening_balance, opening_balance_type FROM ledger'):
            ob = Decimal(repr(r['opening_balance'])).quantize(CENT)
            opening[_tbkey(r['name'])] += ob if r['opening_balance_type'] == 'Dr' else -ob
        net.update(opening)
        for r in conn.execute("""SELECT e.ledger_name, e.entry_type, e.amount FROM voucher_entry e JOIN voucher v ON v.id=e.voucher_id
                                 WHERE v.is_cancelled=0 AND v.is_optional=0 AND v.date<=?""", (fetched['as_at'],)):
            amt = Decimal(repr(r['amount'])).quantize(CENT)
            net[_tbkey(r['ledger_name'])] += amt if r['entry_type'] == 'Dr' else -amt
        ledger_group, ambiguous = {}, []
        for l in bundle['ledgers'].values():
            if not l['parent']:
                continue
            k = _tbkey(l['name'])
            if k in ledger_group and ledger_group[k] != l['parent']:
                raise TallyImportError(f'Ledgers {k!r} collapse to one report name but sit in different groups; cannot compare safely')
            if k in ledger_group:
                ambiguous.append(k)
            ledger_group[k] = l['parent']
        group_parent = {g['name']: g['parent'] for g in bundle['groups'].values()}
        group_names = set(group_parent)

        def beneath(group, ledger):
            cur = ledger_group.get(ledger)
            while cur:
                if cur == group:
                    return True
                cur = group_parent.get(cur)
            return False

        def group_net(source, group):
            return sum((v for k, v in source.items() if beneath(group, k)), Decimal('0'))

        def unexplained(tb):
            """Nonzero TB rows that are neither ledgers, groups nor the Profit & Loss row."""
            return sorted(k for k, v in tb.items() if v != 0 and k not in group_names and k not in ledger_group and k != PL_NAME)

        # 1. Opening trial balance at the books-beginning date.
        label = f'opening trial balance at {live["books_from"]} equals TallyPrime' + (' (empty report = all zero)' if open_empty else '')
        problems = [g for g in sorted(group_names | set(ledger_group) | set(tb_open))
                    if g != PL_NAME and (group_net(opening, g) if g in group_names else opening.get(g, Decimal('0')))
                    != tb_open.get(g, Decimal('0'))]
        check(label, not problems and not unexplained(tb_open), 0, f'{len(problems)} differ: {problems[:5]}')
        # 2. Closing trial balance: ledger rows TallyPrime lists individually.
        listed = {k: v for k, v in tb_close.items() if k in ledger_group}
        diffs = sorted(k for k in listed if net.get(k, Decimal('0')) != listed[k])
        check('closing trial balance: every ledger row listed by TallyPrime matches to the paisa', not diffs, len(listed),
              f'{len(diffs)} differ: {[(k, str(net.get(k, 0)), str(listed[k])) for k in diffs[:5]]}')
        # 3. Closing trial balance: every group row (this is the bulk of the report).
        gdiffs = [(g, str(group_net(net, g)), str(tb_close[g])) for g in sorted(group_names & set(tb_close))
                  if group_net(net, g) != tb_close[g]]
        check('closing trial balance: every group total matches TallyPrime', not gdiffs, len(group_names & set(tb_close)), gdiffs[:5])
        # 4. Things TallyPrime reports that our books cannot explain.
        check('no unexplained nonzero rows in TallyPrime closing trial balance', not unexplained(tb_close), 0, unexplained(tb_close)[:5])
        ours_total = sum(net.values(), Decimal('0'))
        check('our books balance (total debit = total credit)', ours_total == 0, 0, ours_total)
        top = [g for g in group_names if not group_parent.get(g) or group_parent[g] == 'Primary']
        tally_top = sum((tb_close.get(g, Decimal('0')) for g in top), Decimal('0'))
        tally_pl = tb_close.get(PL_NAME, Decimal('0'))
        check('TallyPrime trial balance balances including its Profit & Loss A/c row', tally_top + tally_pl == 0, 0, tally_top + tally_pl)
        our_pl = net.get(PL_NAME, Decimal('0'))
        check(f'{PL_NAME} row: TallyPrime net debit {tally_pl} equals the balance our vouchers and openings give that ledger',
              tally_pl == our_pl, our_pl, tally_pl)
        # 5. Second independent reference: TallyPrime's ledger collection (every ledger; credit +).
        ref_net, merged_ref = _summed({n: -b['closing'] for n, b in balances.items() if b['parent'] and n != PL_NAME})
        ref_net = {k: v for k, v in ref_net.items() if v != 0}
        ours = {k: v for k, v in net.items() if v != 0 and k != PL_NAME}
        cdiffs = sorted(k for k in set(ref_net) | set(ours) if ours.get(k, Decimal('0')) != ref_net.get(k, Decimal('0')))
        note = f'; compared as one row (report cannot tell them apart): {sorted(set(ambiguous) | set(merged_ref))}' if (ambiguous or merged_ref) else ''
        check('ledger-collection closing balance matches for every ledger', not cdiffs, len(ref_net), f'{len(cdiffs)} differ: {cdiffs[:5]}{note}')
        return {'checks': checks, 'reference': {'books_from': live['books_from'], 'as_at': fetched['as_at'],
                                                'tally_top_level_net_debit': str(tally_top), 'tally_pl_row_net_debit': str(tally_pl),
                                                'voucher_census': live['census']}}
    return run


def import_from_tally(data_root, company, *, endpoint=None, transport=None, capture_dir=None, replay_dir=None, as_at=None):
    """Fetch (or replay), import and reconcile. Returns the same report shape as import_company."""
    source = Source(endpoint, transport, capture_dir, replay_dir)
    fetched = fetch_all(source, company, as_at)
    bundle, balances, census = build_bundle(fetched)
    report = import_company_bundle(data_root, bundle, extra_verify=verify_against_tally(fetched, balances, census))
    report['mode'] = 'live' if source.live else 'replay'
    report['requests'] = source.log
    report['live'] = {k: v for k, v in bundle['live'].items() if k not in ('missing_from_fetch', 'unexpected_in_fetch')}
    return report
