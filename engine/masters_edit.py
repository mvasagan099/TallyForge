"""Altering and deleting masters (ledgers, stock items, groups) the way TallyPrime's Alter / Delete do.

Everything that refers to a master by name follows a rename (vouchers, bills, bank rows, price lists, orders, budgets ...), all in
one transaction, so a refused change leaves nothing half done. A master that is used by a voucher cannot be deleted. TallyPrime's
reserved groups keep their names and place, because reports are built on them.

A company imported from TallyPrime brings its masters in by name: renaming or deleting an imported master here does not change
TallyPrime, and importing again would bring the original back as a new master.
"""
import re

RESERVED_GROUPS = {
    "Primary", "Branch / Divisions", "Capital Account", "Current Assets", "Current Liabilities", "Direct Expenses", "Direct Incomes",
    "Fixed Assets", "Indirect Expenses", "Indirect Incomes", "Investments", "Loans (Liability)", "Misc. Expenses (ASSET)",
    "Purchase Accounts", "Sales Accounts", "Suspense A/c", "Bank Accounts", "Bank OD A/c", "Cash-in-Hand", "Deposits (Asset)",
    "Duties & Taxes", "Loans & Advances (Asset)", "Provisions", "Reserves & Surplus", "Retained Earnings", "Secured Loans",
    "Stock-in-Hand", "Sundry Creditors", "Sundry Debtors", "Unsecured Loans"}

# (table, column) pairs that hold the name; tables that belong to a voucher are limited to this company's vouchers
LEDGER_REFS = [("voucher_entry", "ledger_name", True), ("bill_allocation", "ledger_name", True), ("bank_reconciliation", "bank_ledger_name", False),
               ("budget", "ledger_name", False), ("cheque_book", "bank_ledger_name", False), ("fixed_asset", "asset_ledger", False),
               ("fixed_asset", "depr_expense_ledger", False), ("fixed_asset", "accum_depr_ledger", False),
               ("landed_cost_voucher", "expense_ledger", False), ("ledger_price_list", "ledger_name", False), ("order_tracking", "party_name", False),
               ("challan_tracking", "party_name", False), ("rejection_tracking", "party_name", False)]
ITEM_REFS = [("voucher_inventory", "stock_item_name", True), ("stock_batch", "stock_item_name", False), ("bom_header", "finished_item_name", False),
             ("bom_item", "component_item_name", False), ("price_list_item", "stock_item_name", False), ("price_list_rate", "stock_item_name", False),
             ("landed_cost_allocation", "stock_item_name", False), ("order_item", "stock_item_name", False), ("job_work_order", "finished_item_name", False),
             ("job_work_item", "stock_item_name", False), ("quality_inspection", "stock_item_name", False), ("order_line", "stock_item_name", False),
             ("order_allocation", "stock_item_name", False), ("note_original", "stock_item_name", False)]
GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

LEDGER_FIELDS = {"group_name": str, "opening_balance": float, "opening_balance_type": str, "gstin": str, "state": str, "state_code": str,
                 "address": str, "pincode": str, "pan": str, "credit_period_days": int, "credit_limit": float, "maintain_bill_by_bill": int,
                 "contact_person": str, "bank_name": str, "account_no": str, "hsn_sac_code": str, "tax_rate": float}
ITEM_FIELDS = {"group_name": str, "uom_symbol": str, "hsn_code": str, "gst_rate": float, "opening_qty": float, "opening_rate": float,
               "reorder_level": float, "reorder_qty": float, "part_number": str, "standard_cost": float}


def _has(db, table, column):
    return any(r[1] == column for r in db.conn.execute(f"PRAGMA table_info({table})"))


def _rename_refs(db, refs, old, new):
    for table, column, by_voucher in refs:
        if not _has(db, table, column):
            continue
        if by_voucher and _has(db, table, "voucher_id"):
            db.conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ? AND voucher_id IN (SELECT id FROM voucher WHERE company_id = ?)",
                            (new, old, db.active_company_id))
        else:
            db.conn.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new, old))


def _used(db, refs, name):
    for table, column, _by in refs:
        if table in ("voucher_entry", "voucher_inventory", "bill_allocation") and _has(db, table, column):
            if db.conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (name,)).fetchone():
                return True
    return False


def _clean(fields, changes):
    out = {}
    for key, kind in fields.items():
        if key not in changes or changes[key] is None:
            continue
        value = changes[key]
        try:
            out[key] = kind(value) if kind is not str else str(value).strip()
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' is not a valid {kind.__name__}")
    return out


def _update(db, table, name, values, company_scoped=True):
    sets = ", ".join(f"{k} = ?" for k in values)
    where = "name = ?" + (" AND company_id = ?" if company_scoped and _has(db, table, "company_id") else "")
    args = list(values.values()) + [name] + ([db.active_company_id] if company_scoped and _has(db, table, "company_id") else [])
    db.conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", args)


# the simple masters: (table, key column, editable fields, where the name is used)
SIMPLE = {"units": ("uom", "symbol", {"formal_name": str, "decimal_places": int}, [("stock_item", "uom_symbol"), ("compound_uom", "major_uom"), ("compound_uom", "minor_uom")]),
          "godowns": ("godown", "name", {"location": str, "parent": str}, [("stock_item", "godown_name"), ("voucher_inventory", "godown_name")]),
          "cost_centres": ("cost_centre", "name", {"category_name": str, "parent": str}, [("voucher_entry", "cost_centre_name")]),
          "employees": ("employee", "emp_code", {"name": str, "department": str, "designation": str, "basic_salary": float, "pan": str, "pf_uan": str,
                                                 "esi_number": str, "bank_name": str, "account_no": str, "employee_group": str, "date_of_joining": str},
                        [("payroll_month_input", "emp_code"), ("salary_structure", "emp_code"), ("attendance", "emp_code")])}
PROTECTED = {"godowns": {"Main Location"}}
KINDS = ("ledgers", "stock_items", "groups") + tuple(SIMPLE)


def _simple_used(db, kind, name):
    table, key, _fields, refs = SIMPLE[kind]
    if kind == "employees":                                   # payroll inputs or attendance exist for this person (the salary structure goes with them)
        return any(_has(db, t, c) and db.conn.execute(f"SELECT 1 FROM {t} WHERE {c} = ? LIMIT 1", (name,)).fetchone()
                   for t, c in refs if t != "salary_structure")
    for t, c in refs:
        if t in ("stock_item", "compound_uom"):
            if _has(db, t, c) and db.conn.execute(f"SELECT 1 FROM {t} WHERE {c} = ? LIMIT 1", (name,)).fetchone():
                return True
        elif _has(db, t, c) and db.conn.execute(f"SELECT 1 FROM {t} WHERE {c} = ? LIMIT 1", (name,)).fetchone():
            return True
    return False


def _simple_alter(db, kind, name, changes, new_name):
    table, key, fields, refs = SIMPLE[kind]
    if not db.conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (name,)).fetchone():
        raise ValueError(f"{name!r} does not exist")
    if name in PROTECTED.get(kind, ()) and new_name != name:
        raise ValueError(f"'{name}' is the default location and keeps its name")
    values = _clean(fields, changes)
    if kind == "employees":
        if "name" in values and not values["name"]:
            raise ValueError("An employee needs a name")
        if values.get("basic_salary", 0) < 0:
            raise ValueError("A basic salary cannot be negative")
        if values.get("pan") and not re.match(r"^[A-Z]{5}\d{4}[A-Z]$", values["pan"].upper()):
            raise ValueError("A PAN is five letters, four digits and a letter")
        if values.get("pan"):
            values["pan"] = values["pan"].upper()
        if values.get("date_of_joining") and not db._valid_iso_date(values["date_of_joining"]):
            raise ValueError("Date of joining: use YYYY-MM-DD")
    if kind == "units" and values.get("decimal_places", 0) not in range(0, 6):
        raise ValueError("Decimal places must be between 0 and 5")
    if new_name != name:
        if db.conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (new_name,)).fetchone():
            raise ValueError(f"{new_name!r} already exists")
        values[key] = new_name
    if kind in ("godowns", "cost_centres") and values.get("parent") not in (None, "", "Primary"):
        if values["parent"] == name or not db.conn.execute(f"SELECT 1 FROM {table} WHERE name = ?", (values["parent"],)).fetchone():
            raise ValueError(f"'{values['parent']}' cannot be the parent")
    if values:
        db.conn.execute(f"UPDATE {table} SET {', '.join(k + ' = ?' for k in values)} WHERE {key} = ?", list(values.values()) + [name])
    if new_name != name:
        for t, c in refs:
            if not _has(db, t, c):
                continue
            if t in ("voucher_entry", "voucher_inventory"):
                db.conn.execute(f"UPDATE {t} SET {c} = ? WHERE {c} = ? AND voucher_id IN (SELECT id FROM voucher WHERE company_id = ?)", (new_name, name, db.active_company_id))
            else:
                db.conn.execute(f"UPDATE {t} SET {c} = ? WHERE {c} = ?", (new_name, name))
        if kind in ("godowns", "cost_centres"):
            db.conn.execute(f"UPDATE {table} SET parent = ? WHERE parent = ?", (new_name, name))
        if kind == "employees" and _has(db, "payroll_month_input", "emp_code"):
            pass                                              # covered by the references above


def detail(db, kind, name):
    if kind in SIMPLE:
        table, key, _fields, _refs = SIMPLE[kind]
        row = db.conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"{name!r} does not exist")
        out = dict(row)
        out["full_name"] = out.get("name")                    # an employee's name (the key is the employee code)
        out["name"] = out.get(key)
        out["used_in_vouchers"] = _simple_used(db, kind, name)
        out["protected"] = name in PROTECTED.get(kind, ())
        return out
    table = {"ledgers": "ledger", "stock_items": "stock_item", "groups": "account_group"}.get(kind)
    if not table:
        raise ValueError("Masters that can be altered here: ledgers, stock items, groups, units, godowns, cost centres and employees")
    row = db.conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise ValueError(f"{name!r} does not exist")
    out = dict(row)
    out["used_in_vouchers"] = _used(db, LEDGER_REFS if kind == "ledgers" else ITEM_REFS, name) if kind != "groups" else False
    if kind == "groups":
        out["reserved"] = name in RESERVED_GROUPS
        out["children"] = db.conn.execute("SELECT (SELECT COUNT(*) FROM account_group WHERE parent = ?) + (SELECT COUNT(*) FROM ledger WHERE group_name = ?)", (name, name)).fetchone()[0]
    return out


def alter(db, kind, name, changes):
    """Alter a master. ``changes`` may carry ``new_name`` and any of the fields of that master."""
    changes = changes or {}
    new_name = (changes.get("new_name") or name).strip()
    if not new_name:
        raise ValueError("A name cannot be empty")
    if not db.conn.in_transaction:
        db.conn.execute("BEGIN")                                # the pragma only lasts for the transaction it is set in
    db.conn.execute("PRAGMA defer_foreign_keys = ON")
    try:
        if kind == "ledgers":
            if not db.conn.execute("SELECT 1 FROM ledger WHERE name = ? AND company_id = ?", (name, db.active_company_id)).fetchone():
                raise ValueError(f"Ledger {name!r} does not exist")
            values = _clean(LEDGER_FIELDS, changes)
            if "group_name" in values and not db.conn.execute("SELECT 1 FROM account_group WHERE name = ?", (values["group_name"],)).fetchone():
                raise ValueError(f"Group {values['group_name']!r} does not exist")
            if values.get("opening_balance", 0) < 0:
                raise ValueError("An opening balance cannot be negative: choose Dr or Cr")
            if "opening_balance_type" in values and values["opening_balance_type"] not in ("Dr", "Cr"):
                raise ValueError("The opening balance is Dr or Cr")
            if values.get("gstin") and not GSTIN.match(values["gstin"].upper()):
                raise ValueError("That GSTIN does not have the 15-character GST format")
            if values.get("gstin"):
                values["gstin"] = values["gstin"].upper()
                values.setdefault("state_code", values["gstin"][:2])
            if new_name != name:
                if db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (new_name,)).fetchone():
                    raise ValueError(f"A ledger named {new_name!r} already exists")
                values["name"] = new_name
            if values:
                _update(db, "ledger", name, values)
            if new_name != name:
                _rename_refs(db, LEDGER_REFS, name, new_name)
        elif kind == "stock_items":
            if not db.conn.execute("SELECT 1 FROM stock_item WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Stock item {name!r} does not exist")
            values = _clean(ITEM_FIELDS, changes)
            if "uom_symbol" in values and not db.conn.execute("SELECT 1 FROM uom WHERE symbol = ?", (values["uom_symbol"],)).fetchone():
                raise ValueError(f"Unit {values['uom_symbol']!r} does not exist")
            for key in ("opening_qty", "opening_rate", "gst_rate", "standard_cost"):
                if values.get(key, 0) < 0:
                    raise ValueError(f"'{key}' cannot be negative")
            if values.get("hsn_code") and not values["hsn_code"].isdigit():
                raise ValueError("An HSN / SAC code is made of digits only")
            if "opening_qty" in values or "opening_rate" in values:
                current = db.conn.execute("SELECT opening_qty, opening_rate FROM stock_item WHERE name = ?", (name,)).fetchone()
                values["opening_value"] = round(values.get("opening_qty", current[0]) * values.get("opening_rate", current[1]), 2)
            if new_name != name:
                if db.conn.execute("SELECT 1 FROM stock_item WHERE name = ?", (new_name,)).fetchone():
                    raise ValueError(f"A stock item named {new_name!r} already exists")
                values["name"] = new_name
            if values:
                _update(db, "stock_item", name, values)
            if new_name != name:
                _rename_refs(db, ITEM_REFS, name, new_name)
        elif kind == "groups":
            if not db.conn.execute("SELECT 1 FROM account_group WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Group {name!r} does not exist")
            if name in RESERVED_GROUPS:
                raise ValueError(f"'{name}' is one of TallyPrime's reserved groups: its name and place cannot change")
            values = {}
            parent = changes.get("parent")
            if parent is not None:
                parent = str(parent).strip()
                if not db.conn.execute("SELECT 1 FROM account_group WHERE name = ?", (parent,)).fetchone():
                    raise ValueError(f"Group {parent!r} does not exist")
                walk, seen = parent, set()
                while walk and walk not in seen:                    # a group cannot sit under itself or one of its own subgroups
                    if walk == name:
                        raise ValueError("A group cannot be placed under itself or one of its own subgroups")
                    seen.add(walk)
                    row = db.conn.execute("SELECT parent FROM account_group WHERE name = ?", (walk,)).fetchone()
                    walk = row[0] if row else None
                values["parent"] = parent
            for key in ("is_revenue", "affects_gross"):
                if key in changes and changes[key] is not None:
                    values[key] = 1 if changes[key] in (1, True, "1", "Yes", "yes", "true") else 0
            if new_name != name:
                if db.conn.execute("SELECT 1 FROM account_group WHERE name = ?", (new_name,)).fetchone():
                    raise ValueError(f"A group named {new_name!r} already exists")
                values["name"] = new_name
            if values:
                db.conn.execute(f"UPDATE account_group SET {', '.join(k + ' = ?' for k in values)} WHERE name = ?", list(values.values()) + [name])
            if new_name != name:
                db.conn.execute("UPDATE account_group SET parent = ? WHERE parent = ?", (new_name, name))
                db.conn.execute("UPDATE ledger SET group_name = ? WHERE group_name = ?", (new_name, name))
                db.conn.execute("UPDATE stock_item SET group_name = ? WHERE group_name = ?", (new_name, name))
        elif kind in SIMPLE:
            _simple_alter(db, kind, name, changes, new_name)
        else:
            raise ValueError("Masters that can be altered here: ledgers, stock items, groups, units, godowns, cost centres and employees")
        db._log_edit({"ledgers": "Ledger", "stock_items": "StockItem", "groups": "Group", "units": "Unit", "godowns": "Godown", "cost_centres": "CostCentre", "employees": "Employee"}[kind], new_name, "ALTER",
                     f"Altered {name}" + (f" (renamed to {new_name})" if new_name != name else ""))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return {"name": new_name, "renamed": new_name != name}


def delete(db, kind, name):
    """Delete a master that no voucher uses."""
    try:
        if kind == "ledgers":
            if not db.conn.execute("SELECT 1 FROM ledger WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Ledger {name!r} does not exist")
            if _used(db, LEDGER_REFS, name):
                raise ValueError(f"Ledger '{name}' is used in vouchers and cannot be deleted. Alter it, or delete those vouchers first.")
            db.conn.execute("DELETE FROM ledger WHERE name = ?", (name,))
        elif kind == "stock_items":
            if not db.conn.execute("SELECT 1 FROM stock_item WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Stock item {name!r} does not exist")
            if _used(db, ITEM_REFS, name):
                raise ValueError(f"Stock item '{name}' is used in vouchers and cannot be deleted.")
            db.conn.execute("DELETE FROM stock_item WHERE name = ?", (name,))
        elif kind == "groups":
            if name in RESERVED_GROUPS:
                raise ValueError(f"'{name}' is one of TallyPrime's reserved groups and cannot be deleted")
            if not db.conn.execute("SELECT 1 FROM account_group WHERE name = ?", (name,)).fetchone():
                raise ValueError(f"Group {name!r} does not exist")
            if db.conn.execute("SELECT (SELECT COUNT(*) FROM account_group WHERE parent = ?) + (SELECT COUNT(*) FROM ledger WHERE group_name = ?)", (name, name)).fetchone()[0]:
                raise ValueError(f"Group '{name}' still has ledgers or subgroups under it")
            db.conn.execute("DELETE FROM account_group WHERE name = ?", (name,))
        elif kind in SIMPLE:
            table, key, _fields, _refs = SIMPLE[kind]
            if not db.conn.execute(f"SELECT 1 FROM {table} WHERE {key} = ?", (name,)).fetchone():
                raise ValueError(f"{name!r} does not exist")
            if name in PROTECTED.get(kind, ()):
                raise ValueError(f"'{name}' is the default location and cannot be deleted")
            if _simple_used(db, kind, name):
                raise ValueError(f"'{name}' is in use and cannot be deleted")
            if kind in ("godowns", "cost_centres") and db.conn.execute(f"SELECT 1 FROM {table} WHERE parent = ?", (name,)).fetchone():
                raise ValueError(f"'{name}' still has others under it")
            if kind == "employees" and _has(db, "salary_structure", "emp_code"):
                db.conn.execute("DELETE FROM salary_structure WHERE emp_code = ?", (name,))
            db.conn.execute(f"DELETE FROM {table} WHERE {key} = ?", (name,))
        else:
            raise ValueError("Masters that can be deleted here: ledgers, stock items, groups, units, godowns, cost centres and employees")
        db._log_edit({"ledgers": "Ledger", "stock_items": "StockItem", "groups": "Group", "units": "Unit", "godowns": "Godown", "cost_centres": "CostCentre", "employees": "Employee"}[kind], name, "DELETE", f"Deleted {name}")
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise
    return {"deleted": name}
