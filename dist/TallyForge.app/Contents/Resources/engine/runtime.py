"""
TDL Runtime Execution Engine
Executes TDL AST (Report -> Form -> Part -> Line -> Field) against the Enterprise Accounting Database.
Supports all core 2026 Tally reports:
- Balance Sheet, Profit & Loss, Trial Balance, Day Book
- General Ledger Account Statement (running Dr/Cr balances)
- Cash Flow Statement (direct cash/bank inflows & outflows)
- Stock Summary, Godown Summary, Ratio Analysis, Cash/Bank Summary
- Cost Centres & Cost Categories Breakdown
- Cash Book, Bank Book, Sales Register, Purchase Register
- GSTR-1, GSTR-3B Tax Liability
- Receivables & Payables Ageing Analysis (0-30, 31-60, 61-90, 90+ days)
- Bank Reconciliation (BRS)
- Payroll Pay Sheet
- Statutory Edit Log Audit Trail
"""

import re
from decimal import Decimal
from typing import Dict, List, Any, Optional
from engine.tdl_parser import TDLSymbolTable, TDLDefinition
from engine.accounting_db import EnterpriseAccountingDB

class TDLRuntime:
    def __init__(self, symbol_table: TDLSymbolTable, db: EnterpriseAccountingDB):
        self.symbols = symbol_table
        self.db = db

    def _eval_tdl_condition(self, cond: str, context: Dict[str, Any]) -> bool:
        cond = cond.strip()
        # Handle operators: <=, >=, !=, =, <, >
        for op in ["<=", ">=", "!=", "=", "<", ">"]:
            if op in cond:
                parts = cond.split(op, 1)
                left_v = self.evaluate_expression(parts[0].strip(), context)
                right_v = self.evaluate_expression(parts[1].strip(), context)
                try:
                    l_num = float(left_v)
                    r_num = float(right_v)
                    if op == "=": return l_num == r_num
                    if op == "!=": return l_num != r_num
                    if op == "<": return l_num < r_num
                    if op == ">": return l_num > r_num
                    if op == "<=": return l_num <= r_num
                    if op == ">=": return l_num >= r_num
                except (ValueError, TypeError):
                    if op == "=": return str(left_v).strip().lower() == str(right_v).strip().lower()
                    if op == "!=": return str(left_v).strip().lower() != str(right_v).strip().lower()
        res = self.evaluate_expression(cond, context)
        return bool(res) and str(res).strip().lower() not in ["0", "0.0", "false", "no", "none", ""]

    def evaluate_expression(self, expr: str, context: Dict[str, Any]) -> Any:
        expr = expr.strip()
        if not expr:
            return ""

        # String literals
        if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
            return expr[1:-1]

        # Dotted object field access e.g. $Ledger.ClosingBalance or $Party.State
        if re.match(r"^\$[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$", expr):
            parts = expr[1:].split(".")
            curr = context
            for p in parts:
                if isinstance(curr, dict):
                    # Check case-insensitive key
                    found_k = None
                    for k in curr.keys():
                        if k.lower() == p.lower():
                            found_k = k
                            break
                    curr = curr.get(found_k, "") if found_k else ""
                else:
                    curr = ""
                    break
            return curr

        # Check UDF (User Defined Field) in context
        if expr.startswith("$"):
            vname = expr[1:].strip()
            udf = context.get("udf_data")
            if isinstance(udf, dict) and vname in udf:
                return udf[vname]

        # Check registered system formulas
        clean_formula = expr[2:].strip() if expr.startswith("@@") else expr
        if clean_formula in self.symbols.formulas:
            return self.evaluate_expression(self.symbols.formulas[clean_formula], context)

        # Built-in TDL Conditional Expression: If <cond> Then <val1> Else <val2>
        cond_match = re.match(r"(?i)^if\s+(.+?)\s+then\s+(.+?)\s+else\s+(.+)$", expr)
        if cond_match:
            c_part = cond_match.group(1).strip()
            t_part = cond_match.group(2).strip()
            e_part = cond_match.group(3).strip()
            cond_res = self._eval_tdl_condition(c_part, context)
            return self.evaluate_expression(t_part if cond_res else e_part, context)

        # Built-in TDL Functions: $$Function(args)
        if expr.startswith("$$"):
            fn_match = re.match(r"^\$\$([A-Za-z0-9_]+)\((.*)\)$", expr)
            if fn_match:
                fn_name = fn_match.group(1).lower()
                args_str = fn_match.group(2).strip()
                args = [a.strip() for a in args_str.split(",") if a.strip()]

                if fn_name == "if":
                    if len(args) >= 3:
                        cond_res = self._eval_tdl_condition(args[0], context)
                        return self.evaluate_expression(args[1] if cond_res else args[2], context)

                elif fn_name == "localestring":
                    inner_val = self.evaluate_expression(args[0], context) if args else ""
                    try:
                        num = float(inner_val)
                        return f"₹{num:,.2f}"
                    except (ValueError, TypeError):
                        return str(inner_val)

                elif fn_name == "isempty":
                    inner_val = self.evaluate_expression(args[0], context) if args else ""
                    return not bool(inner_val)

                elif fn_name == "number":
                    inner_val = self.evaluate_expression(args[0], context) if args else 0
                    try:
                        return float(inner_val)
                    except (ValueError, TypeError):
                        return 0.0

                elif fn_name == "string":
                    inner_val = self.evaluate_expression(args[0], context) if args else ""
                    return str(inner_val)

                elif fn_name == "uppercase":
                    inner_val = self.evaluate_expression(args[0], context) if args else ""
                    return str(inner_val).upper()

                elif fn_name == "lowercase":
                    inner_val = self.evaluate_expression(args[0], context) if args else ""
                    return str(inner_val).lower()

                elif fn_name == "round":
                    inner_val = self.evaluate_expression(args[0], context) if args else 0
                    decimals = int(self.evaluate_expression(args[1], context)) if len(args) > 1 else 0
                    try:
                        return round(float(inner_val), decimals)
                    except (ValueError, TypeError):
                        return 0.0

                elif fn_name == "min":
                    if len(args) >= 2:
                        v1 = float(self.evaluate_expression(args[0], context))
                        v2 = float(self.evaluate_expression(args[1], context))
                        return min(v1, v2)
                    return 0.0

                elif fn_name == "max":
                    if len(args) >= 2:
                        v1 = float(self.evaluate_expression(args[0], context))
                        v2 = float(self.evaluate_expression(args[1], context))
                        return max(v1, v2)
                    return 0.0

                elif fn_name in ("yearofdate", "year"):
                    d_val = str(self.evaluate_expression(args[0], context) if args else "")
                    parts = d_val.split("-")
                    return parts[0] if parts else ""

                elif fn_name in ("monthofdate", "month"):
                    d_val = str(self.evaluate_expression(args[0], context) if args else "")
                    parts = d_val.split("-")
                    return parts[1] if len(parts) > 1 else ""

                elif fn_name in ("dayofdate", "day"):
                    d_val = str(self.evaluate_expression(args[0], context) if args else "")
                    parts = d_val.split("-")
                    return parts[2] if len(parts) > 2 else ""

                elif fn_name == "sum":
                    # $$Sum(CollectionName, FieldName)
                    if len(args) >= 2:
                        coll = self.resolve_collection(args[0])
                        field = args[1].lstrip("$")
                        total = 0.0
                        for it in coll:
                            v = it.get(field, 0.0)
                            try:
                                total += float(v)
                            except (ValueError, TypeError):
                                pass
                        return total
                    return 0.0

                elif fn_name == "abs":
                    inner_val = self.evaluate_expression(args[0], context) if args else 0
                    try:
                        return abs(float(inner_val))
                    except (ValueError, TypeError):
                        return 0.0

                elif fn_name in ("stringlength", "strlen"):
                    inner_val = str(self.evaluate_expression(args[0], context) if args else "")
                    return len(inner_val)

                elif fn_name == "exact":
                    if len(args) >= 2:
                        s1 = str(self.evaluate_expression(args[0], context))
                        s2 = str(self.evaluate_expression(args[1], context))
                        return s1 == s2
                    return False

                elif fn_name == "padstring":
                    if len(args) >= 2:
                        val = str(self.evaluate_expression(args[0], context))
                        width = int(self.evaluate_expression(args[1], context))
                        pad_char = str(self.evaluate_expression(args[2], context)) if len(args) > 2 else " "
                        return val.ljust(width, pad_char[:1] or " ")
                    return ""

                elif fn_name == "date":
                    import datetime
                    return datetime.date.today().isoformat()

                elif fn_name == "currentcompany":
                    comp = self.db.get_company()
                    return comp.get("name", "Default Company") if comp else "Default Company"

                elif fn_name == "issysname":
                    val = str(self.evaluate_expression(args[0], context) if args else "").lower()
                    return val in ("primary", "profit & loss a/c", "cash", "bank")

                elif fn_name == "filtercount":
                    if len(args) >= 2:
                        coll = self.resolve_collection(args[0])
                        field = args[1].lstrip("$")
                        return len([it for it in coll if it.get(field)])
                    return 0

                elif fn_name == "collectionfield":
                    if len(args) >= 3:
                        field = args[0].lstrip("$")
                        idx = int(self.evaluate_expression(args[1], context)) - 1
                        coll = self.resolve_collection(args[2])
                        if 0 <= idx < len(coll):
                            return coll[idx].get(field, "")
                    return ""

        # Case-insensitive context lookup helper
        def _lookup_ctx(k: str, default: Any = None) -> Any:
            k_clean = k.lower().replace(" ", "").replace("_", "")
            for ck, cv in context.items():
                if ck.lower().replace(" ", "").replace("_", "") == k_clean:
                    return cv
            return default

        # Helper mapping for single variables
        mapping = {
            "name": _lookup_ctx("name") or _lookup_ctx("ledgername") or _lookup_ctx("itemname") or _lookup_ctx("partyname") or "",
            "ledgername": _lookup_ctx("ledgername") or _lookup_ctx("name") or "",
            "groupname": _lookup_ctx("groupname") or "",
            "parent": _lookup_ctx("parent") or "",
            "closingbalance": _lookup_ctx("closingbalance", 0.0),
            "closingbalancetype": _lookup_ctx("closingbalancetype", "Dr"),
            "openingbalance": _lookup_ctx("openingbalance", 0.0),
            "openingtype": _lookup_ctx("openingtype", "Dr"),
            "dramount": _lookup_ctx("dramount") or _lookup_ctx("debit", 0.0),
            "cramount": _lookup_ctx("cramount") or _lookup_ctx("credit", 0.0),
            "date": _lookup_ctx("date", ""),
            "vouchernumber": _lookup_ctx("vouchernumber", ""),
            "vouchertype": _lookup_ctx("vouchertype", ""),
            "narration": _lookup_ctx("narration", ""),
            "totalamount": _lookup_ctx("totalamount") or _lookup_ctx("amount", 0.0),
            "amount": _lookup_ctx("amount") if _lookup_ctx("amount") is not None else _lookup_ctx("totalamount", 0.0),
            "rate": _lookup_ctx("closingrate") or _lookup_ctx("rate", 0.0),
            "qty": _lookup_ctx("closingqty") or _lookup_ctx("qty", 0.0),
            "uom": _lookup_ctx("uom", ""),
            "closingqty": _lookup_ctx("closingqty", 0.0),
            "closingrate": _lookup_ctx("closingrate", 0.0),
            "closingvalue": _lookup_ctx("closingvalue", 0.0),
            "agebracket": _lookup_ctx("agebracket", ""),
            "agedays": _lookup_ctx("agedays", 0),
            "billref": _lookup_ctx("billref", ""),
            "duedate": _lookup_ctx("duedate", ""),
            "costcentre": _lookup_ctx("costcentre", ""),
            "category": _lookup_ctx("category", ""),
            "budgetname": _lookup_ctx("budgetname", ""),
            "budgetedamount": context.get("budgeted_amount") or 0.0,
            "actualamount": context.get("actual_amount") or 0.0,
            "variance": context.get("variance") or 0.0,
            "variancepercentage": context.get("variance_percentage") or 0.0,
            "instrumentno": context.get("instrument_no") or "",
            "instrumentdate": context.get("instrument_date") or "",
            "tdsamount": context.get("tds_amount") or 0.0,
            "sectioncode": context.get("section_code") or "",
            "costperunit": context.get("cost_per_unit") or 0.0,
            "netmfgcost": context.get("net_mfg_cost") or 0.0,
        }

        # Check for arithmetic expressions: e.g. $Amount * 1.18 or $Rate * $Qty or $Amount + @@ComputeCGST
        if any(op in expr for op in ["+", "-", "*", "/"]) and ("$" in expr or "@@" in expr):
            calc_expr = expr
            # Replace all @@Formula with their numeric evaluation
            formulas_found = re.findall(r"@@([A-Za-z0-9_]+)", expr)
            for f_name in formulas_found:
                f_val = self.evaluate_expression(f"@@{f_name}", context)
                try:
                    num_f = float(f_val)
                except (ValueError, TypeError):
                    num_f = 0.0
                calc_expr = re.sub(rf"@@{f_name}\b", str(num_f), calc_expr)

            # Replace all $Var with their numeric representation
            vars_found = re.findall(r"\$([A-Za-z0-9_]+)", calc_expr)
            for v in vars_found:
                clean_k = v.lower().replace(" ", "").replace("_", "")
                val = _lookup_ctx(v, mapping.get(clean_k, 0.0))
                try:
                    num_val = float(val)
                except (ValueError, TypeError):
                    num_val = 0.0
                calc_expr = re.sub(rf"\${v}\b", str(num_val), calc_expr)

            # Safely evaluate arithmetic expression
            try:
                # Restrict to safe math
                if re.match(r"^[0-9\.\s\+\-\*\/\(\)]+$", calc_expr):
                    return eval(calc_expr, {"__builtins__": {}}, {})
            except Exception:
                pass

        # Single $Variable
        if expr.startswith("$"):
            var_name = expr[1:].strip()
            clean_key = var_name.lower().replace(" ", "").replace("_", "")
            if clean_key in mapping:
                return mapping[clean_key]

            # Direct context check
            if var_name in context:
                return context[var_name]

            # UDF field lookup
            udf_data = context.get("udf_fields", {})
            if isinstance(udf_data, dict) and var_name in udf_data:
                return udf_data[var_name]

            return f"${var_name}"

        return expr

    def resolve_collection(self, coll_name: str) -> List[Dict[str, Any]]:
        coll_def = self.symbols.get_definition("Collection", coll_name)
        coll_lower = coll_name.lower()

        if "budget" in coll_lower:
            return self.db.get_budget_variance_report()
        if "ageing" in coll_lower or "inventoryageing" in coll_lower:
            return self.db.get_inventory_ageing_report()
        if "tds" in coll_lower:
            return self.db.get_tds_register()
        if "cheque" in coll_lower:
            return self.db.get_cheque_books()
        if "mfgcost" in coll_lower or "bom" in coll_lower:
            return self.db.get_manufacturing_cost_report()
        if "currency" in coll_lower:
            return self.db.get_currencies()
        if "stock" in coll_lower:
            return self.db.get_stock_summary()
        if "receivable" in coll_lower or "debtor" in coll_lower:
            return self.db.get_outstanding_bills("Sundry Debtors")
        if "payable" in coll_lower or "creditor" in coll_lower:
            return self.db.get_outstanding_bills("Sundry Creditors")
        if "daybook" in coll_lower:
            return self.db.get_vouchers()
        if "salesregister" in coll_lower or "sales" in coll_lower:
            return self.db.get_vouchers(voucher_type="Sales")
        if "purchaseregister" in coll_lower or "purchase" in coll_lower:
            return self.db.get_vouchers(voucher_type="Purchase")
        if "voucher" in coll_lower:
            return self.db.get_vouchers()
        if "employee" in coll_lower or "payroll" in coll_lower:
            return self.db.get_employees()
        if "godown" in coll_lower:
            return self.db.get_godown_summary()
        if "costcentre" in coll_lower or "cost" in coll_lower:
            return self.db.get_cost_centre_breakdown()
        if "editlog" in coll_lower or "audit" in coll_lower:
            return self.db.get_edit_logs()
        if "trial" in coll_lower:
            return self.db.get_trial_balance()
        if "cash" in coll_lower or "bank" in coll_lower:
            return self.db.get_cash_bank_summary()

        items = self.db.get_ledgers()
        if coll_def:
            type_attr = (coll_def.get_attr("Type") or "").lower()
            if "stock" in type_attr or "item" in type_attr:
                items = self.db.get_stock_summary()
            elif "voucher" in type_attr:
                items = self.db.get_vouchers()
            elif "company" in type_attr:
                items = self.db.get_companies()
            elif "employee" in type_attr:
                items = self.db.get_employees()

            child_of = coll_def.get_attr("ChildOf")
            if child_of:
                child_clean = child_of.strip('"\'').lower()
                items = [i for i in items if str(i.get("group_name", "")).lower() == child_clean]

            # Dynamic Condition Filter evaluation
            filter_attr = coll_def.get_attr("Filter")
            if filter_attr:
                filtered = []
                for it in items:
                    try:
                        if self._eval_tdl_condition(filter_attr, it):
                            filtered.append(it)
                    except Exception:
                        filtered.append(it)
                items = filtered

            # Dynamic Sorting evaluation
            sort_attr = coll_def.get_attr("Sort")
            if sort_attr:
                sort_col = sort_attr.strip().lstrip("@$").strip()
                try:
                    items = sorted(items, key=lambda x: str(x.get(sort_col, "")))
                except Exception:
                    pass

        return items

    def render_field(self, field_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        fdef = self.symbols.get_definition("Field", field_name)
        set_as = ""
        width = 20
        align = "left"

        if fdef:
            set_as = fdef.get_attr("Set as") or fdef.get_attr("Set") or ""
            use_type = (fdef.get_attr("Use") or "").lower()
            width_val = fdef.get_attr("Width")
            if width_val and width_val.isdigit():
                width = int(width_val)
            elif "amount" in use_type or "rate" in use_type or "qty" in use_type or "balance" in use_type:
                width = 16
                align = "right"
            elif "name" in use_type or "particulars" in use_type:
                width = 30
            elif "date" in use_type:
                width = 12

        value = self.evaluate_expression(set_as, context) if set_as else context.get(field_name, field_name)

        if isinstance(value, float):
            formatted_val = f"{value:,.2f}"
        else:
            formatted_val = str(value)

        return {
            "name": field_name,
            "raw_value": value,
            "display_value": formatted_val,
            "width": width,
            "align": align
        }

    def render_line(self, line_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
        ldef = self.symbols.get_definition("Line", line_name)
        fields = []
        if ldef:
            field_attr = ldef.get_attr("Field") or ldef.get_attr("Fields") or ""
            field_names = [f.strip() for f in field_attr.split(",") if f.strip()]
            for fname in field_names:
                fields.append(self.render_field(fname, context))
        else:
            fields.append(self.render_field(line_name, context))

        return {
            "name": line_name,
            "fields": fields
        }

    def render_report(self, report_name: str, **kwargs) -> Dict[str, Any]:
        rdef = self.symbols.get_definition("Report", report_name)
        if not rdef:
            return self._render_builtin_fallback(report_name, **kwargs)

        title = rdef.get_attr("Title") or report_name
        form_name = rdef.get_attr("Form") or report_name + "Form"
        fdef = self.symbols.get_definition("Form", form_name)

        parts_data = []
        if fdef:
            parts_attr = fdef.get_attr("Part") or fdef.get_attr("Parts") or ""
            part_names = [p.strip() for p in parts_attr.split(",") if p.strip()]
            for pname in part_names:
                parts_data.append(self._render_part(pname))
        else:
            return self._render_builtin_fallback(report_name, **kwargs)

        return {
            "report": report_name,
            "title": title.strip('"\''),
            "parts": parts_data,
            "company": self.db.get_company()
        }

    def _render_part(self, part_name: str) -> Dict[str, Any]:
        pdef = self.symbols.get_definition("Part", part_name)
        if not pdef:
            return {"name": part_name, "lines": []}

        lines_output = []
        repeat_attr = pdef.get_attr("Repeat")
        line_attr = pdef.get_attr("Line") or pdef.get_attr("Lines") or ""
        line_names = [l.strip() for l in line_attr.split(",") if l.strip()]

        if repeat_attr:
            tokens = [t.strip() for t in repeat_attr.split(":") if t.strip()]
            if len(tokens) >= 2:
                repeat_line, coll_name = tokens[0], tokens[1]
                collection_items = self.resolve_collection(coll_name)

                for lname in line_names:
                    if lname != repeat_line:
                        lines_output.append(self.render_line(lname, {}))

                for item in collection_items:
                    lines_output.append(self.render_line(repeat_line, item))
            else:
                for lname in line_names:
                    lines_output.append(self.render_line(lname, {}))
        else:
            for lname in line_names:
                lines_output.append(self.render_line(lname, {}))

        sub_parts_output = []
        sub_part_attr = pdef.get_attr("Part") or pdef.get_attr("Parts")
        if sub_part_attr:
            for spname in [p.strip() for p in sub_part_attr.split(",") if p.strip()]:
                sub_parts_output.append(self._render_part(spname))

        return {
            "name": part_name,
            "lines": lines_output,
            "sub_parts": sub_parts_output
        }

    def _render_builtin_fallback(self, report_name: str, **kwargs) -> Dict[str, Any]:
        """Provides instant high-fidelity rendering for all 2026 TallyPrime modules."""
        name_lower = report_name.lower()
        company = self.db.get_company()

        # 1. GSTR-1 Outward Supplies
        if "gstr1" in name_lower or "gstr-1" in name_lower:
            gst = self.db.get_gst_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "InvNo", "display_value": "Invoice No", "width": 16, "align": "left"},
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "Party", "display_value": "Receiver Name", "width": 28, "align": "left"},
                    {"name": "Taxable", "display_value": "Taxable Value (₹)", "width": 18, "align": "right"},
                    {"name": "TaxVal", "display_value": "Total Tax (₹)", "width": 14, "align": "right"},
                    {"name": "Total", "display_value": "Invoice Total (₹)", "width": 18, "align": "right"}
                ]}
            ]
            for inv in gst["b2b_invoices"]:
                lines.append({"name": "Row", "fields": [
                    {"name": "InvNo", "display_value": inv["inum"], "width": 16, "align": "left"},
                    {"name": "Date", "display_value": inv["idt"], "width": 12, "align": "left"},
                    {"name": "Party", "display_value": inv["party_name"], "width": 28, "align": "left"},
                    {"name": "Taxable", "display_value": f"{inv['taxable_val']:,.2f}", "width": 18, "align": "right"},
                    {"name": "TaxVal", "display_value": f"{inv['tax_val']:,.2f}", "width": 14, "align": "right"},
                    {"name": "Total", "display_value": f"{inv['val']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL GSTR-1 TAXABLE OUTWARD SUPPLIES", "width": 56, "align": "left"},
                {"name": "Turnover", "display_value": f"{gst['taxable_turnover']:,.2f}", "width": 18, "align": "right"},
                {"name": "Tax", "display_value": f"{gst['total_gst_liability']:,.2f}", "width": 14, "align": "right"},
                {"name": "Gross", "display_value": f"{gst['taxable_turnover']:,.2f}", "width": 18, "align": "right"}
            ]})
            return {"report": report_name, "title": "GSTR-1 Outward Supplies Return", "parts": [{"name": "GSTR1Part", "lines": lines}], "company": company}

        # 2. GSTR-3B Monthly Return
        elif "gstr3b" in name_lower or "gstr-3b" in name_lower:
            gst = self.db.get_gst_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Section", "display_value": "GSTR-3B Return Table", "width": 50, "align": "left"},
                    {"name": "Taxable", "display_value": "Taxable Turnover (₹)", "width": 25, "align": "right"},
                    {"name": "Tax", "display_value": "Tax Payable (₹)", "width": 25, "align": "right"}
                ]},
                {"name": "Row", "fields": [{"name": "S", "display_value": "3.1 (a) Outward taxable supplies (other than zero rated)", "width": 50}, {"name": "T", "display_value": f"{gst['taxable_turnover']:,.2f}", "width": 25, "align": "right"}, {"name": "X", "display_value": f"{gst['total_gst_liability']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "S", "display_value": "  - Central GST (CGST)", "width": 50}, {"name": "T", "display_value": "-", "width": 25, "align": "right"}, {"name": "X", "display_value": f"{gst['cgst_liability']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "S", "display_value": "  - State GST (SGST)", "width": 50}, {"name": "T", "display_value": "-", "width": 25, "align": "right"}, {"name": "X", "display_value": f"{gst['sgst_liability']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "S", "display_value": "  - Integrated GST (IGST)", "width": 50}, {"name": "T", "display_value": "-", "width": 25, "align": "right"}, {"name": "X", "display_value": f"{gst['igst_liability']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "S", "display_value": "TOTAL NET GST PAYABLE", "width": 50}, {"name": "T", "display_value": f"{gst['taxable_turnover']:,.2f}", "width": 25, "align": "right"}, {"name": "X", "display_value": f"{gst['total_gst_liability']:,.2f}", "width": 25, "align": "right"}]}
            ]
            return {"report": report_name, "title": "GSTR-3B Monthly Return Summary", "parts": [{"name": "GSTR3BPart", "lines": lines}], "company": company}

        # 3. Receivables Ageing Analysis
        elif "receivable" in name_lower or "debtor" in name_lower:
            bills = self.db.get_outstanding_bills("Sundry Debtors")
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Party", "display_value": "Customer Name", "width": 28, "align": "left"},
                    {"name": "Ref", "display_value": "Bill Ref / Inv", "width": 16, "align": "left"},
                    {"name": "Date", "display_value": "Bill Date", "width": 12, "align": "left"},
                    {"name": "Due", "display_value": "Due Date", "width": 12, "align": "left"},
                    {"name": "Age", "display_value": "Days", "width": 8, "align": "right"},
                    {"name": "Bracket", "display_value": "Ageing Bracket", "width": 14, "align": "center"},
                    {"name": "Amt", "display_value": "Pending (₹)", "width": 16, "align": "right"}
                ]}
            ]
            total_pending = sum(b["amount"] for b in bills)
            for b in bills:
                lines.append({"name": "Row", "fields": [
                    {"name": "Party", "display_value": b["party_name"], "width": 28, "align": "left"},
                    {"name": "Ref", "display_value": b["bill_ref"], "width": 16, "align": "left"},
                    {"name": "Date", "display_value": b["bill_date"], "width": 12, "align": "left"},
                    {"name": "Due", "display_value": b["due_date"], "width": 12, "align": "left"},
                    {"name": "Age", "display_value": str(b["age_days"]), "width": 8, "align": "right"},
                    {"name": "Bracket", "display_value": b["age_bracket"], "width": 14, "align": "center"},
                    {"name": "Amt", "display_value": f"{b['amount']:,.2f}", "width": 16, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL OVERDUE RECEIVABLES", "width": 90, "align": "left"},
                {"name": "TotalAmt", "display_value": f"{total_pending:,.2f}", "width": 16, "align": "right"}
            ]})
            return {"report": report_name, "title": "Bills Receivable (Ageing Analysis)", "parts": [{"name": "ReceivablesPart", "lines": lines}], "company": company}

        # 4. Payables Ageing Analysis
        elif "payable" in name_lower or "creditor" in name_lower:
            bills = self.db.get_outstanding_bills("Sundry Creditors")
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Party", "display_value": "Supplier Name", "width": 28, "align": "left"},
                    {"name": "Ref", "display_value": "Bill Ref / Inv", "width": 16, "align": "left"},
                    {"name": "Date", "display_value": "Bill Date", "width": 12, "align": "left"},
                    {"name": "Due", "display_value": "Due Date", "width": 12, "align": "left"},
                    {"name": "Age", "display_value": "Days", "width": 8, "align": "right"},
                    {"name": "Bracket", "display_value": "Ageing Bracket", "width": 14, "align": "center"},
                    {"name": "Amt", "display_value": "Pending (₹)", "width": 16, "align": "right"}
                ]}
            ]
            total_pending = sum(b["amount"] for b in bills)
            for b in bills:
                lines.append({"name": "Row", "fields": [
                    {"name": "Party", "display_value": b["party_name"], "width": 28, "align": "left"},
                    {"name": "Ref", "display_value": b["bill_ref"], "width": 16, "align": "left"},
                    {"name": "Date", "display_value": b["bill_date"], "width": 12, "align": "left"},
                    {"name": "Due", "display_value": b["due_date"], "width": 12, "align": "left"},
                    {"name": "Age", "display_value": str(b["age_days"]), "width": 8, "align": "right"},
                    {"name": "Bracket", "display_value": b["age_bracket"], "width": 14, "align": "center"},
                    {"name": "Amt", "display_value": f"{b['amount']:,.2f}", "width": 16, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL OVERDUE PAYABLES", "width": 90, "align": "left"},
                {"name": "TotalAmt", "display_value": f"{total_pending:,.2f}", "width": 16, "align": "right"}
            ]})
            return {"report": report_name, "title": "Bills Payable (Ageing Analysis)", "parts": [{"name": "PayablesPart", "lines": lines}], "company": company}

        # 5. Bank Reconciliation Statement (BRS)
        elif "brs" in name_lower or "bankrecon" in name_lower:
            brs = self.db.get_bank_reconciliation("HDFC Bank")
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Vch Date", "width": 14, "align": "left"},
                    {"name": "VchNo", "display_value": "Voucher No", "width": 16, "align": "left"},
                    {"name": "Chq", "display_value": "Instrument No", "width": 18, "align": "left"},
                    {"name": "BankDate", "display_value": "Bank Cleared Date", "width": 18, "align": "left"},
                    {"name": "Status", "display_value": "BRS Status", "width": 16, "align": "center"},
                    {"name": "Amt", "display_value": "Amount (₹)", "width": 18, "align": "right"}
                ]}
            ]
            for t in brs["transactions"]:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": t["date"], "width": 14, "align": "left"},
                    {"name": "VchNo", "display_value": t["voucher_number"], "width": 16, "align": "left"},
                    {"name": "Chq", "display_value": t["instrument_no"] or "-", "width": 18, "align": "left"},
                    {"name": "BankDate", "display_value": t["bank_date"] or "Pending Clearing", "width": 18, "align": "left"},
                    {"name": "Status", "display_value": t["status"], "width": 16, "align": "center"},
                    {"name": "Amt", "display_value": f"{t['total_amount']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": f"Balance as per Company Books: ₹{brs['book_balance']:,.2f} | Est. Bank Passbook Balance: ₹{brs['passbook_balance']:,.2f}", "width": 100, "align": "left"}
            ]})
            return {"report": report_name, "title": "Bank Reconciliation Statement (HDFC Bank)", "parts": [{"name": "BRSPart", "lines": lines}], "company": company}

        # 6. Payroll Pay Sheet
        elif "payroll" in name_lower or "paysheet" in name_lower:
            employees = self.db.get_employees()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Code", "display_value": "Emp Code", "width": 12, "align": "left"},
                    {"name": "Name", "display_value": "Employee Name", "width": 25, "align": "left"},
                    {"name": "Dept", "display_value": "Department", "width": 20, "align": "left"},
                    {"name": "Gross", "display_value": "Gross Pay (₹)", "width": 16, "align": "right"},
                    {"name": "PF", "display_value": "PF Ded (₹)", "width": 14, "align": "right"},
                    {"name": "PT", "display_value": "PT (₹)", "width": 10, "align": "right"},
                    {"name": "Net", "display_value": "Net Salary (₹)", "width": 18, "align": "right"}
                ]}
            ]
            tot_gross, tot_net = 0.0, 0.0
            for emp in employees:
                calc = self.db.calculate_employee_salary(emp["emp_code"])
                tot_gross += calc["gross_salary"]
                tot_net += calc["net_salary"]
                lines.append({"name": "Row", "fields": [
                    {"name": "Code", "display_value": emp["emp_code"], "width": 12, "align": "left"},
                    {"name": "Name", "display_value": emp["name"], "width": 25, "align": "left"},
                    {"name": "Dept", "display_value": emp["department"], "width": 20, "align": "left"},
                    {"name": "Gross", "display_value": f"{calc['gross_salary']:,.2f}", "width": 16, "align": "right"},
                    {"name": "PF", "display_value": f"{calc['deductions'].get('Provident Fund (PF)', 0):,.2f}", "width": 14, "align": "right"},
                    {"name": "PT", "display_value": f"{calc['deductions'].get('Professional Tax (PT)', 0):,.2f}", "width": 10, "align": "right"},
                    {"name": "Net", "display_value": f"{calc['net_salary']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL SALARY DISBURSEMENTS", "width": 57, "align": "left"},
                {"name": "TotGross", "display_value": f"{tot_gross:,.2f}", "width": 16, "align": "right"},
                {"name": "Blank", "display_value": "-", "width": 24, "align": "center"},
                {"name": "TotNet", "display_value": f"{tot_net:,.2f}", "width": 18, "align": "right"}
            ]})
            return {"report": report_name, "title": "Monthly Payroll Register / Pay Sheet", "parts": [{"name": "PayrollPart", "lines": lines}], "company": company}

        # 7. Audit Trail / Statutory Edit Log
        elif "audit" in name_lower or "editlog" in name_lower:
            logs = self.db.get_edit_logs(50)
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Time", "display_value": "Date & Time", "width": 20, "align": "left"},
                    {"name": "Type", "display_value": "Entity Type", "width": 15, "align": "left"},
                    {"name": "ID", "display_value": "Identifier", "width": 18, "align": "left"},
                    {"name": "Action", "display_value": "Operation", "width": 15, "align": "center"},
                    {"name": "User", "display_value": "User", "width": 12, "align": "left"},
                    {"name": "Details", "display_value": "Audit Log Details", "width": 40, "align": "left"}
                ]}
            ]
            for l in logs:
                lines.append({"name": "Row", "fields": [
                    {"name": "Time", "display_value": l["timestamp"][:19], "width": 20, "align": "left"},
                    {"name": "Type", "display_value": l["entity_type"], "width": 15, "align": "left"},
                    {"name": "ID", "display_value": l["entity_id"], "width": 18, "align": "left"},
                    {"name": "Action", "display_value": l["action"], "width": 15, "align": "center"},
                    {"name": "User", "display_value": l["user_name"], "width": 12, "align": "left"},
                    {"name": "Details", "display_value": l["details"] or "-", "width": 40, "align": "left"}
                ]})
            return {"report": report_name, "title": "Statutory Audit Trail (Edit Log)", "parts": [{"name": "AuditPart", "lines": lines}], "company": company}

        # 8. Stock Summary Report
        elif "stock" in name_lower:
            items = self.db.get_stock_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Item", "display_value": "Item Name", "width": 30, "align": "left"},
                    {"name": "UOM", "display_value": "Unit", "width": 10, "align": "left"},
                    {"name": "CloseQty", "display_value": "Closing Qty", "width": 15, "align": "right"},
                    {"name": "Rate", "display_value": "Rate (₹)", "width": 15, "align": "right"},
                    {"name": "Value", "display_value": "Closing Value (₹)", "width": 20, "align": "right"},
                    {"name": "Reorder", "display_value": "Status", "width": 10, "align": "center"}
                ]}
            ]
            total_val = sum(i["closing_value"] for i in items)
            for i in items:
                lines.append({"name": "Row", "fields": [
                    {"name": "Item", "display_value": i["item_name"], "width": 30, "align": "left"},
                    {"name": "UOM", "display_value": i["uom"], "width": 10, "align": "left"},
                    {"name": "CloseQty", "display_value": f"{i['closing_qty']:,.0f}", "width": 15, "align": "right"},
                    {"name": "Rate", "display_value": f"{i['closing_rate']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Value", "display_value": f"{i['closing_value']:,.2f}", "width": 20, "align": "right"},
                    {"name": "Reorder", "display_value": "REORDER" if i["needs_reorder"] else "OK", "width": 10, "align": "center"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "TotalLabel", "display_value": "TOTAL CLOSING STOCK", "width": 70, "align": "left"},
                {"name": "TotalVal", "display_value": f"{total_val:,.2f}", "width": 20, "align": "right"},
                {"name": "Blank", "display_value": "", "width": 10, "align": "center"}
            ]})
            return {"report": report_name, "title": "Stock Summary", "parts": [{"name": "StockPart", "lines": lines}], "company": company}

        # 9. General Ledger / Account Statement
        elif "generalledger" in name_lower or "ledgerstatement" in name_lower or "accountstatement" in name_lower:
            ledger_name = kwargs.get("ledger") or "Customer ABC Pvt Ltd"
            statement = self.db.get_general_ledger_statement(ledger_name)
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "VchType", "display_value": "Voucher Type", "width": 16, "align": "left"},
                    {"name": "VchNo", "display_value": "Vch No", "width": 16, "align": "left"},
                    {"name": "Narration", "display_value": "Particulars / Narration", "width": 28, "align": "left"},
                    {"name": "Debit", "display_value": "Debit (₹)", "width": 14, "align": "right"},
                    {"name": "Credit", "display_value": "Credit (₹)", "width": 14, "align": "right"},
                    {"name": "Balance", "display_value": "Balance (₹)", "width": 16, "align": "right"}
                ]},
                {"name": "OpeningRow", "fields": [
                    {"name": "Date", "display_value": "1-Apr-2026", "width": 12, "align": "left"},
                    {"name": "VchType", "display_value": "Opening Balance", "width": 16, "align": "left"},
                    {"name": "VchNo", "display_value": "-", "width": 16, "align": "left"},
                    {"name": "Narration", "display_value": "Brought Forward", "width": 28, "align": "left"},
                    {"name": "Debit", "display_value": f"{statement['opening_balance']:,.2f}" if statement['opening_balance_type'] == 'Dr' else "-", "width": 14, "align": "right"},
                    {"name": "Credit", "display_value": f"{statement['opening_balance']:,.2f}" if statement['opening_balance_type'] == 'Cr' else "-", "width": 14, "align": "right"},
                    {"name": "Balance", "display_value": f"{statement['opening_balance']:,.2f} {statement['opening_balance_type']}", "width": 16, "align": "right"}
                ]}
            ]
            for tx in statement["transactions"]:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": tx["date"], "width": 12, "align": "left"},
                    {"name": "VchType", "display_value": tx["voucher_type"], "width": 16, "align": "left"},
                    {"name": "VchNo", "display_value": tx["voucher_number"], "width": 16, "align": "left"},
                    {"name": "Narration", "display_value": tx["narration"][:26], "width": 28, "align": "left"},
                    {"name": "Debit", "display_value": f"{tx['debit']:,.2f}" if tx['debit'] > 0 else "-", "width": 14, "align": "right"},
                    {"name": "Credit", "display_value": f"{tx['credit']:,.2f}" if tx['credit'] > 0 else "-", "width": 14, "align": "right"},
                    {"name": "Balance", "display_value": f"{tx['running_balance']:,.2f} {tx['running_balance_type']}", "width": 16, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": f"CLOSING BALANCE for {ledger_name}", "width": 72, "align": "left"},
                {"name": "Blank1", "display_value": "", "width": 14, "align": "right"},
                {"name": "Blank2", "display_value": "", "width": 14, "align": "right"},
                {"name": "CloseBal", "display_value": f"{statement['closing_balance']:,.2f} {statement['closing_balance_type']}", "width": 16, "align": "right"}
            ]})
            return {"report": report_name, "title": f"Ledger Monthly Statement: {ledger_name}", "parts": [{"name": "GLPart", "lines": lines}], "company": company}

        # 10. Cash Flow Statement
        elif "cashflow" in name_lower:
            cf = self.db.get_cash_flow_statement()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Cash Flow Activities (Direct Method)", "width": 65, "align": "left"},
                    {"name": "Amount", "display_value": "Amount (₹)", "width": 35, "align": "right"}
                ]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Gross Operating Cash Inflows (Sales / Receipts)", "width": 65}, {"name": "A", "display_value": f"{cf['total_inflows']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Less: Gross Operating Cash Outflows (Purchases / Expenses)", "width": 65}, {"name": "A", "display_value": f"({cf['total_outflows']:,.2f})", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "NET CASH INFLOW / (OUTFLOW)", "width": 65}, {"name": "A", "display_value": f"{cf['net_cash_flow']:,.2f}", "width": 35, "align": "right"}]}
            ]
            return {"report": report_name, "title": "Cash Flow Statement (Direct Method)", "parts": [{"name": "CashFlowPart", "lines": lines}], "company": company}

        # 11. Cost Centres Breakdown
        elif "costcentre" in name_lower or "cost_centre" in name_lower or "cost_breakdown" in name_lower:
            cc_items = self.db.get_cost_centre_breakdown()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Name", "display_value": "Cost Centre Name", "width": 35, "align": "left"},
                    {"name": "Category", "display_value": "Cost Category", "width": 25, "align": "left"},
                    {"name": "Debit", "display_value": "Debit (₹)", "width": 15, "align": "right"},
                    {"name": "Credit", "display_value": "Credit (₹)", "width": 15, "align": "right"},
                    {"name": "Net", "display_value": "Net Balance (₹)", "width": 15, "align": "right"}
                ]}
            ]
            for c in cc_items:
                lines.append({"name": "Row", "fields": [
                    {"name": "Name", "display_value": c["cost_centre_name"], "width": 35, "align": "left"},
                    {"name": "Category", "display_value": c["category_name"], "width": 25, "align": "left"},
                    {"name": "Debit", "display_value": f"{c['debit']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Credit", "display_value": f"{c['credit']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Net", "display_value": f"{abs(c['net_amount']):,.2f} {c['direction']}", "width": 15, "align": "right"}
                ]})
            return {"report": report_name, "title": "Cost Centre Summary & Breakdown", "parts": [{"name": "CCPart", "lines": lines}], "company": company}

        # 12. Ratio Analysis Report
        elif "ratio" in name_lower:
            ratios = self.db.get_ratio_analysis()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Metric", "display_value": "Key Financial Ratio / Metric", "width": 55, "align": "left"},
                    {"name": "Value", "display_value": "Computed Value", "width": 45, "align": "right"}
                ]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Current Assets (including Stock)", "width": 55}, {"name": "V", "display_value": f"₹{ratios['current_assets']:,.2f}", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Current Liabilities", "width": 55}, {"name": "V", "display_value": f"₹{ratios['current_liabilities']:,.2f}", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Working Capital (Current Assets - Liabilities)", "width": 55}, {"name": "V", "display_value": f"₹{ratios['working_capital']:,.2f}", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Current Ratio (Benchmark 2:1)", "width": 55}, {"name": "V", "display_value": f"{ratios['current_ratio']} : 1", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Quick Ratio / Acid Test (Benchmark 1:1)", "width": 55}, {"name": "V", "display_value": f"{ratios['quick_ratio']} : 1", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Debt-Equity Ratio", "width": 55}, {"name": "V", "display_value": f"{ratios['debt_equity_ratio']} : 1", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Gross Profit Margin (%)", "width": 55}, {"name": "V", "display_value": f"{ratios['gross_profit_margin_pct']}%", "width": 45, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "M", "display_value": "Net Profit Margin (%)", "width": 55}, {"name": "V", "display_value": f"{ratios['net_profit_margin_pct']}%", "width": 45, "align": "right"}]},
            ]
            return {"report": report_name, "title": "Ratio Analysis", "parts": [{"name": "RatioPart", "lines": lines}], "company": company}

        # 13. Cash / Bank Summary
        elif "cash" in name_lower or "bank" in name_lower:
            cb_items = self.db.get_cash_bank_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Account", "display_value": "Account Name", "width": 50, "align": "left"},
                    {"name": "Type", "display_value": "Under Group", "width": 25, "align": "left"},
                    {"name": "Balance", "display_value": "Closing Balance (₹)", "width": 25, "align": "right"}
                ]}
            ]
            total_funds = sum(cb['balance'] if cb['balance_type'] == 'Dr' else -cb['balance'] for cb in cb_items)
            for cb in cb_items:
                lines.append({"name": "Row", "fields": [
                    {"name": "Account", "display_value": cb["name"], "width": 50, "align": "left"},
                    {"name": "Type", "display_value": cb["group_name"], "width": 25, "align": "left"},
                    {"name": "Balance", "display_value": f"{cb['balance']:,.2f} {cb['balance_type']}", "width": 25, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "TotalLabel", "display_value": "TOTAL LIQUID FUNDS", "width": 75, "align": "left"},
                {"name": "TotalVal", "display_value": f"{total_funds:,.2f} Dr", "width": 25, "align": "right"}
            ]})
            return {"report": report_name, "title": "Cash and Bank Summary", "parts": [{"name": "CashBankPart", "lines": lines}], "company": company}

        # 14. Balance Sheet
        elif "balance" in name_lower:
            fin = self.db.get_financial_summary()
            ledgers = self.db.get_ledgers()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Liabilities", "display_value": "Liabilities & Capital", "width": 35, "align": "left"},
                    {"name": "LAmount", "display_value": "Amount (₹)", "width": 15, "align": "right"},
                    {"name": "Assets", "display_value": "Assets", "width": 35, "align": "left"},
                    {"name": "AAmount", "display_value": "Amount (₹)", "width": 15, "align": "right"}
                ]}
            ]
            liab_items = [l for l in ledgers if l["closing_balance_type"] == "Cr" and l["closing_balance"] > 0]
            asset_items = [l for l in ledgers if l["closing_balance_type"] == "Dr" and l["closing_balance"] > 0]
            asset_items.append({"name": "Stock-in-Hand (Closing Stock)", "closing_balance": fin["closing_stock"], "closing_balance_type": "Dr"})

            max_len = max(len(liab_items), len(asset_items), 1)
            for i in range(max_len):
                l_item = liab_items[i] if i < len(liab_items) else None
                a_item = asset_items[i] if i < len(asset_items) else None
                lines.append({"name": "Row", "fields": [
                    {"name": "LName", "display_value": l_item["name"] if l_item else "", "width": 35, "align": "left"},
                    {"name": "LVal", "display_value": f"{l_item['closing_balance']:,.2f}" if l_item else "", "width": 15, "align": "right"},
                    {"name": "AName", "display_value": a_item["name"] if a_item else "", "width": 35, "align": "left"},
                    {"name": "AVal", "display_value": f"{a_item['closing_balance']:,.2f}" if a_item else "", "width": 15, "align": "right"}
                ]})

            total_liab = sum(l["closing_balance"] for l in liab_items)
            total_assets = sum(a["closing_balance"] for a in asset_items)
            lines.append({"name": "Total", "fields": [
                {"name": "TotalL", "display_value": "Total Liabilities", "width": 35, "align": "left"},
                {"name": "TotalLVal", "display_value": f"{total_liab:,.2f}", "width": 15, "align": "right"},
                {"name": "TotalA", "display_value": "Total Assets", "width": 35, "align": "left"},
                {"name": "TotalAVal", "display_value": f"{total_assets:,.2f}", "width": 15, "align": "right"}
            ]})
            return {"report": report_name, "title": "Balance Sheet", "parts": [{"name": "BSPart", "lines": lines}], "company": company}

        # 15. Profit & Loss
        elif "profit" in name_lower:
            fin = self.db.get_financial_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Particulars", "width": 65, "align": "left"},
                    {"name": "Amount", "display_value": "Amount (₹)", "width": 35, "align": "right"}
                ]},
                {"name": "Row", "fields": [{"name": "L", "display_value": "Sales Accounts (Revenue from Operations)", "width": 65}, {"name": "V", "display_value": f"{fin['sales_total']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "L", "display_value": "Closing Stock (Valuation)", "width": 65}, {"name": "V", "display_value": f"{fin['closing_stock']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "L", "display_value": "Less: Purchase Accounts", "width": 65}, {"name": "V", "display_value": f"({fin['purchase_total']:,.2f})", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "L", "display_value": "GROSS PROFIT c/d", "width": 65}, {"name": "V", "display_value": f"{fin['gross_profit']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "L", "display_value": "NET PROFIT", "width": 65}, {"name": "V", "display_value": f"{fin['net_profit']:,.2f}", "width": 35, "align": "right"}]}
            ]
            return {"report": report_name, "title": "Profit & Loss A/c", "parts": [{"name": "PLPart", "lines": lines}], "company": company}

        # 16. Trial Balance
        elif "trial" in name_lower:
            tb = self.db.get_trial_balance()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Particulars", "width": 50, "align": "left"},
                    {"name": "Group", "display_value": "Primary Group", "width": 26, "align": "left"},
                    {"name": "Debit", "display_value": "Debit (₹)", "width": 14, "align": "right"},
                    {"name": "Credit", "display_value": "Credit (₹)", "width": 14, "align": "right"}
                ]}
            ]
            tot_dr, tot_cr = 0.0, 0.0
            for item in tb:
                dr_val = item["closing_balance"] if item["closing_balance_type"] == "Dr" else 0.0
                cr_val = item["closing_balance"] if item["closing_balance_type"] == "Cr" else 0.0
                tot_dr += dr_val
                tot_cr += cr_val
                lines.append({"name": "Row", "fields": [
                    {"name": "Particulars", "display_value": item["name"], "width": 50, "align": "left"},
                    {"name": "Group", "display_value": item["group_name"], "width": 26, "align": "left"},
                    {"name": "Debit", "display_value": f"{dr_val:,.2f}" if dr_val > 0 else "-", "width": 14, "align": "right"},
                    {"name": "Credit", "display_value": f"{cr_val:,.2f}" if cr_val > 0 else "-", "width": 14, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "TotalLbl", "display_value": "TOTAL TRIAL BALANCE", "width": 76, "align": "left"},
                {"name": "TotDr", "display_value": f"{tot_dr:,.2f}", "width": 14, "align": "right"},
                {"name": "TotCr", "display_value": f"{tot_cr:,.2f}", "width": 14, "align": "right"}
            ]})
            return {"report": report_name, "title": "Trial Balance", "parts": [{"name": "TBPart", "lines": lines}], "company": company}

        # 17. Funds Flow Statement
        elif "fundsflow" in name_lower or "funds" in name_lower:
            ff = self.db.get_funds_flow_statement()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Particulars", "width": 65, "align": "left"},
                    {"name": "Amount", "display_value": "Amount (₹)", "width": 35, "align": "right"}
                ]},
                {"name": "SecHeader", "fields": [{"name": "P", "display_value": "SOURCES OF FUNDS:", "width": 65}, {"name": "A", "display_value": "", "width": 35}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Funds from Operations (Net Profit)", "width": 65}, {"name": "A", "display_value": f"{ff['sources']['funds_from_operations']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Capital Introduced / New Inflows", "width": 65}, {"name": "A", "display_value": f"{ff['sources']['capital_introduced']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Subtotal", "fields": [{"name": "P", "display_value": "TOTAL SOURCES (A)", "width": 65}, {"name": "A", "display_value": f"{ff['sources']['total_sources']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "SecHeader", "fields": [{"name": "P", "display_value": "APPLICATIONS OF FUNDS:", "width": 65}, {"name": "A", "display_value": "", "width": 35}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Fixed Assets Acquired", "width": 65}, {"name": "A", "display_value": f"{ff['applications']['fixed_assets_acquired']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Subtotal", "fields": [{"name": "P", "display_value": "TOTAL APPLICATIONS (B)", "width": 65}, {"name": "A", "display_value": f"{ff['applications']['total_applications']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "NET INCREASE IN WORKING CAPITAL (A - B)", "width": 65}, {"name": "A", "display_value": f"{ff['net_increase_in_working_capital']:,.2f}", "width": 35, "align": "right"}]}
            ]
            return {"report": report_name, "title": "Funds Flow Statement", "parts": [{"name": "FFPart", "lines": lines}], "company": company}

        # 18. Budget Variance Analysis
        elif "budget" in name_lower or "variance" in name_lower:
            bv = self.db.get_budget_variance_report()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Budget", "display_value": "Budget Name", "width": 20, "align": "left"},
                    {"name": "Ledger", "display_value": "Ledger Name", "width": 28, "align": "left"},
                    {"name": "Budgeted", "display_value": "Budgeted (₹)", "width": 16, "align": "right"},
                    {"name": "Actual", "display_value": "Actual (₹)", "width": 16, "align": "right"},
                    {"name": "Variance", "display_value": "Variance (₹)", "width": 14, "align": "right"},
                    {"name": "Pct", "display_value": "Var %", "width": 6, "align": "right"}
                ]}
            ]
            for r in bv:
                lines.append({"name": "Row", "fields": [
                    {"name": "Budget", "display_value": r["budget_name"], "width": 20, "align": "left"},
                    {"name": "Ledger", "display_value": r["ledger_name"], "width": 28, "align": "left"},
                    {"name": "Budgeted", "display_value": f"{r['budgeted_amount']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Actual", "display_value": f"{r['actual_amount']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Variance", "display_value": f"{r['variance']:,.2f}", "width": 14, "align": "right"},
                    {"name": "Pct", "display_value": f"{r['variance_percentage']}%", "width": 6, "align": "right"}
                ]})
            return {"report": report_name, "title": "Budget Variance Analysis", "parts": [{"name": "BVPart", "lines": lines}], "company": company}

        # 19. Inventory Ageing Analysis
        elif "ageing" in name_lower or "aging" in name_lower:
            age = self.db.get_inventory_ageing_report()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Item", "display_value": "Stock Item", "width": 28, "align": "left"},
                    {"name": "TotalQty", "display_value": "Total Qty", "width": 12, "align": "right"},
                    {"name": "Val", "display_value": "Value (₹)", "width": 16, "align": "right"},
                    {"name": "B1", "display_value": "0-30 Days", "width": 11, "align": "right"},
                    {"name": "B2", "display_value": "31-60 Days", "width": 11, "align": "right"},
                    {"name": "B3", "display_value": "61-90 Days", "width": 11, "align": "right"},
                    {"name": "B4", "display_value": ">90 Days", "width": 11, "align": "right"}
                ]}
            ]
            for itm in age:
                lines.append({"name": "Row", "fields": [
                    {"name": "Item", "display_value": itm["stock_item"], "width": 28, "align": "left"},
                    {"name": "TotalQty", "display_value": f"{itm['total_qty']} {itm['uom']}", "width": 12, "align": "right"},
                    {"name": "Val", "display_value": f"{itm['total_value']:,.2f}", "width": 16, "align": "right"},
                    {"name": "B1", "display_value": f"{itm['qty_0_30_days']}", "width": 11, "align": "right"},
                    {"name": "B2", "display_value": f"{itm['qty_31_60_days']}", "width": 11, "align": "right"},
                    {"name": "B3", "display_value": f"{itm['qty_61_90_days']}", "width": 11, "align": "right"},
                    {"name": "B4", "display_value": f"{itm['qty_over_90_days']}", "width": 11, "align": "right"}
                ]})
            return {"report": report_name, "title": "Inventory Ageing Analysis", "parts": [{"name": "AgePart", "lines": lines}], "company": company}

        # 20. GSTR-9 Annual Return
        elif "gstr9" in name_lower or "gstr-9" in name_lower:
            g9 = self.db.get_gstr9_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "GSTR-9 Return Table & Description", "width": 65, "align": "left"},
                    {"name": "Amount", "display_value": "Amount (₹)", "width": 35, "align": "right"}
                ]},
                {"name": "SecHeader", "fields": [{"name": "P", "display_value": "TABLE 4: OUTWARD TAXABLE SUPPLIES", "width": 65}, {"name": "A", "display_value": "", "width": 35}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Taxable Turnover (B2B)", "width": 65}, {"name": "A", "display_value": f"{g9['table_4_outward_supplies']['b2b_taxable_turnover']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Central Tax (CGST)", "width": 65}, {"name": "A", "display_value": f"{g9['table_4_outward_supplies']['cgst_liability']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  State Tax (SGST)", "width": 65}, {"name": "A", "display_value": f"{g9['table_4_outward_supplies']['sgst_liability']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Integrated Tax (IGST)", "width": 65}, {"name": "A", "display_value": f"{g9['table_4_outward_supplies']['igst_liability']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "SecHeader", "fields": [{"name": "P", "display_value": "TABLE 6: INPUT TAX CREDIT (ITC) AVAILED", "width": 65}, {"name": "A", "display_value": "", "width": 35}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Total Input Tax Credit (ITC) Availed", "width": 65}, {"name": "A", "display_value": f"{g9['table_6_itc_availed']['total_itc_availed']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "SecHeader", "fields": [{"name": "P", "display_value": "TABLE 9: TAX PAID & PAYABLE", "width": 65}, {"name": "A", "display_value": "", "width": 35}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Total Tax Payable", "width": 65}, {"name": "A", "display_value": f"{g9['table_9_tax_paid_and_payable']['total_tax_payable']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "  Paid Through ITC", "width": 65}, {"name": "A", "display_value": f"{g9['table_9_tax_paid_and_payable']['paid_through_itc']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "NET TAX PAYABLE IN CASH", "width": 65}, {"name": "A", "display_value": f"{g9['table_9_tax_paid_and_payable']['net_tax_payable_in_cash']:,.2f}", "width": 35, "align": "right"}]}
            ]
            return {"report": report_name, "title": "GSTR-9 Annual Return Summary", "parts": [{"name": "G9Part", "lines": lines}], "company": company}

        # 21. Consolidated Financial Statement
        elif "consolidated" in name_lower:
            cfin = self.db.get_consolidated_financial_summary()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Group Consolidated Summary", "width": 65, "align": "left"},
                    {"name": "Amount", "display_value": "Consolidated (₹)", "width": 35, "align": "right"}
                ]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Consolidated Sales (Turnover)", "width": 65}, {"name": "A", "display_value": f"{cfin['sales_total']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Consolidated Purchases", "width": 65}, {"name": "A", "display_value": f"{cfin['purchase_total']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Consolidated Closing Inventory", "width": 65}, {"name": "A", "display_value": f"{cfin['closing_stock']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "Consolidated Gross Profit", "width": 65}, {"name": "A", "display_value": f"{cfin['gross_profit']:,.2f}", "width": 35, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "CONSOLIDATED NET PROFIT", "width": 65}, {"name": "A", "display_value": f"{cfin['net_profit']:,.2f}", "width": 35, "align": "right"}]}
            ]
            return {"report": report_name, "title": "Consolidated Financial Statement", "parts": [{"name": "CFinPart", "lines": lines}], "company": company}

        # 22. TDS Withholding Tax Register
        elif "tds" in name_lower:
            tds_rows = self.db.get_tds_register()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 14, "align": "left"},
                    {"name": "VchNo", "display_value": "Vch No", "width": 16, "align": "left"},
                    {"name": "TdsLedger", "display_value": "TDS Ledger", "width": 30, "align": "left"},
                    {"name": "Narration", "display_value": "Particulars", "width": 24, "align": "left"},
                    {"name": "Amount", "display_value": "TDS Deducted (₹)", "width": 16, "align": "right"}
                ]}
            ]
            tot_tds = 0.0
            for r in tds_rows:
                tot_tds += float(r["tds_amount"])
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": r["date"], "width": 14, "align": "left"},
                    {"name": "VchNo", "display_value": r["voucher_number"], "width": 16, "align": "left"},
                    {"name": "TdsLedger", "display_value": r["tds_ledger"], "width": 30, "align": "left"},
                    {"name": "Narration", "display_value": (r.get("narration") or "")[:22], "width": 24, "align": "left"},
                    {"name": "Amount", "display_value": f"{r['tds_amount']:,.2f}", "width": 16, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL TDS DEDUCTIONS", "width": 84, "align": "left"},
                {"name": "Tot", "display_value": f"{tot_tds:,.2f}", "width": 16, "align": "right"}
            ]})
            return {"report": report_name, "title": "TDS Withholding Tax Register", "parts": [{"name": "TDSPart", "lines": lines}], "company": company}

        # 23. Cheque Register
        elif "cheque" in name_lower or "chq" in name_lower:
            chqs = self.db.get_cheque_books()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Bank", "display_value": "Bank Ledger", "width": 30, "align": "left"},
                    {"name": "Book", "display_value": "Book Name", "width": 25, "align": "left"},
                    {"name": "From", "display_value": "From No", "width": 15, "align": "right"},
                    {"name": "To", "display_value": "To No", "width": 15, "align": "right"},
                    {"name": "Next", "display_value": "Next Chq No", "width": 15, "align": "right"}
                ]}
            ]
            for c in chqs:
                lines.append({"name": "Row", "fields": [
                    {"name": "Bank", "display_value": c["bank_ledger_name"], "width": 30, "align": "left"},
                    {"name": "Book", "display_value": c["book_name"], "width": 25, "align": "left"},
                    {"name": "From", "display_value": str(c["from_no"]), "width": 15, "align": "right"},
                    {"name": "To", "display_value": str(c["to_no"]), "width": 15, "align": "right"},
                    {"name": "Next", "display_value": str(c["current_no"]), "width": 15, "align": "right"}
                ]})
            return {"report": report_name, "title": "Cheque Register & Books", "parts": [{"name": "ChqPart", "lines": lines}], "company": company}

        # 24. Manufacturing & BOM Cost Breakdown
        elif "mfg" in name_lower or "bom" in name_lower or "manufacturing" in name_lower:
            mfg = self.db.get_manufacturing_cost_report()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "BOM", "display_value": "BOM Name", "width": 25, "align": "left"},
                    {"name": "Item", "display_value": "Finished Item", "width": 25, "align": "left"},
                    {"name": "UnitQty", "display_value": "Unit Qty", "width": 10, "align": "right"},
                    {"name": "RMCost", "display_value": "RM Cost (₹)", "width": 14, "align": "right"},
                    {"name": "Scrap", "display_value": "Scrap (₹)", "width": 12, "align": "right"},
                    {"name": "UnitCost", "display_value": "Cost/Unit (₹)", "width": 14, "align": "right"}
                ]}
            ]
            for b in mfg:
                lines.append({"name": "Row", "fields": [
                    {"name": "BOM", "display_value": b["bom_name"], "width": 25, "align": "left"},
                    {"name": "Item", "display_value": b["finished_item"], "width": 25, "align": "left"},
                    {"name": "UnitQty", "display_value": f"{b['unit_qty']}", "width": 10, "align": "right"},
                    {"name": "RMCost", "display_value": f"{b['total_rm_cost']:,.2f}", "width": 14, "align": "right"},
                    {"name": "Scrap", "display_value": f"{b['scrap_recovery']:,.2f}", "width": 12, "align": "right"},
                    {"name": "UnitCost", "display_value": f"{b['cost_per_unit']:,.2f}", "width": 14, "align": "right"}
                ]})
            return {"report": report_name, "title": "Manufacturing & BOM Cost Sheet", "parts": [{"name": "MfgPart", "lines": lines}], "company": company}

        # 26. Fixed Assets Register & Depreciation
        elif "fixedasset" in name_lower or "assetregister" in name_lower or "asset" in name_lower:
            assets = self.db.get_fixed_assets()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Code", "display_value": "Asset Code", "width": 14, "align": "left"},
                    {"name": "Name", "display_value": "Asset Name", "width": 24, "align": "left"},
                    {"name": "Category", "display_value": "Category", "width": 18, "align": "left"},
                    {"name": "Cost", "display_value": "Cost (₹)", "width": 16, "align": "right"},
                    {"name": "Depreciation", "display_value": "Depr. (₹)", "width": 14, "align": "right"},
                    {"name": "BookVal", "display_value": "Book Value (₹)", "width": 16, "align": "right"},
                    {"name": "Status", "display_value": "Status", "width": 12, "align": "center"}
                ]}
            ]
            tot_cost = sum(a["purchase_cost"] for a in assets)
            tot_dep = sum(a["posted_depreciation"] for a in assets)
            tot_val = sum(a["current_book_value"] for a in assets)
            for a in assets:
                lines.append({"name": "Row", "fields": [
                    {"name": "Code", "display_value": a["asset_code"], "width": 14, "align": "left"},
                    {"name": "Name", "display_value": a["asset_name"][:24], "width": 24, "align": "left"},
                    {"name": "Category", "display_value": a["category_name"][:18], "width": 18, "align": "left"},
                    {"name": "Cost", "display_value": f"{a['purchase_cost']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Depreciation", "display_value": f"{a['posted_depreciation']:,.2f}", "width": 14, "align": "right"},
                    {"name": "BookVal", "display_value": f"{a['current_book_value']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Status", "display_value": a["status"].upper(), "width": 12, "align": "center"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL FIXED ASSETS", "width": 56, "align": "left"},
                {"name": "CostTot", "display_value": f"{tot_cost:,.2f}", "width": 16, "align": "right"},
                {"name": "DepTot", "display_value": f"{tot_dep:,.2f}", "width": 14, "align": "right"},
                {"name": "ValTot", "display_value": f"{tot_val:,.2f}", "width": 16, "align": "right"},
                {"name": "Empty", "display_value": "", "width": 12, "align": "center"}
            ]})
            return {"report": report_name, "title": "Fixed Assets & Depreciation Register", "parts": [{"name": "AssetPart", "lines": lines}], "company": company}

        # 27. Orders & Pending Orders (Sales / Purchase Orders)
        elif "order" in name_lower:
            o_type = kwargs.get("order_type")
            orders = self.db.get_orders(order_type=o_type)
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "OrderNo", "display_value": "Order No", "width": 16, "align": "left"},
                    {"name": "Type", "display_value": "Type", "width": 16, "align": "left"},
                    {"name": "Party", "display_value": "Party Ledger", "width": 22, "align": "left"},
                    {"name": "Total", "display_value": "Order Total (₹)", "width": 15, "align": "right"},
                    {"name": "Fulfilled", "display_value": "Fulfilled (₹)", "width": 15, "align": "right"},
                    {"name": "Pending", "display_value": "Pending (₹)", "width": 15, "align": "right"},
                    {"name": "Status", "display_value": "Status", "width": 12, "align": "center"}
                ]}
            ]
            for o in orders:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": o["order_date"], "width": 12, "align": "left"},
                    {"name": "OrderNo", "display_value": o["order_no"], "width": 16, "align": "left"},
                    {"name": "Type", "display_value": o["order_type"], "width": 16, "align": "left"},
                    {"name": "Party", "display_value": o["party_ledger_name"][:22], "width": 22, "align": "left"},
                    {"name": "Total", "display_value": f"{o['total_amount']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Fulfilled", "display_value": f"{o['fulfilled_amount']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Pending", "display_value": f"{o['pending_amount']:,.2f}", "width": 15, "align": "right"},
                    {"name": "Status", "display_value": o["status"].upper(), "width": 12, "align": "center"}
                ]})
            return {"report": report_name, "title": "Sales & Purchase Order Fulfillment Tracking", "parts": [{"name": "OrderPart", "lines": lines}], "company": company}

        # 28. GSTR-2B Inward Purchase ITC Reconciliation
        elif "gstr2b" in name_lower or "itcrecon" in name_lower:
            recons = self.db.get_gstr2b_reconciliations()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Period", "display_value": "Return Period", "width": 16, "align": "left"},
                    {"name": "Uploaded", "display_value": "Reconciliation Date", "width": 20, "align": "left"},
                    {"name": "TotalInv", "display_value": "Total Inv", "width": 12, "align": "right"},
                    {"name": "Matched", "display_value": "Matched", "width": 12, "align": "right"},
                    {"name": "Mismatch", "display_value": "Mismatch", "width": 12, "align": "right"},
                    {"name": "MissingBooks", "display_value": "Missing in Books", "width": 16, "align": "right"},
                    {"name": "Missing2B", "display_value": "Missing in 2B", "width": 16, "align": "right"}
                ]}
            ]
            for r in recons:
                lines.append({"name": "Row", "fields": [
                    {"name": "Period", "display_value": r["return_period"], "width": 16, "align": "left"},
                    {"name": "Uploaded", "display_value": r["uploaded_at"][:19], "width": 20, "align": "left"},
                    {"name": "TotalInv", "display_value": str(r["total_invoices"]), "width": 12, "align": "right"},
                    {"name": "Matched", "display_value": str(r["matched_count"]), "width": 12, "align": "right"},
                    {"name": "Mismatch", "display_value": str(r["mismatched_count"]), "width": 12, "align": "right"},
                    {"name": "MissingBooks", "display_value": str(r["missing_in_books_count"]), "width": 16, "align": "right"},
                    {"name": "Missing2B", "display_value": str(r["missing_in_2b_count"]), "width": 16, "align": "right"}
                ]})
            return {"report": report_name, "title": "GSTR-2B Inward Purchase ITC 4-Way Reconciliation", "parts": [{"name": "GSTR2BPart", "lines": lines}], "company": company}

        # 29. Landed Cost Summary
        elif "landedcost" in name_lower:
            vouchers = self.db.get_landed_cost_vouchers()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "LandedVch", "display_value": "Landed Vch No", "width": 18, "align": "left"},
                    {"name": "PurchaseVch", "display_value": "Purchase Vch", "width": 18, "align": "left"},
                    {"name": "ExpenseLedger", "display_value": "Expense Ledger", "width": 24, "align": "left"},
                    {"name": "Amount", "display_value": "Additional Cost (₹)", "width": 18, "align": "right"},
                    {"name": "Basis", "display_value": "Distr. Basis", "width": 16, "align": "center"}
                ]}
            ]
            tot_amt = sum(float(lcv["amount"]) for lcv in vouchers)
            for lcv in vouchers:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": lcv["date"], "width": 12, "align": "left"},
                    {"name": "LandedVch", "display_value": lcv["voucher_number"], "width": 18, "align": "left"},
                    {"name": "PurchaseVch", "display_value": lcv["purchase_voucher_no"], "width": 18, "align": "left"},
                    {"name": "ExpenseLedger", "display_value": lcv["expense_ledger"][:24], "width": 24, "align": "left"},
                    {"name": "Amount", "display_value": f"{lcv['amount']:,.2f}", "width": 18, "align": "right"},
                    {"name": "Basis", "display_value": lcv["allocation_method"].upper(), "width": 16, "align": "center"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL ADDITIONAL PURCHASE COSTS", "width": 72, "align": "left"},
                {"name": "Tot", "display_value": f"{tot_amt:,.2f}", "width": 18, "align": "right"},
                {"name": "Empty", "display_value": "", "width": 16, "align": "center"}
            ]})
            return {"report": report_name, "title": "Landed Cost & Additional Purchase Cost Summary", "parts": [{"name": "LandedPart", "lines": lines}], "company": company}

        # 30. Forex Revaluation Statement
        elif "forex" in name_lower or "currencygainloss" in name_lower:
            forex_res = self.db.calculate_forex_gain_loss()
            txns = forex_res.get("transactions", [])
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": "Vch No", "width": 16, "align": "left"},
                    {"name": "Currency", "display_value": "Currency", "width": 12, "align": "center"},
                    {"name": "ForeignAmt", "display_value": "Foreign Amt", "width": 16, "align": "right"},
                    {"name": "BookRate", "display_value": "Booking Rate", "width": 14, "align": "right"},
                    {"name": "CurrRate", "display_value": "Current Rate", "width": 14, "align": "right"},
                    {"name": "Impact", "display_value": "Gain / (Loss) (₹)", "width": 18, "align": "right"}
                ]}
            ]
            for t in txns:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": t["date"], "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": t["voucher_number"], "width": 16, "align": "left"},
                    {"name": "Currency", "display_value": t["currency"], "width": 12, "align": "center"},
                    {"name": "ForeignAmt", "display_value": f"{t['foreign_amount']:,.2f}", "width": 16, "align": "right"},
                    {"name": "BookRate", "display_value": f"{t['booking_rate']:,.2f}", "width": 14, "align": "right"},
                    {"name": "CurrRate", "display_value": f"{t['current_rate']:,.2f}", "width": 14, "align": "right"},
                    {"name": "Impact", "display_value": f"{t['forex_impact']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": f"NET UNREALIZED FOREX {forex_res.get('status', 'GAIN/LOSS').upper()}", "width": 84, "align": "left"},
                {"name": "Tot", "display_value": f"{forex_res.get('total_forex_gain_loss', 0.0):,.2f}", "width": 18, "align": "right"}
            ]})
            return {"report": report_name, "title": "Foreign Exchange Gain/Loss & Revaluation Summary", "parts": [{"name": "ForexPart", "lines": lines}], "company": company}

        # 31. Delivery & Receipt Challan Summary
        elif "challan" in name_lower or "deliverynote" in name_lower or "receiptnote" in name_lower:
            challans = self.db.get_unbilled_challans()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Challan Date", "width": 14, "align": "left"},
                    {"name": "ChallanNo", "display_value": "Challan No", "width": 18, "align": "left"},
                    {"name": "Type", "display_value": "Challan Type", "width": 16, "align": "left"},
                    {"name": "Party", "display_value": "Party Ledger", "width": 24, "align": "left"},
                    {"name": "OrderNo", "display_value": "Order Ref", "width": 16, "align": "left"},
                    {"name": "Amount", "display_value": "Challan Value (₹)", "width": 18, "align": "right"},
                    {"name": "Status", "display_value": "Status", "width": 14, "align": "center"}
                ]}
            ]
            for ch in challans:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": ch["date"], "width": 14, "align": "left"},
                    {"name": "ChallanNo", "display_value": ch["challan_no"], "width": 18, "align": "left"},
                    {"name": "Type", "display_value": ch["challan_type"], "width": 16, "align": "left"},
                    {"name": "Party", "display_value": ch["party_name"][:24], "width": 24, "align": "left"},
                    {"name": "OrderNo", "display_value": ch.get("order_no") or "-", "width": 16, "align": "left"},
                    {"name": "Amount", "display_value": f"{ch['total_amount']:,.2f}", "width": 18, "align": "right"},
                    {"name": "Status", "display_value": ch["status"].upper(), "width": 14, "align": "center"}
                ]})
            return {"report": report_name, "title": "Goods Delivery & Receipt Notes", "parts": [{"name": "ChallanPart", "lines": lines}], "company": company}

        # 32. TCS Register
        elif "tcs" in name_lower:
            tcs_entries = self.db.get_tcs_register()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": "Vch No", "width": 16, "align": "left"},
                    {"name": "Narration", "display_value": "Particulars", "width": 30, "align": "left"},
                    {"name": "Gross", "display_value": "Gross Amount (₹)", "width": 18, "align": "right"},
                    {"name": "TCSAmount", "display_value": "TCS Collected (₹)", "width": 18, "align": "right"}
                ]}
            ]
            tot_tcs = sum(float(t["tcs_amount"]) for t in tcs_entries)
            for t in tcs_entries:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": t["date"], "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": t["voucher_number"], "width": 16, "align": "left"},
                    {"name": "Narration", "display_value": (t.get("voucher_narration") or t.get("narration") or "")[:30], "width": 30, "align": "left"},
                    {"name": "Gross", "display_value": f"{t['total_amount']:,.2f}", "width": 18, "align": "right"},
                    {"name": "TCSAmount", "display_value": f"{t['tcs_amount']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL TCS COLLECTED", "width": 58, "align": "left"},
                {"name": "Tot", "display_value": f"{tot_tcs:,.2f}", "width": 36, "align": "right"}
            ]})
            return {"report": report_name, "title": "Tax Collected at Source (TCS) Register", "parts": [{"name": "TCSPart", "lines": lines}], "company": company}

        # 33. POS Invoicing Register
        elif "pos" in name_lower:
            pos_invoices = self.db.get_pos_invoices()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": "POS Inv No", "width": 16, "align": "left"},
                    {"name": "Total", "display_value": "Bill Total (₹)", "width": 16, "align": "right"},
                    {"name": "Cash", "display_value": "Cash (₹)", "width": 14, "align": "right"},
                    {"name": "Card", "display_value": "Card (₹)", "width": 14, "align": "right"},
                    {"name": "UPI", "display_value": "UPI (₹)", "width": 14, "align": "right"},
                    {"name": "Change", "display_value": "Change (₹)", "width": 14, "align": "right"}
                ]}
            ]
            tot_pos = sum(float(p["total_amount"]) for p in pos_invoices)
            for p in pos_invoices:
                lines.append({"name": "Row", "fields": [
                    {"name": "Date", "display_value": p["date"], "width": 12, "align": "left"},
                    {"name": "VchNo", "display_value": p["voucher_number"], "width": 16, "align": "left"},
                    {"name": "Total", "display_value": f"{p['total_amount']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Cash", "display_value": f"{p.get('cash_amount', 0.0):,.2f}", "width": 14, "align": "right"},
                    {"name": "Card", "display_value": f"{p.get('card_amount', 0.0):,.2f}", "width": 14, "align": "right"},
                    {"name": "UPI", "display_value": f"{p.get('upi_amount', 0.0):,.2f}", "width": 14, "align": "right"},
                    {"name": "Change", "display_value": f"{p.get('change_returned', 0.0):,.2f}", "width": 14, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL POS TURNOVER", "width": 28, "align": "left"},
                {"name": "Tot", "display_value": f"{tot_pos:,.2f}", "width": 16, "align": "right"},
                {"name": "E1", "display_value": "", "width": 56, "align": "right"}
            ]})
            return {"report": report_name, "title": "Point of Sale (POS) Fast Billing Register", "parts": [{"name": "POSPart", "lines": lines}], "company": company}

        # 34. Subcontracting & Job Work Register
        elif "jobwork" in name_lower or "job_work" in name_lower:
            orders = self.db.get_job_work_orders()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "No", "display_value": "Order No", "width": 16, "align": "left"},
                    {"name": "Type", "display_value": "Type", "width": 14, "align": "left"},
                    {"name": "Worker", "display_value": "Job Worker", "width": 22, "align": "left"},
                    {"name": "Item", "display_value": "Finished Item", "width": 24, "align": "left"},
                    {"name": "Qty", "display_value": "Qty", "width": 10, "align": "right"},
                    {"name": "Status", "display_value": "Status", "width": 14, "align": "center"}
                ]}
            ]
            for o in orders:
                lines.append({"name": "Row", "fields": [
                    {"name": "No", "display_value": o["order_no"], "width": 16, "align": "left"},
                    {"name": "Type", "display_value": o["order_type"], "width": 14, "align": "left"},
                    {"name": "Worker", "display_value": o["job_worker_name"], "width": 22, "align": "left"},
                    {"name": "Item", "display_value": o["finished_item_name"][:24], "width": 24, "align": "left"},
                    {"name": "Qty", "display_value": f"{o['finished_qty']:.2f}", "width": 10, "align": "right"},
                    {"name": "Status", "display_value": o["status"], "width": 14, "align": "center"}
                ]})
            return {"report": report_name, "title": "Subcontracting & Job Work Register", "parts": [{"name": "JWPart", "lines": lines}], "company": company}

        # 35. Quality Control & Inspection Register
        elif "quality" in name_lower or "inspection" in name_lower:
            inspections = self.db.get_quality_inspections()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "No", "display_value": "Inspection No", "width": 18, "align": "left"},
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "Ref", "display_value": "Reference", "width": 16, "align": "left"},
                    {"name": "Item", "display_value": "Stock Item", "width": 25, "align": "left"},
                    {"name": "Status", "display_value": "QC Status", "width": 14, "align": "center"},
                    {"name": "Remarks", "display_value": "Remarks", "width": 25, "align": "left"}
                ]}
            ]
            for q in inspections:
                lines.append({"name": "Row", "fields": [
                    {"name": "No", "display_value": q["inspection_no"], "width": 18, "align": "left"},
                    {"name": "Date", "display_value": q["inspection_date"], "width": 12, "align": "left"},
                    {"name": "Ref", "display_value": f"{q['reference_type']} {q['reference_no']}"[:16], "width": 16, "align": "left"},
                    {"name": "Item", "display_value": q["stock_item_name"][:25], "width": 25, "align": "left"},
                    {"name": "Status", "display_value": q["status"], "width": 14, "align": "center"},
                    {"name": "Remarks", "display_value": (q.get("remarks") or "Verified")[:25], "width": 25, "align": "left"}
                ]})
            return {"report": report_name, "title": "Quality Control & Inspection Register", "parts": [{"name": "QIPart", "lines": lines}], "company": company}

        # 36. Multi-Currency Historical Rates Register
        elif "currencyexchange" in name_lower or "currency" in name_lower:
            rates = self.db.get_currency_exchange_rates()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Sym", "display_value": "Symbol", "width": 10, "align": "center"},
                    {"name": "Name", "display_value": "Formal Name", "width": 18, "align": "left"},
                    {"name": "Date", "display_value": "Effective Date", "width": 16, "align": "left"},
                    {"name": "Std", "display_value": "Standard Rate", "width": 16, "align": "right"},
                    {"name": "Buy", "display_value": "Buying Rate", "width": 16, "align": "right"},
                    {"name": "Sell", "display_value": "Selling Rate", "width": 16, "align": "right"}
                ]}
            ]
            for r in rates:
                lines.append({"name": "Row", "fields": [
                    {"name": "Sym", "display_value": r["currency_symbol"], "width": 10, "align": "center"},
                    {"name": "Name", "display_value": r["formal_name"], "width": 18, "align": "left"},
                    {"name": "Date", "display_value": r["effective_date"], "width": 16, "align": "left"},
                    {"name": "Std", "display_value": f"{r['standard_rate']:.4f}", "width": 16, "align": "right"},
                    {"name": "Buy", "display_value": f"{r['buying_rate']:.4f}", "width": 16, "align": "right"},
                    {"name": "Sell", "display_value": f"{r['selling_rate']:.4f}", "width": 16, "align": "right"}
                ]})
            return {"report": report_name, "title": "Multi-Currency & Historical Rates Register", "parts": [{"name": "CurrPart", "lines": lines}], "company": company}

        # 37. Payment & Advance Bill Reconciliation
        elif "paymentrecon" in name_lower or "payment_recon" in name_lower:
            ledgers = self.db.get_ledgers()
            debtors = [l["name"] for l in ledgers if "Debtors" in l["group_name"] or "Creditors" in l["group_name"]]
            target_party = kwargs.get("ledger") or (debtors[0] if debtors else "Cash")
            recon_data = self.db.get_unreconciled_bills_and_payments(target_party)
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Party", "display_value": "Party Ledger", "width": 25, "align": "left"},
                    {"name": "Ref", "display_value": "Bill Ref No", "width": 18, "align": "left"},
                    {"name": "Amt", "display_value": "Bill Total (₹)", "width": 18, "align": "right"},
                    {"name": "Pend", "display_value": "Pending (₹)", "width": 18, "align": "right"},
                    {"name": "Status", "display_value": "Settlement Status", "width": 18, "align": "center"}
                ]}
            ]
            for b in recon_data.get("unpaid_bills", []):
                lines.append({"name": "Row", "fields": [
                    {"name": "Party", "display_value": target_party[:25], "width": 25, "align": "left"},
                    {"name": "Ref", "display_value": b["ref_no"], "width": 18, "align": "left"},
                    {"name": "Amt", "display_value": f"{b['amount']:,.2f}", "width": 18, "align": "right"},
                    {"name": "Pend", "display_value": f"{b['pending_amount']:,.2f}", "width": 18, "align": "right"},
                    {"name": "Status", "display_value": "Pending Allocation", "width": 18, "align": "center"}
                ]})
            return {"report": report_name, "title": "Payment & Advance Bill Reconciliation", "parts": [{"name": "ReconPart", "lines": lines}], "company": company}

        # 38. MCA Rule 3(1) Edit Log Audit Diff Viewer
        elif "auditdiff" in name_lower or "diff" in name_lower:
            cur = self.db.conn.cursor()
            cur.execute("""
                SELECT entity_type, entity_id, timestamp, user_name, action, details
                FROM edit_log
                WHERE action IN ('ALTER', 'DELETE', 'CANCEL')
                ORDER BY id DESC LIMIT 50
            """)
            altered = [dict(r) for r in cur.fetchall()]
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Entity", "display_value": "Entity / Vch", "width": 16, "align": "left"},
                    {"name": "Time", "display_value": "Timestamp", "width": 20, "align": "left"},
                    {"name": "User", "display_value": "Modified By", "width": 14, "align": "left"},
                    {"name": "Action", "display_value": "Action", "width": 12, "align": "center"},
                    {"name": "Details", "display_value": "Audit Trail Log Detail", "width": 45, "align": "left"}
                ]}
            ]
            for a in altered:
                lines.append({"name": "Row", "fields": [
                    {"name": "Entity", "display_value": f"{a['entity_type']} #{a['entity_id']}"[:16], "width": 16, "align": "left"},
                    {"name": "Time", "display_value": a["timestamp"][:19], "width": 20, "align": "left"},
                    {"name": "User", "display_value": a["user_name"], "width": 14, "align": "left"},
                    {"name": "Action", "display_value": a["action"], "width": 12, "align": "center"},
                    {"name": "Details", "display_value": (a.get("details") or "")[:45], "width": 45, "align": "left"}
                ]})
            return {"report": report_name, "title": "Statutory Audit Trail (MCA Edit Log Diff)", "parts": [{"name": "ADPart", "lines": lines}], "company": company}

        # 39. MSME Section 43B(h) 45-Day Compliance
        elif "msme" in name_lower:
            msme_data = self.db.get_msme_compliance_report()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Party", "display_value": "MSME Supplier", "width": 25, "align": "left"},
                    {"name": "Ref", "display_value": "Bill Ref", "width": 16, "align": "left"},
                    {"name": "Amt", "display_value": "Payable (₹)", "width": 16, "align": "right"},
                    {"name": "Age", "display_value": "Age (Days)", "width": 12, "align": "right"},
                    {"name": "Status", "display_value": "43B(h) Status", "width": 22, "align": "center"},
                    {"name": "Int", "display_value": "Interest Sec 16 (₹)", "width": 18, "align": "right"}
                ]}
            ]
            all_invoices = msme_data.get("compliant_invoices", []) + msme_data.get("overdue_invoices", [])
            for inv in all_invoices:
                lines.append({"name": "Row", "fields": [
                    {"name": "Party", "display_value": inv["ledger_name"][:25], "width": 25, "align": "left"},
                    {"name": "Ref", "display_value": inv["ref_name"][:16], "width": 16, "align": "left"},
                    {"name": "Amt", "display_value": f"{inv['pending_amount']:,.2f}", "width": 16, "align": "right"},
                    {"name": "Age", "display_value": str(inv["age_days"]), "width": 12, "align": "right"},
                    {"name": "Status", "display_value": inv["status"], "width": 22, "align": "center"},
                    {"name": "Int", "display_value": f"{inv['interest_liability']:,.2f}", "width": 18, "align": "right"}
                ]})
            lines.append({"name": "Total", "fields": [
                {"name": "Lbl", "display_value": "TOTAL MSME PAYABLES / DISALLOWANCE", "width": 41, "align": "left"},
                {"name": "Tot", "display_value": f"{msme_data.get('total_pending_payables', 0.0):,.2f}", "width": 16, "align": "right"},
                {"name": "E1", "display_value": "", "width": 12, "align": "right"},
                {"name": "Disallow", "display_value": f"Disallowed: ₹{msme_data.get('disallowed_amount_sec_43bh', 0.0):,.2f}", "width": 22, "align": "center"},
                {"name": "E2", "display_value": "", "width": 18, "align": "right"}
            ]})
            return {"report": report_name, "title": "MSME Section 43B(h) 45-Day Compliance Statement", "parts": [{"name": "MSMEPart", "lines": lines}], "company": company}

        # 40. Schedule III Division I Balance Sheet
        elif "scheduleiii" in name_lower or "schedule_iii" in name_lower or "schedule3" in name_lower:
            s3 = self.db.get_schedule_iii_balance_sheet()
            eq = s3["equity_and_liabilities"]
            ast = s3["assets"]
            lines = [
                {"name": "Header", "fields": [
                    {"name": "Particulars", "display_value": "Particulars", "width": 45, "align": "left"},
                    {"name": "Note", "display_value": "Note", "width": 8, "align": "center"},
                    {"name": "Amount", "display_value": "Amount (₹)", "width": 25, "align": "right"}
                ]},
                {"name": "Section", "fields": [{"name": "P", "display_value": "I. EQUITY AND LIABILITIES", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": "", "width": 25, "align": "right"}]},
                {"name": "Sub", "fields": [{"name": "P", "display_value": "  (1) Shareholders' Funds", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['shareholders_funds']['total']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (a) Share Capital", "width": 45, "align": "left"}, {"name": "N", "display_value": "1", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['shareholders_funds']['share_capital']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (b) Reserves & Surplus", "width": 45, "align": "left"}, {"name": "N", "display_value": "2", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['shareholders_funds']['reserves_and_surplus']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Sub", "fields": [{"name": "P", "display_value": "  (2) Non-Current Liabilities", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['non_current_liabilities']['total']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (a) Long-Term Borrowings", "width": 45, "align": "left"}, {"name": "N", "display_value": "3", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['non_current_liabilities']['long_term_borrowings']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Sub", "fields": [{"name": "P", "display_value": "  (3) Current Liabilities", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['current_liabilities']['total']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (a) Short-Term Borrowings", "width": 45, "align": "left"}, {"name": "N", "display_value": "4", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['current_liabilities']['short_term_borrowings']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (b) Trade Payables", "width": 45, "align": "left"}, {"name": "N", "display_value": "5", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['current_liabilities']['trade_payables']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (c) Other Current Liabilities", "width": 45, "align": "left"}, {"name": "N", "display_value": "6", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['current_liabilities']['other_current_liabilities']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "TOTAL EQUITY AND LIABILITIES", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{eq['total_equity_and_liabilities']:,.2f}", "width": 25, "align": "right"}]},

                {"name": "Section", "fields": [{"name": "P", "display_value": "II. ASSETS", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": "", "width": 25, "align": "right"}]},
                {"name": "Sub", "fields": [{"name": "P", "display_value": "  (1) Non-Current Assets", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['non_current_assets']['total']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (a) Property, Plant & Equipment", "width": 45, "align": "left"}, {"name": "N", "display_value": "7", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['non_current_assets']['property_plant_equipment']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (b) Non-Current Investments", "width": 45, "align": "left"}, {"name": "N", "display_value": "8", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['non_current_assets']['non_current_investments']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Sub", "fields": [{"name": "P", "display_value": "  (2) Current Assets", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['current_assets']['total']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (a) Inventories", "width": 45, "align": "left"}, {"name": "N", "display_value": "9", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['current_assets']['inventories']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (b) Trade Receivables", "width": 45, "align": "left"}, {"name": "N", "display_value": "10", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['current_assets']['trade_receivables']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (c) Cash & Cash Equivalents", "width": 45, "align": "left"}, {"name": "N", "display_value": "11", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['current_assets']['cash_and_bank_balances']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Row", "fields": [{"name": "P", "display_value": "    (d) Other Current Assets", "width": 45, "align": "left"}, {"name": "N", "display_value": "12", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['current_assets']['other_current_assets']:,.2f}", "width": 25, "align": "right"}]},
                {"name": "Total", "fields": [{"name": "P", "display_value": "TOTAL ASSETS", "width": 45, "align": "left"}, {"name": "N", "display_value": "", "width": 8, "align": "center"}, {"name": "A", "display_value": f"{ast['total_assets']:,.2f}", "width": 25, "align": "right"}]}
            ]
            return {"report": report_name, "title": "Schedule III Division I Balance Sheet", "parts": [{"name": "S3Part", "lines": lines}], "company": company}

        # 41. Rejections In & Out Register
        elif "rejection" in name_lower:
            rejections = self.db.get_rejections()
            lines = [
                {"name": "Header", "fields": [
                    {"name": "No", "display_value": "Rejection No", "width": 18, "align": "left"},
                    {"name": "Type", "display_value": "Type", "width": 16, "align": "left"},
                    {"name": "Party", "display_value": "Party Name", "width": 24, "align": "left"},
                    {"name": "Date", "display_value": "Date", "width": 12, "align": "left"},
                    {"name": "Order", "display_value": "Order Ref", "width": 16, "align": "left"},
                    {"name": "Status", "display_value": "Status", "width": 18, "align": "center"}
                ]}
            ]
            for r in rejections:
                lines.append({"name": "Row", "fields": [
                    {"name": "No", "display_value": r["rejection_no"], "width": 18, "align": "left"},
                    {"name": "Type", "display_value": r["rejection_type"], "width": 16, "align": "left"},
                    {"name": "Party", "display_value": r["party_name"][:24], "width": 24, "align": "left"},
                    {"name": "Date", "display_value": r["date"], "width": 12, "align": "left"},
                    {"name": "Order", "display_value": (r.get("order_no") or "")[:16], "width": 16, "align": "left"},
                    {"name": "Status", "display_value": r["status"], "width": 18, "align": "center"}
                ]})
            return {"report": report_name, "title": "Rejections In & Out Register", "parts": [{"name": "RejPart", "lines": lines}], "company": company}

        # 25. Day Book (Default)
        from_date = kwargs.get("from_date")
        to_date = kwargs.get("to_date")
        vouchers = self.db.get_vouchers(from_date=from_date, to_date=to_date)
        lines = [
            {"name": "Header", "fields": [
                {"name": "Date", "display_value": "Date", "width": 14, "align": "left"},
                {"name": "Particulars", "display_value": "Particulars", "width": 26, "align": "left"},
                {"name": "VchType", "display_value": "Vch Type", "width": 15, "align": "left"},
                {"name": "VchNo", "display_value": "Vch No.", "width": 15, "align": "left"},
                {"name": "Amount", "display_value": "Amount (₹)", "width": 16, "align": "right"},
                {"name": "Action", "display_value": "Action", "width": 14, "align": "center"}
            ]}
        ]
        for v in vouchers:
            particulars = ", ".join(e["ledger_name"] for e in v.get("entries", []))
            lines.append({"name": "Row", "voucher_id": v.get("id"), "fields": [
                {"name": "Date", "display_value": v["date"], "width": 14, "align": "left"},
                {"name": "Particulars", "display_value": particulars[:26], "width": 26, "align": "left"},
                {"name": "VchType", "display_value": v["voucher_type"], "width": 15, "align": "left"},
                {"name": "VchNo", "display_value": v["voucher_number"], "voucher_id": v.get("id"), "width": 15, "align": "left"},
                {"name": "Amount", "display_value": f"{v['total_amount']:,.2f}", "width": 16, "align": "right"},
                {"name": "Action", "display_value": f"[Alter #{v.get('id', '')}]", "voucher_id": v.get("id"), "width": 14, "align": "center"}
            ]})
        return {"report": report_name, "title": "Day Book", "parts": [{"name": "DBPart", "lines": lines}], "company": company}
