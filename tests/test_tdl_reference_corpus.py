"""
TDL Reference Corpus & Comparison Test Framework
Tests authorized TDL fixtures across parsing, AST definition resolution,
runtime expression evaluation, modifier inheritance, and failure isolation.
Produces a structured compatibility audit log.
"""

import os
import sys
import unittest
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.accounting_db import EnterpriseAccountingDB
from engine.runtime import TDLRuntime

CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdl_corpus")      # independent of the working folder

class TestTDLReferenceCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.symbols = TDLSymbolTable()
        cls.parser = TDLParser(cls.symbols)
        # Parse standard base definition first
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "definitions", "base.tdl")      # independent of the working folder
        with open(base_path, "r", encoding="utf-8") as f:
            cls.parser.parse(f.read())

        cls.db = EnterpriseAccountingDB(":memory:")
        cls.runtime = TDLRuntime(cls.symbols, cls.db)

    def _read_fixture(self, filename: str) -> str:
        fpath = os.path.join(CORPUS_DIR, filename)
        with open(fpath, "r", encoding="utf-8") as f:
            return f.read()

    def test_01_menu_overrides_fixture(self):
        code = self._read_fixture("01_menu_overrides.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 01: {diags}")
        self.parser.parse(code)

        gw = self.symbols.get_definition("Menu", "Gateway of Tally")
        self.assertIsNotNone(gw)
        labels = [it.get("label") for it in gw.items]
        self.assertIn("Branch Consolidation", labels)
        self.assertIn("Executive Dashboard", labels)
        self.assertNotIn("Ratio Analysis", labels)

    def test_02_multiform_nested_parts_fixture(self):
        code = self._read_fixture("02_multiform_nested_parts.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 02: {diags}")
        self.parser.parse(code)

        rep = self.symbols.get_definition("Report", "CorporateSummary")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.get_attr("Title", strip_quotes=True), "Corporate Multi-Part Summary")

        form = self.symbols.get_definition("Form", "CorporateForm")
        self.assertIsNotNone(form)
        parts = [p.strip() for p in form.get_attr("Part", "").split(",") if p.strip()]
        self.assertIn("CorpHeaderPart", parts)
        self.assertIn("CorpBodyPart", parts)
        self.assertIn("CorpFooterPart", parts)

        f_stamp = self.symbols.get_definition("Field", "CorpFooterAuditStamp")
        self.assertIsNotNone(f_stamp)
        self.assertIn("Companies Act 2013", f_stamp.get_attr("Set as"))

    def test_03_dynamic_collections_fixture(self):
        code = self._read_fixture("03_dynamic_collections.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 03: {diags}")
        self.parser.parse(code)

        coll = self.symbols.get_definition("Collection", "HighValueDebtors")
        self.assertIsNotNone(coll)
        self.assertEqual(coll.get_attr("Type"), "Ledger")
        self.assertEqual(coll.get_attr("ChildOf", strip_quotes=True), "Sundry Debtors")
        self.assertEqual(coll.get_attr("Filter"), "IsHighBalance")

        inv_coll = self.symbols.get_definition("Collection", "TopInventoryItems")
        self.assertIsNotNone(inv_coll)
        self.assertEqual(inv_coll.get_attr("Type"), "StockItem")

    def test_04_formulas_and_functions_fixture(self):
        code = self._read_fixture("04_formulas_and_functions.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 04: {diags}")
        self.parser.parse(code)

        self.assertIn("ComputeCGST", self.symbols.formulas)
        self.assertIn("ComputeTotalWithTax", self.symbols.formulas)

        # Evaluate runtime formulas
        ctx = {"Amount": 10000.0, "Name": "Tally Test", "ClosingBalance": -45000.0}
        cgst = self.runtime.evaluate_expression("@@ComputeCGST", ctx)
        self.assertEqual(cgst, 900.0)

        upper_name = self.runtime.evaluate_expression("@@FormatUpperCaseName", ctx)
        self.assertEqual(upper_name, "TALLY TEST")

        abs_val = self.runtime.evaluate_expression("@@AbsoluteVariance", ctx)
        self.assertEqual(abs_val, 45000.0)

        # Test conditional
        ctx_normal = {"ClosingBalance": 50000.0}
        self.assertEqual(self.runtime.evaluate_expression("@@IsDebtorOverLimit", ctx_normal), "NORMAL")
        ctx_over = {"ClosingBalance": 150000.0}
        self.assertEqual(self.runtime.evaluate_expression("@@IsDebtorOverLimit", ctx_over), "OVER LIMIT")

    def test_05_udf_voucher_fields_fixture(self):
        code = self._read_fixture("05_udf_voucher_fields.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 05: {diags}")
        self.parser.parse(code)

        self.assertIn("VehicleRegNo", self.symbols.udfs)
        self.assertEqual(self.symbols.udfs["VehicleRegNo"]["id"], "1001")

        # Evaluate field with UDF data in context
        ctx = {"udf_data": {"VehicleRegNo": "KA-01-MJ-9988", "EWayBillNo": "EWB-8833992211"}}
        v_reg = self.runtime.evaluate_expression("$VehicleRegNo", ctx)
        self.assertEqual(v_reg, "KA-01-MJ-9988")
        ewb = self.runtime.evaluate_expression("$EWayBillNo", ctx)
        self.assertEqual(ewb, "EWB-8833992211")

    def test_06_custom_invoice_printing_fixture(self):
        code = self._read_fixture("06_custom_invoice_printing.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 06: {diags}")
        self.parser.parse(code)

        rep = self.symbols.get_definition("Report", "CustomInvoicePrint")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.get_attr("Title", strip_quotes=True), "Tax Invoice Formal Print Preview")

        line = self.symbols.get_definition("Line", "InvItemRowLine")
        self.assertIsNotNone(line)
        fields = [f.strip() for f in line.get_attr("Field", "").split(",") if f.strip()]
        self.assertIn("InvItemDesc", fields)
        self.assertIn("InvItemHSN", fields)
        self.assertIn("InvItemTotal", fields)

    def test_07_button_key_actions_fixture(self):
        code = self._read_fixture("07_button_key_actions.tdl")
        diags = self.parser.validate_syntax(code)
        self.assertEqual(len(diags), 0, f"Syntax errors in fixture 07: {diags}")
        self.parser.parse(code)

        self.assertIn("ExportPDFBtn", self.symbols.buttons)
        btn = self.symbols.buttons["ExportPDFBtn"]
        self.assertEqual(btn.get("Title").strip('"\''), "Export PDF")
        self.assertEqual(btn.get("Key"), "Alt+P")

        self.assertIn("CustomCloseKey", self.symbols.keys)

    def test_08_extension_conflicts_fixture(self):
        code = self._read_fixture("08_extension_conflicts.tdl")
        self.parser.parse(code)

        # Verify '!' modifier replaced the original report definition
        rep = self.symbols.get_definition("Report", "ConflictTestReport")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.get_attr("Title", strip_quotes=True), "Replaced Completely by Extension B")
        self.assertEqual(rep.get_attr("Form"), "ConflictTestFormB")

if __name__ == "__main__":
    unittest.main()
