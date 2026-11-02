"""
TDL (Tally Definition Language) Lexer & Parser
Implements complete AST parsing, syntax validation with line numbers,
modifier actions (# modify, ! replace, * delete), and support for:
- Menu, Report, Form, Part, Line, Field, Collection
- Button, Key, Variable, Object, UDF (User Defined Fields)
- System: Formula, System: UDF
"""

import re
from typing import Dict, List, Any, Optional

SUPPORTED_DEF_TYPES = {
    "Menu", "Report", "Form", "Part", "Line", "Field", 
    "Collection", "Button", "Key", "Variable", "Object", 
    "Udf", "System"
}

class TDLDefinition:
    def __init__(self, def_type: str, name: str, modifier: str = "", line_number: int = 1):
        self.def_type = def_type.strip().title()
        self.name = name.strip()
        self.modifier = modifier.strip()  # '#', '!', '*', or ''
        self.line_number = line_number
        self.attributes: List[Dict[str, Any]] = []
        self.items: List[Dict[str, Any]] = []  # For menus
        self.deleted_items: List[str] = []

    @property
    def is_modifier(self) -> bool:
        return bool(self.modifier)

    @property
    def sub_definitions(self) -> List[Dict[str, Any]]:
        return self.items

    def add_attribute(self, raw_line: str, line_number: int = 1):
        line = raw_line.strip()
        parts = [p.strip() for p in line.split(":") if p.strip()]
        if not parts:
            return

        key = parts[0]
        val = ":".join(parts[1:]).strip() if len(parts) > 1 else ""

        # Check for Add : Item : "Label" : Action : Target
        if key.lower() == "add" and len(parts) >= 3 and parts[1].lower() == "item":
            label = parts[2].strip('"\'')
            action = parts[3] if len(parts) > 3 else "Display"
            target = parts[4] if len(parts) > 4 else ""
            self.items.append({
                "label": label,
                "action": action,
                "target": target,
                "line_number": line_number
            })
            return

        if key.lower() == "item" and len(parts) >= 2:
            label = parts[1].strip('"\'')
            action = parts[2] if len(parts) > 2 else "Display"
            target = parts[3] if len(parts) > 3 else ""
            self.items.append({
                "label": label,
                "action": action,
                "target": target,
                "line_number": line_number
            })
            return

        # Check for Delete : Item : "Label"
        if key.lower() == "delete" and len(parts) >= 3 and parts[1].lower() == "item":
            del_label = parts[2].strip('"\'').lower()
            self.deleted_items.append(del_label)
            self.items = [it for it in self.items if it.get("label", "").lower() != del_label]
            return

        # Attribute modifier: e.g. #Key: Val or *Key: Val
        attr_mod = ""
        clean_key = key
        if key.startswith(("#", "!", "*")):
            attr_mod = key[0]
            clean_key = key[1:].strip()

        # Handle * (delete attribute)
        if attr_mod == "*":
            self.attributes = [a for a in self.attributes if a["key"].lower() != clean_key.lower()]
            return

        self.attributes.append({
            "key": clean_key,
            "value": val,
            "tokens": parts,
            "modifier": attr_mod,
            "line_number": line_number
        })

    def get_attr(self, key: str, default: Any = None, strip_quotes: bool = False) -> Any:
        for attr in reversed(self.attributes):
            if attr["key"].lower() == key.lower():
                v = attr["value"]
                if strip_quotes and isinstance(v, str):
                    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                        return v[1:-1]
                return v
        return default

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "type": self.def_type,
            "name": self.name,
            "modifier": self.modifier,
            "line_number": self.line_number,
            "attributes": self.attributes,
            "items": self.items
        }
        for a in self.attributes:
            k = a["key"]
            if k not in d:
                d[k] = self.get_attr(k)
        return d


class TDLSymbolTable:
    def __init__(self):
        # definitions[def_type][name] = TDLDefinition
        self.definitions: Dict[str, Dict[str, TDLDefinition]] = {}
        self.formulas: Dict[str, str] = {}
        self.variables: Dict[str, Dict[str, Any]] = {}
        self.buttons: Dict[str, Dict[str, Any]] = {}
        self.keys: Dict[str, Dict[str, Any]] = {}
        self.udfs: Dict[str, Dict[str, Any]] = {}

    def get_definition(self, def_type: str, name: str) -> Optional[TDLDefinition]:
        return self.definitions.get(def_type.title(), {}).get(name)

    def register(self, definition: TDLDefinition):
        dtype = definition.def_type.title()
        if dtype not in self.definitions:
            self.definitions[dtype] = {}

        existing = self.definitions[dtype].get(definition.name)
        
        if definition.modifier == "*":
            # Delete definition
            if definition.name in self.definitions[dtype]:
                del self.definitions[dtype][definition.name]
            return

        if existing and definition.modifier == "#":
            # Process deletions first
            for del_item in getattr(definition, "deleted_items", []):
                existing.items = [it for it in existing.items if it.get("label", "").lower() != del_item.lower()]
            # Modify: Update/append attributes and append items
            attr_dict = {a["key"].lower(): i for i, a in enumerate(existing.attributes)}
            for new_attr in definition.attributes:
                k_lower = new_attr["key"].lower()
                if k_lower in attr_dict:
                    # Update existing attribute in place
                    existing.attributes[attr_dict[k_lower]] = new_attr
                else:
                    existing.attributes.append(new_attr)
                    attr_dict[k_lower] = len(existing.attributes) - 1
            existing.items.extend(definition.items)
            return

        if definition.modifier == "!":
            # Replace completely
            self.definitions[dtype][definition.name] = definition
            return

        # Default register (or overwrite if not previously modified)
        self.definitions[dtype][definition.name] = definition

        # Index specialized types
        if dtype == "Button":
            self.buttons[definition.name] = definition.to_dict()
        elif dtype == "Key":
            self.keys[definition.name] = definition.to_dict()
        elif dtype == "Variable":
            self.variables[definition.name] = definition.to_dict()
        elif dtype == "Udf":
            self.udfs[definition.name] = {
                "name": definition.name,
                "type": definition.get_attr("Type", "String"),
                "id": definition.get_attr("Id", "")
            }

    def register_formula(self, name: str, expr: str):
        self.formulas[name.strip()] = expr.strip()

    def register_udf(self, name: str, data_type: str, udf_id: str = ""):
        self.udfs[name.strip()] = {
            "name": name.strip(),
            "type": data_type.strip(),
            "id": udf_id.strip()
        }

    def all_definitions(self) -> Dict[str, Any]:
        out = {}
        for dtype, defs in self.definitions.items():
            out[dtype] = {name: d.to_dict() for name, d in defs.items()}
        return out


class TDLParser:
    def __init__(self, symbol_table: Optional[TDLSymbolTable] = None):
        self.symbol_table = symbol_table or TDLSymbolTable()

    def validate_syntax(self, text: str) -> List[Dict[str, Any]]:
        """
        Validates TDL code and returns a list of syntax errors/warnings
        with exact line numbers, message, and line context.
        """
        diagnostics = []
        lines = text.splitlines()
        header_regex = re.compile(r"^\[\s*([#!*]?)\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\]\s*$")

        in_def = False
        current_def_type = ""

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            # Comments
            if not line or line.startswith(";") or line.startswith("//"):
                continue

            # Strip inline comments
            clean_line = line
            if ";" in clean_line:
                clean_line = clean_line.split(";", 1)[0].strip()
            if "//" in clean_line:
                clean_line = clean_line.split("//", 1)[0].strip()

            if not clean_line:
                continue

            # Bracket checks
            if clean_line.startswith("["):
                if not clean_line.endswith("]"):
                    diagnostics.append({
                        "line": line_num,
                        "column": len(clean_line),
                        "message": f"Syntax Error: Unclosed definition bracket ']'",
                        "context": line,
                        "severity": "error"
                    })
                    continue

                match = header_regex.match(clean_line)
                if not match:
                    diagnostics.append({
                        "line": line_num,
                        "column": 1,
                        "message": f"Syntax Error: Invalid definition header format. Expected [Type : Name] or [#Type : Name]",
                        "context": line,
                        "severity": "error"
                    })
                else:
                    def_type = match.group(2).strip().title()
                    if def_type not in SUPPORTED_DEF_TYPES:
                        diagnostics.append({
                            "line": line_num,
                            "column": 1,
                            "message": f"Warning: Definition type '{def_type}' is unconventional or custom.",
                            "context": line,
                            "severity": "warning"
                        })
                    in_def = True
                    current_def_type = def_type
            else:
                # Attribute line check
                if not in_def:
                    diagnostics.append({
                        "line": line_num,
                        "column": 1,
                        "message": f"Syntax Error: Attribute statement outside of any definition block.",
                        "context": line,
                        "severity": "error"
                    })
                elif ":" not in clean_line:
                    diagnostics.append({
                        "line": line_num,
                        "column": len(clean_line),
                        "message": f"Syntax Warning: Attribute statement missing separator ':'.",
                        "context": line,
                        "severity": "warning"
                    })

                # Check quotes
                quote_count = clean_line.count('"') + clean_line.count("'")
                if quote_count % 2 != 0:
                    diagnostics.append({
                        "line": line_num,
                        "column": len(clean_line),
                        "message": f"Syntax Error: Unmatched string quotes in line.",
                        "context": line,
                        "severity": "error"
                    })

        return diagnostics

    def parse_string(self, text: str) -> TDLSymbolTable:
        lines = text.splitlines()
        current_def: Optional[TDLDefinition] = None
        in_system_formula = False
        in_system_udf = False

        header_regex = re.compile(r"^\[\s*([#!*]?)\s*([A-Za-z0-9_]+)\s*:\s*(.+?)\]\s*$")

        for line_num, raw_line in enumerate(lines, 1):
            line = raw_line.strip()
            # Strip comments
            if not line or line.startswith(";") or line.startswith("//"):
                continue

            # Remove inline comments
            if ";" in line:
                line = line.split(";", 1)[0].strip()
            if "//" in line:
                line = line.split("//", 1)[0].strip()

            if not line:
                continue

            match = header_regex.match(line)
            if match:
                # Save previous definition
                if current_def:
                    self.symbol_table.register(current_def)
                    current_def = None

                modifier = match.group(1) or ""
                def_type = match.group(2)
                def_name = match.group(3)

                if def_type.lower() == "system" and def_name.lower() == "formula":
                    in_system_formula = True
                    in_system_udf = False
                    current_def = None
                elif def_type.lower() == "system" and def_name.lower() == "udf":
                    in_system_udf = True
                    in_system_formula = False
                    current_def = None
                else:
                    in_system_formula = False
                    in_system_udf = False
                    current_def = TDLDefinition(def_type, def_name, modifier, line_number=line_num)
                continue

            if in_system_formula:
                if ":" in line:
                    fname, fexpr = line.split(":", 1)
                    self.symbol_table.register_formula(fname, fexpr)
                continue

            if in_system_udf:
                if ":" in line:
                    parts = [p.strip() for p in line.split(":") if p.strip()]
                    uname = parts[0]
                    utype = parts[1] if len(parts) > 1 else "String"
                    uid = parts[2] if len(parts) > 2 else ""
                    self.symbol_table.register_udf(uname, utype, uid)
                continue

            if current_def:
                current_def.add_attribute(line, line_number=line_num)

        if current_def:
            self.symbol_table.register(current_def)

        return self.symbol_table

    def parse_file(self, file_path: str) -> TDLSymbolTable:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return self.parse_string(f.read())

    def parse(self, text: str) -> TDLSymbolTable:
        return self.parse_string(text)
