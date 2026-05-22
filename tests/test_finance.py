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

    