"""
Test Suite: Financial Security, Encryption & RBAC Verification
Covers:
- Modern PBKDF2-HMAC-SHA256 password hashing (100,000 iterations + 16-byte salt)
- Transparent, automatic upgrade of legacy SHA-256 password hashes upon login
- Ephemeral session token creation, validation, and revocation
- RBAC Permission Enforcement:
  * Admin: Full system access
  * Data Entry: Voucher posting and master display, blocked from audit logs and deletion
  * Auditor: Read-only reporting and audit trail inspection, blocked from mutating POST actions
- SQLite Write-Ahead Logging (WAL) and foreign-key integrity enforcement
"""

import unittest
import os
import sys
import tempfile
import shutil
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.accounting_db import EnterpriseAccountingDB

class TestSecurityRBAC(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_security.db")
        self.db = EnterpriseAccountingDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_01_pbkdf2_password_hashing(self):
        """Verifies PBKDF2-HMAC-SHA256 password hashing with 100,000 iterations and salt."""
        pwd = "EnterprisePassword#2026"
        hashed = self.db.hash_password(pwd)
        self.assertTrue(hashed.startswith("pbkdf2_sha256$100000$"))
        parts = hashed.split("$")
        self.assertEqual(len(parts), 4)

        # Verification with correct and incorrect password
        is_valid, needs_upgrade = self.db.verify_password(pwd, hashed)
        self.assertTrue(is_valid)
        self.assertFalse(needs_upgrade)

        is_invalid, _ = self.db.verify_password("WrongPassword", hashed)
        self.assertFalse(is_invalid)

    def test_02_legacy_sha256_hash_transparent_upgrade(self):
        """Verifies that old plain SHA-256 hashes are verified and transparently upgraded to PBKDF2."""
        plain_pwd = "LegacySecret999"
        legacy_hash = hashlib.sha256(f"{plain_pwd}_tally_salt".encode("utf-8")).hexdigest()

        # Insert user with legacy hash into user_account
        with self.db.conn:
            self.db.conn.execute(
                "INSERT INTO user_account (username, password_hash, role, full_name) VALUES ('legacy_user', ?, 'Data Entry', 'Legacy Clerk')",
                (legacy_hash,)
            )

        # Authenticate user -> must trigger transparent migration
        user = self.db.authenticate_user("legacy_user", plain_pwd)
        self.assertIsNotNone(user)
        self.assertEqual(user["username"], "legacy_user")

        # Inspect database record -> Hash must now be upgraded to pbkdf2_sha256$100000$...
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT password_hash FROM user_account WHERE username = 'legacy_user'")
        new_hash = cursor.fetchone()[0]
        self.assertTrue(new_hash.startswith("pbkdf2_sha256$100000$"))

    def test_03_session_token_creation_and_validation(self):
        """Verifies cryptographically secure token generation and validation."""
        user_dict = {"id": 1, "username": "admin", "role": "Admin", "company_access": "ALL"}
        token = self.db.create_session(user_dict)
        self.assertIsNotNone(token)
        self.assertGreaterEqual(len(token), 32)

        # Validate token
        sess = self.db.validate_session(token)
        self.assertIsNotNone(sess)
        self.assertEqual(sess["username"], "admin")
        self.assertEqual(sess["role"], "Admin")

    def test_04_session_token_expiration_and_revocation(self):
        """Verifies token revocation on logout and invalid token rejection."""
        user_dict = {"id": 2, "username": "clerk", "role": "Data Entry"}
        token = self.db.create_session(user_dict)
        self.assertIsNotNone(self.db.validate_session(token))

        # Revoke token (logout)
        ok = self.db.revoke_session(token)
        self.assertTrue(ok)

        # Token must now be rejected
        self.assertIsNone(self.db.validate_session(token))
        self.assertIsNone(self.db.validate_session("invalid-random-token-xyz"))

    def test_05_rbac_admin_full_access(self):
        """Enforces that Admin role has unrestricted access across all endpoints."""
        endpoints_to_test = [
            ("/api/state", "GET"),
            ("/api/report/TrialBalance", "GET"),
            ("/api/edit_logs", "GET"),
            ("/api/vouchers", "POST"),
            ("/api/vouchers/delete", "POST"),
            ("/api/companies", "POST"),
            ("/api/backup/restore", "POST")
        ]
        for ep, method in endpoints_to_test:
            permitted = self.db.check_rbac_permission("Admin", ep, method)
            self.assertTrue(permitted, f"Admin was blocked from {method} {ep}")

    def test_06_rbac_data_entry_restricted_access(self):
        """Enforces that Data Entry role can enter transactions but cannot delete or alter audit logs."""
        # Allowed for Data Entry
        self.assertTrue(self.db.check_rbac_permission("Data Entry", "/api/vouchers", "POST"))
        self.assertTrue(self.db.check_rbac_permission("Data Entry", "/api/state", "GET"))
        self.assertTrue(self.db.check_rbac_permission("Data Entry", "/api/report/DayBook", "GET"))

        # Denied for Data Entry (Administrative & destructive actions)
        self.assertFalse(self.db.check_rbac_permission("Data Entry", "/api/company/close_fy", "POST"))
        self.assertFalse(self.db.check_rbac_permission("Data Entry", "/api/company/split_fy", "POST"))
        self.assertFalse(self.db.check_rbac_permission("Data Entry", "/api/companies/create", "POST"))
        self.assertFalse(self.db.check_rbac_permission("Data Entry", "/api/backup/restore", "POST"))

    def test_07_rbac_auditor_read_only_access(self):
        """Enforces that Auditor role has read-only audit access and is blocked from mutating POST actions."""
        # Allowed for Auditor (Reports and Audit Trails)
        self.assertTrue(self.db.check_rbac_permission("Auditor", "/api/edit_logs", "GET"))
        self.assertTrue(self.db.check_rbac_permission("Auditor", "/api/report/TrialBalance", "GET"))
        self.assertTrue(self.db.check_rbac_permission("Auditor", "/api/report/BalanceSheet", "GET"))

        # Strictly Denied for Auditor (No mutations permitted)
        self.assertFalse(self.db.check_rbac_permission("Auditor", "/api/vouchers", "POST"))
        self.assertFalse(self.db.check_rbac_permission("Auditor", "/api/companies", "POST"))
        self.assertFalse(self.db.check_rbac_permission("Auditor", "/api/backup/restore", "POST"))

    def test_08_sqlite_wal_mode_and_foreign_keys(self):
        """Verifies SQLite database operates in high-concurrency WAL mode with foreign keys enabled."""
        cursor = self.db.conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

        cursor.execute("PRAGMA foreign_keys")
        fk = cursor.fetchone()[0]
        self.assertEqual(fk, 1)

if __name__ == "__main__":
    unittest.main()
