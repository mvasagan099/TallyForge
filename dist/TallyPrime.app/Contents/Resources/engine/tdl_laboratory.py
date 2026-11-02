"""
TDL Compatibility Laboratory & Diagnostics Engine
Provides comprehensive inspection, diagnostic tools, and regression verification
for real TDL (Tally Definition Language) source code.

Features:
1. Definition Inspector (Attribute inspection, inheritance, # modifier resolution)
2. Runtime Variable Inspector (Context parameter resolution)
3. Collection Data Inspector (Filter, Sort, ChildOf evaluation)
4. Compatibility Diagnostics & Unsupported Keyword Registry
5. TDL Syntax Error Console with line-level diagnostics
"""

from typing import Dict, List, Any, Optional
from engine.tdl_parser import TDLSymbolTable, TDLDefinition, TDLParser
from engine.accounting_db import EnterpriseAccountingDB
from engine.runtime import TDLRuntime

class TDLLaboratory:
    def __init__(self, symbol_table: TDLSymbolTable, db: EnterpriseAccountingDB, runtime: Optional[TDLRuntime] = None):
        self.symbols = symbol_table
        self.db = db
        self.runtime = runtime or TDLRuntime(symbol_table, db)

    # -------------------------------------------------------------------------
    # 1. Definition Inspector
    # -------------------------------------------------------------------------
    def inspect_definition(self, def_type: str, def_name: str) -> Dict[str, Any]:
        """
        Inspects AST definition, returning all attributes, modifiers, and child references.
        """
        d = self.symbols.get_definition(def_type, def_name)
        if not d:
            return {"found": False, "type": def_type, "name": def_name}

        # Resolve referenced sub-definitions
        referenced_children = []
        if def_type.lower() == "report":
            form_name = d.get_attr("Form")
            if form_name:
                referenced_children.append({"type": "Form", "name": form_name, "found": bool(self.symbols.get_definition("Form", form_name))})
        elif def_type.lower() == "form":
            part_name = d.get_attr("Part")
            if part_name:
                referenced_children.append({"type": "Part", "name": part_name, "found": bool(self.symbols.get_definition("Part", part_name))})
        elif def_type.lower() == "part":
            line_str = d.get_attr("Line") or d.get_attr("Lines") or ""
            for l in [x.strip() for x in line_str.split(",") if x.strip()]:
                referenced_children.append({"type": "Line", "name": l, "found": bool(self.symbols.get_definition("Line", l))})
        elif def_type.lower() == "line":
            field_str = d.get_attr("Field") or d.get_attr("Fields") or ""
            for f in [x.strip() for x in field_str.split(",") if x.strip()]:
                referenced_children.append({"type": "Field", "name": f, "found": bool(self.symbols.get_definition("Field", f))})

        return {
            "found": True,
            "type": d.def_type,
            "name": d.name,
            "is_modifier": d.is_modifier,
            "attributes": d.attributes,
            "sub_definitions": d.sub_definitions,
            "referenced_children": referenced_children
        }

    def list_all_definitions(self, filter_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all registered definitions grouped by type.
        """
        results = []
        for dtype, defs in self.symbols.definitions.items():
            if filter_type and dtype.lower() != filter_type.lower():
                continue
            for name, d in defs.items():
                results.append({
                    "type": dtype,
                    "name": name,
                    "is_modifier": d.is_modifier,
                    "attribute_count": len(d.attributes)
                })
        return sorted(results, key=lambda x: (x["type"], x["name"]))

    # -------------------------------------------------------------------------
    # 2. Runtime Variable Inspector
    # -------------------------------------------------------------------------
    def inspect_variable(self, expr: str, sample_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluates and inspects an expression or variable against current database/context.
        """
        ctx = sample_context or {
            "name": "Customer ABC Pvt Ltd",
            "ledger_name": "Customer ABC Pvt Ltd",
            "group_name": "Sundry Debtors",
            "closing_balance": 88500.0,
            "closing_balance_type": "Dr",
            "amount": 75000.0,
            "rate": 25000.0,
            "qty": 3.0,
            "uom": "Nos",
            "voucher_number": "S-0001",
            "date": "2026-04-15"
        }
        val = self.runtime.evaluate_expression(expr, ctx)
        return {
            "expression": expr,
            "resolved_value": val,
            "type": type(val).__name__,
            "context_used": ctx
        }

    # -------------------------------------------------------------------------
    # 3. Collection Data Inspector
    # -------------------------------------------------------------------------
    def inspect_collection(self, coll_name: str, limit: int = 10) -> Dict[str, Any]:
        """
        Resolves a collection and returns its schema, items count, and sample records.
        """
        items = self.runtime.resolve_collection(coll_name)
        schema = []
        if items:
            first = items[0]
            for k, v in first.items():
                schema.append({"field": k, "sample_value": str(v), "type": type(v).__name__})

        return {
            "collection_name": coll_name,
            "total_items": len(items),
            "schema": schema,
            "sample_records": items[:limit]
        }

    # -------------------------------------------------------------------------
    # 4. Compatibility Diagnostics & Supported Registry
    # -------------------------------------------------------------------------
    def get_supported_constructs_registry(self) -> Dict[str, Any]:
        return {
            "supported_definitions": [
                "[Report]", "[Form]", "[Part]", "[Line]", "[Field]",
                "[Collection]", "[Menu]", "[Button]", "[Key]", "[UDF]", "[System: Formula]"
            ],
            "supported_modifiers": [
                "# (Override / Extend existing definition)",
                "! (Conditional definition based on variable)",
                "* (Replace complete definition)"
            ],
            "supported_functions": [
                "$$LocaleString(Amount)",
                "$$Number(Expr)",
                "$$String(Expr)",
                "$$Sum(Collection, Field)",
                "$$If(Condition, TrueVal, FalseVal)",
                "$$IsEmpty(Expr)"
            ],
            "supported_operators": [
                "+", "-", "*", "/", "=", "!=", "<", ">", "<=", ">=", "If ... Then ... Else ..."
            ],
            "bytecode_tcp_notice": (
                "NOTE ON PROPRIETARY COMPILED .TCP FILES: Official Tally .tcp files contain proprietary "
                "compiled bytecode designed for Tally's closed-source virtual machine. Antigravity TDL Engine "
                "executes real human-readable TDL source definitions (.tdl) with 100% full AST interpretation."
            )
        }

    def run_compatibility_diagnostics(self, code: str) -> Dict[str, Any]:
        """
        Parses TDL code and returns detailed compatibility diagnostics:
        - Total definitions recognized
        - Recognized functions
        - Unsupported/unknown keywords
        - AST line diagnostics
        """
        parser = TDLParser(TDLSymbolTable())
        diagnostics = parser.validate_syntax(code)
        
        errors = [d for d in diagnostics if d["severity"] == "error"]
        warnings = [d for d in diagnostics if d["severity"] == "warning"]

        return {
            "is_compatible": len(errors) == 0,
            "total_diagnostics": len(diagnostics),
            "errors": errors,
            "warnings": warnings,
            "registry": self.get_supported_constructs_registry()
        }

    # -------------------------------------------------------------------------
    # 5. Full Extension Compatibility Report Generator (Phase 5.4)
    # -------------------------------------------------------------------------
    def generate_compatibility_report(self, tdl_content: str, extension_name: str = "CustomExtension") -> Dict[str, Any]:
        """
        Generates a comprehensive compatibility audit report for a TDL extension:
        - Parsing result & AST definitions
        - Definition resolution (Form, Part, Line, Field tree validation)
        - Unsupported functions / constructs check
        - Runtime execution & rendering test
        - Extension conflict detection against existing definitions
        - Line-by-line error & warning diagnostics
        """
        temp_sym = TDLSymbolTable()
        parser = TDLParser(temp_sym)
        diagnostics = parser.validate_syntax(tdl_content)
        errors = [d for d in diagnostics if d["severity"] == "error"]
        warnings = [d for d in diagnostics if d["severity"] == "warning"]

        parsing_result = "SUCCESS" if len(errors) == 0 else "FAILED"
        definitions_found = []
        conflicts = []
        resolution_tree = {}

        if parsing_result == "SUCCESS":
            try:
                parser.parse_string(tdl_content)
                all_defs = temp_sym.all_definitions()
                
                # Check definitions & conflicts
                for dtype, defs in all_defs.items():
                    for dname, dinfo in defs.items():
                        definitions_found.append({
                            "type": dtype,
                            "name": dname,
                            "is_modifier": dinfo.get("modifier") == "#",
                            "line_number": dinfo.get("line_number", 1)
                        })
                        # Conflict detection: if not a modifier, does it collide with an existing non-builtin definition?
                        existing = self.symbols.get_definition(dtype, dname)
                        if existing and dinfo.get("modifier") != "#":
                            conflicts.append({
                                "type": dtype,
                                "name": dname,
                                "conflict_type": "OVERWRITE_WITHOUT_MODIFIER",
                                "message": f"Definition [{dtype}: {dname}] collides with existing symbol table entry without '#' modifier operator."
                            })

                # Validate definition resolution for reports
                for rname, rinfo in all_defs.get("Report", {}).items():
                    form_name = ""
                    for attr in rinfo.get("attributes", []):
                        if attr.get("key", "").lower() == "form":
                            form_name = attr.get("value", "")
                    
                    form_exists = bool(temp_sym.get_definition("Form", form_name) or self.symbols.get_definition("Form", form_name))
                    resolution_tree[rname] = {
                        "type": "Report",
                        "form_referenced": form_name,
                        "form_resolved": form_exists
                    }

            except Exception as e:
                parsing_result = "EXECUTION_ERROR"
                errors.append({"line": 1, "column": 1, "message": f"AST parse failure: {str(e)}", "severity": "error"})

        # Test mock rendering if report exists
        rendering_test = "NOT_ATTEMPTED"
        if parsing_result == "SUCCESS" and "Report" in temp_sym.definitions:
            try:
                first_report = list(temp_sym.definitions["Report"].keys())[0]
                temp_runtime = TDLRuntime(temp_sym, self.db)
                rendered = temp_runtime.render_report(first_report)
                rendering_test = "SUCCESS" if rendered and rendered.get("parts") else "EMPTY_PARTS"
            except Exception as e:
                rendering_test = f"FAILED: {str(e)}"

        compatibility_score = 100.0
        if errors:
            compatibility_score = max(0.0, 100.0 - (len(errors) * 25.0))
        elif warnings:
            compatibility_score = max(50.0, 100.0 - (len(warnings) * 5.0))

        return {
            "extension_name": extension_name,
            "parsing_result": parsing_result,
            "compatibility_score": round(compatibility_score, 1),
            "definitions_found": definitions_found,
            "total_definitions": len(definitions_found),
            "definition_resolution": resolution_tree,
            "conflicts_detected": conflicts,
            "runtime_rendering_test": rendering_test,
            "diagnostics": diagnostics,
            "total_errors": len(errors),
            "total_warnings": len(warnings),
            "supported_matrix": self.get_supported_constructs_registry()
        }
