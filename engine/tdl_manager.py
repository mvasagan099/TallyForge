"""
Offline TDL Extension Manager
Provides complete lifecycle management for TDL (Tally Definition Language) extensions:
1. Discovery and listing of installed .tdl files
2. Syntax validation with line numbers and severity
3. Importing new .tdl extensions
4. Enabling and disabling extensions
5. Loading definitions into active company symbol table
6. Inspecting AST hierarchy (Menus, Reports, Forms, Parts, Lines, Fields, Collections)
7. Dynamic compilation and hot-reloading
8. Runtime error tracking and diagnostics
9. Safe removal of custom extensions
10. Supported vs unsupported TDL compatibility registry
"""

import os
import json
from typing import Dict, List, Any, Optional
from engine.tdl_parser import TDLParser, TDLSymbolTable, SUPPORTED_DEF_TYPES

UNSUPPORTED_TDL_FEATURES = [
    {"feature": "Encrypted .TCP Binary Files", "reason": "Proprietary Tally encrypted binary container format; requires licensed Tally compiler."},
    {"feature": "COM / OLE Automation", "reason": "Windows-only COM server interfaces; not applicable in modern offline cross-platform architecture."},
    {"feature": "ODBC Direct Tunneling", "reason": "Proprietary wire protocol; replaced by standard offline REST/JSON and SQLite endpoints."},
    {"feature": "Arbitrary Python Execution from TDL", "reason": "Restricted by security policy to prevent arbitrary code execution vulnerabilities."}
]

class TDLExtensionManager:
    def __init__(self, definitions_dir: str = "definitions"):
        self.definitions_dir = os.path.abspath(definitions_dir)
        os.makedirs(self.definitions_dir, exist_ok=True)
        self.config_path = os.path.join(self.definitions_dir, "extensions_config.json")
        self.parser = TDLParser()
        self.symbol_table = self.parser.symbol_table
        self.runtime_errors: List[Dict[str, Any]] = []
        self._ensure_config()

    def _ensure_config(self):
        if not os.path.exists(self.config_path):
            initial_config = {
                "base.tdl": {"enabled": True, "protected": True, "description": "Core Tally System TDL Definitions"},
                "custom_extension.tdl": {"enabled": True, "protected": False, "description": "Debtors Analysis Menu & Report Extension"}
            }
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(initial_config, f, indent=2)

    def _load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self, cfg: Dict[str, Any]):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

    def list_extensions(self) -> List[Dict[str, Any]]:
        cfg = self._load_config()
        exts = []
        if not os.path.exists(self.definitions_dir):
            return exts

        for fname in sorted(os.listdir(self.definitions_dir)):
            if fname.endswith(".tdl"):
                fpath = os.path.join(self.definitions_dir, fname)
                fmeta = cfg.get(fname, {"enabled": True, "protected": fname == "base.tdl", "description": f"Custom TDL {fname}"})
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    diags = self.parser.validate_syntax(content)
                    err_count = len([d for d in diags if d.get("severity") == "error"])
                    temp_parser = TDLParser()
                    st = temp_parser.parse_string(content)
                    def_count = sum(len(defs) for defs in st.definitions.values())
                    exts.append({
                        "filename": fname,
                        "description": fmeta.get("description", ""),
                        "enabled": fmeta.get("enabled", True),
                        "protected": fmeta.get("protected", False),
                        "definitions_count": def_count,
                        "syntax_errors": err_count,
                        "status": "Disabled" if not fmeta.get("enabled", True) else ("Error" if err_count > 0 else "Active"),
                        "diagnostics": diags,
                        "file_size": len(content)
                    })
                except Exception as e:
                    exts.append({
                        "filename": fname,
                        "description": fmeta.get("description", ""),
                        "enabled": False,
                        "protected": fmeta.get("protected", False),
                        "definitions_count": 0,
                        "syntax_errors": 1,
                        "status": "Error",
                        "error": str(e)
                    })
        return exts

    def import_extension(self, filename: str, content: str, description: str = "") -> Dict[str, Any]:
        if not filename.endswith(".tdl"):
            filename += ".tdl"
        # Sanitize filename
        safe_name = os.path.basename(filename)
        dest_path = os.path.join(self.definitions_dir, safe_name)

        diagnostics = self.parser.validate_syntax(content)
        errors = [d for d in diagnostics if d.get("severity") == "error"]

        with open(dest_path, "w", encoding="utf-8") as f:
            f.write(content)

        cfg = self._load_config()
        cfg[safe_name] = {
            "enabled": len(errors) == 0,
            "protected": False,
            "description": description or f"Imported TDL {safe_name}"
        }
        self._save_config(cfg)
        self.reload_all()

        return {
            "status": "success",
            "filename": safe_name,
            "valid": len(errors) == 0,
            "diagnostics": diagnostics
        }

    def toggle_extension(self, filename: str, enabled: bool) -> bool:
        cfg = self._load_config()
        safe_name = os.path.basename(filename)
        if safe_name in cfg:
            cfg[safe_name]["enabled"] = enabled
            self._save_config(cfg)
            self.reload_all()
            return True
        return False

    def remove_extension(self, filename: str) -> bool:
        safe_name = os.path.basename(filename)
        if safe_name == "base.tdl":
            raise ValueError("Cannot remove core base.tdl definition file!")

        dest_path = os.path.join(self.definitions_dir, safe_name)
        if os.path.exists(dest_path):
            os.remove(dest_path)

        cfg = self._load_config()
        if safe_name in cfg:
            del cfg[safe_name]
            self._save_config(cfg)

        self.reload_all()
        return True

    def inspect_extension(self, filename: str) -> Dict[str, Any]:
        safe_name = os.path.basename(filename)
        dest_path = os.path.join(self.definitions_dir, safe_name)
        if not os.path.exists(dest_path):
            raise FileNotFoundError(f"Extension file {safe_name} not found!")

        with open(dest_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        temp_parser = TDLParser()
        st = temp_parser.parse_string(content)

        defs_summary = {}
        for dtype, items in st.definitions.items():
            defs_summary[dtype] = []
            for name, d in items.items():
                defs_summary[dtype].append({
                    "name": name,
                    "modifier": d.modifier,
                    "line": d.line_number,
                    "attributes_count": len(d.attributes),
                    "items": d.items
                })

        return {
            "filename": safe_name,
            "total_definitions": sum(len(v) for v in defs_summary.values()),
            "definitions": defs_summary,
            "formulas": list(st.formulas.keys()),
            "udfs": list(st.udfs.keys())
        }

    def reload_all(self) -> TDLSymbolTable:
        """
        Re-parses all enabled TDL files in order (base.tdl first, then extensions)
        and updates the global symbol table.
        """
        cfg = self._load_config()
        self.parser = TDLParser()
        self.symbol_table = self.parser.symbol_table
        self.runtime_errors.clear()

        # Load base.tdl first
        base_path = os.path.join(self.definitions_dir, "base.tdl")
        if os.path.exists(base_path):
            try:
                self.parser.parse_file(base_path)
            except Exception as e:
                self.runtime_errors.append({"file": "base.tdl", "error": str(e)})

        # Load other enabled extensions
        for fname in sorted(os.listdir(self.definitions_dir)):
            if fname.endswith(".tdl") and fname != "base.tdl":
                fmeta = cfg.get(fname, {"enabled": True})
                if fmeta.get("enabled", True):
                    fpath = os.path.join(self.definitions_dir, fname)
                    try:
                        self.parser.parse_file(fpath)
                    except Exception as e:
                        self.runtime_errors.append({"file": fname, "error": str(e)})

        return self.symbol_table

    def get_runtime_errors(self) -> List[Dict[str, Any]]:
        return list(self.runtime_errors)

    def get_compatibility_report(self) -> Dict[str, Any]:
        return {
            "supported_definition_types": sorted(list(SUPPORTED_DEF_TYPES)),
            "supported_modifiers": ["# (Modify)", "! (Replace)", "* (Delete)"],
            "supported_functions": [
                "$$If(condition, true_val, false_val)",
                "$$LocaleString(amount)",
                "$$IsEmpty(field)",
                "$$Number(val)",
                "$$String(val)",
                "$$Sum(collection, field)",
                "$$UpperCase(text)",
                "$$LowerCase(text)",
                "$$Round(num, decimals)",
                "$$Min(a, b)",
                "$$Max(a, b)",
                "$$YearOfDate(date)",
                "$$MonthOfDate(date)",
                "$$DayOfDate(date)"
            ],
            "unsupported_features": UNSUPPORTED_TDL_FEATURES
        }
