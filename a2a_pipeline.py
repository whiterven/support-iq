"""
orchestration/a2a_pipeline.py
The main A2A orchestration pipeline for SupportIQ.

This is the beating heart of the system. When a new ticket arrives:

  1. WATCHER  → Enrich ticket with semantic matches + customer profile
  2. JUDGE    → Score priority, detect surges, correlate deployments
  3. SOLVER   → Generate resolution with knowledge base search
  4. CRITIC   → Quality gate — approve or reject with critique
     └── If rejected → back to SOLVER (max 3 attempts)
  5. DECISION → Auto-resolve | Draft for approval | Escalate
  6. WORKFLOWS → CRM update, Slack notification, Ghost alerts

The self-correcting Critic→Solver loop is what makes this unique.
"""

import os
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional
import requests
from dotenv import load_dotenv

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestration.a2a_client import A2AClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("SupportIQ.Pipeline")

KIBANA_URL = os.getenv("KIBANA_URL")
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY")
ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

AUTO_RESOLVE_THRESHOLD = float(os.getenv("AUTO_RESOLVE_CONFIDENCE_THRESHOLD", "0.90"))
ESCALATE_THRESHOLD = float(os.getenv("ESCALATE_CONFIDENCE_THRESHOLD", "0.65"))
CRITIC_QUALITY_THRESHOLD = float(os.getenv("CRITIC_QUALITY_THRESHOLD", "0.75"))
MAX_SOLVER_ATTEMPTS = 3

ES_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}
KIBANA_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {KIBANA_API_KEY}",
    "kbn-xsrf": "true",
}


class SupportIQPipeline:
    """
    Multi-agent pipeline orchestrator.
    Coordinates all 5 agents via A2A protocol to process a support ticket end-to-end.
    """

    def __init__(self):
        self.client = A2AClient()
        self.pipeline_start = None

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────

    def process_ticket(self, ticket: dict) -> dict:
        """
        Full pipeline execution for a single ticket.
        Returns a complete trace of all agent decisions.
        """
        self.pipeline_start = time.time()
        ticket_id = ticket.get("ticket_id", f"TKT-{int(time.time())}")
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(f"🎫 Processing ticket: {ticket_id}")
        logger.info(f"   Title: {ticket.get('title', 'N/A')[:60]}")
        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        trace = {
            "ticket_id": ticket_id,
            "pipeline_start": datetime.now(timezone.utc).isoformat(),
            "steps": [],
            "final_decision": None,
            "final_resolution": None,
            "total_duration_ms": 0,
        }

        try:
            # ── STEP 1: WATCHER ──────────────────────────────────────────────
            enrichment = self._run_watcher(ticket, trace)

            # ── STEP 2: JUDGE ─────────────────────────────────────────────────
            triage = self._run_judge(ticket, enrichment, trace)

            # ── STEP 3 & 4: SOLVER + CRITIC LOOP ─────────────────────────────
            resolution = self._run_solver_critic_loop(ticket, enrichment, triage, trace)

            # ── STEP 5: DECISION & ACTION ─────────────────────────────────────
            final = self._execute_decision(ticket, resolution, triage, trace)
            trace["final_decision"] = final["decision"]
            trace["final_resolution"] = final.get("resolution_text")

        except Exception as e:
            logger.error(f"Pipeline error for {ticket_id}: {e}", exc_info=True)
            trace["error"] = str(e)
            trace["final_decision"] = "error"

        trace["total_duration_ms"] = int((time.time() - self.pipeline_start) * 1000)
        logger.info(f"✅ Pipeline complete: {ticket_id} → {trace['final_decision']} "
                    f"({trace['total_duration_ms']}ms)")

        # Write full trace to Elasticsearch
        self._write_pipeline_trace(trace)

        return trace

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: WATCHER
    # ─────────────────────────────────────────────────────────────────────────

    def _run_watcher(self, ticket: dict, trace: list) -> dict:
        logger.info("  [1/4] 👀 Watcher: Enriching ticket...")

        message = f"""New support ticket received. Please enrich it:

{json.dumps(ticket, indent=2)}"""

        result = self.client.send_message("watcher", message)
        enrichment = result["parsed"]

        trace["steps"].append({
            "step": 1,
            "agent": "watcher",
            "duration_ms": result["duration_ms"],
            "similar_count": enrichment.get("enrichment", {}).get("similar_count", 0),
            "has_known_solution": enrichment.get("enrichment", {}).get("has_known_solution", False),
            "customer_tier": enrichment.get("enrichment", {}).get("customer_tier", "unknown"),
        })

        logger.info(f"     ✓ Found {enrichment.get('enrichment', {}).get('similar_count', 0)} similar tickets | "
                    f"Known solution: {enrichment.get('enrichment', {}).get('has_known_solution', False)}")

        # Update ticket in Elasticsearch with enrichment
        self._update_ticket_es(ticket["ticket_id"], {
            "similar_tickets": enrichment.get("enrichment", {}).get("similar_tickets", []),
            "customer_tier": enrichment.get("enrichment", {}).get("customer_tier"),
            "sla_hours": enrichment.get("enrichment", {}).get("sla_hours"),
            "status": "enriched",
        })

        return enrichment

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: JUDGE
    # ─────────────────────────────────────────────────────────────────────────

    def _run_judge(self, ticket: dict, enrichment: dict, trace: list) -> dict:
        logger.info("  [2/4] ⚖️  Judge: Scoring priority...")

        message = f"""Triage this enriched support ticket:

Ticket:
{json.dumps(ticket, indent=2)}

Enrichment data:
{json.dumps(enrichment, indent=2)}"""

        result = self.client.send_message("judge", message)
        triage = result["parsed"]

        priority = triage.get("priority_label", "UNKNOWN")
        surge = triage.get("surge_detected", False)
        score = triage.get("priority_score", 0)

        trace["steps"].append({
            "step": 2,
            "agent": "judge",
            "duration_ms": result["duration_ms"],
            "priority_score": score,
            "priority_label": priority,
            "surge_detected": surge,
            "deployment_correlated": triage.get("deployment_correlation") is not None,
        })

        logger.info(f"     ✓ Priority: {priority} ({score:.1f}) | Surge: {surge}")

        # If surge detected + deployment correlated → Ghost Ticket Alert
        if surge and triage.get("deployment_correlation"):
            self._fire_ghost_ticket_alert(ticket, triage)

        # Update Elasticsearch
        self._update_ticket_es(ticket["ticket_id"], {
            "priority_score": score,
            "priority_label": priority,
            "triage_reasoning": triage.get("triage_reasoning"),
            "sla_breach_risk": triage.get("sla_breach_risk"),
            "deployment_correlation": triage.get("deployment_correlation"),
            "status": "triaged",
        })

        return triage

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3+4: SOLVER + CRITIC LOOP (self-correcting)
    # ─────────────────────────────────────────────────────────────────────────

    def _run_solver_critic_loop(self, ticket: dict, enrichment: dict,
                                 triage: dict, trace: list) -> dict:
        logger.info("  [3/4] 🧠 Solver+Critic: Generating and validating resolution...")

        previous_attempt = None
        final_resolution = None

        for attempt in range(1, MAX_SOLVER_ATTEMPTS + 1):
            logger.info(f"     Solver attempt {attempt}/{MAX_SOLVER_ATTEMPTS}...")

            # ── SOLVER ────────────────────────────────────────────────────────
            solver_message = f"""Resolve this support ticket.

Ticket:
{json.dumps(ticket, indent=2)}

Enrichment:
{json.dumps(enrichment, indent=2)}

Triage:
{json.dumps(triage, indent=2)}"""

            if previous_attempt:
                solver_message += f"""

⚠️ PREVIOUS ATTEMPT WAS REJECTED by the Critic Agent.
previous_attempt: {json.dumps(previous_attempt, indent=2)}

DO NOT repeat the same mistakes. Address every point in critic_feedback."""

            solver_result = self.client.send_message("solver", solver_message)
            resolution = solver_result["parsed"]
            confidence = resolution.get("confidence", 0)

            logger.info(f"       Solver: confidence={confidence:.2f}, "
                        f"decision={resolution.get('decision')}")

            # ── CRITIC ────────────────────────────────────────────────────────
            critic_message = f"""Evaluate this resolution draft:

Ticket:
title: {ticket.get('title')}
description: {ticket.get('description')}
category: {ticket.get('category', 'unknown')}

Resolution draft:
{json.dumps(resolution, indent=2)}"""

            critic_result = self.client.send_message("critic", critic_message)
            quality = critic_result["parsed"]
            quality_score = quality.get("quality_score", 0)
            critic_decision = quality.get("decision", "REJECTED")

            logger.info(f"       Critic: quality={quality_score:.2f}, decision={critic_decision}")

            trace["steps"].append({
                "step": f"3.{attempt}",
                "agent": "solver+critic",
                "attempt": attempt,
                "solver_confidence": confidence,
                "critic_quality": quality_score,
                "critic_decision": critic_decision,
                "solver_duration_ms": solver_result["duration_ms"],
                "critic_duration_ms": critic_result["duration_ms"],
            })

            if critic_decision == "APPROVED":
                logger.info(f"     ✓ Critic APPROVED on attempt {attempt}")
                final_resolution = {
                    **resolution,
                    "critic_quality_score": quality_score,
                    "attempts": attempt,
                    "final": True,
                }
                break
            else:
                logger.info(f"     ✗ Critic REJECTED. Critique: {quality.get('critique', '')[:100]}")
                previous_attempt = {
                    "resolution_draft": resolution.get("resolution_draft"),
                    "confidence": confidence,
                    "critic_feedback": quality.get("critique"),
                    "improvement_required": quality.get("improvement_required"),
                }

        if not final_resolution:
            # After max attempts, use the last draft anyway but flag it
            logger.warning(f"  ⚠️ Max attempts reached. Using last draft with low quality flag.")
            final_resolution = {
                **resolution,
                "critic_quality_score": quality_score,
                "attempts": MAX_SOLVER_ATTEMPTS,
                "quality_warning": True,
                "final": True,
            }

        # Update Elasticsearch
        self._update_ticket_es(ticket["ticket_id"], {
            "resolution_draft": final_resolution.get("resolution_draft"),
            "resolution_confidence": final_resolution.get("confidence"),
            "critic_score": final_resolution.get("critic_quality_score"),
            "resolution_attempts": final_resolution.get("attempts"),
            "status": "resolved_draft",
        })

        return final_resolution

    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: DECISION & EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    def _execute_decision(self, ticket: dict, resolution: dict,
                           triage: dict, trace: list) -> dict:
        confidence = resolution.get("confidence", 0)
        decision = resolution.get("decision", "escalate")
        resolution_text = resolution.get("resolution_draft", "")
        ticket_id = ticket["ticket_id"]

        logger.info(f"  [4/4] ⚡ Executing decision: {decision} (confidence={confidence:.2f})")

        if decision == "auto_resolve":
            self._trigger_workflow("crm_update", {
                "ticket_id": ticket_id,
                "resolution_text": resolution_text,
                "resolved_by": "agent",
                "is_auto_resolved": True,
                "confidence": confidence,
            })
            self._notify_slack(
                f"✅ *Auto-resolved:* `{ticket_id}`\n"
                f"*Confidence:* {confidence:.0%} | *Quality:* {resolution.get('critic_quality_score', 0):.0%}\n"
                f"*Attempts:* {resolution.get('attempts', 1)}\n"
                f"*Response sent to customer.*",
                emoji="robot_face"
            )
            self._update_ticket_es(ticket_id, {
                "status": "resolved",
                "resolution_final": resolution_text,
                "resolved_by": "agent",
            })

        elif decision == "draft_for_approval":
            self._notify_slack(
                f"📋 *Needs approval:* `{ticket_id}`\n"
                f"*Confidence:* {confidence:.0%} | *Quality:* {resolution.get('critic_quality_score', 0):.0%}\n"
                f"*Draft response:*\n```{resolution_text[:500]}```\n"
                f"React with 👍 to send, 👎 to reject and escalate.",
                emoji="pencil"
            )
            self._update_ticket_es(ticket_id, {"status": "pending_approval"})

        else:  # escalate
            self._notify_slack(
                f"🆘 *Escalation required:* `{ticket_id}`\n"
                f"*Priority:* {triage.get('priority_label', 'UNKNOWN')}\n"
                f"*Reason:* Low confidence ({confidence:.0%}). Human expertise needed.\n"
                f"*Draft available:* SupportIQ prepared a starting point — check Kibana.",
                emoji="sos"
            )
            self._update_ticket_es(ticket_id, {
                "status": "escalated",
                "resolution_draft": resolution_text,
            })

        trace["steps"].append({
            "step": 5,
            "agent": "pipeline",
            "decision": decision,
            "confidence": confidence,
            "quality_score": resolution.get("critic_quality_score"),
        })

        return {"decision": decision, "resolution_text": resolution_text}

    # ─────────────────────────────────────────────────────────────────────────
    # GHOST TICKET ALERT
    # ─────────────────────────────────────────────────────────────────────────

    def _fire_ghost_ticket_alert(self, ticket: dict, triage: dict):
        """Fire the pre-emptive surge alert via the Ghost Alert workflow."""
        logger.info("  🚨 Firing Ghost Ticket Alert...")
        correlation = triage.get("deployment_correlation", {})
        related = (correlation.get("related_deployments") or [{}])
        top_deploy = related[0] if related else {}

        self._trigger_workflow("ghost_alert", {
            "category": ticket.get("category"),
            "current_hourly_rate": triage.get("surge_data", {}).get("current_hourly_rate", "?"),
            "sigma_level": triage.get("surge_data", {}).get("sigma_level", "?"),
            "current_count": triage.get("surge_data", {}).get("current_count_in_window", "?"),
            "window_minutes": 60,
            "deployment_id": top_deploy.get("deployment_id", "unknown"),
            "service": top_deploy.get("service", "unknown"),
            "deployed_at": top_deploy.get("deployed_at", "unknown"),
            "rollback_available": top_deploy.get("rollback_available", False),
            "draft_template": f"Hi, we're aware of an issue with {ticket.get('category', 'this feature')} "
                              f"and our team is actively working on a fix. We'll update you within the hour.",
        })

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _update_ticket_es(self, ticket_id: str, updates: dict):
        """Update a ticket document in Elasticsearch."""
        url = f"{ELASTIC_URL}/support-tickets/_update_by_query"
        payload = {
            "query": {"term": {"ticket_id": ticket_id}},
            "script": {
                "source": " ".join(
                    [f"ctx._source['{k}'] = params.{k};" for k in updates.keys()]
                ),
                "params": {**updates, "updated_at": datetime.now(timezone.utc).isoformat()},
            }
        }
        try:
            requests.post(url, headers=ES_HEADERS, json=payload, timeout=10)
        except Exception as e:
            logger.warning(f"ES update failed for {ticket_id}: {e}")

    def _write_pipeline_trace(self, trace: dict):
        """Write the full pipeline trace to agent-traces index."""
        url = f"{ELASTIC_URL}/agent-traces/_doc"
        doc = {
            "trace_id": f"pipeline-{trace['ticket_id']}-{int(time.time())}",
            "ticket_id": trace["ticket_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent_name": "pipeline",
            "action": "full_pipeline",
            "decision": trace.get("final_decision"),
            "duration_ms": trace.get("total_duration_ms"),
            "steps": json.dumps(trace.get("steps", [])),
        }
        try:
            requests.post(url, headers=ES_HEADERS, json=doc, timeout=10)
        except Exception as e:
            logger.warning(f"Trace write failed: {e}")

    def _trigger_workflow(self, workflow_id: str, payload: dict):
        """Trigger an Elastic Workflow via its webhook endpoint."""
        workflow_map = {
            "crm_update":   "/supportiq/resolve",
            "ghost_alert":  "/supportiq/ghost-alert",
            "kb_draft":     "/supportiq/kb-draft",
            "feedback":     "/supportiq/feedback",
        }
        path = workflow_map.get(workflow_id)
        if not path:
            logger.warning(f"Unknown workflow: {workflow_id}")
            return

        url = f"{KIBANA_URL}/api/workflows/execute{path}"
        try:
            requests.post(url, headers=KIBANA_HEADERS, json=payload, timeout=15)
        except Exception as e:
            logger.warning(f"Workflow trigger failed ({workflow_id}): {e}")

    def _notify_slack(self, text: str, emoji: str = "robot_face"):
        """Post a notification to Slack via webhook."""
        if not SLACK_WEBHOOK_URL:
            return
        try:
            requests.post(SLACK_WEBHOOK_URL, json={"text": f":{emoji}: {text}"}, timeout=5)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# MAIN RUNNER — Webhook listener for incoming tickets
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import http.server
    import socketserver
    from urllib.parse import urlparse

    pipeline = SupportIQPipeline()

    print("=" * 60)
    print("🚀 SupportIQ A2A Pipeline — Starting")
    print("=" * 60)

    # Health check
    print("\nPinging all agents via A2A...")
    health = pipeline.client.ping_all_agents()
    for name, status in health.items():
        icon = "✅" if status["status"] == "online" else "❌"
        print(f"  {icon} {name}: {status['status']}")

    print("\n✅ SupportIQ pipeline ready. Listening for tickets on port 8080...\n")

    class TicketHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path == "/ticket":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                self.send_response(202)
                self.end_headers()
                self.wfile.write(b'{"status": "processing"}')
                # Process async
                import threading
                threading.Thread(target=pipeline.process_ticket, args=(body,)).start()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # Suppress default HTTP logs

    with socketserver.TCPServer(("", 8080), TicketHandler) as server:
        server.serve_forever()
