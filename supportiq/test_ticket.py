"""
orchestration/test_ticket.py
Sends a realistic test ticket through the full SupportIQ pipeline.
Perfect for the hackathon demo video.

Usage:
  python orchestration/test_ticket.py
  python orchestration/test_ticket.py --scenario surge    (triggers Ghost Ticket demo)
  python orchestration/test_ticket.py --scenario critic   (triggers Critic rejection demo)
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from a2a_pipeline import SupportIQPipeline

# ─── Demo Scenarios ────────────────────────────────────────────────────────

SCENARIOS = {

    # Standard ticket — demonstrates normal auto-resolve flow
    "standard": {
        "ticket_id": f"TKT-DEMO-{int(time.time())}",
        "title": "Cannot login — 2FA SMS not arriving",
        "description": "I've been trying to log in for the past hour and the 2FA SMS code is not being sent to my phone number +1 (555) 234-5678. I've requested the code 5 times and nothing has arrived. I checked my spam folder. My account email is john.doe@enterprise-corp.com. This is blocking our entire team from working.",
        "customer_id": "CUST-10042",
        "category": "authentication",
        "channel": "email",
        "created_at": datetime.now(timezone.utc).isoformat(),
    },

    # Surge scenario — demonstrates Ghost Ticket pre-emption
    "surge": {
        "ticket_id": f"TKT-SURGE-{int(time.time())}",
        "title": "Payment checkout completely broken",
        "description": "Since about 2pm today, none of our customers can complete checkout. They get to the payment step, enter their card details, and the spinner just runs forever. No error message. Our sales have dropped to zero. This started right after your maintenance window ended. We are an enterprise customer and this is CRITICAL.",
        "customer_id": "CUST-10001",
        "category": "payment",
        "channel": "slack",
        "created_at": datetime.now(timezone.utc).isoformat(),
    },

    # Critic scenario — demonstrates the self-correcting quality loop
    "critic": {
        "ticket_id": f"TKT-CRITIC-{int(time.time())}",
        "title": "API returning 401 despite valid v3 API key",
        "description": "We migrated to the v3 API last week as required. Our API key was regenerated for v3. Since yesterday, all our API calls are returning 401 Unauthorized. The key is definitely valid — I can see it in the dashboard. Our system is processing 50,000 requests/day and we're completely blocked. URGENT.",
        "customer_id": "CUST-10015",
        "category": "api",
        "channel": "api",
        "created_at": datetime.now(timezone.utc).isoformat(),
    },
}


def print_trace(trace: dict):
    """Pretty print the pipeline execution trace."""
    print("\n" + "━"*60)
    print(f"📊 PIPELINE TRACE: {trace['ticket_id']}")
    print("━"*60)

    for step in trace.get("steps", []):
        agent = step.get("agent", "?")
        step_num = step.get("step", "?")

        if "solver+critic" in agent:
            attempt = step.get("attempt", 1)
            s_conf = step.get("solver_confidence", 0)
            c_qual = step.get("critic_quality", 0)
            c_dec = step.get("critic_decision", "?")
            icon = "✅" if c_dec == "APPROVED" else "❌"
            print(f"  [{step_num}] 🧠+🔍 Solver→Critic (attempt {attempt}): "
                  f"confidence={s_conf:.0%}, quality={c_qual:.0%} {icon}")
        elif agent == "watcher":
            print(f"  [{step_num}] 👀 Watcher: "
                  f"{step.get('similar_count', 0)} similar tickets found, "
                  f"tier={step.get('customer_tier', '?')}, "
                  f"known_solution={step.get('has_known_solution', False)}")
        elif agent == "judge":
            surge_icon = "🚨" if step.get("surge_detected") else ""
            print(f"  [{step_num}] ⚖️  Judge: priority={step.get('priority_label', '?')} "
                  f"({step.get('priority_score', 0):.0f}) {surge_icon}")
        elif agent == "pipeline":
            print(f"  [{step_num}] ⚡ Decision: {step.get('decision', '?').upper()} "
                  f"(confidence={step.get('confidence', 0):.0%})")

    print("\n" + "─"*60)
    decision = trace.get("final_decision", "?")
    icons = {"auto_resolve": "🤖✅", "draft_for_approval": "📋👤", "escalate": "🆘"}
    print(f"FINAL DECISION: {icons.get(decision, '?')} {decision.upper()}")
    print(f"TOTAL TIME    : {trace.get('total_duration_ms', 0):,}ms "
          f"({trace.get('total_duration_ms', 0)/1000:.1f}s)")

    if trace.get("final_resolution"):
        print(f"\nRESOLUTION PREVIEW:")
        print(f"  {trace['final_resolution'][:200]}...")

    print("─"*60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="standard")
    args = parser.parse_args()

    ticket = SCENARIOS[args.scenario]

    print("="*60)
    print(f"🎯 SupportIQ Demo — Scenario: {args.scenario.upper()}")
    print("="*60)
    print(f"Ticket: {ticket['title']}")
    print(f"Category: {ticket['category']} | Customer: {ticket['customer_id']}")
    print("\nStarting pipeline...\n")

    pipeline = SupportIQPipeline()
    trace = pipeline.process_ticket(ticket)

    print_trace(trace)
