"""Independent evidence for reporting adjustments and registration-period scopes."""
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.company_store import CompanyStore
from engine.consolidation import save_group
from engine.gst_books import book_report


class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.store=CompanyStore(self.root,self.root/'prefs.json')
        self.alpha=self.store.create({'name':'Alpha'})
        self.beta=self.store.create({'name':'Beta'})
        self.a=self.store.open(self.alpha)
        self.b=self.store.open(self.beta)
        self.a.add_ledger('Due from Beta','Sundry Debtors',1000,'Dr')
        self.a.add_ledger('Capital','Capital Account',1000,'Cr')
        self.b.add_ledger('Cash','Cash-in-Hand',1000,'Dr')
        self.b.add_ledger('Due to Alpha','Sundry Creditors',1000,'Cr')

    def tearDown(self):
        self.a.close();self.b.close();self.temp.cleanup()

    def elimination(self, **values):
        return {**{'kind':'Balance','debit_company':self.alpha,'debit_ledger':'Due from Beta','credit_company':self.beta,'credit_ledger':'Due to Alpha','amount':1000,'reference':'IC-1'},**values}

    def test_balance_elimination_balances_group_and_preserves_source(self):
        result=self.store.consolidated([self.alpha,self.beta],[self.elimination()])
        self.assertEqual(result['eliminated_balances'],1000)
        self.assertEqual(result['before_eliminations']['assets_total']-result['totals']['assets_total'],1000)
        self.assertEqual(result['before_eliminations']['liab_total']-result['totals']['liab_total'],1000)
        self.assertEqual(result['trial_totals']['difference'],0)
        self.assertEqual(sum(r['amount'] for r in result['elimination_journal'] if r['entry_type']=='Dr'),1000)
        self.assertEqual(self.a.get_ledger_closing_balance('Due from Beta')[0],1000)
        self.assertFalse(result['source_books_modified'])

    def test_income_expense_elimination_preserves_net_profit(self):
        self.a.add_ledger('Interco purchases','Purchase Accounts',500,'Dr')
        self.a.add_ledger('Supplier payable','Sundry Creditors',500,'Cr')
        self.b.add_ledger('Interco sales','Sales Accounts',500,'Cr')
        self.b.add_ledger('Customer receivable','Sundry Debtors',500,'Dr')
        entry=self.elimination(kind='IncomeExpense',debit_ledger='Interco purchases',credit_ledger='Interco sales',amount=500)
        result=self.store.consolidated([self.alpha,self.beta],[entry])
        self.assertEqual(result['before_eliminations']['sales_total']-result['totals']['sales_total'],500)
        self.assertEqual(result['before_eliminations']['purchase_total']-result['totals']['purchase_total'],500)
        self.assertEqual(result['before_eliminations']['net_profit'],result['totals']['net_profit'])
        self.assertEqual(result['trial_totals']['difference'],0)

    def test_invalid_cumulative_and_nonfinite_adjustments_rejected(self):
        for entries in ([self.elimination(amount=1001)], [self.elimination(amount=600),self.elimination(amount=600)], [self.elimination(amount='NaN')], [self.elimination(reference='')], [self.elimination(debit_company=self.beta)], [self.elimination(kind='IncomeExpense')]):
            with self.subTest(entries=entries),self.assertRaises(ValueError):
                self.store.consolidated([self.alpha,self.beta],entries)
        self.assertEqual(self.a.get_ledger_closing_balance('Due from Beta')[0],1000)

    def test_same_names_in_different_groups_stay_separate(self):
        self.a.add_ledger('Shared','Cash-in-Hand',1)
        self.b.add_ledger('Shared','Indirect Expenses',2)
        result=self.store.consolidated([self.alpha,self.beta])
        self.assertEqual(len([r for r in result['trial_balance'] if r['name']=='Shared']),2)

    def test_secured_members_require_current_authorization(self):
        self.b.create_user('reviewer','password123','Auditor')
        self.b.update_company_security(1,True)
        account=self.b.authenticate_user('reviewer','password123')
        token=self.b.create_session(account)
        with self.assertRaises(PermissionError):
            self.store.consolidated([self.alpha,self.beta])
        auth={self.beta:{'context':'ctx-b','token':token}}
        self.assertEqual(len(self.store.consolidated([self.alpha,self.beta],authorizations=auth,contexts={'ctx-b':self.b})['members']),2)
        with self.b.conn:
            self.b.conn.execute("UPDATE user_account SET is_active=0 WHERE username='reviewer'")
        with self.assertRaises(PermissionError):
            self.store.consolidated([self.alpha,self.beta],authorizations=auth,contexts={'ctx-b':self.b})

    def test_group_persistence_does_not_store_credentials(self):
        group=save_group(self.a,self.store,{'name':'Group','storage_keys':[self.alpha,self.beta],'eliminations':[self.elimination()],'authorizations':{'secret':'credential'}})
        self.assertEqual(self.a.get_setting('consolidation_groups')[0]['id'],group['id'])
        self.assertNotIn('credential',json.dumps(self.a.get_setting('consolidation_groups')))

    def test_reopening_existing_legacy_books_does_not_reseed_deleted_demo_balances(self):
        from engine.accounting_db import EnterpriseAccountingDB
        legacy=EnterpriseAccountingDB(str(self.root/'tally_data.db'))
        with legacy.conn:
            legacy.conn.execute('DELETE FROM ledger')
        legacy.close()
        reopened=self.store.open('legacy:1')
        try:
            self.assertEqual(reopened.get_ledgers(),[])
        finally:
            reopened.close()

    def test_mismatching_currency_financial_year_or_legacy_books_rejected(self):
        with self.b.conn:self.b.conn.execute("UPDATE company SET currency_symbol='$'")
        with self.assertRaisesRegex(ValueError,'currencies'):self.store.consolidated([self.alpha,self.beta])
        with self.b.conn:self.b.conn.execute("UPDATE company SET currency_symbol='₹',financial_year_from='2025-04-01'")
        with self.assertRaisesRegex(ValueError,'financial years'):self.store.consolidated([self.alpha,self.beta])
        with self.b.conn:self.b.conn.execute("UPDATE company SET financial_year_from='2026-04-01'")
        self.b.create_company('Legacy second')
        with self.assertRaisesRegex(ValueError,'legacy'):self.store.consolidated([self.alpha,self.beta])


class GSTBookReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.store=CompanyStore(self.root,self.root/'prefs.json')
        self.key=self.store.create({'name':'Books','gstin':'33ABCDE1234F1Z5','state_code':'33'})
        self.db=self.store.open(self.key)
        self.db.save_gst_registration({'name':'Branch','gstin':'33ABCDF1234F1Z5'})
        self.reg=self.db.get_gst_registrations()[0]['id']
        for name,group in [('Customer','Sundry Debtors'),('Vendor','Sundry Creditors'),('Sales','Sales Accounts'),('Purchases','Purchase Accounts'),('CGST','Duties & Taxes'),('SGST','Duties & Taxes')]:self.db.add_ledger(name,group)
        with self.db.conn:self.db.conn.execute("UPDATE ledger SET gstin='33ABCDE1234F1Z5',state_code='33' WHERE name IN ('Customer','Vendor')")
        self.db.add_stock_item('Widget',hsn_code='1234',gst_rate=5,opening_qty=100,opening_rate=10)

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def voucher(self,date='2026-04-30',registration=None,inward=False):
        entries=[{'ledger_name':'Purchases','entry_type':'Dr','amount':100},{'ledger_name':'CGST','entry_type':'Dr','amount':2.5},{'ledger_name':'SGST','entry_type':'Dr','amount':2.5},{'ledger_name':'Vendor','entry_type':'Cr','amount':105}] if inward else [
            {'ledger_name':'Customer','entry_type':'Dr','amount':105},{'ledger_name':'Sales','entry_type':'Cr','amount':100},{'ledger_name':'CGST','entry_type':'Cr','amount':2.5},{'ledger_name':'SGST','entry_type':'Cr','amount':2.5}]
        return self.db.add_voucher('Purchase' if inward else 'Sales',date,'Report',entries,inventory_items=[{'stock_item_name':'Widget','qty':1,'rate':100,'amount':100,'direction':'In' if inward else 'Out'}],gst_registration_id=registration)

    def test_period_and_registration_scoping_use_actual_posted_five_percent(self):
        self.voucher();self.voucher(registration=self.reg);self.voucher(date='2026-05-01',registration=self.reg)
        self.voucher(registration=self.reg,inward=True)
        report=book_report(self.db,'2026-04',str(self.reg))
        self.assertEqual(len(report['invoices']),2)
        self.assertEqual(report['totals']['Outward']['taxable_value'],100)
        self.assertEqual(report['totals']['Outward']['cgst'],2.5)
        self.assertEqual(report['totals']['Inward']['cgst'],2.5)
        self.assertEqual(report['posted_tax_difference']['cgst'],0)
        self.assertEqual(len(book_report(self.db,'2026-04','main')['invoices']),1)
        self.assertFalse(report['portal_submitted'])

    def test_master_changes_do_not_rewrite_historical_details(self):
        self.voucher()
        with self.db.conn:
            self.db.conn.execute("UPDATE stock_item SET gst_rate=18,hsn_code='9999' WHERE name='Widget'")
            self.db.conn.execute("UPDATE ledger SET gstin='29ABCDE1234F1Z5' WHERE name='Customer'")
        row=book_report(self.db,'2026-04','main')['invoices'][0]
        self.assertEqual(row['lines'][0]['gst_rate'],'5.00')
        self.assertEqual(row['lines'][0]['hsn'],'1234')
        self.assertEqual(row['party_gstin'],'33ABCDE1234F1Z5')
        self.assertTrue(row['snapshot_at_posting'])

    def test_cancelled_optional_notes_and_older_entries_are_explicit(self):
        cancelled=self.voucher();self.db.cancel_voucher(cancelled)
        old=self.voucher();optional=self.voucher()
        with self.db.conn:
            self.db.conn.execute('DELETE FROM voucher_gst_snapshot WHERE voucher_id=?',(old,))
            self.db.conn.execute('UPDATE voucher SET is_optional=1 WHERE id=?',(optional,))
        self.db.add_voucher('Credit Note','2026-04-01','Note',[{'ledger_name':'Sales','entry_type':'Dr','amount':10},{'ledger_name':'Customer','entry_type':'Cr','amount':10}])
        report=book_report(self.db,'2026-04')
        self.assertEqual(len(report['invoices']),1)
        self.assertTrue(any(w['code']=='historical_master_values' for w in report['warnings']))
        self.assertEqual(len(report['excluded_documents']),1)

    def test_invalid_period_or_registration_does_not_modify_books(self):
        self.voucher()
        for period,reg in [('2026-13','main'),('04-2026','all'),('2026-04','999'),('2026-04','invalid')]:
            with self.subTest(period=period,registration=reg),self.assertRaises(ValueError):book_report(self.db,period,reg)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],1)

    def test_alteration_updates_snapshot_and_duplicate_retains_it(self):
        original=self.voucher()
        duplicate=self.db.duplicate_voucher(original,'2026-04-29','DUP-1')
        self.assertIsNotNone(self.db.conn.execute('SELECT payload FROM voucher_gst_snapshot WHERE voucher_id=?',(duplicate,)).fetchone())
        self.db.alter_voucher(original,'2026-04-28','Changed',[
            {'ledger_name':'Customer','entry_type':'Dr','amount':210},{'ledger_name':'Sales','entry_type':'Cr','amount':200},{'ledger_name':'CGST','entry_type':'Cr','amount':5},{'ledger_name':'SGST','entry_type':'Cr','amount':5}],inventory_items=[{'stock_item_name':'Widget','qty':2,'rate':100,'amount':200,'direction':'Out'}])
        result=book_report(self.db,'2026-04')
        self.assertEqual(result['totals']['Outward']['cgst'],7.5)


if __name__=='__main__':unittest.main()
