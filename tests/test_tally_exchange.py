"""App-level Tally import/export: background jobs, validation, safe file export (synthetic data)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import tally_exchange  # noqa: E402
from engine.accounting_db import EnterpriseAccountingDB  # noqa: E402
from engine.company_store import CompanyStore  # noqa: E402
from engine.tally_live import import_from_tally  # noqa: E402
from test_tally_live_import import CO, FakeTally  # noqa: E402
from test_tally_xml_import import MASTERS, day_book, entry, envelope, voucher, write  # noqa: E402


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.src = self.root / 'src'
        self.src.mkdir()
        write(self.src, 'Master.xml', envelope(MASTERS))
        write(self.src, 'DayBook.xml', envelope(day_book()))
        self.store = CompanyStore(str(self.data), str(self.data / 'company-path.json'), explicit=True)

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, job, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            info = tally_exchange.status(job)
            if info['state'] != 'running':
                return info
            time.sleep(0.1)
        self.fail('import job did not finish')

    def test_files_import_runs_in_the_background_and_reports_verification(self):
        job = tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})['job']
        info = self.wait(job)
        self.assertEqual(info['state'], 'done', info)
        result = info['result']
        self.assertTrue(result['verification_ok'])
        self.assertEqual(result['failed_checks'], [])
        self.assertEqual(result['counts']['vouchers_created'], 5)
        keys = [c['storage_key'] for c in self.store.catalogue()['companies']]
        self.assertEqual(keys, [result['storage_key']])

    def test_replay_of_a_live_capture_imports_offline(self):
        cap = self.root / 'capture'
        import_from_tally(self.root / 'data_live', CO, endpoint='http://127.0.0.1:9000', transport=FakeTally(), capture_dir=cap) \
            if (self.root / 'data_live').mkdir() is None else None
        job = tally_exchange.start(self.store, {'mode': 'replay', 'company': CO, 'replay_dir': str(cap)})['job']
        info = self.wait(job)
        self.assertEqual(info['state'], 'done', info)
        self.assertTrue(info['result']['verification_ok'])
        self.assertEqual(info['result']['mode'], 'replay')
        self.assertEqual(info['result']['requests'], 0)

    def test_requests_are_validated_before_anything_starts(self):
        bad = [({}, 'Choose a mode'),
               ({'mode': 'files', 'folder': 'relative/path'}, 'absolute'),
               ({'mode': 'files', 'folder': str(self.data)}, 'differ'),
               ({'mode': 'files', 'folder': str(self.root / 'missing')}, None),
               ({'mode': 'live', 'company': CO}, 'Confirm that you saved'),
               ({'mode': 'live', 'company': '', 'confirm_live': True}, 'Company name'),
               ({'mode': 'live', 'company': CO, 'confirm_live': True, 'capture_dir': 'relative'}, 'absolute'),
               ({'mode': 'replay', 'company': CO, 'replay_dir': str(self.root / 'nope')}, None)]
        for data, text in bad:
            with self.assertRaises((ValueError, OSError)) as ctx:
                tally_exchange.start(self.store, data)
            if text:
                self.assertIn(text, str(ctx.exception))

    def test_only_one_import_runs_at_a_time(self):
        self.assertTrue(tally_exchange._lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(ValueError, 'already running'):
                tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})
        finally:
            tally_exchange._lock.release()
        with self.assertRaisesRegex(ValueError, 'Unknown import job'):
            tally_exchange.status('nope')

    def test_a_native_tally_folder_is_never_a_data_folder(self):
        for name in ('Company.1800', 'Manager.1800'):
            (self.data / name).write_bytes(b'\0' * 512)
        with self.assertRaisesRegex(ValueError, 'native Tally files'):
            tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})

    def test_failed_import_is_reported_as_an_error_job_not_a_crash(self):
        write(self.src, 'DayBook.xml', envelope([voucher(9, 'Journal', '20260410', [entry('Ghost', '-1.00'), entry('Cash', '1.00')])]))
        info = self.wait(tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})['job'])
        self.assertEqual(info['state'], 'error')
        self.assertIn('absent from the master export', info['error'])

    def test_export_writes_a_file_and_refuses_to_overwrite(self):
        info = self.wait(tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})['job'])
        key = info['result']['storage_key']
        out = tally_exchange.export_to_file(self.store, None, {'storage_key': key, 'path': str(self.root)})
        target = Path(out['path'])
        self.assertEqual(target.name, 'Synthetic Traders.xml')
        self.assertEqual(out['vouchers'], 5)
        self.assertEqual(target.stat().st_size, out['bytes'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            tally_exchange.export_to_file(self.store, None, {'storage_key': key, 'path': str(self.root)})
        again = tally_exchange.export_to_file(self.store, None, {'storage_key': key, 'path': str(self.root), 'overwrite': True})
        self.assertEqual(again['sha256'], out['sha256'])               # deterministic
        for bad in ('relative.xml', str(self.root / 'no_such_dir' / 'x.xml'), ''):
            with self.assertRaises(ValueError):
                tally_exchange.export_to_file(self.store, None, {'storage_key': key, 'path': bad})
        self.assertEqual([p.name for p in self.root.glob('*.tmp')], [])

    def test_exported_file_imports_back_and_vouchers_only_files_use_existing_ledgers(self):
        info = self.wait(tally_exchange.start(self.store, {'mode': 'files', 'folder': str(self.src)})['job'])
        key = info['result']['storage_key']
        exported = Path(tally_exchange.export_to_file(self.store, None, {'storage_key': key, 'path': str(self.root)})['path'])
        # Re-importing the same file is a no-op (GUID-keyed).
        again = tally_exchange.import_xml_text(self.store, exported.read_text(encoding='utf-8'))
        self.assertTrue(again['success'])
        self.assertEqual(again['summary']['counts'].get('vouchers_unchanged'), 5)
        self.assertEqual(len(self.store.catalogue()['companies']), 1)
        # A file holding only a new voucher may reference ledgers that already exist in the company.
        only = envelope([voucher(30, 'Journal', '20260430', [entry('Rent', '-2.00'), entry('Cash', '2.00')], 'J/30')])
        result = tally_exchange.import_xml_text(self.store, only)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['summary']['counts'].get('vouchers_created'), 1)
        db = EnterpriseAccountingDB(str(next(self.data.glob('1*/company.db'))), seed_demo=False)
        try:
            self.assertEqual(db.conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0], 6)
        finally:
            db.close()
        with self.assertRaises(ValueError):
            tally_exchange.import_xml_text(self.store, '   ')


if __name__ == '__main__':
    unittest.main()
