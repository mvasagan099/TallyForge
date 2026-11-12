"""Regression coverage for audited data loss and incomplete local workflows."""
import base64
import datetime
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.accounting_db import EnterpriseAccountingDB
from engine.backup_scheduler import configure, run_due
from engine.data_exchange import import_workbook
from engine.company_store import CompanyStore


def xlsx(rows):
    from xml.sax.saxutils import escape
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for i, row in enumerate(rows, 1):
        xml += '<row>'
        for j, value in enumerate(row):
            xml += f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
        xml += '</row>'
    xml += '</sheetData></worksheet>'
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', xml)
    return base64.b64encode(buffer.getvalue()).decode()


class ReliabilityWorkflows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.db = EnterpriseAccountingDB(str(self.root/'company.db'), seed_demo=False)
        self.db.create_company('Real company')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_full_backup_roundtrip_over_5000_and_custom_tables(self):
        self.db.conn.executemany("INSERT INTO voucher(voucher_type,voucher_number,date,narration,total_amount,company_id) VALUES('Journal',?,'2026-04-01','',0,1)", [(str(n),) for n in range(5001)])
        self.db.conn.execute('CREATE TABLE extension_data (id INTEGER PRIMARY KEY, payload TEXT)')
        self.db.conn.execute("INSERT INTO extension_data VALUES(7,'retain me')")
        self.db.conn.commit()
        backup = self.db.create_local_backup(str(self.root/'backups'))
        with self.db.conn:
            self.db.conn.execute('DELETE FROM voucher')
            self.db.conn.execute('DELETE FROM extension_data')
        self.db.restore_local_backup(backup)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0], 5001)
        self.assertEqual(self.db.conn.execute('SELECT payload FROM extension_data WHERE id=7').fetchone()[0], 'retain me')
        self.assertTrue(list((self.root/'snapshots'/'pre_restore').glob('*.db')))

    def test_tampered_backup_does_not_modify_open_books(self):
        backup = self.db.create_local_backup(str(self.root/'backups'))
        with open(backup, 'ab') as stream:
            stream.write(b'tamper')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.db.restore_local_backup(backup)
        self.assertEqual(self.db.get_company()['name'], 'Real company')

    def test_features_persist_and_prevent_disabled_inventory(self):
        self.db.set_features({'maintain-inv': False})
        self.db.close()
        self.db = EnterpriseAccountingDB(str(self.root/'company.db'), seed_demo=False)
        self.assertFalse(self.db.get_features()['maintain-inv'])
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.db.add_stock_item('Forbidden')
        with self.assertRaises(ValueError):
            self.db.set_features({'editlog': False})

    def test_fifo_and_lifo_differ_from_average(self):
        self.db.add_stock_item('Widget', opening_qty=10, opening_rate=10)
        with self.db.conn:
            for date,direction,qty,rate in [('2026-04-02','In',10,20),('2026-04-03','Out',12,30)]:
                cursor=self.db.conn.execute("INSERT INTO voucher(voucher_type,voucher_number,date,total_amount,company_id) VALUES('Sales',?,?,0,1)", (date,date))
                self.db.conn.execute("INSERT INTO voucher_inventory(voucher_id,stock_item_name,qty,rate,amount,direction) VALUES(?,'Widget',?,?,?,?)", (cursor.lastrowid,qty,rate,qty*rate,direction))
        self.db.set_inventory_valuation('Widget','FIFO')
        self.assertEqual(self.db.get_stock_item_summary('Widget')['closing_value'],160)
        self.db.set_inventory_valuation('Widget','LIFO')
        self.assertEqual(self.db.get_stock_item_summary('Widget')['closing_value'],80)
        self.db.set_inventory_valuation('Widget','Average Cost')
        self.assertEqual(self.db.get_stock_item_summary('Widget')['closing_value'],120)

    def test_ims_decisions_and_registrations_are_saved_locally(self):
        registrations=self.db.save_gst_registration({'name':'Branch', 'gstin':'33ABCDE1234F1Z5'})
        self.assertEqual(registrations[0]['state_code'],'33')
        result=self.db.import_ims('2026-04',[{'supplier_gstin':'33ABCDE1234F1Z5','invoice_number':'S-1','invoice_date':'2026-04-01','taxable_value':100,'tax_value':18}])
        self.db.act_ims(result['invoices'][0]['id'],'Accepted','Verified')
        self.assertEqual(self.db.get_ims('2026-04')['accepted_tax'],18)
        self.assertFalse(self.db.get_ims('2026-04')['portal_sync'])

    def test_workbook_preview_import_saved_mapping_and_atomic_failure(self):
        content=xlsx([['Account','Group'],['New cash','Cash-in-Hand']])
        data={'content_base64':content,'kind':'Ledgers','mapping':{'name':'Account','group_name':'Group'},'mapping_name':'Accounts'}
        self.assertFalse(import_workbook(self.db,data)['applied'])
        self.assertEqual(self.db.get_ledgers(),[])
        self.assertEqual(import_workbook(self.db,{**data,'preview':False})['imported'],1)
        bad=xlsx([['Account','Group'],['Should rollback','Cash-in-Hand'],['New cash','Cash-in-Hand']])
        with self.assertRaises(ValueError):
            import_workbook(self.db,{'content_base64':bad,'kind':'Ledgers','mapping_name':'Accounts','preview':False})
        self.assertNotIn('Should rollback',[l['name'] for l in self.db.get_ledgers()])

    def test_backup_scheduler_due_and_retention(self):
        configure(self.db,{'enabled':True,'interval_minutes':5,'keep':2})
        now=datetime.datetime(2026,4,1)
        self.assertTrue(run_due(self.db,now))
        self.assertIsNone(run_due(self.db,now+datetime.timedelta(minutes=1)))
        self.assertTrue(run_due(self.db,now+datetime.timedelta(minutes=6)))
        self.assertTrue(run_due(self.db,now+datetime.timedelta(minutes=12)))
        self.assertEqual(len(list((self.root/'backups'/'scheduled').glob('*.db'))),2)

    def test_payroll_is_balanced_including_esi_and_idempotent(self):
        self.db.add_ledger('Wages','Indirect Expenses')
        self.db.add_ledger('Salary bank','Bank Accounts')
        self.db.add_ledger('Statutory payable','Current Liabilities')
        self.db.set_setting('payroll_config',{'expense_ledger':'Wages','payment_ledger':'Salary bank','deductions_ledger':'Statutory payable'})
        self.db.add_employee('E1','Employee',basic_salary=10000)
        voucher=self.db.process_payroll_voucher('April 2026','2026-04-30')
        self.assertEqual(voucher,self.db.process_payroll_voucher('April 2026','2026-04-30'))
        entries=self.db.conn.execute('SELECT entry_type,SUM(amount) FROM voucher_entry WHERE voucher_id=? GROUP BY entry_type',(voucher,)).fetchall()
        self.assertEqual(entries[0][1],entries[1][1])
        self.assertIn('ESI',self.db.conn.execute('SELECT payslips FROM payroll_run').fetchone()[0])

    def test_archive_single_company_preserves_recovery(self):
        store=CompanyStore(self.root,self.root/'prefs.json')
        key=store.create({'name':'Archive me'})
        member=store.open(key)
        result=store.archive_company(member)
        member.close()
        self.assertTrue(Path(result['recovery_backup']).exists())
        self.assertEqual(store.catalogue()['companies'],[])

    def test_security_tokens_follow_disabled_users_and_current_roles(self):
        self.db.create_user('reviewer','password123','Auditor')
        user=self.db.authenticate_user('reviewer','password123')
        token=self.db.create_session(user)
        with self.db.conn:
            self.db.conn.execute("UPDATE user_account SET role='Viewer' WHERE username='reviewer'")
        self.assertEqual(self.db.validate_session(token)['role'],'Viewer')
        with self.db.conn:
            self.db.conn.execute("UPDATE user_account SET is_active=0 WHERE username='reviewer'")
        self.assertIsNone(self.db.validate_session(token))

    def test_einvoice_rejects_missing_details_without_affecting_plain_preview(self):
        voucher=self.db.add_voucher('Sales','2026-04-01','Local sale',[
            {'ledger_name':'Cash','entry_type':'Dr','amount':100},
            {'ledger_name':'Sales','entry_type':'Cr','amount':100}])
        with self.assertRaisesRegex(ValueError,'GSTIN'):
            self.db.generate_einvoice_json(voucher)
        preview=self.db.generate_einvoice_json(voucher,for_submission=False)
        self.assertEqual(preview['BuyerDtls']['Gstin'],'')
        self.assertEqual(preview['SellerDtls']['Gstin'],'')

    def test_registration_voucher_series_and_validation(self):
        registrations=self.db.save_gst_registration({'name':'Branch', 'gstin':'33ABCDE1234F1Z5'})
        reg=registrations[0]['id']
        entries=[{'ledger_name':'Cash','entry_type':'Dr','amount':100},{'ledger_name':'Sales','entry_type':'Cr','amount':100}]
        voucher=self.db.add_voucher('Sales','2026-04-01','Branch sale',entries,gst_registration_id=reg)
        self.assertTrue(self.db.conn.execute('SELECT voucher_number FROM voucher WHERE id=?',(voucher,)).fetchone()[0].startswith(f'R{reg}-'))
        with self.assertRaises(ValueError):
            self.db.add_voucher('Sales','2026-04-01','Wrong branch',entries,gst_registration_id=999)

    def test_excel_export_reimports_with_literal_formula_text(self):
        from engine.data_exchange import export_workbook, workbook_rows
        self.db.add_ledger('=literal account','Cash-in-Hand')
        content=export_workbook(self.db)
        headers,rows=workbook_rows(base64.b64encode(content).decode())
        self.assertIn('name',headers)
        self.assertEqual(rows[0]['name'],'=literal account')


if __name__=='__main__': unittest.main()
