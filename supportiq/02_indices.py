"""
setup/02_indices.py
Creates all 6 Elasticsearch indices with correct mappings.
Uses semantic_text fields for automatic vector generation via our Gemini embedding endpoint.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
EMBEDDING_ENDPOINT = "supportiq-embeddings"

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}

INDICES = {

    # ── 1. Support Tickets ─────────────────────────────────────────────────────
    "support-tickets": {
        "mappings": {
            "properties": {
                "ticket_id":        {"type": "keyword"},
                "created_at":       {"type": "date"},
                "updated_at":       {"type": "date"},
                "status":           {"type": "keyword"},   # open | triaged | resolved | escalated | closed
                "priority_score":   {"type": "float"},
                "priority_label":   {"type": "keyword"},   # critical | high | medium | low
                "category":         {"type": "keyword"},   # payment | auth | checkout | api | billing ...
                "customer_id":      {"type": "keyword"},
                "customer_tier":    {"type": "keyword"},   # enterprise | pro | free
                "title":            {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "description":      {"type": "text"},
                # semantic_text fields — automatically vectorized by our embedding endpoint
                "title_semantic":           {"type": "semantic_text", "inference_id": EMBEDDING_ENDPOINT},
                "description_semantic":     {"type": "semantic_text", "inference_id": EMBEDDING_ENDPOINT},
                # Agent enrichment fields
                "similar_tickets":          {"type": "object", "dynamic": True},
                "triage_reasoning":         {"type": "text"},
                "resolution_draft":         {"type": "text"},
                "resolution_final":         {"type": "text"},
                "resolution_confidence":    {"type": "float"},
                "critic_score":             {"type": "float"},
                "critic_feedback":          {"type": "text"},
                "resolution_attempts":      {"type": "integer"},
                "resolved_by":              {"type": "keyword"},   # agent | human
                "assigned_agent_id":        {"type": "keyword"},
                "sla_deadline":             {"type": "date"},
                "sla_breached":             {"type": "boolean"},
                "deployment_correlation":   {"type": "object", "dynamic": True},
                "feedback_score":           {"type": "integer"},   # 1 (thumbs up) or -1 (thumbs down)
                "feedback_agent_id":        {"type": "keyword"},
            }
        }
    },

    # ── 2. Knowledge Base ──────────────────────────────────────────────────────
    "knowledge-base": {
        "mappings": {
            "properties": {
                "article_id":       {"type": "keyword"},
                "created_at":       {"type": "date"},
                "updated_at":       {"type": "date"},
                "category":         {"type": "keyword"},
                "title":            {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "content":          {"type": "text"},
                "title_semantic":   {"type": "semantic_text", "inference_id": EMBEDDING_ENDPOINT},
                "content_semantic": {"type": "semantic_text", "inference_id": EMBEDDING_ENDPOINT},
                "tags":             {"type": "keyword"},
                "version":          {"type": "keyword"},
                "usage_count":      {"type": "integer"},
                "avg_feedback":     {"type": "float"},
                "negative_feedback_rate": {"type": "float"},
                "draft":            {"type": "boolean"},
                "draft_source_ticket_ids": {"type": "keyword"},
            }
        }
    },

    # ── 3. Deployments ─────────────────────────────────────────────────────────
    "deployments": {
        "mappings": {
            "properties": {
                "deployment_id":    {"type": "keyword"},
                "deployed_at":      {"type": "date"},
                "service":          {"type": "keyword"},   # checkout-service | auth-service | ...
                "version":          {"type": "keyword"},
                "environment":      {"type": "keyword"},   # production | staging
                "deployed_by":      {"type": "keyword"},
                "team":             {"type": "keyword"},
                "commit_sha":       {"type": "keyword"},
                "pr_url":           {"type": "keyword"},
                "description":      {"type": "text"},
                "description_semantic": {"type": "semantic_text", "inference_id": EMBEDDING_ENDPOINT},
                "rollback_available": {"type": "boolean"},
                "correlated_ticket_surge": {"type": "boolean"},
            }
        }
    },

    # ── 4. Agent Traces (full audit trail) ────────────────────────────────────
    "agent-traces": {
        "mappings": {
            "properties": {
                "trace_id":         {"type": "keyword"},
                "ticket_id":        {"type": "keyword"},
                "timestamp":        {"type": "date"},
                "agent_name":       {"type": "keyword"},   # watcher | judge | solver | critic | analyst
                "action":           {"type": "keyword"},
                "input_summary":    {"type": "text"},
                "output_summary":   {"type": "text"},
                "tool_calls":       {"type": "object", "dynamic": True},
                "confidence":       {"type": "float"},
                "duration_ms":      {"type": "integer"},
                "token_count":      {"type": "integer"},
                "decision":         {"type": "keyword"},   # auto_resolve | draft | escalate | reject | approve
                "reasoning":        {"type": "text"},
            }
        }
    },

    # ── 5. Customer Profiles ──────────────────────────────────────────────────
    "customer-profiles": {
        "mappings": {
            "properties": {
                "customer_id":      {"type": "keyword"},
                "company_name":     {"type": "keyword"},
                "tier":             {"type": "keyword"},   # enterprise | pro | free
                "contract_value":   {"type": "float"},
                "sla_hours":        {"type": "integer"},   # 4 | 8 | 24 | 72
                "open_tickets":     {"type": "integer"},
                "lifetime_tickets": {"type": "integer"},
                "avg_csat":         {"type": "float"},
                "last_ticket_at":   {"type": "date"},
                "account_manager":  {"type": "keyword"},
                "health_score":     {"type": "float"},
            }
        }
    },

    # ── 6. Feedback ───────────────────────────────────────────────────────────
    "feedback": {
        "mappings": {
            "properties": {
                "feedback_id":      {"type": "keyword"},
                "ticket_id":        {"type": "keyword"},
                "article_id":       {"type": "keyword"},
                "timestamp":        {"type": "date"},
                "score":            {"type": "integer"},   # 1 or -1
                "agent_id":         {"type": "keyword"},   # support agent who gave feedback
                "resolution_text":  {"type": "text"},
                "category":         {"type": "keyword"},
                "channel":          {"type": "keyword"},   # slack | kibana | api
            }
        }
    },
}


def create_index(name: str, config: dict):
    url = f"{ELASTIC_URL}/{name}"

    # Check if exists
    check = requests.head(url, headers=HEADERS)
    if check.status_code == 200:
        print(f"  ⚠️  Index '{name}' already exists. Skipping.")
        return

    resp = requests.put(url, headers=HEADERS, json=config)
    if resp.status_code in (200, 201):
        print(f"  ✅ Created index '{name}'")
    else:
        print(f"  ❌ Failed to create '{name}': {resp.status_code} — {resp.text}")
        raise RuntimeError(f"Index creation failed: {name}")


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 2: Creating Elasticsearch Indices")
    print("=" * 60)

    for index_name, index_config in INDICES.items():
        create_index(index_name, index_config)

    print(f"\n✅ Step 2 complete. {len(INDICES)} indices created.")
    print("   Indices: " + " | ".join(INDICES.keys()))
