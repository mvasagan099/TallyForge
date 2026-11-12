"""Monthly payroll bookkeeping with explicit attendance inputs and immutable runs.

Manual deduction amounts are employer inputs. Legacy estimates are labelled and
are not a current statutory income-tax or Form 16 implementation.
"""
import calendar
import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re

CENT = Decimal('0.01')
MONTHS = {name.lower():index for index,name in enumerate(calendar.month_name) if name}


def period_key(value):
    if not isinstance(value,str):
        raise ValueError('Choose a payroll month (YYYY-MM)')
    value=value.strip()
    if re.fullmatch(r'\d{4}-\d{2}',value):
        year,month=map(int,value.split('-'))
    else:
        match=re.fullmatch(r'([A-Za-z]+)\s+(\d{4})',value)
        if not match or match[1].lower() not in MONTHS:
            raise ValueError('Choose a payroll month (YYYY-MM or Month YYYY)')
        year,month=int(match[2]),MONTHS[match[1].lower()]
    datetime.date(year,month,1)
    return f'{year:04d}-{month:02d}'


def number(value, label, places=2):
    try:
        result=Decimal(str(value))
        quantum=Decimal(1).scaleb(-places)
        if not result.is_finite() or result<0 or result!=result.quantize(quantum):
            raise ValueError(f'{label} must be non-negative with at most {places} decimals')
        return result.quantize(quantum)
    except (InvalidOperation,TypeError):
        raise ValueError(f'Invalid {label}')


def cents(value):
    return Decimal(str(value)).quantize(CENT,rounding=ROUND_HALF_UP)


def init_schema(db):
    db.conn.execute('''CREATE TABLE IF NOT EXISTS payroll_month_input (
        company_id INTEGER NOT NULL, period TEXT NOT NULL, emp_code TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(company_id,period,emp_code))''')


def single_company(db):
    if db.conn.execute('SELECT COUNT(*) FROM company').fetchone()[0]!=1:
        raise ValueError('Monthly payroll requires separate company books; shared legacy employee data is not reliably scoped')


def find_run(db, period):
    period=period_key(period)
    matches=[]
    for row in db.conn.execute('SELECT * FROM payroll_run WHERE company_id=?',(db.active_company_id,)):
        try:
            if period_key(row['month'])==period:
                matches.append(dict(row))
        except ValueError as exc:
            raise ValueError('An unrecognised historical payroll period exists; reconcile it before another run') from exc
    if len(matches)>1:
        raise ValueError('Multiple historical payroll runs exist for this month; reconcile them before proceeding')
    return matches[0] if matches else None


def assert_open(db, period):
    if find_run(db,period):
        raise ValueError('This payroll month is finalised; record corrections in a later month after reviewing any reversal')


def default_input(db, period, code):
    employee=db.conn.execute('SELECT 1 FROM employee WHERE emp_code=?',(code,)).fetchone()
    if not employee:
        raise ValueError('Employee not found in this company')
    year,month=map(int,period.split('-'))
    days=calendar.monthrange(year,month)[1]
    legacy=db.conn.execute('SELECT * FROM attendance WHERE emp_code=? AND month=?',(code,period)).fetchone()
    salary=db.calculate_employee_salary(code)
    return {'scheduled_days':str(days),'paid_days':str(days),'overtime_hours':'0','overtime_rate':'0',
            'arrears':'0','bonus':'0','other_deductions':'0','deductions':salary['deductions'],
            'reference':'','saved':False,'legacy_attendance':dict(legacy) if legacy else None}


def get_inputs(db, period):
    single_company(db)
    period=period_key(period)
    saved={r['emp_code']:json.loads(r['payload']) for r in db.conn.execute('SELECT emp_code,payload FROM payroll_month_input WHERE company_id=? AND period=?',(db.active_company_id,period))}
    employees=[]
    for employee in db.get_employees():
        code=employee['emp_code']
        values={**default_input(db,period,code),**saved.get(code,{}),'saved':code in saved}
        employees.append({'emp_code':code,'name':employee['name'],'inputs':values})
    return {'period':period,'employees':employees,'finalised':bool(find_run(db,period)),
            'note':'Review paid days and all deduction amounts before saving. Displayed defaults use the existing unverified deduction estimates.'}


def save_input(db, data):
    single_company(db)
    period=period_key(data.get('period',''))
    code=str(data.get('emp_code','')).strip()
    if not db.conn.execute('SELECT 1 FROM employee WHERE emp_code=?',(code,)).fetchone():
        raise ValueError('Employee not found in this company')
    values={key:str(number(data.get(key,0),key)) for key in ('scheduled_days','paid_days','overtime_hours','overtime_rate','arrears','bonus','other_deductions')}
    if not 0<Decimal(values['scheduled_days'])<=31 or Decimal(values['paid_days'])>Decimal(values['scheduled_days']):
        raise ValueError('Scheduled days must be between 0 and 31, with paid days not exceeding scheduled days')
    if Decimal(values['overtime_hours'])>744:
        raise ValueError('Overtime hours cannot exceed 744 in one month')
    deductions=data.get('deductions')
    if not isinstance(deductions,dict) or len(deductions)>30 or any(not isinstance(k,str) or not k.strip() or len(k)>80 for k in deductions):
        raise ValueError('Provide named deduction amounts (at most 30)')
    values['deductions']={name:str(number(amount,name)) for name,amount in deductions.items()}
    values['reference']=str(data.get('reference','')).strip()
    if not values['reference'] or len(values['reference'])>240:
        raise ValueError('Enter an attendance/payroll reference of at most 240 characters')
    if db.conn.in_transaction:
        raise ValueError('Finish the pending transaction before saving payroll inputs')
    with db.conn:
        db.conn.execute('BEGIN IMMEDIATE')
        assert_open(db,period)
        db.conn.execute('INSERT OR REPLACE INTO payroll_month_input VALUES(?,?,?,?)',(db.active_company_id,period,code,json.dumps(values)))
        db._log_edit('PayrollInput',period+':'+code,'SAVE',values['reference'])
    return {'period':period,'emp_code':code,**values,'saved':True}


def payroll_accounts(db):
    config=db.get_setting('payroll_config',{})
    if not config and db.seed_demo:
        config={'expense_ledger':'Staff Salaries','payment_ledger':'HDFC Bank','deductions_ledger':'Duties & Taxes'}
    if not config:
        raise ValueError('Configure payroll expense, payment and deductions ledgers before processing')
    from engine.gst_books import group_chain
    groups={g['name']:g['parent'] for g in db.get_groups()}
    chains={}
    for field in ('expense_ledger','payment_ledger','deductions_ledger'):
        row=db.conn.execute('SELECT group_name FROM ledger WHERE name=? AND company_id=?',(config.get(field),db.active_company_id)).fetchone()
        if not row:
            raise ValueError(f'Payroll ledger is missing: {field}')
        chains[field]=group_chain(row['group_name'],groups)
    if len(set(config.values()))!=3:
        raise ValueError('Choose three different payroll ledgers')
    if not any(g in chains['expense_ledger'] for g in ('Direct Expenses','Indirect Expenses')):
        raise ValueError('Salary expense ledger must be an expense account')
    if not any(g in chains['payment_ledger'] for g in ('Cash-in-Hand','Bank Accounts','Bank OD A/c','Current Liabilities')):
        raise ValueError('Salary payment/payable ledger must be cash, bank or a current liability')
    if 'Current Liabilities' not in chains['deductions_ledger']:
        raise ValueError('Deduction ledger must be a current liability')
    return config,chains


def calculate_month(db, period):
    single_company(db)
    period=period_key(period)
    config,chains=payroll_accounts(db)
    saved={r['emp_code']:json.loads(r['payload']) for r in db.conn.execute('SELECT emp_code,payload FROM payroll_month_input WHERE company_id=? AND period=?',(db.active_company_id,period))}
    slips=[]
    for employee in db.get_employees():
        slip=db.calculate_employee_salary(employee['emp_code'])
        code=employee['emp_code']
        inputs=saved.get(code)
        warnings=[]
        if inputs:
            factor=Decimal(inputs['paid_days'])/Decimal(inputs['scheduled_days'])
            earnings={name:cents(Decimal(str(amount))*factor) for name,amount in slip['earnings'].items()}
            for name,amount in (('Overtime',cents(Decimal(inputs['overtime_hours'])*Decimal(inputs['overtime_rate']))),('Arrears',cents(inputs['arrears'])),('Bonus',cents(inputs['bonus']))):
                if amount:
                    earnings[name]=earnings.get(name,Decimal(0))+amount
            deductions={name:cents(amount) for name,amount in inputs['deductions'].items()}
            if Decimal(inputs['other_deductions']):
                deductions['Other deductions']=deductions.get('Other deductions',Decimal(0))+cents(inputs['other_deductions'])
            slip['earnings']={name:float(amount) for name,amount in earnings.items()}
            slip['deductions']={name:float(amount) for name,amount in deductions.items()}
            slip['gross_salary']=float(sum(earnings.values(),Decimal(0)))
            slip['total_deductions']=float(sum(deductions.values(),Decimal(0)))
            slip['net_salary']=float(cents(slip['gross_salary'])-cents(slip['total_deductions']))
            basis='Manual reviewed amounts'
            warnings.append('Deduction amounts are manually supplied; no statutory rate or income-tax validation is performed.')
        else:
            basis='Legacy estimates'
            warnings.append('No monthly inputs were saved. Full regular earnings and the existing unverified PF/PT/ESI estimates are used.')
        if cents(slip['net_salary'])<0:
            raise ValueError(f'Employee {code}: deductions exceed monthly earnings')
        slip.update(period=period,inputs=inputs,deduction_basis=basis,warnings=warnings)
        slip['statutory_deductions']=[{'name':k,'amount':v} for k,v in slip['deductions'].items()]
        slips.append(slip)
    if not slips:
        raise ValueError('Add employees before preparing payroll')
    totals={key:float(sum((cents(s[key]) for s in slips),Decimal(0))) for key in ('gross_salary','total_deductions','net_salary')}
    company=db.get_company()
    employer={key:company.get(key,'') for key in ('name','address','gstin')}
    result={'period':period,'employer':employer,'currency':company.get('currency_symbol','₹'),'accounts':config,'payslips':slips,'totals':totals,
            'manual_inputs_saved':len(saved),'note':'Payroll bookkeeping only. Review manually supplied deductions or legacy estimates; this is not statutory tax computation or Form 16.'}
    result['preview_id']=hashlib.sha256(json.dumps({'database':str(Path(db.db_path).resolve()),'company_id':db.active_company_id,**result},sort_keys=True).encode()).hexdigest()
    return result,chains


def preview(db, period):
    period=period_key(period)
    run=find_run(db,period)
    if run:
        return posted_run(db,period)
    result,_=calculate_month(db,period)
    return {**result,'finalised':False}


def posted_run(db, period):
    period=period_key(period)
    run=find_run(db,period)
    if not run:
        raise ValueError('No finalised payroll exists for this month')
    voucher=db.conn.execute('SELECT * FROM voucher WHERE id=? AND company_id=?',(run['voucher_id'],db.active_company_id)).fetchone()
    slips=json.loads(run['payslips'])
    return {'period':period,'voucher_id':run['voucher_id'],'payment_date':voucher['date'] if voucher else None,
            'finalised':True,'status':'Missing voucher' if not voucher else ('Cancelled' if voucher['is_cancelled'] else 'Posted'),
            'payslips':slips,'totals':{key:float(sum((cents(s[key]) for s in slips),Decimal(0))) for key in ('gross_salary','total_deductions','net_salary')},
            'currency':slips[0].get('currency','') if slips else '',
            'note':'Saved payroll snapshots. Later changes to employees or salary masters do not change these payslips.'}


def process(db, period, payment_date, expected_preview=None):
    period=period_key(period)
    datetime.date.fromisoformat(payment_date)
    if db.conn.in_transaction:
        raise ValueError('Finish the pending transaction before posting payroll')
    with db.conn:
        db.conn.execute('BEGIN IMMEDIATE')
        run=find_run(db,period)
        if run:
            record=posted_run(db,period)
            if record['status']!='Posted':
                raise ValueError('The payroll voucher was cancelled or removed; review the reversal before another run')
            return run['voucher_id']
        result,chains=calculate_month(db,period)
        if expected_preview is not None and expected_preview!=result['preview_id']:
            raise ValueError('Payroll inputs or salary masters changed; refresh the preview before posting')
        if result['manual_inputs_saved'] and expected_preview is None:
            raise ValueError('Preview this month before posting attendance-based payroll')
        gross=cents(result['totals']['gross_salary']);net=cents(result['totals']['net_salary']);deductions=gross-net
        if gross<=0:
            raise ValueError('Total monthly earnings must be positive')
        accounts=result['accounts']
        entries=[(accounts['expense_ledger'],'Dr',gross)]
        if net:entries.append((accounts['payment_ledger'],'Cr',net))
        if deductions:entries.append((accounts['deductions_ledger'],'Cr',deductions))
        number='PAY-'+period.replace('-','')
        if db.conn.execute('SELECT 1 FROM voucher WHERE voucher_number=?',(number,)).fetchone():
            raise ValueError('The payroll voucher number already exists; reconcile that voucher before posting')
        cursor=db.conn.execute('INSERT INTO voucher(voucher_type,voucher_number,date,narration,total_amount,currency_symbol,company_id) VALUES(?,?,?,?,?,?,?)',('Payroll',number,payment_date,'Payroll for '+period,float(gross),result['currency'],db.active_company_id))
        voucher_id=cursor.lastrowid
        for ledger,side,amount in entries:
            db.conn.execute('INSERT INTO voucher_entry(voucher_id,ledger_name,entry_type,amount,narration) VALUES(?,?,?,?,?)',(voucher_id,ledger,side,float(amount),'Payroll '+period))
        if net and 'Bank Accounts' in chains['payment_ledger']:
            db.conn.execute("INSERT INTO bank_reconciliation(voucher_id,bank_ledger_name,instrument_date,status) VALUES(?,?,?,'Unreconciled')",(voucher_id,accounts['payment_ledger'],payment_date))
        slips=[{**s,'currency':result['currency'],'payment_date':payment_date,'employer':result['employer']} for s in result['payslips']]
        db.conn.execute('INSERT INTO payroll_run VALUES(?,?,?,?)',(db.active_company_id,period,voucher_id,json.dumps(slips)))
        db._log_edit('PayrollRun',period,'POST',number)
        return voucher_id


def assert_not_finalised_voucher(db, voucher_id):
    if db.conn.execute('SELECT 1 FROM payroll_run WHERE voucher_id=?',(voucher_id,)).fetchone():
        raise ValueError('A finalised payroll voucher cannot be altered, deleted or duplicated; review a cancellation and record corrections separately')
