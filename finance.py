from auth import current_user_id
from config import EXCHANGE_RATES
from database import execute, query_all, query_one



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
        "total_gbp": convert_currency(total_try, "TRY", "GBP"),
        "total_kwd": convert_currency(total_try, "TRY", "KWD"),
        "total_jpy": convert_currency(total_try, "TRY", "JPY"),
    }

def money(value, currency="TRY"):
    labels = {"TRY": "₺", "USD": "$", "EUR": "€", "GBP": "£", "KWD": "ك", "JPY": "¥"}
    return f"{float(value or 0):,.2f} {labels.get(currency, currency)}"

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


def account_has_history(account_id):
    checks = [
        ("transactions", "account_id"),
        ("transfers", "from_account_id"),
        ("transfers", "to_account_id"),
        ("credits", "account_id"),
        ("investments", "from_account_id"),
        ("investments", "to_account_id"),
        ("recurring_payments", "account_id"),
    ]
    return any(
        query_one(f"SELECT id FROM {table} WHERE {column} = ? AND user_id = ? LIMIT 1", (account_id, current_user_id()))
        for table, column in checks
    )
