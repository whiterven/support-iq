"""
setup/04_workflows.py
Registers all 5 SupportIQ Elastic Workflows via the Kibana Workflows API.
Also registers the 9 custom ES|QL tools with each agent.

Workflows registered:
  1. supportiq_ticket_intake   — webhook intake trigger
  2. supportiq_crm_update      — CRM write + ticket close
  3. supportiq_ghost_alert     — pre-emptive surge alert to Slack
  4. supportiq_kb_draft        — KB article draft for human review
  5. supportiq_record_feedback — capture 👍/👎 from Slack

Usage:
  python setup/04_workflows.py
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

# Allow imports from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

KIBANA_URL = os.getenv("KIBANA_URL", "").rstrip("/")
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY", "")
ELASTIC_URL = os.getenv("ELASTIC_URL", "").rstrip("/")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/placeholder")
SLACK_SUPPORT_CHANNEL = os.getenv("SLACK_SUPPORT_CHANNEL", "#support-ops")
SLACK_ALERTS_CHANNEL = os.getenv("SLACK_ALERTS_CHANNEL", "#support-alerts")
SLACK_ENGINEERING_CHANNEL = os.getenv("SLACK_ENGINEERING_CHANNEL", "#engineering")
CRM_API_URL = os.getenv("CRM_API_URL", "https://crm.example.com/api/v1")
CRM_API_KEY = os.getenv("CRM_API_KEY", "placeholder")

KIBANA_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {KIBANA_API_KEY}",
    "kbn-xsrf": "true",
}

ES_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}

# ── Agent → Tool mapping ───────────────────────────────────────────────────
# These match the tool names used in 03_agents.py system prompts.
# Agent Builder resolves tool names to registered ES|QL / Workflow tools.

AGENT_TOOL_ASSIGNMENTS = {
    "supportiq_watcher": [
        "find_similar_tickets",
        "get_customer_profile",
    ],
    "supportiq_judge": [
        "score_ticket_priority",
        "detect_ticket_surge",
        "correlate_spike_to_deployment",
    ],
    "supportiq_solver": [
        "search_knowledge_base",
    ],
    "supportiq_critic": [
        "score_resolution_quality",
    ],
    "supportiq_analyst": [
        "weekly_performance_metrics",
        "kb_gap_detector",
        "detect_ticket_surge",
        "correlate_spike_to_deployment",
    ],
}

# ── Workflow Definitions ────────────────────────────────────────────────────

WORKFLOWS = [
    {
        "id": "supportiq_ticket_intake",
        "name": "SupportIQ: Ticket Intake",
        "description": "Receives new support tickets via webhook and writes them to Elasticsearch.",
        "trigger": {"type": "webhook", "path": "/supportiq/intake", "method": "POST"},
        "steps": [
            {
                "id": "index_ticket",
                "type": "elasticsearch",
                "action": "index",
                "index": "support-tickets",
                "document_template": {
                    "ticket_id": "{{ctx.body.ticket_id}}",
                    "created_at": "{{now()}}",
                    "updated_at": "{{now()}}",
                    "status": "open",
                    "title": "{{ctx.body.title}}",
                    "description": "{{ctx.body.description}}",
                    "customer_id": "{{ctx.body.customer_id}}",
                    "category": "{{ctx.body.category}}",
                    "resolution_attempts": 0,
                },
            },
            {
                "id": "slack_ack",
                "type": "http",
                "method": "POST",
                "url": SLACK_WEBHOOK_URL,
                "body": {
                    "channel": SLACK_SUPPORT_CHANNEL,
                    "text": ":ticket: *New ticket:* `{{ctx.body.ticket_id}}` — {{ctx.body.title}} | _SupportIQ analyzing..._",
                },
            },
        ],
    },
    {
        "id": "supportiq_crm_update",
        "name": "SupportIQ: CRM Update & Close",
        "description": "Updates CRM and closes ticket in Elasticsearch on resolution.",
        "trigger": {"type": "webhook", "path": "/supportiq/resolve", "method": "POST"},
        "steps": [
            {
                "id": "update_es",
                "type": "elasticsearch",
                "action": "update_by_query",
                "index": "support-tickets",
                "query": {"term": {"ticket_id": "{{ctx.body.ticket_id}}"}},
                "update": {
                    "status": "resolved",
                    "resolution_final": "{{ctx.body.resolution_text}}",
                    "resolved_by": "{{ctx.body.resolved_by}}",
                    "updated_at": "{{now()}}",
                },
            },
            {
                "id": "update_crm",
                "type": "http",
                "method": "PATCH",
                "url": f"{CRM_API_URL}/tickets/{{{{ctx.body.ticket_id}}}}",
                "headers": {"Authorization": f"Bearer {CRM_API_KEY}"},
                "body": {
                    "status": "resolved",
                    "resolution": "{{ctx.body.resolution_text}}",
                    "resolved_by_ai": "{{ctx.body.is_auto_resolved}}",
                    "confidence_score": "{{ctx.body.confidence}}",
                },
            },
            {
                "id": "slack_notify_resolved",
                "type": "http",
                "method": "POST",
                "url": SLACK_WEBHOOK_URL,
                "body": {
                    "channel": SLACK_SUPPORT_CHANNEL,
                    "text": ":white_check_mark: *Resolved:* `{{ctx.body.ticket_id}}` | Method: {{ctx.body.resolved_by}} | Confidence: {{ctx.body.confidence}}",
                },
            },
        ],
    },
    {
        "id": "supportiq_ghost_alert",
        "name": "SupportIQ: Ghost Ticket Surge Alert",
        "description": "Pre-emptive surge alert to support and engineering when deployment correlation found.",
        "trigger": {"type": "webhook", "path": "/supportiq/ghost-alert", "method": "POST"},
        "steps": [
            {
                "id": "alert_support",
                "type": "http",
                "method": "POST",
                "url": SLACK_WEBHOOK_URL,
                "body": {
                    "channel": SLACK_ALERTS_CHANNEL,
                    "text": (
                        ":rotating_light: *Ghost Ticket Alert* | Category: `{{ctx.body.category}}` | "
                        "{{ctx.body.current_count}} tickets in {{ctx.body.window_minutes}}min ({{ctx.body.sigma_level}}σ) | "
                        "Correlated with deployment `{{ctx.body.deployment_id}}` ({{ctx.body.service}})\n"
                        "*Draft template:* ```{{ctx.body.draft_template}}```"
                    ),
                },
            },
            {
                "id": "alert_engineering",
                "type": "http",
                "method": "POST",
                "url": SLACK_WEBHOOK_URL,
                "body": {
                    "channel": SLACK_ENGINEERING_CHANNEL,
                    "text": (
                        ":warning: *SupportIQ Deployment Correlation* | "
                        "Deployment `{{ctx.body.deployment_id}}` of `{{ctx.body.service}}` "
                        "may be causing a surge in `{{ctx.body.category}}` support tickets. "
                        "Rollback available: {{ctx.body.rollback_available}}"
                    ),
                },
            },
            {
                "id": "log_trace",
                "type": "elasticsearch",
                "action": "index",
                "index": "agent-traces",
                "document_template": {
                    "trace_id": "ghost-{{ctx.body.category}}-{{now()}}",
                    "timestamp": "{{now()}}",
                    "agent_name": "analyst",
                    "action": "ghost_ticket_alert",
                    "decision": "pre_emptive_alert",
                    "output_summary": "Surge in {{ctx.body.category}} correlated with {{ctx.body.deployment_id}}",
                },
            },
        ],
    },
    {
        "id": "supportiq_kb_draft",
        "name": "SupportIQ: KB Draft for Review",
        "description": "Posts auto-generated KB article drafts to Slack for 1-click approval.",
        "trigger": {"type": "webhook", "path": "/supportiq/kb-draft", "method": "POST"},
        "steps": [
            {
                "id": "index_draft",
                "type": "elasticsearch",
                "action": "index",
                "index": "knowledge-base",
                "document_template": {
                    "article_id": "DRAFT-{{ctx.body.category}}-{{now()}}",
                    "created_at": "{{now()}}",
                    "updated_at": "{{now()}}",
                    "category": "{{ctx.body.category}}",
                    "title": "{{ctx.body.title}}",
                    "content": "{{ctx.body.content}}",
                    "draft": True,
                },
            },
            {
                "id": "slack_review_request",
                "type": "http",
                "method": "POST",
                "url": SLACK_WEBHOOK_URL,
                "body": {
                    "channel": SLACK_SUPPORT_CHANNEL,
                    "text": (
                        ":pencil: *New KB Draft Ready for Review*\n"
                        "Category: `{{ctx.body.category}}` | Title: {{ctx.body.title}}\n"
                        "Based on {{ctx.body.ticket_count}} tickets with no KB coverage.\n"
                        "React :white_check_mark: to publish | :x: to discard"
                    ),
                },
            },
        ],
    },
    {
        "id": "supportiq_record_feedback",
        "name": "SupportIQ: Record Human Feedback",
        "description": "Captures Slack emoji reactions (👍/👎) and writes feedback to Elasticsearch for RLHF-lite.",
        "trigger": {"type": "webhook", "path": "/supportiq/feedback", "method": "POST"},
        "steps": [
            {
                "id": "index_feedback",
                "type": "elasticsearch",
                "action": "index",
                "index": "feedback",
                "document_template": {
                    "feedback_id": "FB-{{ctx.body.ticket_id}}-{{now()}}",
                    "ticket_id": "{{ctx.body.ticket_id}}",
                    "timestamp": "{{now()}}",
                    "score": "{{ctx.body.score}}",
                    "agent_id": "{{ctx.body.slack_user_id}}",
                    "channel": "slack",
                    "category": "{{ctx.body.category}}",
                },
            },
            {
                "id": "update_ticket_feedback",
                "type": "elasticsearch",
                "action": "update_by_query",
                "index": "support-tickets",
                "query": {"term": {"ticket_id": "{{ctx.body.ticket_id}}"}},
                "update": {
                    "feedback_score": "{{ctx.body.score}}",
                    "feedback_agent_id": "{{ctx.body.slack_user_id}}",
                },
            },
        ],
    },
]


def register_workflow(workflow: dict):
    """Register a single workflow via the Kibana Workflows API."""
    wf_id = workflow["id"]
    url = f"{KIBANA_URL}/api/workflows/{wf_id}"

    check = requests.get(url, headers=KIBANA_HEADERS)
    if check.status_code == 200:
        print(f"  ⚠️  Workflow '{wf_id}' already exists. Updating...")
        resp = requests.put(url, headers=KIBANA_HEADERS, json=workflow)
    else:
        resp = requests.post(f"{KIBANA_URL}/api/workflows", headers=KIBANA_HEADERS, json=workflow)

    if resp.status_code in (200, 201):
        print(f"  ✅  Workflow '{wf_id}' registered.")
    else:
        print(f"  ❌  Failed '{wf_id}': {resp.status_code} — {resp.text[:200]}")


def register_esql_tools():
    """
    Register the 9 custom ES|QL tools with Elastic Agent Builder.
    Tools are defined in tools/esql_tools.py and exposed here via the
    Kibana Agent Builder tools API.
    """
    from tools.esql_tools import ALL_TOOLS

    print(f"\n  Registering {len(ALL_TOOLS)} custom ES|QL tools...")
    for tool_def in ALL_TOOLS:
        tool_id = tool_def["name"]
        url = f"{KIBANA_URL}/api/agent_builder/tools/{tool_id}"

        payload = {
            "name": tool_def["name"],
            "description": tool_def["description"],
            "parameters": tool_def["parameters"],
            "type": "esql",          # custom ES|QL backed tool
            "elastic_url": ELASTIC_URL,
        }

        check = requests.get(url, headers=KIBANA_HEADERS)
        if check.status_code == 200:
            resp = requests.put(url, headers=KIBANA_HEADERS, json=payload)
        else:
            resp = requests.post(
                f"{KIBANA_URL}/api/agent_builder/tools",
                headers=KIBANA_HEADERS,
                json={"id": tool_id, **payload},
            )

        icon = "✅" if resp.status_code in (200, 201) else "⚠️ "
        print(f"    {icon} Tool: {tool_id}")


def assign_tools_to_agents():
    """
    Assign the correct subset of tools to each agent.
    Each agent only sees the tools it needs — keeps context windows clean.
    """
    print("\n  Assigning tools to agents...")
    for agent_id, tool_names in AGENT_TOOL_ASSIGNMENTS.items():
        url = f"{KIBANA_URL}/api/agent_builder/agents/{agent_id}/tools"
        resp = requests.put(url, headers=KIBANA_HEADERS, json={"tools": tool_names})
        icon = "✅" if resp.status_code in (200, 201, 204) else "⚠️ "
        print(f"    {icon} Agent '{agent_id}': {', '.join(tool_names)}")


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 4: Registering Elastic Workflows & Custom Tools")
    print("=" * 60)

    print(f"\n📋 Registering {len(WORKFLOWS)} Elastic Workflows...")
    for wf in WORKFLOWS:
        register_workflow(wf)

    print("\n🔧 Registering ES|QL Tools with Agent Builder...")
    try:
        register_esql_tools()
    except ImportError as e:
        print(f"  ⚠️  Could not import esql_tools: {e}")
        print("     Ensure PYTHONPATH includes the project root.")

    print("\n🔗 Assigning tools to agents...")
    assign_tools_to_agents()

    print("\n✅  Step 4 complete.")
    print("    Workflows : " + " | ".join(w["id"] for w in WORKFLOWS))
    print("    Tools     : 9 ES|QL tools registered")
    print("    Agents    : Tools scoped per agent")
