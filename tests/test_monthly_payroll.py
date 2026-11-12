"""Monthly payroll arithmetic, immutable snapshots and transactional posting."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.company_store import CompanyStore
from engine.payroll_workflows import get_inputs, period_key, posted_run, preview, process, save_input


class MonthlyPayrollTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
        self.store=CompanyStore(self.root,self.root/'prefs.json')
        self.key=self.store.create({'name':'Payroll company'})
        self.db=self.store.open(self.key)
        for name,group in [('Wages','Indirect Expenses'),('Bank','Bank Accounts'),('Deductions','Current Liabilities')]:self.db.add_ledger(name,group)
        self.db.set_setting('payroll_config',{'expense_ledger':'Wages','payment_ledger':'Bank','deductions_ledger':'Deductions'})
        self.db.add_employee('E1','Employee',basic_salary=30000,bank_acc_no='123456789')

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def inputs(self, **values):
        return {**{'period':'2026-04','emp_code':'E1','scheduled_days':30,'paid_days':15,'overtime_hours':2,'overtime_rate':100,'arrears':1000,'bonus':500,'other_deductions':100,'deductions':{'Reviewed PF':1800,'Reviewed PT':200},'reference':'Attendance-April'},**values}

    def test_attendance_overtime_arrears_and_manual_deductions(self):
        save_input(self.db,self.inputs())
        report=preview(self.db,'2026-04')
        # 30,000 * 15/30 + 2*100 + 1,000 + 500 = 16,700.
        self.assertEqual(report['totals'],{'gross_salary':16700,'total_deductions':2100,'net_salary':14600})
        slip=report['payslips'][0]
        self.assertEqual(slip['earnings']['Basic Salary'],15000)
        self.assertEqual(slip['deduction_basis'],'Manual reviewed amounts')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],0)

    def test_month_aliases_do_not_post_twice(self):
        self.assertEqual(period_key(' April 2026 '),'2026-04')
        save_input(self.db,self.inputs())
        report=preview(self.db,'April 2026')
        voucher=process(self.db,'April 2026','2026-04-30',report['preview_id'])
        self.assertEqual(process(self.db,'2026-04','2026-05-01'),voucher)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],1)
        self.assertEqual(self.db.conn.execute('SELECT month FROM payroll_run').fetchone()[0],'2026-04')
        entries=self.db.conn.execute('SELECT entry_type,SUM(amount) FROM voucher_entry GROUP BY entry_type').fetchall()
        self.assertEqual(entries[0][1],entries[1][1])

    def test_old_month_label_is_also_detected(self):
        report=preview(self.db,'2026-04')
        voucher=process(self.db,'2026-04','2026-04-30',report['preview_id'])
        with self.db.conn:self.db.conn.execute("UPDATE payroll_run SET month='April 2026'")
        self.assertEqual(process(self.db,'2026-04','2026-04-30'),voucher)

    def test_stale_preview_is_rejected_after_input_or_master_change(self):
        save_input(self.db,self.inputs())
        old=preview(self.db,'2026-04')['preview_id']
        save_input(self.db,self.inputs(bonus=600))
        with self.assertRaisesRegex(ValueError,'changed'):process(self.db,'2026-04','2026-04-30',old)
        old=preview(self.db,'2026-04')['preview_id']
        with self.db.conn:self.db.conn.execute("UPDATE salary_structure SET amount=40000 WHERE emp_code='E1'")
        with self.assertRaisesRegex(ValueError,'changed'):process(self.db,'2026-04','2026-04-30',old)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM payroll_run').fetchone()[0],0)

    def test_input_validation_and_negative_net_are_rejected(self):
        for values in ({'paid_days':31},{'scheduled_days':0},{'bonus':'NaN'},{'arrears':-1},{'overtime_hours':745},{'reference':''},{'deductions':{'PF':-10}},{'emp_code':'missing'}):
            with self.subTest(values=values),self.assertRaises(ValueError):save_input(self.db,self.inputs(**values))
        save_input(self.db,self.inputs(deductions={'Recovery':50000}))
        with self.assertRaisesRegex(ValueError,'exceed'):preview(self.db,'2026-04')

    def test_manual_attendance_requires_preview(self):
        save_input(self.db,self.inputs())
        with self.assertRaisesRegex(ValueError,'Preview'):process(self.db,'2026-04','2026-04-30')

    def test_posting_and_snapshot_are_atomic_on_storage_failure(self):
        save_input(self.db,self.inputs())
        fingerprint=preview(self.db,'2026-04')['preview_id']
        with self.db.conn:self.db.conn.execute("CREATE TRIGGER fail_run BEFORE INSERT ON payroll_run BEGIN SELECT RAISE(ABORT,'snapshot failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):process(self.db,'2026-04','2026-04-30',fingerprint)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM voucher_entry').fetchone()[0],0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM bank_reconciliation').fetchone()[0],0)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM payroll_run').fetchone()[0],0)
        with self.db.conn:self.db.conn.execute('DROP TRIGGER fail_run')
        self.assertIsInstance(process(self.db,'2026-04','2026-04-30',fingerprint),int)

    def test_saved_payslips_survive_employee_changes_and_deletion(self):
        save_input(self.db,self.inputs())
        report=preview(self.db,'2026-04')
        process(self.db,'2026-04','2026-04-30',report['preview_id'])
        with self.db.conn:self.db.conn.execute("UPDATE employee SET name='Changed',basic_salary=99999 WHERE emp_code='E1'")
        self.db.delete_employee('E1')
        result=posted_run(self.db,'2026-04')
        self.assertEqual(result['payslips'][0]['name'],'Employee')
        self.assertEqual(result['totals']['gross_salary'],16700)
        self.assertEqual(result['status'],'Posted')

    def test_finalised_input_and_direct_voucher_mutations_are_blocked(self):
        report=preview(self.db,'2026-04')
        voucher=process(self.db,'2026-04','2026-04-30',report['preview_id'])
        with self.assertRaisesRegex(ValueError,'finalised'):save_input(self.db,self.inputs())
        with self.assertRaisesRegex(ValueError,'finalised'):self.db.alter_voucher(voucher,'2026-04-30','Change',[])
        with self.assertRaisesRegex(ValueError,'finalised'):self.db.delete_voucher(voucher)
        with self.assertRaisesRegex(ValueError,'finalised'):self.db.duplicate_voucher(voucher)
        self.db.cancel_voucher(voucher)
        self.assertEqual(posted_run(self.db,'2026-04')['status'],'Cancelled')
        with self.assertRaisesRegex(ValueError,'cancelled'):process(self.db,'2026-04','2026-04-30')

    def test_legacy_estimates_are_labelled_and_payable_account_is_supported(self):
        self.db.add_ledger('Salary payable','Current Liabilities')
        self.db.set_setting('payroll_config',{'expense_ledger':'Wages','payment_ledger':'Salary payable','deductions_ledger':'Deductions'})
        report=preview(self.db,'2026-04')
        self.assertEqual(report['payslips'][0]['deduction_basis'],'Legacy estimates')
        process(self.db,'2026-04','2026-04-30',report['preview_id'])
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM bank_reconciliation').fetchone()[0],0)

    def test_legacy_attendance_is_visible_for_review_but_not_silently_applied(self):
        with self.db.conn:self.db.conn.execute("INSERT INTO attendance(emp_code,month,present_days,absent_days) VALUES('E1','2026-04',20,10)")
        inputs=get_inputs(self.db,'2026-04')['employees'][0]['inputs']
        self.assertEqual(inputs['legacy_attendance']['present_days'],20)
        self.assertFalse(inputs['saved'])
        self.assertEqual(preview(self.db,'2026-04')['totals']['gross_salary'],30000)

    def test_wrong_ledger_groups_shared_legacy_and_invalid_periods_rejected(self):
        for period in ('2026-13','April','2026-4','0000-01'):
            with self.assertRaises(ValueError):preview(self.db,period)
        self.db.set_setting('payroll_config',{'expense_ledger':'Bank','payment_ledger':'Wages','deductions_ledger':'Deductions'})
        with self.assertRaisesRegex(ValueError,'expense'):preview(self.db,'2026-04')
        self.db.create_company('Other company')
        with self.assertRaisesRegex(ValueError,'separate'):get_inputs(self.db,'2026-04')

    def test_bank_reconciliation_matches_net_pay_instead_of_gross(self):
        save_input(self.db,self.inputs())
        report=preview(self.db,'2026-04')
        process(self.db,'2026-04','2026-04-30',report['preview_id'])
        brs=self.db.get_bank_reconciliation('Bank')
        self.assertEqual(brs['transactions'][0]['total_amount'],14600)
        self.assertEqual(brs['unreconciled_cheques_issued'],14600)
        self.assertEqual(brs['passbook_balance'],0)
        result=self.db.auto_reconcile_statement_csv('Bank','date,amount\n2026-05-01,14600')
        self.assertEqual(result['matched'],1)

    def test_foreign_company_preview_cannot_be_reused(self):
        save_input(self.db,self.inputs())
        one=preview(self.db,'2026-04')['preview_id']
        backup=self.db.create_local_backup(str(self.root/'copy'))
        from engine.accounting_db import EnterpriseAccountingDB
        other=EnterpriseAccountingDB(str(self.root/'other.db'),seed_demo=False)
        try:
            other.restore_local_backup(backup)
            with self.assertRaisesRegex(ValueError,'changed'):process(other,'2026-04','2026-04-30',one)
        finally:other.close()

    def test_ambiguous_bank_amounts_are_left_for_manual_reconciliation(self):
        save_input(self.db,self.inputs())
        report=preview(self.db,'2026-04')
        process(self.db,'2026-04','2026-04-30',report['preview_id'])
        self.db.add_voucher('Payment','2026-04-30','Separate same-amount payment',[
            {'ledger_name':'Wages','entry_type':'Dr','amount':14600},
            {'ledger_name':'Bank','entry_type':'Cr','amount':14600}])
        result=self.db.auto_reconcile_statement_csv('Bank','date,amount\n2026-05-01,14600')
        self.assertEqual(result['matched'],0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM bank_reconciliation WHERE status='Unreconciled'").fetchone()[0],2)


if __name__=='__main__':unittest.main()
