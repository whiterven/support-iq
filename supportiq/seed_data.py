"""
data/seed_data.py
Generates realistic synthetic support tickets and knowledge base articles.
Creates a believable dataset for demonstrating SupportIQ's capabilities.

Usage:
  python data/seed_data.py --tickets 500 --kb-articles 150
"""

import os
import json
import random
import string
import uuid
import argparse
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}

# ─── Configuration ─────────────────────────────────────────────────────────

CATEGORIES = ["payment", "authentication", "checkout", "api", "billing",
               "account", "performance", "integration", "email", "mobile"]

CUSTOMER_TIERS = ["enterprise", "enterprise", "pro", "pro", "pro", "free", "free", "free"]

CUSTOMER_TIER_SLA = {"enterprise": 4, "pro": 8, "free": 72}

TICKET_TEMPLATES = {
    "payment": [
        ("Payment processing failed at checkout", "Customer reports that their credit card charge is failing with error code CARD_DECLINED even though their card has sufficient funds. They have tried 3 different cards with the same result."),
        ("Duplicate charge on customer account", "Customer was charged twice for the same order. Transaction IDs: {txn1} and {txn2}. Customer requesting immediate refund."),
        ("PayPal integration not working", "Customer cannot complete checkout using PayPal. They are redirected to PayPal but when returning to the site, the order shows as pending indefinitely."),
    ],
    "authentication": [
        ("Cannot login - 2FA not working", "Customer's two-factor authentication is not sending SMS codes. They have tried resetting but the issue persists. Account is locked."),
        ("SSO integration broken after recent update", "Enterprise customer reports that their SAML SSO login stopped working after yesterday's maintenance window. Entire team is locked out."),
        ("Password reset email not arriving", "Customer has requested password reset 3 times in the last hour. Emails are not arriving. Checked spam folder."),
    ],
    "checkout": [
        ("Cart items disappearing on mobile", "Customer adds items to cart on iPhone Safari but when they proceed to checkout, the cart is empty. Issue only on mobile."),
        ("Coupon code not applying discount", "Valid coupon code SAVE20 is accepted but the 20% discount is not reflected in the order total. The code shows as applied."),
        ("Checkout stuck on payment step", "After entering payment details, the checkout spinner runs indefinitely. No error message shown. Order is not being created."),
    ],
    "api": [
        ("API rate limit hit despite low usage", "Customer is getting 429 errors but their usage dashboard shows they are only at 40% of their rate limit. Rate limit appears to be miscalculated."),
        ("Webhook events not delivering", "Customer's webhook endpoint is correctly configured and returns 200, but events are not being delivered. Verified with ngrok."),
        ("API key suddenly returning 401 Unauthorized", "Valid API key that was working yesterday is now returning 401. Key has not been revoked."),
    ],
    "billing": [
        ("Invoice not generated for last month", "Monthly invoice for March has not been generated. Previous months were fine. Customer needs it for accounting."),
        ("Subscription plan not upgrading correctly", "Customer upgraded from Pro to Enterprise via the billing portal but their account still shows Pro features. Payment was taken."),
        ("VAT not being applied to EU invoice", "EU-based enterprise customer is not seeing VAT applied to their invoices. This is causing accounting compliance issues."),
    ],
}

KB_ARTICLES = {
    "payment": [
        {
            "title": "Troubleshooting Credit Card Decline Errors",
            "content": """When a customer's credit card is declined despite having sufficient funds, follow these steps:

1. **Check error code**: CARD_DECLINED typically indicates a bank-side block. Ask the customer to contact their bank.
2. **Verify 3DS**: Check if 3D Secure authentication is completing successfully in the payment logs.
3. **Try alternative**: Suggest the customer use a different browser or clear cookies.
4. **Check processor status**: Verify our payment processor (Stripe) status page for any ongoing incidents.
5. **Manual review**: If the customer is enterprise tier, escalate to the payments team for manual review.

Common resolution: 70% of cases are resolved by the customer contacting their bank to authorize international online transactions.""",
        },
        {
            "title": "Handling Duplicate Charge Refund Requests",
            "content": """For duplicate charge disputes:

1. **Verify duplicate**: Check Stripe dashboard for both transaction IDs. Confirm they are genuine duplicates (same amount, same card, within 10 minutes).
2. **Issue refund**: Use Stripe dashboard to initiate refund for the duplicate charge immediately.
3. **Timeline**: Inform customer refund will appear in 5-10 business days.
4. **Document**: Log the issue in our billing system with both transaction IDs.
5. **Escalate if disputed**: If customer denies both charges are valid, escalate to billing team.

Template response: "I've verified the duplicate charge and have initiated a full refund of $X. You'll see this in 5-10 business days. We apologize for the inconvenience." """,
        },
    ],
    "authentication": [
        {
            "title": "Resolving 2FA SMS Delivery Failures",
            "content": """When customers are not receiving 2FA SMS codes:

1. **Check SMS provider status**: Verify Twilio status at status.twilio.com
2. **Carrier filtering**: Some carriers block automated SMS. Ask if customer is on a VOIP number.
3. **Rate limiting**: Check if customer hit the SMS rate limit (5 per hour). Wait 15 minutes.
4. **Phone number format**: Verify number is in E.164 format (+1XXXXXXXXXX).
5. **Alternative**: Offer to enable authenticator app (TOTP) as an alternative to SMS.
6. **Emergency access**: For enterprise customers, provide backup access code from admin console.

Resolution time: Most SMS issues resolve within 15 minutes after carrier retry.""",
        },
        {
            "title": "Diagnosing SSO/SAML Integration Issues",
            "content": """For enterprise SSO failures after updates:

1. **Check IdP metadata**: Verify our Service Provider metadata URL is still accessible and hasn't changed.
2. **Certificate expiry**: Enterprise SSO certificates expire. Check expiry date in IdP settings.
3. **ACS URL**: Confirm the Assertion Consumer Service URL in IdP matches our current callback URL.
4. **Attribute mapping**: Verify email attribute is correctly mapped (typically email or emailAddress).
5. **Test with SAML tracer**: Ask admin to use SAML Tracer browser extension to capture assertion.
6. **Emergency bypass**: Provide temporary password login for account owner while SSO is fixed.

Critical: Enterprise SSO issues are CRITICAL priority. Escalate immediately if more than 3 users are locked out.""",
        },
    ],
}


def random_date(days_back: int = 90) -> datetime:
    now = datetime.now(timezone.utc)
    offset = random.uniform(0, days_back * 24 * 60 * 60)
    return now - timedelta(seconds=offset)


def random_customer_id() -> str:
    return f"CUST-{random.randint(10000, 99999)}"


def generate_tickets(count: int) -> list:
    tickets = []
    for i in range(count):
        category = random.choice(CATEGORIES)
        tier = random.choice(CUSTOMER_TIERS)
        templates = TICKET_TEMPLATES.get(category, TICKET_TEMPLATES["payment"])
        template = random.choice(templates)
        title, description = template

        # Fill in placeholders
        description = description.replace("{txn1}", f"TXN-{random.randint(100000, 999999)}")
        description = description.replace("{txn2}", f"TXN-{random.randint(100000, 999999)}")

        created = random_date(90)

        # Some tickets are resolved (for historical training data)
        is_resolved = random.random() < 0.7
        resolve_hours = random.uniform(0.5, CUSTOMER_TIER_SLA.get(tier, 72) * 2)
        updated = created + timedelta(hours=resolve_hours) if is_resolved else created

        ticket = {
            "ticket_id": f"TKT-{i+1:05d}",
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
            "status": "resolved" if is_resolved else "open",
            "priority_score": round(random.uniform(20, 95), 1),
            "priority_label": random.choice(["CRITICAL", "HIGH", "MEDIUM", "LOW"]),
            "category": category,
            "customer_id": random_customer_id(),
            "customer_tier": tier,
            "title": title,
            "description": description,
            # Semantic fields — will be auto-vectorized by the inference pipeline
            "title_semantic": title,
            "description_semantic": description,
            "resolution_confidence": round(random.uniform(0.65, 0.98), 3) if is_resolved else None,
            "resolution_final": f"Resolution for {title}: Issue identified and resolved. Customer notified." if is_resolved else None,
            "resolved_by": random.choice(["agent", "human"]) if is_resolved else None,
            "sla_hours": CUSTOMER_TIER_SLA.get(tier, 72),
            "sla_breached": random.random() < 0.15,
            "feedback_score": random.choice([1, 1, 1, -1]) if is_resolved else None,
        }
        tickets.append(ticket)

    return tickets


def generate_kb_articles() -> list:
    articles = []
    article_num = 1
    for category, templates in KB_ARTICLES.items():
        for template in templates:
            article = {
                "article_id": f"KB-{article_num:04d}",
                "created_at": random_date(180).isoformat(),
                "updated_at": random_date(30).isoformat(),
                "category": category,
                "title": template["title"],
                "content": template["content"],
                "title_semantic": template["title"],
                "content_semantic": template["content"],
                "tags": [category, "troubleshooting", "support"],
                "draft": False,
                "usage_count": random.randint(5, 150),
                "avg_feedback": round(random.uniform(2.5, 5.0), 1),
                "negative_feedback_rate": round(random.uniform(0, 0.3), 2),
            }
            articles.append(article)
            article_num += 1
    return articles


def generate_customers(count: int = 100) -> list:
    customers = []
    for i in range(count):
        tier = random.choice(CUSTOMER_TIERS)
        customers.append({
            "customer_id": f"CUST-{i+10000:05d}",
            "company_name": f"Company {i+1}",
            "tier": tier,
            "contract_value": {
                "enterprise": round(random.uniform(50000, 500000), 2),
                "pro": round(random.uniform(1000, 50000), 2),
                "free": 0.0,
            }[tier],
            "sla_hours": CUSTOMER_TIER_SLA.get(tier, 72),
            "open_tickets": random.randint(0, 10),
            "lifetime_tickets": random.randint(1, 200),
            "avg_csat": round(random.uniform(2.5, 5.0), 1),
            "last_ticket_at": random_date(30).isoformat(),
            "health_score": round(random.uniform(40, 100), 1),
        })
    return customers


def generate_deployments(count: int = 50) -> list:
    services = ["checkout-service", "auth-service", "payment-service",
                 "api-gateway", "notification-service", "billing-service"]
    deployments = []
    for i in range(count):
        service = random.choice(services)
        deployments.append({
            "deployment_id": f"d-{uuid.uuid4().hex[:8]}",
            "deployed_at": random_date(30).isoformat(),
            "service": service,
            "version": f"v{random.randint(1, 5)}.{random.randint(0, 20)}.{random.randint(0, 10)}",
            "environment": "production",
            "deployed_by": f"engineer{random.randint(1, 20)}@company.com",
            "team": service.split("-")[0] + "-team",
            "description": f"Deploy {service} with bug fixes and performance improvements",
            "description_semantic": f"Deploy {service} with bug fixes and performance improvements",
            "rollback_available": random.random() < 0.9,
            "correlated_ticket_surge": False,
        })
    return deployments


def bulk_index(index: str, documents: list, batch_size: int = 100):
    """Bulk index documents into Elasticsearch."""
    url = f"{ELASTIC_URL}/_bulk"
    total = len(documents)
    indexed = 0

    for i in range(0, total, batch_size):
        batch = documents[i:i + batch_size]
        bulk_body = ""
        for doc in batch:
            bulk_body += json.dumps({"index": {"_index": index}}) + "\n"
            bulk_body += json.dumps(doc) + "\n"

        resp = requests.post(
            url,
            headers={**HEADERS, "Content-Type": "application/x-ndjson"},
            data=bulk_body,
            timeout=60,
        )
        if resp.status_code == 200:
            indexed += len(batch)
            print(f"  Indexed {indexed}/{total}...", end="\r")
        else:
            print(f"\n  ❌ Bulk indexing error: {resp.status_code} — {resp.text[:200]}")

    print(f"  ✅ Indexed {indexed}/{total} documents into '{index}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed SupportIQ with synthetic data")
    parser.add_argument("--tickets", type=int, default=500)
    parser.add_argument("--kb-articles", type=int, default=20)
    parser.add_argument("--customers", type=int, default=100)
    parser.add_argument("--deployments", type=int, default=50)
    args = parser.parse_args()

    print("=" * 60)
    print("🌱 Seeding SupportIQ with Synthetic Data")
    print("=" * 60)

    print(f"\n1. Generating {args.tickets} support tickets...")
    tickets = generate_tickets(args.tickets)
    bulk_index("support-tickets", tickets)

    print(f"\n2. Generating knowledge base articles...")
    kb_articles = generate_kb_articles()
    bulk_index("knowledge-base", kb_articles)

    print(f"\n3. Generating {args.customers} customer profiles...")
    customers = generate_customers(args.customers)
    bulk_index("customer-profiles", customers)

    print(f"\n4. Generating {args.deployments} deployment events...")
    deployments = generate_deployments(args.deployments)
    bulk_index("deployments", deployments)

    print("\n✅ Data seeding complete!")
    print(f"   Tickets     : {len(tickets)}")
    print(f"   KB Articles : {len(kb_articles)}")
    print(f"   Customers   : {len(customers)}")
    print(f"   Deployments : {len(deployments)}")
