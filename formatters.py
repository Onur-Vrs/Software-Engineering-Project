def money(value, currency="TRY"):
    labels = {"TRY": "₺", "USD": "$", "EUR": "€", "GBP": "£", "KWD": "ك", "JPY": "¥"}
    return f"{float(value or 0):,.2f} {labels.get(currency, currency)}"
