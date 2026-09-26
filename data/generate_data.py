"""
Generate a realistic synthetic D2C store dataset (Indian e-commerce).

Output CSVs keep the SmartRetailAnalytics column layout
(customers.csv / products.csv / sales.csv) and add carts.csv + payments.csv.
Deterministic (seeded) so demos are reproducible. Dates are relative to today.
"""
import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT = Path(__file__).parent
rng = random.Random(42)
TODAY = datetime.now(timezone.utc).replace(tzinfo=None).replace(hour=12, minute=0, second=0, microsecond=0)

FIRST = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna", "Ishaan", "Rohan",
         "Ananya", "Diya", "Saanvi", "Aadhya", "Pari", "Anika", "Navya", "Myra", "Sara", "Ira",
         "Kabir", "Dhruv", "Ayaan", "Atharv", "Priya", "Neha", "Kavya", "Riya", "Meera", "Tara",
         "Vismaya", "Nikhil", "Pooja", "Rahul", "Sneha", "Karan", "Isha", "Aditi", "Varun", "Tanvi"]
LAST = ["Sharma", "Patel", "Iyer", "Reddy", "Nair", "Gupta", "Mehta", "Joshi", "Kulkarni", "Desai",
        "Rao", "Singh", "Katkar", "Menon", "Shah", "Pillai", "Verma", "Bose", "Chopra", "Kapoor"]
CITIES = ["Mumbai", "Pune", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Ahmedabad", "Jaipur", "Kochi"]

CATALOG = {
    "Apparel": [("Linen Kurta", 1499), ("Cotton Tee", 599), ("Denim Jacket", 2799), ("Silk Dupatta", 1199),
                ("Chino Trousers", 1699), ("Handloom Saree", 3999), ("Hoodie", 1899)],
    "Beauty": [("Vitamin C Serum", 799), ("Sunscreen SPF50", 499), ("Kumkumadi Oil", 1299), ("Lip Tint", 349),
               ("Hair Mask", 649), ("Face Wash", 299)],
    "Electronics": [("Wireless Earbuds", 2499), ("Smart Watch", 4999), ("Power Bank 20k", 1599),
                    ("Bluetooth Speaker", 2199), ("USB-C Charger", 899), ("Neckband", 1299)],
    "Home": [("Brass Diya Set", 999), ("Cotton Bedsheet", 1399), ("Ceramic Mug Set", 749), ("Scented Candle", 549),
             ("Jute Rug", 2299), ("Copper Bottle", 899)],
    "Grocery": [("Cold Brew Coffee", 449), ("Assam Tea 500g", 399), ("Trail Mix", 299), ("Organic Honey", 549),
                ("A2 Ghee 1L", 1199), ("Millet Muesli", 379)],
}

# Customer archetypes: (weight, orders range, active window in days before today, preferred category bias)
ARCHETYPES = {
    "vip":      (0.07, (10, 22), (0, 330)),
    "loyal":    (0.18, (5, 10), (0, 300)),
    "regular":  (0.25, (2, 5), (10, 240)),
    "new":      (0.15, (1, 2), (0, 35)),
    "lapsing":  (0.15, (3, 7), (75, 330)),   # used to buy, went quiet
    "one_time": (0.20, (1, 1), (60, 360)),
}
METHODS = ["upi", "upi", "upi", "card", "card", "netbanking", "wallet"]
FAIL_REASONS = {
    "upi": ["upi_pin_incorrect", "payment_timeout", "insufficient_funds", "upi_app_declined"],
    "card": ["bank_declined", "authentication_failed", "insufficient_funds", "card_expired"],
    "netbanking": ["payment_timeout", "bank_server_down"],
    "wallet": ["insufficient_funds", "payment_timeout"],
}


def pick_archetype():
    r, acc = rng.random(), 0.0
    for name, (w, *_rest) in ARCHETYPES.items():
        acc += w
        if r <= acc:
            return name
    return "regular"


def main(n_customers: int = 320):
    products, pid = [], 1
    for cat, items in CATALOG.items():
        for name, price in items:
            products.append({"product_id": f"P{pid:03d}", "product_name": name, "category": cat,
                             "price": price, "margin_pct": rng.choice([25, 30, 35, 40, 45, 55])})
            pid += 1
    by_cat = {c: [p for p in products if p["category"] == c] for c in CATALOG}

    customers, sales, payments, carts = [], [], [], []
    txn, order_no, pay_no, cart_no = 1, 1, 1, 1

    for i in range(1, n_customers + 1):
        cid = f"C{i:04d}"
        arche = pick_archetype()
        _, (omin, omax), (win_start, win_end) = ARCHETYPES[arche]
        fav = rng.choice(list(CATALOG))
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        signup = TODAY - timedelta(days=win_end + rng.randint(0, 20))
        customers.append({
            "customer_id": cid, "customer_name": name, "city": rng.choice(CITIES),
            "email": f"{name.lower().replace(' ', '.')}{i}@example.in",
            "phone": f"+91 9{rng.randint(100000000, 999999999)}",
            "signup_date": signup.date().isoformat(), "marketing_opt_in": rng.random() > 0.08,
            "segment_hint": arche,
        })

        for _ in range(rng.randint(omin, omax)):
            day = rng.randint(win_start, win_end)
            ts = TODAY - timedelta(days=day, hours=rng.randint(0, 20))
            oid = f"ORD{order_no:05d}"
            order_no += 1
            n_items = rng.choice([1, 1, 1, 2, 2, 3])
            order_total = 0
            for _ in range(n_items):
                cat = fav if rng.random() < 0.65 else rng.choice(list(CATALOG))
                p = rng.choice(by_cat[cat])
                qty = rng.choice([1, 1, 1, 2])
                amt = p["price"] * qty
                order_total += amt
                sales.append({"transaction_id": txn, "order_id": oid, "product_id": p["product_id"],
                              "customer_id": cid, "quantity": qty, "amount": amt,
                              "transaction_date": ts.isoformat(), "payment_id": f"pay_demo{pay_no:06d}"})
                txn += 1
            payments.append({"razorpay_payment_id": f"pay_demo{pay_no:06d}", "razorpay_order_id": f"order_{oid}",
                             "customer_id": cid, "amount": order_total, "status": "captured",
                             "method": rng.choice(METHODS), "error_reason": "", "created_at": ts.isoformat()})
            pay_no += 1

        # Abandoned carts & failed payments in the last 14 days
        cart_prob = {"vip": 0.25, "loyal": 0.25, "regular": 0.22, "new": 0.35, "lapsing": 0.18, "one_time": 0.12}[arche]
        if rng.random() < cart_prob:
            stage = rng.choices(["cart", "checkout", "payment_failed"], weights=[5, 3, 2])[0]
            picked = {}
            for _ in range(rng.choice([1, 1, 2, 3])):
                p = rng.choice(by_cat[fav] if rng.random() < 0.7 else products)
                picked.setdefault(p["product_id"], {"product_id": p["product_id"], "name": p["product_name"],
                                                     "qty": 0, "price": p["price"]})["qty"] += rng.choice([1, 1, 2])
            items = list(picked.values())
            value = sum(i["price"] * i["qty"] for i in items)
            ts = TODAY - timedelta(hours=rng.randint(3, 14 * 24))
            carts.append({"cart_id": f"cart_{cart_no:05d}", "customer_id": cid, "status": "abandoned",
                          "stage": stage, "value": value, "items": items, "updated_at": ts.isoformat()})
            cart_no += 1
            if stage == "payment_failed":
                method = rng.choice(METHODS)
                payments.append({"razorpay_payment_id": f"pay_fail{pay_no:06d}", "razorpay_order_id": "",
                                 "customer_id": cid, "amount": value, "status": "failed",
                                 "method": method, "error_reason": rng.choice(FAIL_REASONS[method]),
                                 "created_at": ts.isoformat()})
                pay_no += 1

    def write(name, rows, fields):
        with open(OUT / name, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r[k] for k in fields})

    import json
    for c in carts:
        c["items"] = json.dumps(c["items"])
    write("customers.csv", customers, ["customer_id", "customer_name", "city", "email", "phone", "signup_date", "marketing_opt_in"])
    write("products.csv", products, ["product_id", "product_name", "category", "price", "margin_pct"])
    write("sales.csv", sales, ["transaction_id", "order_id", "product_id", "customer_id", "quantity", "amount", "transaction_date", "payment_id"])
    write("payments.csv", payments, ["razorpay_payment_id", "razorpay_order_id", "customer_id", "amount", "status", "method", "error_reason", "created_at"])
    write("carts.csv", carts, ["cart_id", "customer_id", "status", "stage", "value", "items", "updated_at"])
    print(f"Generated {len(customers)} customers, {len(products)} products, {len(sales)} sale lines, "
          f"{len(payments)} payments, {len(carts)} abandoned carts")


if __name__ == "__main__":
    main()
