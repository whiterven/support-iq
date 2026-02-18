"""
tools/workflow_tools.py
Elastic Workflow definitions for SupportIQ.
These are registered via the Kibana Workflows API.

Workflows handle the rules-based automation that agents trigger:
  - CRM updates
  - Slack notifications
  - KB article draft creation
  - Ghost Ticket surge alerts
  - Human feedback capture
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

KIBANA_URL = os.getenv("KIBANA_URL")
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SUPPORT_CHANNEL = os.getenv("SLACK_SUPPORT_CHANNEL", "#support-ops")
SLACK_ALERTS_CHANNEL = os.getenv("SLACK_ALERTS_CHANNEL", "#support-alerts")
SLACK_ENGINEERING_CHANNEL = os.getenv("SLACK_ENGINEERING_CHANNEL", "#engineering")
CRM_API_URL = os.getenv("CRM_API_URL")
CRM_API_KEY = os.getenv("CRM_API_KEY")
ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")

KIBANA_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {KIBANA_API_KEY}",
    "kbn-xsrf": "true",
}


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW 1: ticket_intake
# Triggered: Externally (Slack webhook, email, API)
# Action: Ingest ticket into Elasticsearch and trigger the Watcher agent
# ─────────────────────────────────────────────────────────────────────────────

WORKFLOW_TICKET_INTAKE = {
    "id": "supportiq_ticket_intake",
    "name": "SupportIQ: Ticket Intake",
    "description": "Receives new support tickets from external channels and writes them to Elasticsearch. Triggers the Watcher agent pipeline.",
    "trigger": {
        "type": "webhook",
        "config": {
            "method": "POST",
            "path": "/supportiq/intake",
        }
    },
    "steps": [
        {
            "id": "generate_ticket_id",
            "type": "script",
            "config": {
                "language": "painless",
                "source": """
                    def ticketId = 'TKT-' + System.currentTimeMillis();
                    def now = ZonedDateTime.now(ZoneOffset.UTC).toString();
                    ctx.ticket_id = ticketId;
                    ctx.created_at = now;
                    ctx.status = 'open';
                    ctx.resolution_attempts = 0;
                """
            }
        },
        {
            "id": "index_ticket",
            "type": "elasticsearch_index",
            "config": {
                "index": "support-tickets",
                "document": {
                    "ticket_id": "{{ticket_id}}",
                    "created_at": "{{created_at}}",
                    "updated_at": "{{created_at}}",
                    "status": "open",
                    "title": "{{ctx.body.title}}",
                    "description": "{{ctx.body.description}}",
                    "customer_id": "{{ctx.body.customer_id}}",
                    "category": "{{ctx.body.category}}",
                    "resolution_attempts": 0,
                }
            }
        },
        {
            "id": "notify_slack_received",
            "type": "http_request",
            "config": {
                "url": SLACK_WEBHOOK_URL,
                "method": "POST",
                "body": {
                    "text": "🎫 *New ticket received:* `{{ticket_id}}`\n*Title:* {{ctx.body.title}}\n*Customer:* {{ctx.body.customer_id}}\n_SupportIQ is analyzing..._"
                }
            }
        }
    ]
}


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW 2: crm_update
# Triggered: By Solver agent (auto-resolve decision) or human approval
# Action: Update CRM with resolution + close ticket in Elasticsearch
# ─────────────────────────────────────────────────────────────────────────────

WORKFLOW_CRM_UPDATE = {
    "id": "supportiq_crm_update",
    "name": "SupportIQ: CRM Update & Ticket Close",
    "description": "Updates the CRM with the resolution text and closes the ticket in Elasticsearch. Triggered by auto-resolve or human approval.",
    "trigger": {
        "type": "webhook",
        "config": {
            "method": "POST",
            "path": "/supportiq/resolve",
        }
    },
    "steps": [
        {
            "id": "update_elasticsearch",
            "type": "elasticsearch_update",
            "config": {
                "index": "support-tickets",
                "query": {"term": {"ticket_id": "{{ctx.body.ticket_id}}"}},
                "update": {
                    "status": "resolved",
                    "resolution_final": "{{ctx.body.resolution_text}}",
                    "resolved_by": "{{ctx.body.resolved_by}}",
                    "updated_at": "{{now}}",
                }
            }
        },
        {
            "id": "update_crm",
            "type": "http_request",
            "config": {
                "url": f"{CRM_API_URL}/tickets/{{{{ctx.body.ticket_id}}}}",
                "method": "PATCH",
                "headers": {"Authorization": f"Bearer {CRM_API_KEY}"},
                "body": {
                    "status": "resolved",
                    "resolution": "{{ctx.body.resolution_text}}",
                    "resolved_by_ai": "{{ctx.body.is_auto_resolved}}",
                    "confidence_score": "{{ctx.body.confidence}}",
                }
            }
        },
        {
            "id": "notify_slack_resolved",
            "type": "http_request",
            "config": {
                "url": SLACK_WEBHOOK_URL,
                "method": "POST",
                "body": {
                    "text": "✅ *Ticket resolved:* `{{ctx.body.ticket_id}}`\n*Method:* {{ctx.body.resolved_by}}\n*Confidence:* {{ctx.body.confidence}}\n*Response:* {{ctx.body.resolution_text}}"
                }
            }
        }
    ]
}


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW 3: ghost_alert
# Triggered: By Judge/Analyst agents when surge + deployment correlation found
# Action: Pre-emptive alert to support team AND engineering
# ─────────────────────────────────────────────────────────────────────────────

WORKFLOW_GHOST_ALERT = {
    "id": "supportiq_ghost_alert",
    "name": "SupportIQ: Ghost Ticket Surge Alert",
    "description": "Pre-emptive alert when a ticket surge is detected and correlated with a deployment. Sends alerts to support team and engineering channel with a draft response template.",
    "trigger": {
        "type": "webhook",
        "config": {
            "method": "POST",
            "path": "/supportiq/ghost-alert",
        }
    },
    "steps": [
        {
            "id": "alert_support_team",
            "type": "http_request",
            "config": {
                "url": SLACK_WEBHOOK_URL,
                "method": "POST",
                "body": {
                    "channel": SLACK_ALERTS_CHANNEL,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "🚨 Ghost Ticket Alert — Surge Detected"}
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*Category:* `{{ctx.body.category}}`\n*Current rate:* {{ctx.body.current_hourly_rate}} tickets/hr ({{ctx.body.sigma_level}}σ above baseline)\n*Likely cause:* Deployment `{{ctx.body.deployment_id}}` ({{ctx.body.service}}) at {{ctx.body.deployed_at}}"
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*📋 Draft Response Template (1-click copy):*\n```{{ctx.body.draft_template}}```"
                            }
                        },
                        {
                            "type": "context",
                            "elements": [{"type": "mrkdwn", "text": "_SupportIQ detected this surge before it hit your queue. No action needed — I'm monitoring._"}]
                        }
                    ]
                }
            }
        },
        {
            "id": "alert_engineering",
            "type": "http_request",
            "config": {
                "url": SLACK_WEBHOOK_URL,
                "method": "POST",
                "body": {
                    "channel": SLACK_ENGINEERING_CHANNEL,
                    "text": "⚠️ *SupportIQ Deployment Correlation Alert*\nDeployment `{{ctx.body.deployment_id}}` of `{{ctx.body.service}}` appears to be causing a support surge in the `{{ctx.body.category}}` category.\n*{{ctx.body.current_count}} tickets in the last {{ctx.body.window_minutes}} minutes* ({{ctx.body.sigma_level}}σ above baseline).\nPlease review and consider rollback if needed. Rollback available: {{ctx.body.rollback_available}}"
                }
            }
        },
        {
            "id": "log_ghost_ticket",
            "type": "elasticsearch_index",
            "config": {
                "index": "agent-traces",
                "document": {
                    "trace_id": "ghost-{{ctx.body.category}}-{{now}}",
                    "timestamp": "{{now}}",
                    "agent_name": "analyst",
                    "action": "ghost_ticket_alert",
                    "output_summary": "Surge detected in {{ctx.body.category}}, correlated with deployment {{ctx.body.deployment_id}}",
                    "decision": "pre_emptive_alert",
                }
            }
        }
    ]
}


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW 4: kb_draft_for_review
# Triggered: By Analyst agent (KB gap detection)
# Action: Post draft KB article to Slack with 1-click approve button
# ─────────────────────────────────────────────────────────────────────────────

WORKFLOW_KB_DRAFT = {
    "id": "supportiq_kb_draft",
    "name": "SupportIQ: KB Draft for Review",
    "description": "Posts auto-generated KB article drafts to Slack for 1-click human approval. On approval, publishes the article to the knowledge base.",
    "trigger": {
        "type": "webhook",
        "config": {
            "method": "POST",
            "path": "/supportiq/kb-draft",
        }
    },
    "steps": [
        {
            "id": "index_draft",
            "type": "elasticsearch_index",
            "config": {
                "index": "knowledge-base",
                "document": {
                    "article_id": "DRAFT-{{ctx.body.category}}-{{now}}",
                    "created_at": "{{now}}",
                    "updated_at": "{{now}}",
                    "category": "{{ctx.body.category}}",
                    "title": "{{ctx.body.title}}",
                    "content": "{{ctx.body.content}}",
                    "draft": True,
                    "draft_source_ticket_ids": "{{ctx.body.source_ticket_ids}}",
                }
            }
        },
        {
            "id": "post_to_slack",
            "type": "http_request",
            "config": {
                "url": SLACK_WEBHOOK_URL,
                "method": "POST",
                "body": {
                    "channel": SLACK_SUPPORT_CHANNEL,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {"type": "plain_text", "text": "📝 New KB Article Draft — Review Needed"}
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*Category:* `{{ctx.body.category}}`\n*Title:* {{ctx.body.title}}\n*Based on:* {{ctx.body.ticket_count}} recent tickets with no KB coverage\n\n{{ctx.body.content_preview}}"
                            }
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "✅ Approve & Publish"},
                                    "style": "primary",
                                    "value": "{{ctx.body.article_id}}",
                                    "action_id": "kb_approve",
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "✏️ Edit First"},
                                    "value": "{{ctx.body.article_id}}",
                                    "action_id": "kb_edit",
                                },
                            ]
                        }
                    ]
                }
            }
        }
    ]
}


# ─────────────────────────────────────────────────────────────────────────────
# WORKFLOW 5: record_feedback
# Triggered: By Slack reaction events (👍/👎 on resolution messages)
# Action: Write feedback to Elasticsearch for RLHF-lite training signal
# ─────────────────────────────────────────────────────────────────────────────

WORKFLOW_RECORD_FEEDBACK = {
    "id": "supportiq_record_feedback",
    "name": "SupportIQ: Record Human Feedback",
    "description": "Captures 👍/👎 reactions on resolution messages in Slack and writes them to Elasticsearch. Powers the weekly RLHF-lite quality improvement cycle.",
    "trigger": {
        "type": "webhook",
        "config": {
            "method": "POST",
            "path": "/supportiq/feedback",
        }
    },
    "steps": [
        {
            "id": "map_reaction_to_score",
            "type": "script",
            "config": {
                "language": "painless",
                "source": """
                    def reaction = ctx.body.reaction;
                    if (reaction == '+1' || reaction == 'white_check_mark' || reaction == 'thumbsup') {
                        ctx.score = 1;
                    } else if (reaction == '-1' || reaction == 'thumbsdown' || reaction == 'x') {
                        ctx.score = -1;
                    } else {
                        ctx.score = 0;
                    }
                """
            }
        },
        {
            "id": "index_feedback",
            "type": "elasticsearch_index",
            "config": {
                "index": "feedback",
                "document": {
                    "feedback_id": "FB-{{ctx.body.ticket_id}}-{{now}}",
                    "ticket_id": "{{ctx.body.ticket_id}}",
                    "timestamp": "{{now}}",
                    "score": "{{score}}",
                    "agent_id": "{{ctx.body.slack_user_id}}",
                    "channel": "slack",
                    "category": "{{ctx.body.category}}",
                }
            }
        },
        {
            "id": "update_ticket_feedback",
            "type": "elasticsearch_update",
            "config": {
                "index": "support-tickets",
                "query": {"term": {"ticket_id": "{{ctx.body.ticket_id}}"}},
                "update": {
                    "feedback_score": "{{score}}",
                    "feedback_agent_id": "{{ctx.body.slack_user_id}}",
                }
            }
        }
    ]
}


ALL_WORKFLOWS = [
    WORKFLOW_TICKET_INTAKE,
    WORKFLOW_CRM_UPDATE,
    WORKFLOW_GHOST_ALERT,
    WORKFLOW_KB_DRAFT,
    WORKFLOW_RECORD_FEEDBACK,
]
