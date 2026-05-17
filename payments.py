from datetime import date

from activity_log import add_activity
from auth import current_user_id
from database import execute, query_all, query_one
from formatters import money


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


def process_due_recurring_payments():
    current_month = date.today().strftime("%Y-%m")
    today_day = date.today().day
    payments = query_all(
        """
        SELECT rp.*, a.balance, a.name AS account_name
        FROM recurring_payments rp
        JOIN accounts a ON a.id = rp.account_id
        WHERE rp.user_id = ? AND rp.active = 1 AND rp.day_of_month <= ?
        AND (rp.last_paid_month IS NULL OR rp.last_paid_month != ?)
        """,
        (current_user_id(), today_day, current_month),
    )
    for payment in payments:
        amount = float(payment["amount"])
        if payment["credit_id"]:
            credit_row = query_one("SELECT * FROM credits WHERE id = ? AND user_id = ?", (payment["credit_id"], current_user_id()))
            if not credit_row or credit_row["paid_installments"] >= credit_row["months"] or credit_row["remaining_balance"] <= 0:
                execute("UPDATE recurring_payments SET active = 0 WHERE id = ?", (payment["id"],))
                continue
            amount = min(float(credit_row["monthly_installment"]), float(credit_row["remaining_balance"]))
            execute(
                "UPDATE credits SET remaining_balance = remaining_balance - ?, paid_installments = paid_installments + 1, last_payment_month = ? WHERE id = ?",
                (amount, current_month, credit_row["id"]),
            )

        execute("UPDATE accounts SET balance = balance - ? WHERE id = ? AND user_id = ?", (amount, payment["account_id"], current_user_id()))
        execute(
            "INSERT INTO transactions (user_id, account_id, type, category, amount, currency, transaction_date, description) VALUES (?, ?, 'Expense', ?, ?, ?, ?, ?)",
            (current_user_id(), payment["account_id"], payment["category"], amount, payment["currency"], date.today().isoformat(), f"Automatic payment: {payment['title']}"),
        )
        next_runs = None if payment["remaining_runs"] is None else max(0, int(payment["remaining_runs"]) - 1)
        if next_runs == 0:
            execute("DELETE FROM recurring_payments WHERE id = ?", (payment["id"],))
        else:
            execute("UPDATE recurring_payments SET last_paid_month = ?, remaining_runs = ?, active = 1 WHERE id = ?", (current_month, next_runs, payment["id"]))
        add_activity(f"Automatic payment completed: {payment['title']} ({money(amount, payment['currency'])})")
