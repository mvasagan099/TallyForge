"""The TallyPrime-style shell is wired into the page and carries TallyPrime's shortcut keys (static checks only)."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'static'


class ClassicShellTests(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / 'index.html').read_text(encoding='utf-8')
        self.js = (STATIC / 'tp_classic.js').read_text(encoding='utf-8')

    def test_the_page_loads_the_classic_stylesheet_and_script_after_the_main_script(self):
        self.assertIn('/static/tp_classic.css', self.html)
        self.assertLess(self.html.index('/static/tally.js'), self.html.index('/static/tp_classic.js'))
        self.assertIn(':root[data-theme="classic"]', (STATIC / 'tp_classic.css').read_text(encoding='utf-8'))

    def test_voucher_shortcuts_follow_tallyprime(self):
        expected = {'Alt+F7': 'Stock Journal', 'Alt+F8': 'Delivery Note', 'Alt+F9': 'Receipt Note', 'Ctrl+F5': 'Rejections Out',
                    'Ctrl+F6': 'Rejections In', 'Ctrl+F7': 'Physical Stock', 'Ctrl+F8': 'Sales Order', 'Ctrl+F9': 'Purchase Order'}
        for combo, voucher in expected.items():
            self.assertRegex(self.js, rf'"{re.escape(combo)}": "{voucher}"')
        for combo in ('F10', 'F12', 'Alt+F2', 'Alt+F3', 'Ctrl+F3', 'Ctrl+F1', 'Ctrl+Q', 'Ctrl+E', 'Alt+M', 'Alt+X', 'Alt+D', 'Ctrl+H', 'Ctrl+D'):
            self.assertIn(f'"{combo}"', self.js)
        self.assertIn('"Alt+F6"', self.js)        # credit note and debit note keep Alt+F6 / Alt+F5 in the voucher list
        self.assertIn('"Alt+F5"', self.js)

    def test_the_gateway_menu_matches_tallyprime(self):
        for label in ('Create', 'Alter', 'Chart of Accounts', 'Vouchers', 'Day Book', 'Banking', 'Balance Sheet', 'Profit & Loss A/c',
                      'Stock Summary', 'Ratio Analysis', 'Display More Reports', 'Quit'):
            self.assertIn(f'label: "{label}"', self.js)

    def test_the_button_bar_lists_other_vouchers_and_configure(self):
        self.assertIn('openOtherVouchers()"><span class="fkey-label">F10</span>', self.html)
        self.assertIn('openConfig()"><span class="fkey-label">F12</span>Configure', self.html)

    def test_configuration_options_are_all_used(self):
        for option in ('style', 'homePanels', 'buttonBar', 'invoiceMode', 'acceptPrompt'):
            self.assertGreaterEqual(len(re.findall(rf'cfg\.{option}\b', self.js)), 2, option)

    def test_the_masters_editor_is_wired_in(self):
        self.assertIn('/static/masters_edit.js', self.html)
        editor = (STATIC / 'masters_edit.js').read_text(encoding='utf-8')
        for route in ('/api/masters/detail', '/api/masters/alter', '/api/masters/delete'):
            self.assertIn(route, editor)
        self.assertIn('"tp-master-modal"', self.js)        # Esc closes the editor instead of leaving the screen

    def test_f2_and_alt_f2_open_the_period_dialog(self):
        for needle in ('"Alt+F2"', 'openPeriodDialog', 'tp-period-modal', 'window.openDatePrompt = openPeriodDialog', 'tf_period'):
            self.assertIn(needle, self.js)
        for kind in ('units', 'godowns', 'cost_centres'):
            self.assertIn(kind, (STATIC / 'masters_edit.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
