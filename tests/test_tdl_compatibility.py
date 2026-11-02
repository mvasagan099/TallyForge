"""
TDL Language & Runtime Compatibility Test Suite (Phase 5.4)
Comprehensive verification of TDL AST parsing, '#' modifier inheritance,
dotted object field resolution, User Defined Fields (UDF), dynamic collection filtering,
and TDL Laboratory compatibility reporting.
"""

import unittest
import os
import tempfile
from engine.tdl_parser import TDLParser, TDLSymbolTable
from engine.runtime import TDLRuntime
from engine.accounting_db import EnterpriseAccountingDB
from engine.tdl_laboratory import TDLLaboratory


class TestTDLCompatibility(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temp_db_fd, cls.temp_db_path = tempfile.mkstemp(suffix="_tdl_compat.db")
        os.close(cls.temp_db_fd)
        cls.db = EnterpriseAccountingDB(cls.temp_db_path)
        cls.symbols = TDLSymbolTable()
        cls.parser = TDLParser(cls.symbols)

        # Parse base definition file
        base_path = os.path.join(os.path.dirname(__file__), "..", "definitions", "base.tdl")
        if os.path.exists(base_path):
            cls.parser.parse_file(base_path)

        cls.runtime = TDLRuntime(cls.symbols, cls.db)
        cls.lab = TDLLaboratory(cls.symbols, cls.runtime, cls.parser)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        if os.path.exists(cls.temp_db_path):
            os.remove(cls.temp_db_path)

    # 1. Parse Reference TDL AST
    def test_01_parse_reference_tdl_ast(self):
        # Verify definitions from base.tdl exist in symbol table
        menu = self.symbols.get_definition("Menu", "Gateway of Tally")
        self.assertIsNotNone(menu)
        self.assertGreater(len(menu.items), 5)

        tb_rep = self.symbols.get_definition("Report", "TrialBalance")
        self.assertIsNotNone(tb_rep)
        self.assertEqual(tb_rep.get_attr("Title"), '"Trial Balance"')

    # 2. Modifier Inheritance and Override (# modifier)
    def test_02_modifier_inheritance_and_override(self):
        mod_tdl = """
        [#Menu: Gateway of Tally]
        Add : Item : "Executive Audit KPI" : Display : ExecAuditReport
        SubTitle : "Certified Offline 2026"

        [Report: ExecAuditReport]
        Title: "Executive Audit Summary"
        Form: ExecAuditForm

        [Form: ExecAuditForm]
        Part: ExecAuditPart

        [Part: ExecAuditPart]
        Line: ExecAuditLine

        [Line: ExecAuditLine]
        Field: SimpleField
        """
        self.parser.parse_string(mod_tdl)
        menu = self.symbols.get_definition("Menu", "Gateway of Tally")
        self.assertIsNotNone(menu)
        has_exec_item = any(item.get("label") == "Executive Audit KPI" for item in menu.items)
        self.assertTrue(has_exec_item)
        self.assertEqual(menu.get_attr("SubTitle"), '"Certified Offline 2026"')

    # 3. Dotted Object Field Access Resolution ($Object.Field)
    def test_03_dotted_object_resolution(self):
        context = {
            "Ledger": {
                "Name": "State Bank of India",
                "ClosingBalance": 125000.50,
                "Group": "Bank Accounts"
            },
            "Party": {
                "State": "Maharashtra",
                "GSTIN": "27AAACB1234F1Z1"
            }
        }
        res_bal = self.runtime.evaluate_expression("$Ledger.ClosingBalance", context)
        self.assertEqual(res_bal, 125000.50)

        res_state = self.runtime.evaluate_expression("$Party.State", context)
        self.assertEqual(res_state, "Maharashtra")

    # 4. User Defined Field (UDF) Evaluation
    def test_04_udf_field_evaluation(self):
        context = {
            "name": "Custom Invoice Line",
            "amount": 45000.0,
            "udf_data": {
                "CustomApprovalNote": "Approved by CFO on 2026-10-31",
                "HSNOverride": "84713010"
            }
        }
        val_note = self.runtime.evaluate_expression("$CustomApprovalNote", context)
        self.assertEqual(val_note, "Approved by CFO on 2026-10-31")

        val_hsn = self.runtime.evaluate_expression("$HSNOverride", context)
        self.assertEqual(val_hsn, "84713010")

    # 5. Dynamic Collection Queries (Type, ChildOf, Filter, Sort)
    def test_05_dynamic_collection_filtering(self):
        tdl_coll = """
        [Collection: FilteredStockItems]
        Type: StockItem
        ChildOf: Primary
        Sort: name
        """
        self.parser.parse_string(tdl_coll)
        items = self.runtime.resolve_collection("FilteredStockItems")
        self.assertIsInstance(items, list)
        self.assertGreater(len(items), 0)

        # Check sorting order
        names = [i["name"] for i in items if "name" in i]
        self.assertEqual(names, sorted(names))

    # 6. Conflict Detection without '#' Modifier
    def test_06_conflict_detection_without_hash(self):
        # Attempting to declare an existing Report without '#' should flag a conflict in laboratory
        conflicting_tdl = """
        [Report: TrialBalance]
        Title: "Overwritten Trial Balance"
        """
        report = self.lab.generate_compatibility_report(conflicting_tdl, "ConflictTest")
        self.assertIn("conflicts_detected", report)
        self.assertGreater(len(report["conflicts_detected"]), 0)
        self.assertIn("TrialBalance", report["conflicts_detected"][0]["name"])

    # 7. Compatibility Report Generation API
    def test_07_compatibility_report_generation(self):
        valid_tdl = """
        [Report: CustomProfitKPI]
        Title: "KPI Profit Dashboard"
        Form: CustomProfitForm

        [Form: CustomProfitForm]
        Part: CustomProfitPart

        [Part: CustomProfitPart]
        Line: CustomProfitLine

        [Line: CustomProfitLine]
        Field: SimpleField

        [Field: SimpleField]
        Set as: $$LocaleString($Amount * 1.18)
        """
        report = self.lab.generate_compatibility_report(valid_tdl, "CustomProfitKPI")
        self.assertEqual(report["parsing_result"], "SUCCESS")
        self.assertGreaterEqual(report["compatibility_score"], 80.0)
        self.assertEqual(len(report["conflicts_detected"]), 0)
        self.assertGreater(report["total_definitions"], 0)

    # 8. Expression Evaluator with Built-in Functions & Precedence
    def test_08_expression_parser_and_builtins(self):
        # Math with variable
        val1 = self.runtime.evaluate_expression("$Rate * $Qty", {"rate": 500.0, "qty": 12.0})
        self.assertEqual(val1, 6000.0)

        # Built-in LocaleString
        val2 = self.runtime.evaluate_expression("$$LocaleString(150000)", {})
        self.assertEqual(val2, "₹150,000.00")

        # Built-in IsEmpty
        val3 = self.runtime.evaluate_expression("$$IsEmpty('')", {})
        self.assertTrue(val3)

        # Conditional Expression
        val4 = self.runtime.evaluate_expression("If $Amount > 1000 Then 'HIGH' Else 'LOW'", {"amount": 2500.0})
        self.assertEqual(val4, "HIGH")


if __name__ == "__main__":
    unittest.main()
