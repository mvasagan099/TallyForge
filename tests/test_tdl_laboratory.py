"""
Test Suite: TDL Compatibility Laboratory & AST Verification
Covers:
- TDL AST generation across Report, Form, Part, Line, Field
- Definition Inspector for hierarchy, attributes, and child linkages
- Variable Inspector for runtime context parameter resolution
- Collection Inspector for schema and data source resolution
- # Modifier overrides for extending definitions without clobbering
- Syntax error diagnostics with exact line numbers
- Formula and mathematical expression evaluation
"""

import unittest
import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.tdl_parser import TDLSymbolTable, TDLParser
from engine.accounting_db import EnterpriseAccountingDB
from engine.runtime import TDLRuntime
from engine.tdl_laboratory import TDLLaboratory

class TestTDLLaboratory(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_tdl_lab.db")
        self.db = EnterpriseAccountingDB(self.db_path)
        self.symbols = TDLSymbolTable()
        self.parser = TDLParser(self.symbols)

        # Parse base.tdl and tdl_test_suite.tdl
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        base_tdl = os.path.join(base_dir, "definitions", "base.tdl")
        suite_tdl = os.path.join(base_dir, "definitions", "tdl_test_suite.tdl")

        if os.path.exists(base_tdl):
            self.parser.parse_file(base_tdl)
        if os.path.exists(suite_tdl):
            self.parser.parse_file(suite_tdl)

        self.runtime = TDLRuntime(self.symbols, self.db)
        self.lab = TDLLaboratory(self.symbols, self.db, self.runtime)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_01_tdl_parser_ast_generation(self):
        """Verifies AST construction from raw TDL source."""
        code = """
        [Report: CustomSalesSummary]
            Form: CustomSalesForm
            Title: "Custom Sales Analysis Report"

        [Form: CustomSalesForm]
            Part: CustomSalesPart

        [Part: CustomSalesPart]
            Line: CustomSalesTitleLine, CustomSalesDataLine

        [Line: CustomSalesDataLine]
            Fields: FldParty, FldAmount

        [Field: FldParty]
            Use: Name Field
            Set as: $Name

        [Field: FldAmount]
            Use: Amount Field
            Set as: $Amount
        """
        self.parser.parse_string(code)

        rep = self.symbols.get_definition("Report", "CustomSalesSummary")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.get_attr("Form"), "CustomSalesForm")
        self.assertEqual(rep.get_attr("Title"), '"Custom Sales Analysis Report"')

        form = self.symbols.get_definition("Form", "CustomSalesForm")
        self.assertIsNotNone(form)
        self.assertEqual(form.get_attr("Part"), "CustomSalesPart")

    def test_02_definition_inspector(self):
        """Inspects definition hierarchy and child links."""
        info = self.lab.inspect_definition("Report", "TrialBalance")
        self.assertTrue(info["found"])
        self.assertEqual(info["type"], "Report")
        self.assertEqual(info["name"], "TrialBalance")
        self.assertGreater(len(info["referenced_children"]), 0)
        self.assertEqual(info["referenced_children"][0]["type"], "Form")

    def test_03_variable_inspector_and_evaluation(self):
        """Evaluates dynamic expressions in context using Variable Inspector."""
        ctx = {"amount": 50000.0, "rate": 25000.0, "qty": 2.0, "party_name": "Apex Tech"}

        # Math formula: $Amount * 1.18 -> 59000.0
        res = self.lab.inspect_variable("$Amount * 1.18", ctx)
        self.assertEqual(res["resolved_value"], 59000.0)

        # String expression
        res_str = self.lab.inspect_variable("$Name", ctx)
        self.assertEqual(res_str["resolved_value"], "Apex Tech")

    def test_04_collection_inspector_data_source(self):
        """Inspects collection definitions and verifies data retrieval."""
        # Check LedgerCollection
        coll_info = self.lab.inspect_collection("LedgerCollection")
        self.assertEqual(coll_info["collection_name"], "LedgerCollection")
        self.assertGreater(coll_info["total_items"], 0)
        self.assertGreater(len(coll_info["schema"]), 0)
        schema_fields = [s["field"] for s in coll_info["schema"]]
        self.assertIn("name", schema_fields)
        self.assertIn("group_name", schema_fields)

    def test_05_definition_modifier_hash_symbol(self):
        """Verifies that [#Menu: ...] extends an existing definition without clobbering previous items."""
        menu = self.symbols.get_definition("Menu", "Gateway of Tally")
        initial_subdefs = len(menu.sub_definitions) if menu else 0

        # Apply modifier
        mod_code = """
        [#Menu: Gateway of Tally]
            Add: Item: "Custom Lab Diagnostic Report": Display: AuditEditLog
        """
        self.parser.parse_string(mod_code)

        menu_after = self.symbols.get_definition("Menu", "Gateway of Tally")
        self.assertIsNotNone(menu_after)
        self.assertGreaterEqual(len(menu_after.sub_definitions), initial_subdefs)

        # Check item exists in menu
        dict_rep = menu_after.to_dict()
        items = [it.get("label") for it in dict_rep.get("items", [])]
        self.assertIn("Custom Lab Diagnostic Report", items)

    def test_06_ast_syntax_error_diagnostics(self):
        """Tests that invalid TDL syntax produces line-numbered error diagnostics."""
        broken_code = """[Report: BrokenReport
            Form: MissingBracketForm
            Title: "Invalid syntax report"
        """
        diag = self.lab.run_compatibility_diagnostics(broken_code)
        self.assertFalse(diag["is_compatible"])
        self.assertGreater(len(diag["errors"]), 0)
        # Line number should be identified
        self.assertIn("line", diag["errors"][0])
        self.assertIn("message", diag["errors"][0])

    def test_07_custom_formula_and_udf_evaluation(self):
        """Tests conditional formula and math evaluation in TDL expressions."""
        ctx_high = {"amount": 75000.0}
        ctx_low = {"amount": 25000.0}

        # Test conditional formula: If $Amount > 50000 Then "High Value" Else "Standard"
        expr = 'If $Amount > 50000 Then "High Value" Else "Standard"'
        val_high = self.runtime.evaluate_expression(expr, ctx_high)
        val_low = self.runtime.evaluate_expression(expr, ctx_low)

        self.assertEqual(val_high, "High Value")
        self.assertEqual(val_low, "Standard")

if __name__ == "__main__":
    unittest.main()
