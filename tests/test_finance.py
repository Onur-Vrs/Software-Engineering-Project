import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import database

sys.modules.setdefault("flask", SimpleNamespace(flash=lambda *args, **kwargs: None, session={}))

import finance
import payments


class FixedDate:
    @classmethod
    def today(cls):
        return cls()

    def strftime(self, value):
        if value == "%Y-%m":
            return "2026-05"
        raise ValueError(value)

    @property
    def day(self):
        return 23

    def isoformat(self):
        return "2026-05-23"


class FinanceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.temp_dir.name) / "banking-test.db"

        self.database_patch = patch.object(database, "DATABASE", str(self.db_path))
        self.finance_user_patch = patch.object(finance, "current_user_id", return_value=1)
        self.payments_user_patch = patch.object(payments, "current_user_id", return_value=1)
        self.activity_patch = patch.object(payments, "add_activity")

        self.database_patch.start()
        self.finance_user_patch.start()
        self.payments_user_patch.start()
        self.activity_patch.start()
        database.init_db()

    def tearDown(self):
        self.activity_patch.stop()
        self.payments_user_patch.stop()
        self.finance_user_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def create_user(self, user_id=1, customer_number="1001"):
        database.execute(
            "INSERT INTO users (id, name, customer_number) VALUES (?, ?, ?)",
            (user_id, f"User {user_id}", customer_number),
        )

    def create_account(self, user_id=1, name="Main", account_type="Bank", balance=1000, currency="TRY"):
        return database.execute(
            """
            INSERT INTO accounts (user_id, name, type, balance, currency)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, name, account_type, balance, currency),
        ).lastrowid

    def test_convert_currency_uses_configured_exchange_rates(self):
        self.assertAlmostEqual(finance.convert_currency(2, "USD", "TRY"), 90.34)
        self.assertAlmostEqual(finance.convert_currency(45.17, "TRY", "USD"), 1.0)

    def test_currency_summary_totals_only_signed_in_users_accounts(self):
        self.create_user(1, "1001")
        self.create_user(2, "1002")
        self.create_account(user_id=1, name="Cash TRY", balance=100, currency="TRY")
        self.create_account(user_id=1, name="Cash USD", balance=2, currency="USD")
        self.create_account(user_id=2, name="Other User Cash", balance=5000, currency="TRY")

        summary = finance.currency_summary()

        self.assertAlmostEqual(summary["total_try"], 190.34)
        self.assertAlmostEqual(summary["total_usd"], 190.34 / 45.17)