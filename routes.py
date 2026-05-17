from datetime import date

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from activity_log import add_activity
from auth import current_user_id, require_user
from config import ACCOUNT_TYPES, CURRENCIES, CATEGORIES, INVESTMENT_PRICES
from database import execute, query_all, query_one
from finance import budget_warning, convert_currency, get_account, monthly_category_spending, update_balance
from formatters import money
from payments import reduce_credit_auto_payment


def register_routes(app):
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
            add_activity("Created customer profile in foreign currency accounts")
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


    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("signin"))


    @app.route("/profile", methods=["GET", "POST"])
    def profile():
        if not require_user():
            return redirect(url_for("signin"))
        user_row = query_one("SELECT * FROM users WHERE id = ?", (current_user_id(),))
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            customer_number = request.form.get("customer_number", "").strip()
            new_password = request.form.get("new_password", "")
            if not name or not customer_number:
                flash("Name and customer ID are required.", "danger")
                return redirect(url_for("profile"))
            existing = query_one("SELECT id FROM users WHERE customer_number = ? AND id != ?", (customer_number, current_user_id()))
            if existing:
                flash("This customer ID already belongs to another user.", "danger")
                return redirect(url_for("profile"))
            if new_password:
                execute(
                    "UPDATE users SET name = ?, customer_number = ?, password_hash = ? WHERE id = ?",
                    (name, customer_number, generate_password_hash(new_password), current_user_id()),
                )
            else:
                execute(
                    "UPDATE users SET name = ?, customer_number = ? WHERE id = ?",
                    (name, customer_number, current_user_id()),
                )
            session["user_name"] = name
            add_activity("Updated profile settings")
            flash("Profile settings updated.", "success")
            return redirect(url_for("profile"))
        return render_template("profile.html", user=user_row)


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


    @app.route("/accounts", methods=["GET", "POST"])
    def accounts():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            action = request.form.get("action", "create")
            if action == "delete":
                account = get_account(request.form["account_id"])
                execute("DELETE FROM transactions WHERE account_id = ? AND user_id = ?", (account["id"], current_user_id()))
                execute("DELETE FROM transfers WHERE (from_account_id = ? OR to_account_id = ?) AND user_id = ?", (account["id"], account["id"], current_user_id()))
                execute("DELETE FROM credits WHERE account_id = ? AND user_id = ?", (account["id"], current_user_id()))
                execute("DELETE FROM investments WHERE (from_account_id = ? OR to_account_id = ?) AND user_id = ?", (account["id"], account["id"], current_user_id()))
                execute("DELETE FROM recurring_payments WHERE account_id = ? AND user_id = ?", (account["id"], current_user_id()))
                execute("DELETE FROM accounts WHERE id = ? AND user_id = ?", (account["id"], current_user_id()))
                add_activity(f"Deleted account: {account['name']}")
                flash("Account deleted.", "success")
                return redirect(url_for("accounts"))
            if action == "update":
                account = get_account(request.form["account_id"])
                account_type = request.form["type"]
                credit_limit = float(request.form.get("credit_limit", 0)) if account_type == "Credit Card" else 0
                used_credit = min(float(account["used_credit"]), credit_limit) if account_type == "Credit Card" else 0
                execute(
                    """
                    UPDATE accounts
                    SET name = ?, type = ?, currency = ?, balance = ?, credit_limit = ?, used_credit = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (request.form["name"], account_type, request.form["currency"], float(request.form["balance"]), credit_limit, used_credit, account["id"], current_user_id()),
                )
                add_activity(f"Updated account information: {request.form['name']}")
                flash("Account information updated.", "success")
                return redirect(url_for("accounts"))
            credit_limit = float(request.form.get("credit_limit", 0)) if request.form["type"] == "Credit Card" else 0
            execute(
                "INSERT INTO accounts (user_id, name, type, balance, currency, credit_limit) VALUES (?, ?, ?, ?, ?, ?)",
                (current_user_id(), request.form["name"], request.form["type"], float(request.form.get("balance", 0)), request.form["currency"], credit_limit),
            )
            add_activity(f"Created account: {request.form['name']}")
            flash("Account created.", "success")
            return redirect(url_for("accounts"))
        rows = query_all("SELECT * FROM accounts WHERE user_id = ? ORDER BY id DESC", (current_user_id(),))
        return render_template("accounts.html", accounts=rows, account_types=ACCOUNT_TYPES, currencies=CURRENCIES)


    @app.route("/transactions", methods=["GET", "POST"])
    def transactions():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            account = get_account(request.form["account_id"])
            amount = float(request.form["amount"])
            if request.method == "POST":
                tx_type = request.form["type"]
            if tx_type == "Expense":
                if account["type"] == "Credit Card":
                    if account["used_credit"] + amount > account["credit_limit"]:
                        flash("Credit card limit exceeded.", "danger")
                        return redirect(url_for("transactions"))
                    update_balance(account["id"], account["balance"], account["used_credit"] + amount)
                else:
                    update_balance(account["id"], account["balance"] - amount)
            else:
                if account["type"] == "Credit Card":
                    payment = min(amount, float(account["used_credit"]))
                    extra = amount - payment
                    update_balance(account["id"], account["balance"] + extra, account["used_credit"] - payment)
                else:
                    update_balance(account["id"], account["balance"] + amount)

            execute(
                "INSERT INTO transactions (user_id, account_id, type, category, amount, currency, transaction_date, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (current_user_id(), account["id"], tx_type, request.form["category"], amount, account["currency"], request.form["transaction_date"], request.form.get("description", "")),
            )
            
            add_activity(f"Added {tx_type.lower()} transaction: {money(amount, account['currency'])}")
            warning = budget_warning(request.form["category"], request.form["transaction_date"]) if tx_type == "Expense" and account["currency"] == "TRY" and account["type"] != "Credit Card" else None
            flash(warning or "Transaction saved.", "warning" if warning else "success")
            return redirect(url_for("transactions"))
        rows = query_all("SELECT t.*, a.name account_name FROM transactions t JOIN accounts a ON a.id = t.account_id WHERE t.user_id = ? ORDER BY t.transaction_date DESC, t.id DESC", (current_user_id(),))
        return render_template("transactions.html", transactions=rows, accounts=query_all("SELECT * FROM accounts WHERE user_id = ?", (current_user_id(),)), categories=CATEGORIES, tx_type="Expense" ,today=date.today().isoformat())


    @app.route("/budgets", methods=["GET", "POST"])
    def budgets():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            execute(
                "INSERT INTO budgets (user_id, category, month, limit_amount) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, category, month) DO UPDATE SET limit_amount = excluded.limit_amount",
                (current_user_id(), request.form["category"], request.form["month"], float(request.form["limit_amount"])),
            )
            add_activity(f"Saved budget for {request.form['category']} in {request.form['month']}")
            flash("Budget saved.", "success")
            return redirect(url_for("budgets"))
        month = date.today().strftime("%Y-%m")
        return render_template("budgets.html", categories=CATEGORIES, month=month, budgets=budget_status(month))


    @app.route("/transfers", methods=["GET", "POST"])
    def transfers():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            from_account = get_account(request.form["from_account_id"])
            to_account = get_account(request.form["to_account_id"])
            amount = float(request.form["amount"])
            if from_account["id"] == to_account["id"]:
                flash("Choose two different accounts.", "danger")
                return redirect(url_for("transfers"))
            received_amount = convert_currency(amount, from_account["currency"], to_account["currency"])
            update_balance(from_account["id"], from_account["balance"] - amount)
            update_balance(to_account["id"], to_account["balance"] + received_amount)
            execute("INSERT INTO transfers (user_id, from_account_id, to_account_id, amount, currency, transfer_date) VALUES (?, ?, ?, ?, ?, ?)", (current_user_id(), from_account["id"], to_account["id"], amount, from_account["currency"], date.today().isoformat()))
            add_activity(f"Transferred {money(amount, from_account['currency'])} to {money(received_amount, to_account['currency'])}")
            flash(f"Transfer completed. Receiver got {money(received_amount, to_account['currency'])}.", "success")
            return redirect(url_for("transfers"))
        history = query_all("SELECT tr.*, a1.name from_name, a2.name to_name FROM transfers tr JOIN accounts a1 ON a1.id = tr.from_account_id JOIN accounts a2 ON a2.id = tr.to_account_id WHERE tr.user_id = ? ORDER BY tr.id DESC", (current_user_id(),))
        accounts = query_all("SELECT * FROM accounts WHERE user_id = ? AND type != 'Credit Card'", (current_user_id(),))
        return render_template("transfers.html", accounts=accounts, transfers=history)


    @app.route("/automatic-payments", methods=["GET", "POST"])
    def automatic_payments():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            action = request.form.get("action", "create")
            if action == "delete":
                execute("DELETE FROM recurring_payments WHERE id = ? AND user_id = ?", (request.form["payment_id"], current_user_id()))
                add_activity("Deleted automatic payment")
                flash("Automatic payment deleted.", "success")
            elif action == "update":
                account = get_account(request.form["account_id"])
                execute(
                    """
                    UPDATE recurring_payments
                    SET account_id = ?, title = ?, category = ?, amount = ?, currency = ?, day_of_month = ?, active = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    
                    (account["id"], request.form["title"], request.form["category"], float(request.form["amount"]), account["currency"], int(request.form["day_of_month"]), int(request.form.get("active", 0)), request.form["payment_id"], current_user_id()),
                )
                add_activity(f"Updated automatic payment: {request.form['title']}")
                flash("Automatic payment updated.", "success")
            else:
                account = get_account(request.form["account_id"])
                execute(
                    "INSERT INTO recurring_payments (user_id, account_id, title, category, amount, currency, day_of_month) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (current_user_id(), account["id"], request.form["title"], request.form["category"], float(request.form["amount"]), account["currency"], int(request.form["day_of_month"])),
                )
                add_activity(f"Created automatic monthly payment: {request.form['title']}")
                flash("Automatic payment created.", "success")
            return redirect(url_for("automatic_payments"))
        rows = query_all("SELECT rp.*, a.name account_name FROM recurring_payments rp JOIN accounts a ON a.id = rp.account_id WHERE rp.user_id = ? ORDER BY rp.id DESC", (current_user_id(),))
        accounts = query_all("SELECT * FROM accounts WHERE user_id = ? AND type != 'Credit Card'", (current_user_id(),))
        return render_template("automatic_payments.html", payments=rows, accounts=accounts, categories=CATEGORIES )


    @app.route("/credit", methods=["GET", "POST"])
    def credit():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            action = request.form["action"]
            if action == "loan":
                account = get_account(request.form["account_id"])
                principal = float(request.form["principal"])
                rate = float(request.form["interest_rate"])
                months = int(request.form["months"])
                total = principal + principal * rate / 100
                update_balance(account["id"], account["balance"] + principal)
                credit_id = execute("INSERT INTO credits (user_id, account_id, principal, interest_rate, months, total_repayment, monthly_installment, remaining_balance, credit_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (current_user_id(), account["id"], principal, rate, months, total, total / months, total, date.today().isoformat())).lastrowid
                execute(
                    "INSERT INTO recurring_payments (user_id, account_id, title, category, amount, currency, day_of_month, last_paid_month, remaining_runs, credit_id) VALUES (?, ?, ?, 'Bills', ?, ?, ?, ?, ?, ?)",
                    (current_user_id(), account["id"], f"Bank credit installment #{credit_id}", total / months, account["currency"], min(date.today().day, 28), date.today().strftime("%Y-%m"), months, credit_id),
                )
                add_activity(f"Took bank credit: {money(principal, account['currency'])}")
                flash("Bank credit added and monthly automatic installments created.", "success")
            elif action == "pay_loan":
                credit_row = query_one("SELECT * FROM credits WHERE id = ? AND user_id = ?", (request.form["credit_id"], current_user_id()))
                if credit_row["paid_installments"] >= credit_row["months"] or credit_row["remaining_balance"] <= 0:
                    flash("This credit has no remaining installments.", "warning")
                    return redirect(url_for("credit"))
                account = get_account(credit_row["account_id"])
                amount = min(float(credit_row["monthly_installment"]), float(credit_row["remaining_balance"]))
                update_balance(account["id"], account["balance"] - amount)
                execute("UPDATE credits SET remaining_balance = remaining_balance - ?, paid_installments = paid_installments + 1, last_payment_month = ? WHERE id = ?", (amount, date.today().strftime("%Y-%m"), credit_row["id"]))
                reduce_credit_auto_payment(credit_row["id"])
                add_activity(f"Paid bank credit installment: {money(amount, account['currency'])}")
                flash("Loan installment paid.", "success")
            elif action == "pay_card":
                card = get_account(request.form["card_id"])
                source = get_account(request.form["source_id"])
                amount = min(float(request.form["amount"]), float(card["used_credit"]))
                update_balance(source["id"], source["balance"] - amount)
                update_balance(card["id"], card["balance"], card["used_credit"] - amount)
                add_activity(f"Paid credit card debt: {money(amount, source['currency'])}")
                flash("Credit card payment completed.", "success")
            return redirect(url_for("credit"))
        accounts = query_all("SELECT * FROM accounts WHERE user_id = ?", (current_user_id(),))
        cards = [a for a in accounts if a["type"] == "Credit Card"]
        sources = [a for a in accounts if a["type"] != "Credit Card"]
        credits = query_all("SELECT c.*, a.currency, a.name account_name FROM credits c JOIN accounts a ON a.id = c.account_id WHERE c.user_id = ? ORDER BY c.id DESC", (current_user_id(),))
        return render_template("credit.html", accounts=sources, cards=cards, credits=credits)


    @app.route("/investments", methods=["GET", "POST"])
    def investments():
        if not require_user():
            return redirect(url_for("signin"))
        if request.method == "POST":
            from_account = get_account(request.form["from_account_id"])
            to_account = get_account(request.form["to_account_id"])
            asset = request.form["asset"]
            quantity = float(request.form["quantity"])
            price = INVESTMENT_PRICES[asset]
            cost = price * quantity
            received_amount = convert_currency(cost, from_account["currency"], to_account["currency"])
            update_balance(from_account["id"], from_account["balance"] - cost)
            update_balance(to_account["id"], to_account["balance"] + received_amount)
            execute("INSERT INTO investments (user_id, from_account_id, to_account_id ,asset, quantity, buy_price, investment_date) VALUES (?, ?, ?, ?, ?, ?, ?)", (current_user_id(), from_account["id"], to_account["id"], asset, quantity, price, date.today().isoformat()))
            add_activity(f"Bought {quantity} {asset} for {money(cost, from_account['currency'])}")
            flash("Investment purchased.", "success")
            return redirect(url_for("investments"))
        accounts = query_all("SELECT * FROM accounts WHERE user_id = ? AND type != 'Credit Card'", (current_user_id(),))
        rows = query_all("SELECT * FROM investments WHERE user_id = ? ORDER BY id DESC", (current_user_id(),))
        return render_template("investments.html", accounts=accounts, assets=INVESTMENT_PRICES, investments=rows)


    @app.route("/activity")
    def activity():
        if not require_user():
            return redirect(url_for("signin"))
        rows = query_all("SELECT * FROM activities WHERE user_id = ? ORDER BY id DESC", (current_user_id(),))
        return render_template("activity.html", activities=rows)
