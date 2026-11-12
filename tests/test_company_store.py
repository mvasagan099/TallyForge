"""Company-file isolation, path persistence, discovery and legacy safety."""
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.company_store import CompanyStore
from engine.accounting_db import EnterpriseAccountingDB


class CompanyStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.settings = self.root / 'settings' / 'company-path.json'
        self.store = CompanyStore(self.root, self.settings)

    def tearDown(self):
        self.tmp.cleanup()

    def test_separate_files_preserve_isolated_books(self):
        first = self.store.create({'name': 'Alpha', 'gstin': '', 'currency_symbol': '$'})
        second = self.store.create({'name': 'Beta'})
        self.assertEqual(first, '10000:1')
        self.assertEqual(second, '10001:1')
        a = self.store.open(first)
        a.add_ledger('Alpha only', 'Capital Account', 100)
        self.assertEqual(a.get_stock_items(), [])
        self.assertEqual(len(a.get_companies()), 1)
        a.close()
        b = self.store.open(second)
        self.assertNotIn('Alpha only', [l['name'] for l in b.get_ledgers()])
        self.assertEqual(b.get_company()['name'], 'Beta')
        b.close()
        a = self.store.open(first)
        self.assertIn('Alpha only', [l['name'] for l in a.get_ledgers()])
        self.assertEqual(a.get_company()['currency_symbol'], '$')
        a.close()
        self.assertEqual(len(self.store.catalogue()['companies']), 2)

    def test_path_is_persisted_and_limits_catalogue(self):
        self.store.create({'name': 'Original'})
        other = self.root / 'Other'
        other.mkdir()
        self.store.set_path(str(other))
        self.assertEqual(self.store.catalogue()['companies'], [])
        self.assertEqual(CompanyStore(self.root, self.settings).root, other)
        self.assertEqual(CompanyStore(self.root, self.settings, explicit=True).root, self.root)
        with self.assertRaises((ValueError, OSError)):
            self.store.set_path(str(other / 'missing'))
        self.assertEqual(self.store.root, other)
        with self.assertRaises(ValueError):
            self.store.open('10000:1')

    def test_legacy_and_corrupt_files_are_reported_without_conversion(self):
        db = EnterpriseAccountingDB(str(self.root / 'tally_data.db'))
        db.create_company('Legacy Two')
        db.close()
        before = (self.root / 'tally_data.db').read_bytes()
        broken = self.root / '10020'
        broken.mkdir()
        (broken / 'company.db').write_text('invalid sqlite')
        data = self.store.catalogue()
        self.assertEqual(len(data['companies']), 2)
        self.assertTrue(all(c['legacy'] for c in data['companies']))
        self.assertEqual(len(data['errors']), 1)
        self.assertEqual(before, (self.root / 'tally_data.db').read_bytes())
        db = self.store.open('legacy:2')
        self.assertEqual(db.get_company()['name'], 'Legacy Two')
        db.close()

    def test_validation_and_path_traversal_do_not_create_files(self):
        for data in ({'name': ''}, {'name': 'Bad', 'books_from': '2025-01-01'}):
            with self.assertRaises(ValueError):
                self.store.create(data)
        with self.assertRaises(ValueError):
            self.store.open('../outside:1')
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
