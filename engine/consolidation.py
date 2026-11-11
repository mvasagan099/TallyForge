"""Read-only company consolidation with explicit, balanced elimination adjustments."""
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import uuid

CENT = Decimal('0.01')
ASSETS = {'Current Assets', 'Fixed Assets', 'Investments'}
LIABILITIES = {'Current Liabilities', 'Loans (Liability)'}
EXPENSES = {'Purchase Accounts', 'Direct Expenses', 'Indirect Expenses'}
INCOME = {'Sales Accounts', 'Direct Incomes', 'Indirect Incomes'}


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number != number.quantize(CENT):
            raise ValueError('Amounts must be finite with at most two decimal places')
        return number.quantize(CENT)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid monetary amount')


def root_group(name, groups):
    seen = set()
    while name in groups and groups[name] not in ('Primary', '', None):
        if name in seen:
            raise ValueError('Cyclic account group hierarchy')
        seen.add(name)
        name = groups[name]
    return name


def allowed_company(account, cid):
    access = account.get('company_access', 'ALL')
    if access == 'ALL':
        return True
    try:
        values = json.loads(access) if access.startswith('[') else access.split(',')
        return isinstance(values, list) and str(cid) in {str(value) for value in values}
    except (ValueError, TypeError, AttributeError):
        return False


def authorize_member(member, key, authorizations, contexts):
    if not member.get_company().get('security_enabled'):
        return
    reference = authorizations.get(key, {})
    engine = contexts.get(reference.get('context'))
    if engine is None or Path(engine.db_path).resolve() != Path(member.db_path).resolve() or engine.active_company_id != member.active_company_id:
        raise PermissionError(f'Sign in to protected company {key} before consolidation')
    account = engine.validate_session(reference.get('token', ''))
    if not account or account.get('role') not in ('Admin', 'Auditor') or not allowed_company(account, member.active_company_id):
        raise PermissionError(f'Financial reporting permission is required for {key}')


def consolidate(store, keys, eliminations=None, authorizations=None, contexts=None):
    if not isinstance(keys, list) or len(keys) < 2 or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('Select at least two distinct companies')
    if len(keys) > 50 or not isinstance(eliminations or [], list) or len(eliminations or []) > 500:
        raise ValueError('A group supports at most 50 companies and 500 elimination entries')
    totals, members, books, rows = {}, [], {}, {}
    currency = period = None
    authorizations, contexts = authorizations or {}, contexts or {}
    for key in keys:
        member = store.open(key)
        try:
            authorize_member(member, key, authorizations, contexts)
            # Legacy reports still have unscoped queries. Never silently consolidate their mixed books.
            if member.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0] != 1:
                raise ValueError('Split legacy shared-company books before file-based consolidation')
            company = member.get_company()
            scope = (company.get('financial_year_from'), company.get('financial_year_to'))
            if currency is not None and company.get('currency_symbol') != currency:
                raise ValueError('Consolidation requires matching base currencies')
            if period is not None and scope != period:
                raise ValueError('Consolidation requires matching financial years')
            currency, period = company.get('currency_symbol'), scope
            members.append({'storage_key': key, 'name': company['name']})
            groups = {r['name']: r['parent'] for r in member.get_groups()}
            for name, value in member.get_financial_summary().items():
                if name in ('sales_total','purchase_total','closing_stock','gross_profit','net_profit','assets_total','liab_total'):
                    totals[name] = totals.get(name, Decimal(0)) + money(value)
            for row in member.get_trial_balance():
                identity = (key, row['name'])
                signed = money(row.get('dr_amount', 0)) - money(row.get('cr_amount', 0))
                books[identity] = {'name': row['name'], 'group_name': row['group_name'], 'root': root_group(row['group_name'], groups), 'balance': signed}
                # Preserve equal names in different accounting groups.
                aggregate = (row['group_name'], row['name'])
                rows[aggregate] = rows.get(aggregate, Decimal(0)) + signed
        finally:
            member.close()
    original = dict(totals)
    adjustments, journal = [], []
    balance_amount = transaction_amount = Decimal(0)
    for index, entry in enumerate(eliminations or [], 1):
        if not isinstance(entry, dict):
            raise ValueError('Each elimination must be an object')
        debit_id = (entry.get('debit_company'), entry.get('debit_ledger'))
        credit_id = (entry.get('credit_company'), entry.get('credit_ledger'))
        if debit_id[0] == credit_id[0] or debit_id not in books or credit_id not in books:
            raise ValueError(f'Elimination {index}: choose existing ledgers from two group members')
        amount = money(entry.get('amount'))
        if amount <= 0:
            raise ValueError('Elimination amount must be positive')
        debit, credit = books[debit_id], books[credit_id]
        kind = entry.get('kind', 'Balance')
        if kind == 'Balance':
            if debit['root'] not in ASSETS or credit['root'] not in LIABILITIES:
                raise ValueError('Balance elimination requires a receivable/asset debit and payable/liability credit')
            balance_amount += amount
            totals['assets_total'] -= amount
            totals['liab_total'] -= amount
        elif kind == 'IncomeExpense':
            if debit['root'] not in EXPENSES or credit['root'] not in INCOME:
                raise ValueError('Income/expense elimination requires an expense debit and income credit')
            transaction_amount += amount
            if credit['root'] == 'Sales Accounts':
                totals['sales_total'] -= amount
            if debit['root'] == 'Purchase Accounts':
                totals['purchase_total'] -= amount
            if credit['root'] in ('Sales Accounts', 'Direct Incomes'):
                totals['gross_profit'] -= amount
            if debit['root'] in ('Purchase Accounts', 'Direct Expenses'):
                totals['gross_profit'] += amount
            # The equal income and expense cancellation leaves group net profit unchanged.
        else:
            raise ValueError('Elimination kind must be Balance or IncomeExpense')
        if debit['balance'] < amount or -credit['balance'] < amount:
            raise ValueError(f'Elimination {index} exceeds remaining reciprocal ledger balances')
        if not str(entry.get('reference', '')).strip():
            raise ValueError('An elimination reference is required for review')
        debit['balance'] -= amount
        credit['balance'] += amount
        rows[(debit['group_name'], debit['name'])] -= amount
        rows[(credit['group_name'], credit['name'])] += amount
        adjustments.append({**{k: entry[k] for k in ('kind','debit_company','debit_ledger','credit_company','credit_ledger','reference') if k in entry}, 'amount': float(amount)})
        journal.extend([
            {'company': credit_id[0], 'ledger': credit_id[1], 'entry_type': 'Dr', 'amount': float(amount), 'reference': entry['reference']},
            {'company': debit_id[0], 'ledger': debit_id[1], 'entry_type': 'Cr', 'amount': float(amount), 'reference': entry['reference']}])
    trial = [{'name': name, 'group_name': group, 'debit': float(max(value, Decimal(0))), 'credit': float(max(-value, Decimal(0)))} for (group,name),value in sorted(rows.items())]
    dr = sum((money(r['debit']) for r in trial), Decimal(0))
    cr = sum((money(r['credit']) for r in trial), Decimal(0))
    return {'members': members, 'currency': currency, 'financial_year': {'from':period[0], 'to':period[1]},
            'before_eliminations': {k:float(v) for k,v in original.items()}, 'totals': {k:float(v) for k,v in totals.items()},
            'trial_balance':trial, 'trial_totals': {'debit':float(dr),'credit':float(cr),'difference':float(dr-cr)},
            'eliminations': adjustments, 'elimination_journal': journal,
            'eliminated_balances': float(balance_amount), 'eliminated_income_expense': float(transaction_amount),
            'intercompany_eliminations_applied': bool(adjustments), 'source_books_modified': False,
            'note': 'All retained ledger balances, with no date filter. Reporting adjustments only. Investment/equity elimination, unrealised inventory profit, minority interests and currency translation are not included.'}


def save_group(db, store, data):
    keys = data.get('storage_keys')
    if not isinstance(keys, list) or len(keys)<2 or len(keys)>50 or not all(isinstance(k,str) for k in keys) or len(set(keys))!=len(keys):
        raise ValueError('Choose 2–50 distinct company members')
    if not isinstance(data.get('eliminations', []), list):
        raise ValueError('Eliminations must be an array')
    name = str(data.get('name', '')).strip()
    if not name or len(name)>120:
        raise ValueError('Enter a group name of at most 120 characters')
    known = {c['storage_key'] for c in store.catalogue()['companies']}
    if not set(keys)<=known:
        raise ValueError('All members must exist in the selected data folder')
    groups = db.get_setting('consolidation_groups', [])
    group_id = data.get('id') or uuid.uuid4().hex
    if data.get('id') and group_id not in {g['id'] for g in groups}:
        raise ValueError('Group does not exist in this company')
    # Never persist credentials or capability tokens from the request.
    fields = ('kind','debit_company','debit_ledger','credit_company','credit_ledger','amount','reference')
    clean = [{k:e[k] for k in fields if k in e} for e in data.get('eliminations', []) if isinstance(e,dict)]
    if len(clean)!=len(data.get('eliminations', [])):
        raise ValueError('Each elimination must be an object')
    group = {'id':group_id,'name':name,'data_path':str(store.root),'storage_keys':keys,'eliminations':clean}
    result = [g for g in groups if g['id']!=group_id]+[group]
    with db.conn:
        db.conn.execute('INSERT OR REPLACE INTO company_setting VALUES(?,?,?)',(db.active_company_id,'consolidation_groups',json.dumps(result)))
        db._log_edit('ConsolidationGroup',group_id,'SAVE',name)
    return group
