from datetime import date, datetime
import sqlite3

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-change-later"
DATABASE = "informations.db"

EXPENSE_CATEGORIES = ["Food", "Rent", "Transport", "Bills", "Health", "Education", "Shopping", "Entertainment", "Credit" , "Credit Card" , "Other"]
INCOME_CATEGORIES =["Salary", "Rent Income", "Retired Salary", "Exchange"]
INVESTMENT_PRICES = {"Gold": 6702.45 , "Silver": 109.37 , "USD": 45.17, "EUR": 53.01 , "GBP": 61.43 , "KWD": 147.80 , "JPY": 0.28}
ACCOUNT_TYPES = ["Bank", "Cash", "Credit Card", "Deposit", "Investment"]
CURRENCIES = ["TRY", "USD", "EUR" , "GBP" , "KWD" , "JPY"]
EXCHANGE_RATES = {"TRY": 1.0, "Gold": 6702.45 , "Silver": 109.37 , "USD": 45.17, "EUR": 53.01 , "GBP": 61.43 , "KWD": 147.80 , "JPY": 0.28}


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


def current_user_id():
    return session.get("user_id")


def require_user():
    if not current_user_id():
        flash("Please sign in first.", "warning")
        return False
    return True


def money(value, currency="TRY"):
    labels = {"TRY": "₺", "USD": "$", "EUR": "€" , "GBP": "£" , "KWD": "ك" , "JPY": "¥"}
    return f"{float(value or 0):,.2f} {labels.get(currency, currency)}"


app.jinja_env.filters["money"] = money


def convert_currency(amount, from_currency, to_currency):
    amount_in_try = float(amount or 0) * EXCHANGE_RATES[from_currency]
    return amount_in_try / EXCHANGE_RATES[to_currency]


def currency_summary():
    balances = query_all(
        "SELECT currency, COALESCE(SUM(balance), 0) total FROM accounts WHERE user_id = ? GROUP BY currency",
        (current_user_id(),),
    )
    total_try = sum(convert_currency(row["total"], row["currency"], "TRY") for row in balances)
    return {
        "rates": EXCHANGE_RATES,
        "total_try": total_try,
        "total_usd": convert_currency(total_try, "TRY", "USD"),
        "total_eur": convert_currency(total_try, "TRY", "EUR"),
        "total_eur": convert_currency(total_try, "TRY", "GBP"),
        "total_eur": convert_currency(total_try, "TRY", "KWD"),
        "total_eur": convert_currency(total_try, "TRY", "JPY"),
    }


@app.context_processor
def inject_currency_data():
    if current_user_id():
        return {"currency_summary": currency_summary(), "exchange_rates": EXCHANGE_RATES}
    return {"currency_summary": None, "exchange_rates": EXCHANGE_RATES}


def column_exists(table, column):
    return any(row["name"] == column for row in query_all(f"PRAGMA table_info({table})"))


def add_column_if_missing(table, column, definition):
    if not column_exists(table, column):
        execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_activity(message):
    execute(
        "INSERT INTO activities (user_id, message, created_at) VALUES (?, ?, ?)",
        (current_user_id(), message, datetime.now().strftime("%Y-%m-%d %H:%M")),
    )


def get_account(account_id):
    return query_one("SELECT * FROM accounts WHERE id = ? AND user_id = ?", (account_id, current_user_id()))


def update_balance(account_id, balance, used_credit=None):
    if used_credit is None:
        execute("UPDATE accounts SET balance = ? WHERE id = ? AND user_id = ?", (balance, account_id, current_user_id()))
    else:
        execute(
            "UPDATE accounts SET balance = ?, used_credit = ? WHERE id = ? AND user_id = ?",
            (balance, used_credit, account_id, current_user_id()),
        )


def reduce_credit_auto_payment(credit_id):
    payment = query_one(
        "SELECT * FROM recurring_payments WHERE credit_id = ? AND user_id = ?",
        (credit_id, current_user_id()),
    )
    if not payment:
        return
    if payment["remaining_runs"] is None:
        return
    next_runs = max(0, int(payment["remaining_runs"]) - 1)
    if next_runs == 0:
        execute("DELETE FROM recurring_payments WHERE id = ? AND user_id = ?", (payment["id"], current_user_id()))
    else:
        execute("UPDATE recurring_payments SET remaining_runs = ? WHERE id = ? AND user_id = ?", (next_runs, payment["id"], current_user_id()))


def monthly_category_spending(category, month):
    row = query_one(
        """
        SELECT COALESCE(SUM(t.amount), 0) AS total
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.user_id = ? AND t.type = 'Expense' AND t.category = ? AND substr(t.transaction_date, 1, 7) = ? AND a.type != 'Credit Card'
        """,
        (current_user_id(), category, month),
    )
    return float(row["total"])


def budget_warning(category, transaction_date):
    month = transaction_date[:7]
    budget = query_one(
        "SELECT * FROM budgets WHERE user_id = ? AND category = ? AND month = ?",
        (current_user_id(), category, month),
    )
    if not budget:
        return None
    spent = monthly_category_spending(category, month)
    limit_amount = float(budget["limit_amount"])
    if spent >= limit_amount:
        return f"Budget exceeded for {category}. Spent {money(spent)} from {money(limit_amount)}."
    if spent >= limit_amount * 0.8:
        return f"Budget warning: {category} is at {round((spent / limit_amount) * 100)}% of the monthly limit."
    return None


def account_has_history(account_id):
    checks = [
        ("transactions", "account_id"),
        ("transfers", "from_account_id"),
        ("transfers", "to_account_id"),
        ("credits", "account_id"),
        ("investments", "account_id"),
        ("recurring_payments", "account_id"),
    ]
    return any(
        query_one(f"SELECT id FROM {table} WHERE {column} = ? AND user_id = ? LIMIT 1", (account_id, current_user_id()))
        for table, column in checks
    )


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

    @app.route("/")
    def login():
        return redirect(url_for("signin"))


    @app.route("/signin", methods=["GET", "POST"])
    def signin():
        if request.method == "POST":
            customer_number = request.form.get("customer_number", "").strip()
            password = request.form.get("password", "")
            user_row = query_one("SELECT * FROM users WHERE customer_number = ?", (customer_number,))
            if user_row and user_row["password_hash"] and check_password_hash(user_row["password_hash"], password):
                session["user_id"] = user_row["id"]
                session["user_name"] = user_row["name"]
                return redirect(url_for("dashboard"))
            flash("Wrong customer number or password.", "danger")
            return redirect(url_for("signin"))
        return render_template("login.html")


    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            customer_number = request.form.get("customer_number", "").strip()
            password = request.form.get("password", "")
            security_answer = request.form.get("security_answer", "").strip().lower()
            if not name or not customer_number or not password or not security_answer:
                flash("All fields are required.", "danger")
                return redirect(url_for("signup"))
            if query_one("SELECT id FROM users WHERE customer_number = ?", (customer_number,)):
                flash("This customer number already exists.", "danger")
                return redirect(url_for("signup"))
            user_id = execute(
                "INSERT INTO users (name, customer_number, password_hash, security_answer) VALUES (?, ?, ?, ?)",
                (name, customer_number, generate_password_hash(password), security_answer),
            ).lastrowid
            session["user_id"] = user_id
            session["user_name"] = name
            execute("INSERT INTO accounts (user_id, name, type, balance, currency) VALUES (?, 'Main TRY Account', 'Bank', 0, 'TRY')", (user_id,))
            add_activity("Created customer profile with TRY, USD, and EUR accounts")
            return redirect(url_for("dashboard"))
        return render_template("signup.html")


    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            customer_number = request.form.get("customer_number", "").strip()
            security_answer = request.form.get("security_answer", "").strip().lower()
            new_password = request.form.get("new_password", "")
            user_row = query_one("SELECT * FROM users WHERE customer_number = ?", (customer_number,))
            if not user_row or user_row["security_answer"] != security_answer:
                flash("Customer number or security answer is wrong.", "danger")
                return redirect(url_for("forgot_password"))
            execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user_row["id"]))
            flash("Password changed. You can sign in now.", "success")
            return redirect(url_for("signin"))
        return render_template("forgot_password.html")
    
    @app.route("/dashboard")
    def dashboard():
        if not require_user():
            return redirect(url_for("signin"))
        month = date.today().strftime("%Y-%m")
        income = query_one("SELECT COALESCE(SUM(amount), 0) total FROM transactions WHERE user_id = ? AND type = 'Income' AND currency = 'TRY' AND substr(transaction_date, 1, 7) = ?", (current_user_id(), month))["total"]
        expenses = query_one("SELECT COALESCE(SUM(amount), 0) total FROM transactions WHERE user_id = ? AND type = 'Expense' AND currency = 'TRY' AND substr(transaction_date, 1, 7) = ?", (current_user_id(), month))["total"]
        balances = query_all("SELECT currency, COALESCE(SUM(balance), 0) total FROM accounts WHERE user_id = ? AND currency = 'TRY' GROUP BY currency ORDER BY currency", (current_user_id(),))
        card_summary = query_one("SELECT COALESCE(SUM(used_credit), 0) used, COALESCE(SUM(credit_limit), 0) limit_total FROM accounts WHERE user_id = ? AND type = 'Credit Card'", (current_user_id(),))
        activities = query_all("SELECT * FROM activities WHERE user_id = ? ORDER BY id DESC LIMIT 6", (current_user_id(),))
        budgets = budget_status(month)
        monthly_profit = float(income) - float(expenses)
        return render_template("dashboard.html", income=income, expenses=expenses, balances=balances, card_summary=card_summary, monthly_profit=monthly_profit, activities=activities, budgets=budgets)

    def budget_status(month):
        budgets = query_all("SELECT * FROM budgets WHERE user_id = ? AND month = ? ORDER BY category", (current_user_id(), month))
        result = []
        for budget in budgets:
            spent = monthly_category_spending(budget["category"], month)
            limit_amount = float(budget["limit_amount"])
            raw_percent = round((spent / limit_amount) * 100)
            status = "danger" if raw_percent >= 100 else "warning" if raw_percent >= 80 else "safe"
            result.append({"category": budget["category"], "month": month, "spent": spent, "limit": limit_amount, "percent": min(100, raw_percent), "raw_percent": raw_percent, "status": status})
        return result