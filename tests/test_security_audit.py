"""
Security, Authorization & Audit Integrity Test Suite (Phase 5.5)
Verifies PBKDF2 password hashing, RBAC permission enforcement on read/write endpoints,
sole admin deletion prevention, TallyVault protection, and MCA Rule 3(1) tamper-evident
audit log hash-chaining.
"""

import unittest
import os
import tempfile
from engine.accounting_db import EnterpriseAccountingDB


class TestSecurityAudit(unittest.TestCase):

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix="_sec.db")
        os.close(self.temp_db_fd)
        self.db = EnterpriseAccountingDB(self.temp_db_path)

    def tearDown(self):
        self.db.close()
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    # 1. Default Roles Presence & RBAC Enforcement
    def test_01_default_roles_presence(self):
        for role in ["Admin", "Data Entry", "Auditor", "Viewer"]:
            # All valid roles can read basic system state
            self.assertTrue(self.db.check_rbac_permission(role, "/api/state", "GET"))
        # Invalid role should be denied
        self.assertFalse(self.db.check_rbac_permission("Hacker", "/api/vouchers", "POST"))

    # 2. PBKDF2 Password Hashing
    def test_02_password_hashing(self):
        # Create a new user with password
        self.db.create_user("auditor_test", "SecretPass123!", role="Auditor")
        # Verify correct credentials
        valid_user = self.db.authenticate_user("auditor_test", "SecretPass123!")
        self.assertIsNotNone(valid_user)
        self.assertEqual(valid_user["username"], "auditor_test")
        self.assertEqual(valid_user["role"], "Auditor")

        # Verify wrong password rejected
        invalid_user = self.db.authenticate_user("auditor_test", "WrongPassword!")
        self.assertIsNone(invalid_user)

    # 3. RBAC Write Endpoint Protection (Viewer blocked)
    def test_03_rbac_write_endpoint_protection(self):
        # Viewer role must not have permission to post vouchers
        allowed_write = self.db.check_rbac_permission("Viewer", "/api/vouchers", method="POST")
        self.assertFalse(allowed_write)

        # Admin must have permission
        allowed_admin = self.db.check_rbac_permission("Admin", "/api/vouchers", method="POST")
        self.assertTrue(allowed_admin)

    # 4. RBAC Sensitive Read Protection (Viewer blocked from payroll, audit, users)
    def test_04_rbac_sensitive_read_protection(self):
        for sensitive_path in ["/api/payroll/employees", "/api/users", "/api/system/audit_history"]:
            can_viewer_read = self.db.check_rbac_permission("Viewer", sensitive_path, method="GET")
            self.assertFalse(can_viewer_read, f"Viewer should not be permitted to read {sensitive_path}")

        # Auditor can read audit history
        can_auditor_read = self.db.check_rbac_permission("Auditor", "/api/system/audit_history", method="GET")
        self.assertTrue(can_auditor_read)

    # 5. Sole Admin Deletion Prevention
    def test_05_admin_sole_deletion_prevention(self):
        # Find admin user id
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT id FROM user_account WHERE username = 'admin'")
        admin_id = cursor.fetchone()[0]

        # Deleting sole admin must raise ValueError
        with self.assertRaises(ValueError):
            self.db.delete_user(admin_id)

    # 6. Tamper-evident Audit Trail Hash-Chaining
    def test_06_tamper_evident_audit_trail(self):
        # Initial database is untampered
        int_check1 = self.db.verify_data_integrity()
        self.assertFalse(int_check1["audit_tampered"])

        # Intentionally tamper an edit_log record directly in SQLite
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT id FROM edit_log LIMIT 1")
        row = cursor.fetchone()
        if row:
            log_id = row[0]
            with self.db.conn:
                self.db.conn.execute("UPDATE edit_log SET details = 'MALICIOUS_TAMPERED_RECORD' WHERE id = ?", (log_id,))

            # Verification must detect tampering
            int_check2 = self.db.verify_data_integrity()
            self.assertTrue(int_check2["audit_tampered"])

    # 7. TallyVault Protection
    def test_07_tallyvault_protection(self):
        comp_id = self.db.active_company_id
        # Enable vault password
        self.db.update_company_security(comp_id, security_enabled=True, vault_password="VaultPassword2026")
        comp = self.db.get_company()
        self.assertTrue(bool(comp.get("security_enabled")))
        self.assertNotEqual(comp.get("vault_password"), "VaultPassword2026")  # Must be hashed


if __name__ == "__main__":
    unittest.main()
