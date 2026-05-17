from flask import Flask

from auth import current_user_id
from config import EXCHANGE_RATES, SECRET_KEY
from database import init_db
from finance import currency_summary
from formatters import money
from payments import process_due_recurring_payments
from routes import register_routes


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = SECRET_KEY
    app.jinja_env.filters["money"] = money

    @app.context_processor
    def inject_currency_data():
        if current_user_id():
            return {
                "currency_summary": currency_summary(),
                "exchange_rates": EXCHANGE_RATES,
            }
        return {
            "currency_summary": None,
            "exchange_rates": EXCHANGE_RATES,
        }

    @app.before_request
    def prepare_request():
        init_db()
        if current_user_id():
            process_due_recurring_payments()

    register_routes(app)
    return app


app = create_app()


if __name__ == "__main__":
    init_db()
    app.run(debug=False)
