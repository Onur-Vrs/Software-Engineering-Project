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

    def test_budget_status_marks_category_as_warning(self):
        self.create_user()
        account_id = self.create_account()
        database.execute(
            """
            INSERT INTO budgets (user_id, category, month, limit_amount)
            VALUES (?, ?, ?, ?)
            """,
            (1, "Food", "2026-05", 100),
        )
        database.execute(
            """
            INSERT INTO transactions
                (user_id, account_id, type, category, amount, currency, transaction_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Expense", "Food", 85, "TRY", "2026-05-10", "Groceries"),
        )

        self.assertEqual(
            finance.budget_status("2026-05"),
            [
                {
                    "category": "Food",
                    "month": "2026-05",
                    "spent": 85.0,
                    "limit": 100.0,
                    "percent": 85,
                    "raw_percent": 85,
                    "status": "warning",
                }
            ],
        )

    def test_budget_warning_reports_exceeded_limit(self):
        self.create_user()
        account_id = self.create_account()
        database.execute(
            """
            INSERT INTO budgets (user_id, category, month, limit_amount)
            VALUES (?, ?, ?, ?)
            """,
            (1, "Bills", "2026-05", 100),
        )
        database.execute(
            """
            INSERT INTO transactions
                (user_id, account_id, type, category, amount, currency, transaction_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Expense", "Bills", 125, "TRY", "2026-05-12", "Electricity"),
        )

        warning = finance.budget_warning("Bills", "2026-05-23")

        self.assertIn("Budget exceeded for Bills", warning)
        self.assertIn("125.00", warning)

    def test_budget_warning_returns_none_when_spending_is_safe(self):
        self.create_user()
        account_id = self.create_account()
        database.execute(
            """
            INSERT INTO budgets (user_id, category, month, limit_amount)
            VALUES (?, ?, ?, ?)
            """,
            (1, "Health", "2026-05", 100),
        )
        database.execute(
            """
            INSERT INTO transactions
                (user_id, account_id, type, category, amount, currency, transaction_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Expense", "Health", 20, "TRY", "2026-05-08", "Medicine"),
        )

        self.assertIsNone(finance.budget_warning("Health", "2026-05-23"))

    def test_budget_status_caps_percent_but_keeps_raw_percent(self):
        self.create_user()
        account_id = self.create_account()
        database.execute(
            """
            INSERT INTO budgets (user_id, category, month, limit_amount)
            VALUES (?, ?, ?, ?)
            """,
            (1, "Shopping", "2026-05", 100),
        )
        database.execute(
            """
            INSERT INTO transactions
                (user_id, account_id, type, category, amount, currency, transaction_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Expense", "Shopping", 150, "TRY", "2026-05-11", "Shoes"),
        )

        status = finance.budget_status("2026-05")[0]

        self.assertEqual(status["percent"], 100)
        self.assertEqual(status["raw_percent"], 150)
        self.assertEqual(status["status"], "danger")

    def test_monthly_category_spending_ignores_income_other_months_and_credit_cards(self):
        self.create_user()
        bank_account_id = self.create_account(name="Bank")
        credit_card_id = self.create_account(name="Card", account_type="Credit Card")
        rows = [
            (1, bank_account_id, "Expense", "Food", 50, "TRY", "2026-05-01", "Included"),
            (1, bank_account_id, "Income", "Food", 999, "TRY", "2026-05-02", "Ignored income"),
            (1, bank_account_id, "Expense", "Food", 25, "TRY", "2026-04-30", "Ignored month"),
            (1, credit_card_id, "Expense", "Food", 75, "TRY", "2026-05-03", "Ignored card"),
        ]
        for row in rows:
            database.execute(
                """
                INSERT INTO transactions
                    (user_id, account_id, type, category, amount, currency, transaction_date, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )

        self.assertEqual(finance.monthly_category_spending("Food", "2026-05"), 50.0)

    def test_get_account_only_returns_signed_in_users_account(self):
        self.create_user(1, "1001")
        self.create_user(2, "1002")
        own_account_id = self.create_account(user_id=1, name="Mine")
        other_account_id = self.create_account(user_id=2, name="Other")

        self.assertEqual(finance.get_account(own_account_id)["name"], "Mine")
        self.assertIsNone(finance.get_account(other_account_id))

    def test_update_balance_updates_cash_account(self):
        self.create_user()
        account_id = self.create_account(balance=100)

        finance.update_balance(account_id, 250)

        account = database.query_one("SELECT balance, used_credit FROM accounts WHERE id = ?", (account_id,))
        self.assertEqual(account["balance"], 250)
        self.assertEqual(account["used_credit"], 0)

    def test_update_balance_updates_credit_card_used_credit(self):
        self.create_user()
        account_id = self.create_account(account_type="Credit Card", balance=1000)

        finance.update_balance(account_id, 800, used_credit=200)

        account = database.query_one("SELECT balance, used_credit FROM accounts WHERE id = ?", (account_id,))
        self.assertEqual(account["balance"], 800)
        self.assertEqual(account["used_credit"], 200)

    def test_account_has_history_detects_user_transactions(self):
        self.create_user()
        account_id = self.create_account()
        database.execute(
            """
            INSERT INTO transactions
                (user_id, account_id, type, category, amount, currency, transaction_date, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Income", "Salary", 500, "TRY", "2026-05-01", "Paycheck"),
        )

        self.assertTrue(finance.account_has_history(account_id))

    def test_account_has_history_returns_false_for_clean_account(self):
        self.create_user()
        account_id = self.create_account()

        self.assertFalse(finance.account_has_history(account_id))

    def test_reduce_credit_auto_payment_decrements_remaining_runs(self):
        self.create_user()
        account_id = self.create_account()
        credit_id = database.execute(
            """
            INSERT INTO credits
                (user_id, account_id, principal, interest_rate, months, total_repayment, monthly_installment, remaining_balance, credit_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, 1000, 10, 5, 1100, 220, 1100, "2026-05-01"),
        ).lastrowid
        database.execute(
            """
            INSERT INTO recurring_payments
                (user_id, account_id, title, category, amount, currency, day_of_month, remaining_runs, credit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Loan", "Credit", 220, "TRY", 1, 3, credit_id),
        )

        payments.reduce_credit_auto_payment(credit_id)

        payment = database.query_one("SELECT remaining_runs FROM recurring_payments WHERE credit_id = ?", (credit_id,))
        self.assertEqual(payment["remaining_runs"], 2)

    def test_reduce_credit_auto_payment_deletes_when_no_runs_left(self):
        self.create_user()
        account_id = self.create_account()
        credit_id = database.execute(
            """
            INSERT INTO credits
                (user_id, account_id, principal, interest_rate, months, total_repayment, monthly_installment, remaining_balance, credit_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, 1000, 10, 1, 1100, 1100, 1100, "2026-05-01"),
        ).lastrowid
        database.execute(
            """
            INSERT INTO recurring_payments
                (user_id, account_id, title, category, amount, currency, day_of_month, remaining_runs, credit_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Loan", "Credit", 1100, "TRY", 1, 1, credit_id),
        )

        payments.reduce_credit_auto_payment(credit_id)

        payment = database.query_one("SELECT * FROM recurring_payments WHERE credit_id = ?", (credit_id,))
        self.assertIsNone(payment)

    def test_process_due_recurring_payment_skips_future_payment_day(self):
        self.create_user()
        account_id = self.create_account(balance=500)
        database.execute(
            """
            INSERT INTO recurring_payments
                (user_id, account_id, title, category, amount, currency, day_of_month, remaining_runs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Rent", "Rent", 300, "TRY", 30, 1),
        )

        with patch.object(payments, "date", FixedDate):
            payments.process_due_recurring_payments()

        account = database.query_one("SELECT balance FROM accounts WHERE id = ?", (account_id,))
        transaction_count = database.query_one("SELECT COUNT(*) AS total FROM transactions")["total"]

        self.assertEqual(account["balance"], 500)
        self.assertEqual(transaction_count, 0)

    def test_process_due_recurring_payment_updates_balance_and_transaction(self):
        self.create_user()
        account_id = self.create_account(balance=500)
        database.execute(
            """
            INSERT INTO recurring_payments
                (user_id, account_id, title, category, amount, currency, day_of_month, remaining_runs)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (1, account_id, "Internet", "Bills", 120, "TRY", 10, 1),
        )

        with patch.object(payments, "date", FixedDate):
            payments.process_due_recurring_payments()

        account = database.query_one("SELECT balance FROM accounts WHERE id = ?", (account_id,))
        transaction = database.query_one("SELECT * FROM transactions WHERE account_id = ?", (account_id,))
        recurring = database.query_one("SELECT * FROM recurring_payments WHERE account_id = ?", (account_id,))

        self.assertEqual(account["balance"], 380)
        self.assertEqual(transaction["type"], "Expense")
        self.assertEqual(transaction["category"], "Bills")
        self.assertEqual(transaction["amount"], 120)
        self.assertEqual(transaction["transaction_date"], "2026-05-23")
        self.assertIsNone(recurring)


    if __name__ == "__main__":
        unittest.main()