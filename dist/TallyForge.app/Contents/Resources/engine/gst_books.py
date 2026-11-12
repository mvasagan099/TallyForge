"""Period/registration-scoped GST book reports using posted tax amounts.

These are review reports, not a GSTN upload format or an ITC eligibility decision.
"""
import calendar
import datetime
from decimal import Decimal, ROUND_HALF_UP
import json
import re

CENT = Decimal('0.01')


def dec(value):
    value = Decimal(str(value or 0))
    if not value.is_finite():
        raise ValueError('Non-finite value in GST books')
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def group_chain(name, groups):
    chain = []
    while name and name != 'Primary' and name not in chain:
        chain.append(name)
        name = groups.get(name)
    return chain


def snapshot(db, voucher_id):
    row = db.conn.execute('SELECT * FROM voucher WHERE id=? AND company_id=?',(voucher_id, db.active_company_id)).fetchone()
    if not row:
        raise ValueError('Voucher not found in the active company')
    voucher = dict(row)
    if voucher['voucher_type'] not in ('Sales','POS','Purchase'):
        return None
    company = db.get_company()
    registration = None
    if voucher.get('gst_registration_id'):
        record = db.conn.execute('SELECT * FROM gst_registration WHERE company_id=? AND id=?',(db.active_company_id,voucher['gst_registration_id'])).fetchone()
        if not record:
            raise ValueError('Voucher registration is missing')
        registration = dict(record)
    seller = registration or company
    groups = {g['name']:g['parent'] for g in db.get_groups()}
    entries = [dict(r) for r in db.conn.execute('SELECT ve.*,l.group_name,l.gstin,l.state_code FROM voucher_entry ve JOIN ledger l ON l.name=ve.ledger_name AND l.company_id=? WHERE ve.voucher_id=? ORDER BY ve.id',(db.active_company_id,voucher_id))]
    inward = voucher['voucher_type']=='Purchase'
    party_type = 'Cr' if inward else 'Dr'
    candidates = [e for e in entries if e['entry_type']==party_type and 'Duties & Taxes' not in group_chain(e['group_name'],groups)]
    preferred = 'Sundry Creditors' if inward else 'Sundry Debtors'
    preferred_candidates = [e for e in candidates if preferred in group_chain(e['group_name'],groups)]
    candidates = preferred_candidates or [e for e in candidates if any(g in group_chain(e['group_name'],groups) for g in ('Cash-in-Hand','Bank Accounts','Bank OD A/c'))] or candidates
    party = candidates[0] if candidates else {}
    notes = []
    if len(candidates)!=1:
        notes.append({'code':'party_ambiguous','message':'Identify a single supplier/customer ledger before relying on this invoice report.'})
    tax = {key: Decimal(0) for key in ('cgst','sgst','igst','cess')}
    for entry in entries:
        if 'Duties & Taxes' not in group_chain(entry['group_name'],groups):
            continue
        label = entry['ledger_name'].lower()
        components = [name for name in tax if name in label]
        if len(components)==1:
            sign = 1 if entry['entry_type']==('Dr' if inward else 'Cr') else -1
            tax[components[0]] += dec(entry['amount'])*sign
        else:
            notes.append({'code':'unclassified_tax_ledger','message':f"Review tax ledger {entry['ledger_name']}; its tax component is not explicit."})
    lines = []
    for record in db.conn.execute('SELECT vi.*,s.hsn_code,s.gst_rate FROM voucher_inventory vi JOIN stock_item s ON s.name=vi.stock_item_name AND s.company_id=? WHERE vi.voucher_id=? ORDER BY vi.id',(db.active_company_id,voucher_id)):
        item = dict(record)
        lines.append({'item':item['stock_item_name'],'hsn':item.get('hsn_code') or '',
                      'quantity':float(item['qty']),'taxable_value':str(dec(item['amount'])),
                      'gst_rate':str(dec(item['gst_rate']))})
    document_value = dec(party.get('amount', voucher['total_amount']))
    total_tax = sum(tax.values(),Decimal(0))
    taxable = sum((dec(line['taxable_value']) for line in lines),Decimal(0)) if lines else document_value-total_tax
    if not lines:
        notes.append({'code':'no_item_tax_breakup','message':'Taxable amount is inferred from the party total less posted GST; no item/HSN breakdown was recorded.'})
    if abs(taxable+total_tax-document_value)>CENT:
        notes.append({'code':'invoice_value_mismatch','message':'Item taxable value plus posted GST differs from the party amount; review discounts, charges and tax postings.'})
    if any(not re.fullmatch(r'\d{4,8}',line['hsn']) for line in lines):
        notes.append({'code':'missing_hsn','message':'One or more item HSN/SAC codes need review.'})
    party_gstin = (party.get('gstin') or '').strip().upper()
    seller_gstin = (seller.get('gstin') or '').strip().upper()
    if party_gstin and not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]',party_gstin):
        notes.append({'code':'invalid_party_gstin','message':'Party GSTIN has an invalid format.'})
    if not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]',seller_gstin):
        notes.append({'code':'invalid_registration_gstin','message':'The company registration GSTIN needs review.'})
    seller_state, party_state = seller.get('state_code') or '', party.get('state_code') or ''
    if seller_state and party_state:
        interstate = seller_state!=party_state
        if (interstate and (tax['cgst'] or tax['sgst'])) or (not interstate and tax['igst']):
            notes.append({'code':'tax_component_state_mismatch','message':'Posted GST components differ from the recorded company/party states; verify place of supply.'})
        if lines:
            calculated = sum((dec(dec(line['taxable_value'])*dec(line['gst_rate'])/100) for line in lines),Decimal(0))
            if abs(calculated-(tax['cgst']+tax['sgst']+tax['igst']))>CENT:
                notes.append({'code':'rate_tax_mismatch','message':'GST calculated from recorded item rates differs from the posted tax ledgers.'})
    return {'registration_id':voucher.get('gst_registration_id'), 'registration_gstin':seller_gstin,
            'registration_state':seller_state,'direction':'Inward' if inward else 'Outward',
            'party':party.get('ledger_name',''),'party_gstin':party_gstin,'party_state':party_state,
            'party_class':'Registered' if party_gstin else 'Unregistered',
            'document_value':str(document_value),'taxable_value':str(taxable),
            'tax':{k:str(v) for k,v in tax.items()},'lines':lines,'warnings':notes}


def capture(db, voucher_id):
    value = snapshot(db,voucher_id)
    if value is not None:
        db.conn.execute('INSERT OR REPLACE INTO voucher_gst_snapshot(voucher_id,payload) VALUES(?,?)',(voucher_id,json.dumps(value)))


def book_report(db, period, registration='all'):
    db.require_feature('gst')
    if not isinstance(period,str) or not re.fullmatch(r'\d{4}-\d{2}',period):
        raise ValueError('Period must be YYYY-MM')
    year, month = map(int,period.split('-'))
    first = datetime.date(year,month,1)
    last = datetime.date(year,month,calendar.monthrange(year,month)[1])
    params = [db.active_company_id,first.isoformat(),last.isoformat()]
    scope = ''
    registration_name = 'All registrations'
    if registration=='main':
        scope = ' AND v.gst_registration_id IS NULL'
        registration_name = 'Main registration'
    elif registration!='all':
        try:
            rid = int(registration)
        except (ValueError,TypeError):
            raise ValueError('Choose an existing registration, main or all')
        row = db.conn.execute('SELECT name FROM gst_registration WHERE id=? AND company_id=?',(rid,db.active_company_id)).fetchone()
        if not row:
            raise ValueError('Registration not found in this company')
        registration_name = row['name']
        scope = ' AND v.gst_registration_id=?'
        params.append(rid)
    base_currency = db.get_company().get('currency_symbol', '₹')
    vouchers = db.conn.execute('SELECT v.* FROM voucher v WHERE v.company_id=? AND v.date>=? AND v.date<=? AND v.is_cancelled=0 AND v.is_optional=0'+scope+' ORDER BY v.date,v.id',params).fetchall()
    totals = {direction:{k:Decimal(0) for k in ('document_value','taxable_value','cgst','sgst','igst','cess')} for direction in ('Inward','Outward')}
    invoices, excluded, warnings = [], [], []
    for row in vouchers:
        voucher = dict(row)
        if voucher['voucher_type'] not in ('Sales','POS','Purchase'):
            if voucher['voucher_type'] in ('Credit Note','Debit Note'):
                excluded.append({'voucher_id':voucher['id'],'number':voucher['voucher_number'],'reason':'Credit/debit notes need an explicit original-supply classification and are excluded from these totals.'})
            continue
        if voucher.get('currency_symbol') != base_currency:
            excluded.append({'voucher_id':voucher['id'],'number':voucher['voucher_number'],'reason':'Foreign-currency document requires a validated base-currency tax basis; excluded from these totals.'})
            continue
        stored = db.conn.execute('SELECT payload FROM voucher_gst_snapshot WHERE voucher_id=?',(voucher['id'],)).fetchone()
        data = json.loads(stored['payload']) if stored else snapshot(db,voucher['id'])
        if not stored:
            data['warnings'].append({'code':'historical_master_values','message':'This older voucher has no posting snapshot; party/rate details were read from current masters.'})
        item = {**data,'voucher_id':voucher['id'],'number':voucher['voucher_number'],'date':voucher['date'],'voucher_type':voucher['voucher_type'],'snapshot_at_posting':bool(stored)}
        invoices.append(item)
        target = totals[item['direction']]
        for key in ('document_value','taxable_value'):
            target[key]+=dec(item[key])
        for key, value in item['tax'].items():
            target[key]+=dec(value)
        warnings.extend([{'voucher_id':voucher['id'],'number':voucher['voucher_number'],**warning} for warning in item['warnings']])
    return {'period':period,'from':first.isoformat(),'to':last.isoformat(),'registration':str(registration),
            'registration_name':registration_name,'invoices':invoices,
            'totals':{direction:{k:float(v) for k,v in values.items()} for direction,values in totals.items()},
            'posted_tax_difference':{k:float(totals['Outward'][k]-totals['Inward'][k]) for k in ('cgst','sgst','igst','cess')},
            'warnings':warnings,'excluded_documents':excluded,'portal_submitted':False,
            'note':'Book review only: posted tax difference is not tax payable or eligible ITC. Returns, adjustments, eligibility, place-of-supply exceptions and portal reconciliation require review.'}
