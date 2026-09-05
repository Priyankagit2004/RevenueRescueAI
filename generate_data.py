import csv
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path


# =========================================
# CONFIGURATION
# =========================================

random.seed(42)

BASE_DIR = Path(__file__).resolve().parent

TRANSACTIONS_FILE = BASE_DIR / "transactions.csv"

CHECKOUTS_FILE = BASE_DIR / "checkout_abandonments.csv"

INVOICES_FILE = BASE_DIR / "invoices.csv"


# =========================================
# TRANSACTION DATA CONFIGURATION
# =========================================

PAYMENT_METHODS = [
    "Credit Card",
    "Debit Card",
    "Net Banking",
    "UPI"
]

FAILURE_REASONS = [
    "Network Error",
    "Bank Timeout",
    "Payment Gateway Error",
    "Card Declined",
    "Insufficient Funds"
]

FAILURE_WEIGHTS = [
    0.25,
    0.20,
    0.20,
    0.20,
    0.15
]


# =========================================
# CHECKOUT DATA CONFIGURATION
# =========================================

ABANDONMENT_STAGES = [
    "Cart",
    "Address",
    "Payment",
    "Payment Authentication"
]

ABANDONMENT_STAGE_WEIGHTS = [
    0.15,
    0.20,
    0.40,
    0.25
]


# =========================================
# B2B RECEIVABLES DATA CONFIGURATION
# =========================================

B2B_CUSTOMERS = [
    {"name": "Apex Global Logistics", "email": "billing@apexlogistics.com"},
    {"name": "Novatech Cloud Solutions", "email": "ap@novatechcloud.io"},
    {"name": "Starlight Media Group", "email": "finance@starlightmedia.com"},
    {"name": "BlueRidge Manufacturing", "email": "invoices@blueridgemfg.com"},
    {"name": "Zenith Retail Partners", "email": "accounts@zenithretail.in"},
    {"name": "OmniCorp Digital", "email": "payables@omnicorp.net"},
    {"name": "Solstice Energy Labs", "email": "finance@solsticeenergy.org"},
    {"name": "Vanguard Pharma Corp", "email": "ap@vanguardpharma.com"},
    {"name": "Nexus Infrastructure", "email": "vendorpay@nexusinfra.com"},
    {"name": "Hyperion Software Systems", "email": "accounting@hyperionsystems.io"}
]


# =========================================
# GENERATE TRANSACTION DATA
# =========================================

def generate_transactions():

    rows = []

    current_time = datetime.now()

    for _ in range(150):

        transaction_id = uuid.uuid4().hex[:8]

        amount = random.randint(
            500,
            10000
        )

        payment_method = random.choice(
            PAYMENT_METHODS
        )

        is_failed = random.random() < 0.70

        if is_failed:

            status = "FAILED"

            failure_reason = random.choices(
                FAILURE_REASONS,
                weights=FAILURE_WEIGHTS,
                k=1
            )[0]

        else:

            status = "SUCCESS"

            failure_reason = ""

        created_at = current_time - timedelta(
            minutes=random.randint(
                1,
                10080
            )
        )

        rows.append({

            "transaction_id":
                transaction_id,

            "amount":
                amount,

            "payment_method":
                payment_method,

            "status":
                status,

            "failure_reason":
                failure_reason,

            "created_at":
                created_at.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

        })

    return rows


# =========================================
# GENERATE CHECKOUT ABANDONMENT DATA
# =========================================

def generate_checkout_abandonments():

    rows = []

    current_time = datetime.now()

    for _ in range(80):

        checkout_id = uuid.uuid4().hex[:8]

        customer_id = (
            "cust_" +
            uuid.uuid4().hex[:6]
        )

        cart_amount = random.randint(
            300,
            15000
        )

        items_count = random.randint(
            1,
            8
        )

        abandonment_stage = random.choices(
            ABANDONMENT_STAGES,
            weights=ABANDONMENT_STAGE_WEIGHTS,
            k=1
        )[0]

        minutes_since_abandonment = random.randint(
            10,
            10080
        )

        previous_successful_orders = random.randint(
            0,
            12
        )

        previous_abandonments = random.randint(
            0,
            5
        )

        recovery_consent = random.choices(
            ["YES", "NO"],
            weights=[0.85, 0.15],
            k=1
        )[0]

        abandoned_at = current_time - timedelta(
            minutes=minutes_since_abandonment
        )

        rows.append({

            "checkout_id":
                checkout_id,

            "customer_id":
                customer_id,

            "cart_amount":
                cart_amount,

            "items_count":
                items_count,

            "abandonment_stage":
                abandonment_stage,

            "minutes_since_abandonment":
                minutes_since_abandonment,

            "previous_successful_orders":
                previous_successful_orders,

            "previous_abandonments":
                previous_abandonments,

            "recovery_consent":
                recovery_consent,

            "status":
                "ABANDONED",

            "abandoned_at":
                abandoned_at.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

        })

    return rows


# =========================================
# GENERATE B2B INVOICES DATA
# =========================================

def generate_invoices():

    rows = []
    current_time = datetime.now()

    aging_distribution = [
        ("Current", 0, 0, 10),
        ("1-30 Days", 1, 30, 25),
        ("31-60 Days", 31, 60, 25),
        ("61-90 Days", 61, 90, 20),
        ("90+ Days", 91, 150, 20)
    ]

    for bucket_name, min_days, max_days, count in aging_distribution:
        for _ in range(count):
            invoice_id = f"INV-{random.randint(10000, 99999)}"
            customer = random.choice(B2B_CUSTOMERS)
            invoice_amount = random.randint(15000, 450000)

            if bucket_name == "Current":
                days_overdue = 0
                due_date = current_time + timedelta(days=random.randint(3, 30))
                invoice_date = due_date - timedelta(days=30)
                payment_status = random.choice(["UNPAID", "PAID"])
                recovery_stage = "STANDARD"
                promise_status = "NONE"
                promise_date_str = ""
                promise_amount = 0
            else:
                days_overdue = random.randint(min_days, max_days)
                due_date = current_time - timedelta(days=days_overdue)
                invoice_date = due_date - timedelta(days=30)
                payment_status = random.choices(
                    ["UNPAID", "PARTIALLY_PAID", "PAID"],
                    weights=[0.75, 0.15, 0.10],
                    k=1
                )[0]

                if days_overdue <= 30:
                    recovery_stage = "FRIENDLY_REMINDER"
                elif days_overdue <= 60:
                    recovery_stage = "FIRM_REMINDER"
                elif days_overdue <= 90:
                    recovery_stage = "STRONG_ESCALATION"
                else:
                    recovery_stage = "MANUAL_REVIEW"

                # Promise to pay variations
                promise_choice = random.choices(
                    ["NONE", "ACTIVE", "BROKEN", "KEPT"],
                    weights=[0.45, 0.25, 0.20, 0.10],
                    k=1
                )[0]

                promise_status = promise_choice

                if promise_status == "ACTIVE":
                    promise_date = current_time + timedelta(days=random.randint(1, 14))
                    promise_date_str = promise_date.strftime("%Y-%m-%d")
                    promise_amount = round(invoice_amount * random.uniform(0.5, 1.0))
                elif promise_status == "BROKEN":
                    promise_date = current_time - timedelta(days=random.randint(2, 10))
                    promise_date_str = promise_date.strftime("%Y-%m-%d")
                    promise_amount = round(invoice_amount * random.uniform(0.5, 1.0))
                    recovery_stage = "BROKEN_PROMISE_ESCALATION"
                elif promise_status == "KEPT":
                    promise_date = current_time - timedelta(days=random.randint(5, 15))
                    promise_date_str = promise_date.strftime("%Y-%m-%d")
                    promise_amount = invoice_amount
                    payment_status = "PAID"
                    recovery_stage = "RESOLVED"
                else:
                    promise_date_str = ""
                    promise_amount = 0

            consent = random.choices(["YES", "NO"], weights=[0.90, 0.10], k=1)[0]
            dnd = random.choices(["NO", "YES"], weights=[0.92, 0.08], k=1)[0]

            rows.append({
                "invoice_id": invoice_id,
                "customer_name": customer["name"],
                "customer_email": customer["email"],
                "invoice_amount": invoice_amount,
                "invoice_date": invoice_date.strftime("%Y-%m-%d"),
                "due_date": due_date.strftime("%Y-%m-%d"),
                "days_overdue": days_overdue,
                "aging_bucket": bucket_name,
                "payment_status": payment_status,
                "recovery_stage": recovery_stage,
                "consent": consent,
                "dnd": dnd,
                "promise_to_pay_date": promise_date_str,
                "promise_to_pay_amount": promise_amount,
                "promise_status": promise_status
            })

    return rows


# =========================================
# SAVE TRANSACTION DATA
# =========================================

def save_transactions():

    transactions = generate_transactions()

    fieldnames = [

        "transaction_id",

        "amount",

        "payment_method",

        "status",

        "failure_reason",

        "created_at"

    ]

    with open(
        TRANSACTIONS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            transactions
        )

    print(
        f"Created {len(transactions)} transactions"
    )


# =========================================
# SAVE CHECKOUT DATA
# =========================================

def save_checkout_abandonments():

    checkouts = (
        generate_checkout_abandonments()
    )

    fieldnames = [

        "checkout_id",

        "customer_id",

        "cart_amount",

        "items_count",

        "abandonment_stage",

        "minutes_since_abandonment",

        "previous_successful_orders",

        "previous_abandonments",

        "recovery_consent",

        "status",

        "abandoned_at"

    ]

    with open(
        CHECKOUTS_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            checkouts
        )

    print(
        f"Created {len(checkouts)} abandoned checkouts"
    )


# =========================================
# SAVE INVOICES DATA
# =========================================

def save_invoices():

    invoices = generate_invoices()

    fieldnames = [
        "invoice_id",
        "customer_name",
        "customer_email",
        "invoice_amount",
        "invoice_date",
        "due_date",
        "days_overdue",
        "aging_bucket",
        "payment_status",
        "recovery_stage",
        "consent",
        "dnd",
        "promise_to_pay_date",
        "promise_to_pay_amount",
        "promise_status"
    ]

    with open(
        INVOICES_FILE,
        "w",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            invoices
        )

    print(
        f"Created {len(invoices)} B2B invoices"
    )


# =========================================
# MAIN
# =========================================

if __name__ == "__main__":

    print(
        "Generating RevenueRescue AI data..."
    )

    print()

    save_transactions()

    save_checkout_abandonments()

    save_invoices()

    print()

    print(
        "Data generation completed successfully!"
    )

    print()

    print(
        f"Transactions file: {TRANSACTIONS_FILE}"
    )

    print(
        f"Checkout file: {CHECKOUTS_FILE}"
    )

    print(
        f"Invoices file: {INVOICES_FILE}"
    )