"""Exercise API handlers and live engine rebinding without opening a network socket."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


class CompanyStorageAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        cls.previous_env = {k: os.environ.get(k) for k in ('TALLY_DATA_DIR', 'TALLY_DB_NAME')}
        os.environ['TALLY_DATA_DIR'] = str(cls.root)
        os.environ['TALLY_DB_NAME'] = 'legacy.db'
        spec = importlib.util.spec_from_file_location('_company_storage_test_server', Path(__file__).resolve().parents[1] / 'server.py')
        cls.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.server)

    @classmethod
    def tearDownClass(cls):
        for engine in set(cls.server._company_contexts.values()):
            engine.close()
        cls.server._bootstrap_db.close()
        for key, value in cls.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cls.temp.cleanup()

    @classmethod
    def request(cls, path, data=None, context=None, token=None):
        # Exercise real handlers without binding a socket (blocked by this workspace sandbox).
        from io import BytesIO
        handler = cls.server.TallyRequestHandler.__new__(cls.server.TallyRequestHandler)
        handler.path = path
        handler.client_address = ('127.0.0.1', 12345)
        body = b'' if data is None else json.dumps(data).encode()
        handler.headers = {'Content-Length': str(len(body))}
        if context is not None:
            handler.headers['X-Company-Context'] = context
        if token is not None:
            handler.headers['Authorization'] = 'Bearer ' + token
        handler.rfile = BytesIO(body)
        handler.send_json = lambda status, value: (status, value)
        return handler.do_GET() if data is None else handler.do_POST()

    def test_complete_company_lifecycle(self):
        self.assertEqual(self.request('/api/storage')[1]['companies'], [])
        status, created = self.request('/api/storage/create', {'name': 'Alpha'})
        self.assertEqual(status, 201)
        first = created['storage_key']
        _, created = self.request('/api/storage/create', {'name': 'Beta'})
        second = created['storage_key']
        status, selected = self.request('/api/storage/select', {'storage_key': first})
        self.assertEqual(status, 200)
        old_context = selected['company_context']
        _, state = self.request('/api/state', context=old_context)
        self.assertEqual(state['company']['name'], 'Alpha')
        self.assertEqual(state['stock_items'], [])
        status, _ = self.request('/api/ledgers', {'name': 'Alpha only', 'group_name': 'Capital Account'}, old_context)
        self.assertEqual(status, 201)
        _, selected = self.request('/api/storage/select', {'storage_key': second})
        new_context = selected['company_context']
        status, _ = self.request('/api/ledgers', {'name': 'Independent Alpha write', 'group_name': 'Capital Account'}, old_context)
        self.assertEqual(status, 201)
        _, state = self.request('/api/state', context=new_context)
        self.assertEqual(state['company']['name'], 'Beta')
        self.assertNotIn('Alpha only', [l['name'] for l in state['ledgers']])
        # Reports use the newly selected DB, including the TDL runtime.
        self.assertEqual(self.request('/api/report/BalanceSheet', context=new_context)[0], 200)
        status, _ = self.request('/api/storage/select', {'storage_key': '../outside:1'})
        self.assertEqual(status, 400)
        self.assertEqual(self.request('/api/state', context=new_context)[1]['company']['name'], 'Beta')
        self.assertEqual(self.request('/api/companies/shut', {'company_id': 1}, new_context)[0], 200)
        self.assertEqual(self.request('/api/state')[1]['company'].get('name'), None)
        _, selected = self.request('/api/storage/select', {'storage_key': first})
        self.assertIn('Alpha only', [l['name'] for l in self.request('/api/state', context=selected['company_context'])[1]['ledgers']])
        other = self.root / 'Other'
        other.mkdir()
        self.assertEqual(self.request('/api/storage/path', {'data_path': str(other)})[0], 200)
        self.assertEqual(self.request('/api/storage')[1]['companies'], [])
        self.assertEqual(json.loads((self.root / 'company-path.json').read_text())['data_path'], str(other))

    def test_secured_company_login_and_role_enforcement(self):
        _, made = self.request('/api/storage/create', {'name': 'Protected'})
        key = made['storage_key']
        _, selected = self.request('/api/storage/select', {'storage_key': key})
        context = selected['company_context']
        self.assertEqual(self.request('/api/companies/security', {'company_id':1,'security_enabled':True,'admin_username':'owner','admin_password':'owner-password'}, context)[0],200)
        self.assertEqual(self.request('/api/state', context=context)[0],401)
        self.assertEqual(self.request('/api/state', context=context,token='invalid')[0],401)
        self.assertEqual(self.request('/api/storage/select', {'storage_key':key})[0],401)
        _, selected = self.request('/api/storage/select', {'storage_key':key,'username':'owner','password':'owner-password'})
        context, token = selected['company_context'], selected['token']
        self.assertEqual(self.request('/api/state',context=context,token=token)[0],200)
        self.assertEqual(self.request('/api/users',{'username':'entry','password':'entry-password','role':'Data Entry'},context,token)[0],201)
        _, account = self.request('/api/auth/login', {'username':'entry','password':'entry-password'}, context)
        entry_token = account['token']
        self.assertEqual(self.request('/api/companies/security',{'security_enabled':False},context,entry_token)[0],403)
        self.assertEqual(self.request('/api/users/update',{'id':2,'role':'Admin'},context,entry_token)[0],403)
        self.assertEqual(self.request('/api/company/features',{'maintain-inv':False},context,entry_token)[0],403)

    def test_invalid_token_never_becomes_unsecured_company_admin(self):
        _, made = self.request('/api/storage/create', {'name':'Unsecured'})
        _, selected = self.request('/api/storage/select', {'storage_key':made['storage_key']})
        context=selected['company_context']
        self.assertEqual(self.request('/api/state',context=context,token='bad')[0],401)
        self.assertEqual(self.request('/api/company/features',{'gst':False},context,token='bad')[0],401)
        self.assertEqual(self.request('/api/state',context=context)[0],200)

    def test_single_company_removal_invalidates_its_context(self):
        _, made=self.request('/api/storage/create',{'name':'Remove safely'})
        _, selected=self.request('/api/storage/select',{'storage_key':made['storage_key']})
        context=selected['company_context']
        status,result=self.request('/api/companies/delete',{'company_id':1},context)
        self.assertEqual(status,200)
        self.assertTrue(Path(result['recovery_backup']).exists())
        self.assertEqual(self.request('/api/state',context=context)[0],409)
        self.assertNotIn(made['storage_key'],[c['storage_key'] for c in self.request('/api/storage')[1]['companies']])

    def test_consolidation_saved_groups_and_protected_member_access(self):
        _, alpha=self.request('/api/storage/create',{'name':'Group parent'})
        _, beta=self.request('/api/storage/create',{'name':'Group subsidiary'})
        _, active=self.request('/api/storage/select',{'storage_key':alpha['storage_key']})
        owner_context=active['company_context']
        owner=self.server._company_contexts[owner_context]
        owner.add_ledger('Due from subsidiary','Sundry Debtors',100,'Dr')
        owner.add_ledger('Capital','Capital Account',100,'Cr')
        _, active=self.request('/api/storage/select',{'storage_key':beta['storage_key']})
        child_context=active['company_context']
        child=self.server._company_contexts[child_context]
        child.add_ledger('Cash','Cash-in-Hand',100,'Dr')
        child.add_ledger('Due to parent','Sundry Creditors',100,'Cr')
        self.assertEqual(self.request('/api/companies/security',{'company_id':1,'security_enabled':True,'admin_username':'group-reader','admin_password':'reader-password'},child_context)[0],200)
        elimination={'kind':'Balance','debit_company':alpha['storage_key'],'debit_ledger':'Due from subsidiary','credit_company':beta['storage_key'],'credit_ledger':'Due to parent','amount':100,'reference':'IC-API'}
        payload={'name':'Group report','storage_keys':[alpha['storage_key'],beta['storage_key']],'eliminations':[elimination]}
        self.assertEqual(self.request('/api/company/consolidated',payload,owner_context)[0],403)
        self.assertEqual(self.request('/api/company/consolidation/authorize',{'storage_key':beta['storage_key'],'username':'group-reader','password':'wrong'},owner_context)[0],403)
        status,access=self.request('/api/company/consolidation/authorize',{'storage_key':beta['storage_key'],'username':'group-reader','password':'reader-password'},owner_context)
        self.assertEqual(status,200)
        payload['authorizations']={beta['storage_key']:{'context':access['context'],'token':access['token']}}
        status,group=self.request('/api/company/consolidation/groups',payload,owner_context)
        self.assertEqual(status,201)
        self.assertNotIn(access['token'],json.dumps(owner.get_setting('consolidation_groups')))
        status,report=self.request('/api/company/consolidated',{'group_id':group['id'],'authorizations':payload['authorizations']},owner_context)
        self.assertEqual(status,200)
        self.assertEqual(report['eliminated_balances'],100)
        self.assertEqual(report['trial_totals']['difference'],0)
        self.assertEqual(self.request('/api/state',context=owner_context)[1]['company']['name'],'Group parent')
        self.assertEqual(self.request('/api/company/consolidation/groups/delete',{'id':group['id']},owner_context)[0],200)
        self.assertEqual(self.request('/api/company/consolidation/groups',context=owner_context)[1],[])

    def test_gst_monthly_books_endpoint_does_not_include_other_months(self):
        _, made=self.request('/api/storage/create',{'name':'GST scope','gstin':'33ABCDE1234F1Z5','state_code':'33'})
        _, chosen=self.request('/api/storage/select',{'storage_key':made['storage_key']})
        context=chosen['company_context']
        engine=self.server._company_contexts[context]
        for day in ('2026-04-30','2026-05-01'):
            engine.add_voucher('Sales',day,'Cash sale',[{'ledger_name':'Cash','entry_type':'Dr','amount':100},{'ledger_name':'Sales','entry_type':'Cr','amount':100}])
        status,result=self.request('/api/gst/books?period=2026-04&registration=main',context=context)
        self.assertEqual(status,200)
        self.assertEqual(len(result['invoices']),1)
        self.assertEqual(result['totals']['Outward']['document_value'],100)
        self.assertFalse(result['portal_submitted'])

    def test_payroll_month_preview_post_and_role_permissions(self):
        _, made=self.request('/api/storage/create',{'name':'Payroll API'})
        _, chosen=self.request('/api/storage/select',{'storage_key':made['storage_key']})
        context=chosen['company_context'];engine=self.server._company_contexts[context]
        for name,group in [('Wages','Indirect Expenses'),('Payroll bank','Bank Accounts'),('Payroll deductions','Current Liabilities')]:engine.add_ledger(name,group)
        engine.set_setting('payroll_config',{'expense_ledger':'Wages','payment_ledger':'Payroll bank','deductions_ledger':'Payroll deductions'})
        engine.add_employee('E1','Payroll employee',basic_salary=10000)
        values={'period':'2026-04','emp_code':'E1','scheduled_days':30,'paid_days':15,'deductions':{'Manual deduction':100},'reference':'Approved attendance'}
        self.assertEqual(self.request('/api/payroll/month',values,context)[0],200)
        status,report=self.request('/api/payroll/preview?period=2026-04',context=context)
        self.assertEqual(status,200);self.assertEqual(report['totals']['net_salary'],4900)
        self.assertEqual(self.request('/api/payroll/month',{**values,'bonus':100},context)[0],200)
        self.assertEqual(self.request('/api/payroll/process',{'month':'2026-04','date':'2026-04-30','preview_id':report['preview_id']},context)[0],400)
        _,report=self.request('/api/payroll/preview?period=2026-04',context=context)
        status,result=self.request('/api/payroll/process',{'month':'2026-04','date':'2026-04-30','preview_id':report['preview_id']},context)
        self.assertEqual(status,201)
        self.assertEqual(self.request('/api/payroll/process',{'month':'April 2026','date':'2026-04-30'},context)[1]['voucher_id'],result['voucher_id'])
        _,saved=self.request('/api/payroll/run?period=2026-04',context=context)
        self.assertEqual(saved['totals']['net_salary'],5000)
        self.assertEqual(saved['payslips'][0]['employer']['name'],'Payroll API')
        engine.create_user('entry-payroll','entry-password','Data Entry')
        user=engine.authenticate_user('entry-payroll','entry-password');token=engine.create_session(user)
        for endpoint in ('/api/payroll/month?period=2026-04','/api/payroll/run?period=2026-04','/api/employees','/api/report/PayrollSummary'):
            self.assertEqual(self.request(endpoint,context=context,token=token)[0],403)
        self.assertEqual(self.request('/api/state',context=context,token=token)[1]['employees'],[])
        self.assertEqual(self.request('/api/payroll/process',{'month':'2026-05','date':'2026-05-31'},context,token)[0],403)

    def test_native_detection_inspection_and_bridge_are_not_local_financial_open(self):
        from test_native_tally_access import native_fixture
        from unittest.mock import patch
        folder=self.server.company_store.root/'100124'
        native_fixture(folder,'Native company')
        status,catalogue=self.request('/api/storage')
        self.assertEqual(status,200)
        native=next(c for c in catalogue['native_companies'] if c['company_number']=='100124')
        status,result=self.request('/api/storage/native/inspect',{'storage_key':native['storage_key']})
        self.assertEqual(status,200);self.assertFalse(result['standalone_books_readable'])
        from test_native_records import records_fixture
        records_fixture(folder)
        status,result=self.request('/api/storage/native/read',{'storage_key':native['storage_key'],'kind':'masters'})
        self.assertEqual(status,200)
        self.assertEqual(result['rows'][0]['names'],['Master பெயர் <&>'])
        self.assertFalse(result['standalone_books_readable'])
        self.assertFalse(result['source_files_written'])
        self.assertEqual(self.request('/api/storage/native/read',{'storage_key':native['storage_key'],'limit':1000})[0],400)
        from test_native_records import transaction_fixture
        transaction_fixture(folder)
        status,result=self.request('/api/storage/native/read',{'storage_key':native['storage_key'],'kind':'transactions'})
        self.assertEqual(status,200)
        self.assertEqual(result['rows'][0]['metadata'][0]['page_number'],2)
        status,result=self.request('/api/storage/native/read',{'storage_key':native['storage_key'],'kind':'detail','source':'TranMgr.1800','sequence':15})
        self.assertEqual(status,200)
        self.assertTrue(any(raw['signed_integer_raw']=='-1234567890123456789' for row in result['rows'] for raw in row['scalar_candidates']))
        from test_native_links import link_fixture
        guid=link_fixture(folder)
        status,result=self.request('/api/storage/native/links',{'storage_key':native['storage_key'],'voucher_guid':guid})
        self.assertEqual(status,200)
        self.assertEqual(result['total_candidates'],2)
        self.assertEqual(result['rows'][0]['source_pages'],[1,4])
        self.assertFalse(result['standalone_books_readable'])
        self.assertEqual(self.request('/api/storage/native/links',{'storage_key':native['storage_key'],'voucher_guid':guid,'limit':101})[0],400)
        from test_native_directory import directory_fixture
        directory_fixture(folder)
        status,result=self.request('/api/storage/native/directory',{'storage_key':native['storage_key']})
        self.assertEqual(status,200)
        self.assertTrue(result['directory_verified'])
        self.assertFalse(result['financial_status_verified'])
        self.assertEqual(result['node_count'],3)
        self.assertEqual(self.request('/api/storage/native/directory',{'storage_key':'native:../outside'})[0],400)
        from test_native_reference import reference_fixture
        reference_fixture(folder)
        status,result=self.request('/api/storage/native/reference/verify',{'storage_key':native['storage_key']})
        self.assertEqual(status,200)
        self.assertTrue(result['reference_allocations_verified'])
        self.assertEqual(result['totals']['ledger_entries_verified'],2)
        status,result=self.request('/api/storage/native/books',{'storage_key':native['storage_key']})
        self.assertEqual(status,200)
        self.assertTrue(result['books']['read_only'])
        self.assertEqual(len(result['books']['vouchers']),1)
        self.assertIsNone(result['books']['masters'][0]['opening_balance'])
        self.assertEqual(self.request('/api/storage/native/books',{'storage_key':'native:../outside'})[0],400)
        status,result=self.request('/api/storage/native/sales/preview',{'storage_key':native['storage_key'],'sequence':999})
        self.assertEqual(status,200)
        self.assertEqual(result['voucher']['master_id'],'41')
        self.assertFalse(result['financial_status_verified'])
        self.assertEqual(self.request('/api/storage/select',{'storage_key':native['storage_key']})[0],400)
        response=b'<ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME="Live Company"><NAME>Live Company</NAME></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>'
        with patch('engine.tally_bridge.send_xml',return_value=response):
            status,result=self.request('/api/tally/bridge/read',{'kind':'companies','endpoint':'http://127.0.0.1:9000'})
        self.assertEqual(status,200);self.assertEqual(result['rows'][0]['name'],'Live Company')
        self.assertFalse(result['native_file_decoder'])


if __name__ == '__main__':
    unittest.main()
