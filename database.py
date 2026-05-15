import sqlite3

from config import DATABASE


def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def execute(query, params=()):
    with db() as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur


def query_all(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchall()


def query_one(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchone()


def column_exists(table, column):
    return any(row["name"] == column for row in query_all(f"PRAGMA table_info({table})"))


def add_column_if_missing(table, column, definition):
    if not column_exists(table, column):
        execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    schema = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        customer_number TEXT NOT NULL UNIQUE,
        password_hash TEXT,
        security_answer TEXT
    );

    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        balance REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'TRY',
        credit_limit REAL NOT NULL DEFAULT 0,
        used_credit REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'TRY',
        transaction_date TEXT NOT NULL,
        description TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (account_id) REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        month TEXT NOT NULL,
        limit_amount REAL NOT NULL,
        UNIQUE(user_id, category, month),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        from_account_id INTEGER NOT NULL,
        to_account_id INTEGER NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'TRY',
        transfer_date TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS credits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        principal REAL NOT NULL,
        interest_rate REAL NOT NULL,
        months INTEGER NOT NULL,
        total_repayment REAL NOT NULL,
        monthly_installment REAL NOT NULL,
        remaining_balance REAL NOT NULL DEFAULT 0,
        paid_installments INTEGER NOT NULL DEFAULT 0,
        last_payment_month TEXT,
        credit_date TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS investments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        from_account_id INTEGER NOT NULL,
        to_account_id INTEGER NOT NULL,
        asset TEXT NOT NULL,
        quantity REAL NOT NULL,
        buy_price REAL NOT NULL,
        investment_date TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (from_account_id) REFERENCES accounts(id),
        FOREIGN KEY (to_account_id) REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS recurring_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        account_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'TRY',
        day_of_month INTEGER NOT NULL,
        last_paid_month TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        remaining_runs INTEGER,
        credit_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (account_id) REFERENCES accounts(id)
    );

    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """
    with db() as conn:
        conn.executescript(schema)
    add_column_if_missing("users", "password_hash", "TEXT")
    add_column_if_missing("users", "security_answer", "TEXT")
    add_column_if_missing("accounts", "currency", "TEXT NOT NULL DEFAULT 'TRY'")
    add_column_if_missing("transactions", "currency", "TEXT NOT NULL DEFAULT 'TRY'")
    add_column_if_missing("transfers", "currency", "TEXT NOT NULL DEFAULT 'TRY'")
    add_column_if_missing("credits", "remaining_balance", "REAL NOT NULL DEFAULT 0")
    add_column_if_missing("credits", "paid_installments", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing("credits", "last_payment_month", "TEXT")
    add_column_if_missing("recurring_payments", "remaining_runs", "INTEGER")
    add_column_if_missing("recurring_payments", "credit_id", "INTEGER")
    execute("UPDATE credits SET remaining_balance = total_repayment WHERE remaining_balance = 0")
