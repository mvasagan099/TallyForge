"""
Versioned Statutory Tax & Legal Configuration
All statutory rates, thresholds, legal references, and state codes are defined here
as versioned configuration data rather than hardcoded logic.
"""

from typing import Dict, Any, List

STATUTORY_CONFIG_VERSION = "2026.1"

# 1. State Code Mappings (GST 2-Digit Codes conforming to Indian Standard)
GST_STATE_CODES: Dict[str, str] = {
    "01": "Jammu & Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman & Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory"
}

# Reverse lookup: State Name -> State Code
STATE_NAME_TO_CODE: Dict[str, str] = {v.lower(): k for k, v in GST_STATE_CODES.items()}

# 2. Standard GST Tax Slabs
GST_TAX_SLABS: List[Dict[str, Any]] = [
    {"rate": 0.0, "description": "Nil Rated / Exempt Goods & Services"},
    {"rate": 5.0, "cgst_rate": 2.5, "sgst_rate": 2.5, "igst_rate": 5.0, "description": "Essential Goods / Transport"},
    {"rate": 12.0, "cgst_rate": 6.0, "sgst_rate": 6.0, "igst_rate": 12.0, "description": "Standard Rate 1 (Processed Food / Hardware)"},
    {"rate": 18.0, "cgst_rate": 9.0, "sgst_rate": 9.0, "igst_rate": 18.0, "description": "Standard Rate 2 (IT / Electronics / Services)"},
    {"rate": 28.0, "cgst_rate": 14.0, "sgst_rate": 14.0, "igst_rate": 28.0, "description": "Luxury & Demerit Goods"}
]

# 3. TDS (Tax Deducted at Source) Sections & Thresholds under Income Tax Act, 1961
TDS_SECTIONS_CONFIG: Dict[str, Dict[str, Any]] = {
    "194C": {
        "section": "194C",
        "description": "Payments to Contractors & Sub-Contractors",
        "threshold_single": 30000.0,
        "threshold_aggregate": 100000.0,
        "rate_individual": 1.0,
        "rate_other": 2.0,
        "legal_citation": "Section 194C, Income-tax Act, 1961"
    },
    "194J": {
        "section": "194J",
        "description": "Fees for Professional or Technical Services",
        "threshold_single": 30000.0,
        "threshold_aggregate": 30000.0,
        "rate_individual": 10.0,
        "rate_other": 10.0,
        "legal_citation": "Section 194J, Income-tax Act, 1961"
    },
    "194I": {
        "section": "194I",
        "description": "Rent for Land, Building, or Furniture",
        "threshold_single": 240000.0,
        "threshold_aggregate": 240000.0,
        "rate_individual": 10.0,
        "rate_other": 10.0,
        "legal_citation": "Section 194I, Income-tax Act, 1961"
    },
    "194Q": {
        "section": "194Q",
        "description": "Purchase of Goods by Buyers exceeding ₹50 Lakhs turnover",
        "threshold_single": 5000000.0,
        "threshold_aggregate": 5000000.0,
        "rate_individual": 0.1,
        "rate_other": 0.1,
        "legal_citation": "Section 194Q, Income-tax Act, 1961 (Inserted via Finance Act, 2021)"
    }
}

# 4. Statutory Legal Basis Documentation & Citations
STATUTORY_LEGAL_BASIS: Dict[str, Dict[str, str]] = {
    "MCA_EDIT_LOG": {
        "mandate": "Statutory Audit Trail / Edit Log Feature Mandate",
        "law": "Companies Act, 2013",
        "rule": "Proviso to Rule 3(1) of Companies (Accounts) Rules, 2014",
        "notification": "Ministry of Corporate Affairs (MCA) Notification G.S.R. 235(E) dated March 31, 2021, read with G.S.R. 247(E) dated March 31, 2022",
        "effective_date": "April 1, 2023",
        "requirement": "Every company using accounting software to maintain books of account must use software with an edit log feature recording every change made to books of accounts, date when change occurred, ensuring edit trail cannot be disabled."
    },
    "GST_INVOICE_ITC": {
        "mandate": "Input Tax Credit & Invoicing Rules",
        "law": "Central Goods and Services Tax Act, 2017 (CGST Act)",
        "sections": "Section 16 (Eligibility and conditions for taking input tax credit), Section 31 (Tax invoice)",
        "rules": "Rules 46, 47, and 48 of CGST Rules, 2017"
    },
    "E_INVOICE": {
        "mandate": "Electronic Invoicing (e-Invoice) Schema",
        "law": "Rule 48(4) of CGST Rules, 2017",
        "schema_version": "Version 1.1",
        "publisher": "National Informatics Centre (NIC) / Goods and Services Tax Network (GSTN)",
        "offline_notice": "Generated as an offline JSON payload conforming to schema specifications for subsequent upload to Invoice Registration Portal (IRP)."
    },
    "E_WAY_BILL": {
        "mandate": "Electronic Way Bill (e-Way Bill)",
        "law": "Rule 138 of CGST Rules, 2017",
        "publisher": "National Informatics Centre (NIC)",
        "threshold": "Consignment value exceeding ₹50,000",
        "offline_notice": "Generated as an offline JSON payload for pre-registration and vehicle tracking."
    },
    "PAYROLL_STATUTORY": {
        "pf_mandate": "Employees' Provident Funds and Miscellaneous Provisions Act, 1952",
        "pf_rate": "12.0% of Basic + Dearness Allowance",
        "pt_mandate": "State Professional Tax Acts (e.g. Karnataka Tax on Professions, Trades, Callings and Employments Act, 1976)",
        "pt_slab": "₹200 per month for monthly gross salary exceeding ₹25,000",
        "esi_mandate": "Employees' State Insurance Act, 1948",
        "esi_employee_rate": "0.75% of Gross Salary for monthly wages up to ₹21,000",
        "esi_employer_rate": "3.25% of Gross Salary",
        "gratuity_mandate": "Payment of Gratuity Act, 1972 (15 days wages per completed year of service)"
    }
}

# 5. TCS (Tax Collected at Source) under Section 206C, Income-tax Act, 1961
TCS_SECTIONS_CONFIG: Dict[str, Dict[str, Any]] = {
    "206C(1H)": {
        "section": "206C(1H)",
        "description": "Tax Collection at Source on Sale of Goods exceeding ₹50 Lakhs turnover",
        "threshold_aggregate": 5000000.0,
        "rate": 0.1,
        "rate_pan_missing": 1.0,
        "legal_citation": "Section 206C(1H), Income-tax Act, 1961 (Finance Act, 2020)"
    },
    "206C(1)": {
        "section": "206C(1)",
        "description": "Sale of Scrap, Minerals, Coal, Lignite, Timber",
        "threshold_aggregate": 0.0,
        "rate": 1.0,
        "rate_pan_missing": 5.0,
        "legal_citation": "Section 206C(1), Income-tax Act, 1961"
    }
}

# 6. Fixed Asset Depreciation Guidelines (Companies Act 2013, Schedule II & Income Tax Act)
FIXED_ASSET_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "Computers & IT Assets": {"useful_life_months": 36, "slm_rate_pct": 33.33, "wdv_rate_pct": 63.16},
    "Plant & Machinery": {"useful_life_months": 180, "slm_rate_pct": 6.67, "wdv_rate_pct": 18.10},
    "Furniture & Fixtures": {"useful_life_months": 120, "slm_rate_pct": 10.0, "wdv_rate_pct": 25.89},
    "Vehicles (Commercial/Automobiles)": {"useful_life_months": 96, "slm_rate_pct": 12.5, "wdv_rate_pct": 31.23},
    "Buildings & Office Premises": {"useful_life_months": 360, "slm_rate_pct": 3.33, "wdv_rate_pct": 9.50}
}

# 7. Employee State Insurance (ESI) Statutory Parameters (ESI Act 1948)
ESI_STATUTORY_CONFIG: Dict[str, Any] = {
    "wage_ceiling_monthly": 21000.0,
    "employee_contribution_rate_pct": 0.75,
    "employer_contribution_rate_pct": 3.25,
    "disability_wage_ceiling_monthly": 25000.0,
    "statutory_act": "Employees' State Insurance Act, 1948"
}

