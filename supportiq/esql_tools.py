"""
tools/esql_tools.py
All 9 custom ES|QL tools for SupportIQ.
These are the analytical backbone of the system — each one is a precisely crafted
ES|QL query that gives agents real, data-driven intelligence.

These are registered in Elastic Agent Builder as custom tools.
"""

import os
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
EMBEDDING_ENDPOINT = "supportiq-embeddings"
KIBANA_URL = os.getenv("KIBANA_URL")
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY")

ES_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}
KIBANA_HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {KIBANA_API_KEY}",
    "kbn-xsrf": "true",
}


def run_esql(query: str, params: dict = None) -> dict:
    """Execute an ES|QL query and return results."""
    url = f"{ELASTIC_URL}/_query"
    payload = {"query": query}
    if params:
        payload["params"] = params
    resp = requests.post(url, headers=ES_HEADERS, json=payload)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: find_similar_tickets
# Used by: Watcher Agent
# Finds semantically similar resolved tickets using hybrid search
# ─────────────────────────────────────────────────────────────────────────────

TOOL_FIND_SIMILAR_TICKETS = {
    "name": "find_similar_tickets",
    "description": "Find the top 5 most semantically similar resolved tickets to the current ticket. Returns past tickets with their resolution summaries.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "The ticket title"},
            "description": {"type": "string", "description": "The ticket description"},
            "category": {"type": "string", "description": "The suspected ticket category"},
        },
        "required": ["title", "description"],
    },
}


def find_similar_tickets(title: str, description: str, category: str = None, top_k: int = 5) -> dict:
    """
    Hybrid semantic search over resolved tickets.
    Uses semantic_text field for vector similarity, boosted by keyword match.
    """
    category_filter = f'AND category == "{category}"' if category else ""

    query = f"""
    FROM support-tickets
    | WHERE status == "resolved" OR status == "closed"
    | WHERE resolution_confidence >= 0.7
    {category_filter}
    | SORT _score DESC
    | LIMIT {top_k}
    | KEEP ticket_id, title, category, resolution_final, resolution_confidence,
            customer_tier, created_at, feedback_score
    """

    # For full semantic search, use the Search API with knn + BM25 hybrid
    url = f"{ELASTIC_URL}/support-tickets/_search"
    payload = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [
                    {"semantic": {"field": "description_semantic", "query": description}},
                ],
                "filter": [
                    {"terms": {"status": ["resolved", "closed"]}},
                    {"range": {"resolution_confidence": {"gte": 0.7}}},
                ],
            }
        },
        "knn": {
            "field": "title_semantic",
            "query_vector_builder": {
                "text_embedding": {
                    "model_id": EMBEDDING_ENDPOINT,
                    "model_text": title,
                }
            },
            "k": 10,
            "num_candidates": 50,
            "boost": 0.3,
        },
        "_source": ["ticket_id", "title", "category", "resolution_final",
                    "resolution_confidence", "customer_tier", "feedback_score"],
    }
    resp = requests.post(url, headers=ES_HEADERS, json=payload)
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", [])
    return {
        "similar_tickets": [
            {
                "ticket_id": h["_source"].get("ticket_id"),
                "title": h["_source"].get("title"),
                "category": h["_source"].get("category"),
                "resolution_summary": (h["_source"].get("resolution_final", "") or "")[:300],
                "similarity_score": round(h["_score"], 3),
                "resolution_confidence": h["_source"].get("resolution_confidence"),
                "feedback_score": h["_source"].get("feedback_score"),
            }
            for h in hits
        ],
        "count": len(hits),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: get_customer_profile
# Used by: Watcher Agent
# ─────────────────────────────────────────────────────────────────────────────

TOOL_GET_CUSTOMER_PROFILE = {
    "name": "get_customer_profile",
    "description": "Retrieve customer tier, SLA hours, contract value, CSAT score, and open ticket count from the customer profile index.",
    "parameters": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "The customer ID from the ticket"},
        },
        "required": ["customer_id"],
    },
}


def get_customer_profile(customer_id: str) -> dict:
    query = f"""
    FROM customer-profiles
    | WHERE customer_id == "{customer_id}"
    | LIMIT 1
    | KEEP customer_id, company_name, tier, contract_value, sla_hours,
            open_tickets, avg_csat, health_score, account_manager
    """
    result = run_esql(query)
    rows = result.get("values", [])
    columns = [c["name"] for c in result.get("columns", [])]

    if not rows:
        # Return default free-tier profile if customer not found
        return {
            "customer_id": customer_id,
            "tier": "free",
            "sla_hours": 72,
            "contract_value": 0,
            "open_tickets": 0,
            "avg_csat": 3.0,
            "found": False,
        }

    profile = dict(zip(columns, rows[0]))
    profile["found"] = True
    return profile


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: score_ticket_priority
# Used by: Judge Agent
# The core triage scoring query
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SCORE_TICKET_PRIORITY = {
    "name": "score_ticket_priority",
    "description": "Calculate a priority score for a ticket using ES|QL analytics. Considers customer tier, SLA breach risk, ticket recurrence frequency, and time factors.",
    "parameters": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string"},
            "customer_id": {"type": "string"},
            "category": {"type": "string"},
            "created_at": {"type": "string", "description": "ISO timestamp"},
            "sla_hours": {"type": "integer"},
        },
        "required": ["ticket_id", "category", "sla_hours"],
    },
}


def score_ticket_priority(ticket_id: str, category: str, sla_hours: int,
                           customer_id: str = None, created_at: str = None) -> dict:
    """
    Multi-dimensional priority scoring:
    1. Recurrence frequency: how often has this category appeared in last 30 days
    2. SLA breach risk: based on current age vs SLA deadline
    3. Backlog pressure: how many tickets in same category are currently open
    """
    now = datetime.now(timezone.utc).isoformat()

    recurrence_query = f"""
    FROM support-tickets
    | WHERE category == "{category}"
    | WHERE created_at >= NOW() - 30 days
    | STATS recurrence_count = COUNT(*), avg_resolve_hours = AVG(TIMESTAMP_DIFF(HOUR, created_at, updated_at))
    """

    backlog_query = f"""
    FROM support-tickets
    | WHERE category == "{category}"
    | WHERE status IN ("open", "triaged")
    | STATS open_count = COUNT(*), oldest_ticket_age_hours = MAX(TIMESTAMP_DIFF(HOUR, created_at, NOW()))
    """

    recurrence_result = run_esql(recurrence_query)
    backlog_result = run_esql(backlog_query)

    recurrence_values = recurrence_result.get("values", [[0, 0]])[0]
    backlog_values = backlog_result.get("values", [[0, 0]])[0]

    recurrence_count = recurrence_values[0] or 0
    avg_resolve_hours = recurrence_values[1] or sla_hours
    open_count = backlog_values[0] or 0
    oldest_age_hours = backlog_values[1] or 0

    # SLA breach risk: if avg resolution time > SLA, risk is high
    sla_breach_risk = min(1.0, avg_resolve_hours / max(sla_hours, 1))

    # Recurrence score: normalize to 0-1 (>20 tickets/month = max)
    recurrence_score = min(1.0, recurrence_count / 20)

    return {
        "ticket_id": ticket_id,
        "recurrence_count_30d": recurrence_count,
        "recurrence_score": round(recurrence_score, 3),
        "avg_resolve_hours": round(avg_resolve_hours or 0, 1),
        "sla_breach_risk": round(sla_breach_risk, 3),
        "open_tickets_same_category": open_count,
        "oldest_open_age_hours": oldest_age_hours,
        "backlog_pressure": "HIGH" if open_count > 10 else "MEDIUM" if open_count > 3 else "LOW",
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: detect_ticket_surge
# Used by: Judge Agent, Analyst Agent
# 2-sigma anomaly detection on ticket volume
# ─────────────────────────────────────────────────────────────────────────────

TOOL_DETECT_TICKET_SURGE = {
    "name": "detect_ticket_surge",
    "description": "Detect if a ticket category is experiencing an abnormal surge using statistical anomaly detection (2-sigma). Compares current hourly rate against the 30-day baseline.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Ticket category to check"},
            "window_minutes": {"type": "integer", "description": "Detection window in minutes", "default": 60},
        },
        "required": ["category"],
    },
}


def detect_ticket_surge(category: str, window_minutes: int = 60) -> dict:
    """
    Compares current rate vs 30-day hourly baseline.
    If current_rate > baseline_mean + (2 * baseline_stddev) → surge detected.
    """

    # Get baseline stats from the last 30 days (hourly bucketing)
    baseline_query = f"""
    FROM support-tickets
    | WHERE category == "{category}"
    | WHERE created_at >= NOW() - 30 days
    | EVAL hour_bucket = DATE_TRUNC(1 hour, created_at)
    | STATS hourly_count = COUNT(*) BY hour_bucket
    | STATS baseline_mean = AVG(hourly_count), baseline_stddev = STDDEV_SAMPLE(hourly_count),
            baseline_p95 = PERCENTILE(hourly_count, 95)
    """

    # Get current window count
    current_query = f"""
    FROM support-tickets
    | WHERE category == "{category}"
    | WHERE created_at >= NOW() - {window_minutes} minutes
    | STATS current_count = COUNT(*)
    """

    baseline_result = run_esql(baseline_query)
    current_result = run_esql(current_query)

    baseline_cols = [c["name"] for c in baseline_result.get("columns", [])]
    baseline_vals = baseline_result.get("values", [[0, 1, 0]])[0]
    baseline = dict(zip(baseline_cols, baseline_vals))

    current_count = current_result.get("values", [[0]])[0][0] or 0

    mean = baseline.get("baseline_mean", 1) or 1
    stddev = baseline.get("baseline_stddev", 0.5) or 0.5

    # Normalize current count to hourly rate
    current_hourly_rate = current_count * (60 / window_minutes)

    threshold_2sigma = mean + (2 * stddev)
    surge_detected = current_hourly_rate > threshold_2sigma

    sigma_level = (current_hourly_rate - mean) / stddev if stddev > 0 else 0

    return {
        "category": category,
        "surge_detected": surge_detected,
        "current_count_in_window": current_count,
        "current_hourly_rate": round(current_hourly_rate, 1),
        "baseline_mean_hourly": round(mean, 1),
        "baseline_stddev": round(stddev, 2),
        "threshold_2sigma": round(threshold_2sigma, 1),
        "sigma_level": round(sigma_level, 2),
        "severity": "CRITICAL" if sigma_level > 4 else "HIGH" if sigma_level > 2.5 else "MEDIUM" if surge_detected else "NORMAL",
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5: correlate_spike_to_deployment
# Used by: Judge Agent, Analyst Agent
# THE NUCLEAR FEATURE — links ticket surges to deployments
# ─────────────────────────────────────────────────────────────────────────────

TOOL_CORRELATE_SPIKE_TO_DEPLOYMENT = {
    "name": "correlate_spike_to_deployment",
    "description": "Check if a ticket surge correlates with a recent deployment event. Searches the deployments index for deploys that happened within 90 minutes before the surge started. This is the Ghost Ticket pre-emption feature.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string"},
            "surge_start_timestamp": {"type": "string", "description": "ISO timestamp when surge was detected"},
            "correlation_window_minutes": {"type": "integer", "default": 90},
        },
        "required": ["category"],
    },
}


def correlate_spike_to_deployment(category: str, surge_start_timestamp: str = None,
                                   correlation_window_minutes: int = 90) -> dict:
    if not surge_start_timestamp:
        surge_start_timestamp = datetime.now(timezone.utc).isoformat()

    query = f"""
    FROM deployments
    | WHERE deployed_at >= NOW() - {correlation_window_minutes} minutes
    | WHERE environment == "production"
    | SORT deployed_at DESC
    | LIMIT 5
    | KEEP deployment_id, service, version, deployed_at, deployed_by, team, description, rollback_available
    """

    result = run_esql(query)
    cols = [c["name"] for c in result.get("columns", [])]
    rows = result.get("values", [])

    deployments = [dict(zip(cols, row)) for row in rows]

    # Heuristic: check if any deployment's service name relates to the ticket category
    related = []
    for d in deployments:
        service = (d.get("service") or "").lower()
        desc = (d.get("description") or "").lower()
        cat_lower = category.lower()
        if cat_lower in service or cat_lower in desc:
            d["relevance"] = "HIGH"
            related.append(d)
        else:
            d["relevance"] = "LOW"

    return {
        "category": category,
        "correlation_found": len(related) > 0,
        "related_deployments": related,
        "all_recent_deployments": deployments,
        "correlation_window_minutes": correlation_window_minutes,
        "ghost_ticket_recommended": len(related) > 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 6: search_knowledge_base
# Used by: Solver Agent
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SEARCH_KNOWLEDGE_BASE = {
    "name": "search_knowledge_base",
    "description": "Search the knowledge base for articles relevant to the support ticket. Returns top articles with their full content for the Solver to use in generating a resolution.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "top_k": {"type": "integer", "default": 3},
        },
        "required": ["title", "description"],
    },
}


def search_knowledge_base(title: str, description: str, category: str = None, top_k: int = 3) -> dict:
    """Hybrid semantic + BM25 search over the knowledge base."""
    url = f"{ELASTIC_URL}/knowledge-base/_search"
    category_filter = [{"term": {"category": category}}] if category else []

    payload = {
        "size": top_k,
        "query": {
            "bool": {
                "must": [
                    {"semantic": {"field": "content_semantic", "query": description}},
                ],
                "filter": [
                    {"term": {"draft": False}},
                ] + category_filter,
                "should": [
                    {"match": {"title": {"query": title, "boost": 2}}},
                    {"match": {"content": {"query": description}}},
                ],
            }
        },
        "_source": ["article_id", "title", "content", "category", "tags",
                    "avg_feedback", "usage_count", "updated_at"],
    }

    resp = requests.post(url, headers=ES_HEADERS, json=payload)
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", [])

    return {
        "articles": [
            {
                "article_id": h["_source"].get("article_id"),
                "title": h["_source"].get("title"),
                "content": h["_source"].get("content", ""),
                "category": h["_source"].get("category"),
                "relevance_score": round(h["_score"], 3),
                "avg_feedback": h["_source"].get("avg_feedback"),
                "usage_count": h["_source"].get("usage_count"),
            }
            for h in hits
        ],
        "count": len(hits),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 7: score_resolution_quality
# Used by: Critic Agent
# The self-correcting loop engine
# ─────────────────────────────────────────────────────────────────────────────

TOOL_SCORE_RESOLUTION_QUALITY = {
    "name": "score_resolution_quality",
    "description": "Score a resolution draft by comparing it semantically against the top-rated historical resolutions for the same ticket category. Returns a quality score from 0.0 to 1.0.",
    "parameters": {
        "type": "object",
        "properties": {
            "resolution_draft": {"type": "string", "description": "The resolution text to evaluate"},
            "category": {"type": "string"},
        },
        "required": ["resolution_draft", "category"],
    },
}


def score_resolution_quality(resolution_draft: str, category: str) -> dict:
    """
    Retrieves the top 5 highest-rated resolutions for this category
    and computes semantic similarity to identify quality.
    """
    url = f"{ELASTIC_URL}/support-tickets/_search"

    payload = {
        "size": 5,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"category": category}},
                    {"term": {"status": "resolved"}},
                    {"range": {"feedback_score": {"gte": 1}}},
                    {"range": {"resolution_confidence": {"gte": 0.85}}},
                ],
                "must": [
                    {"semantic": {"field": "description_semantic", "query": resolution_draft}},
                ],
            }
        },
        "_source": ["resolution_final", "resolution_confidence", "feedback_score"],
    }

    resp = requests.post(url, headers=ES_HEADERS, json=payload)
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", [])

    if not hits:
        return {
            "semantic_similarity_score": 0.5,
            "comparison_count": 0,
            "note": "No high-quality reference resolutions found for this category. Score is neutral.",
        }

    # Normalize the top hit score as a proxy for semantic similarity
    max_possible = hits[0]["_score"] if hits else 1
    avg_score = sum(h["_score"] for h in hits) / len(hits)
    normalized = min(1.0, avg_score / max(max_possible, 0.001))

    return {
        "semantic_similarity_score": round(normalized, 3),
        "comparison_count": len(hits),
        "top_reference_score": round(hits[0]["_score"], 3) if hits else 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 8: weekly_performance_metrics
# Used by: Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────

TOOL_WEEKLY_PERFORMANCE_METRICS = {
    "name": "weekly_performance_metrics",
    "description": "Generate weekly performance metrics: auto-resolution rate, P90 time-to-resolve, volume by category, escalation rate, and CSAT trends.",
    "parameters": {
        "type": "object",
        "properties": {
            "include_feedback": {"type": "boolean", "default": False},
            "weeks_back": {"type": "integer", "default": 1},
        },
    },
}


def weekly_performance_metrics(include_feedback: bool = False, weeks_back: int = 1) -> dict:
    """Comprehensive weekly analytics dashboard data."""

    metrics_query = f"""
    FROM support-tickets
    | WHERE created_at >= NOW() - {weeks_back * 7} days
    | STATS
        total_tickets = COUNT(*),
        auto_resolved = COUNT(CASE WHEN resolved_by == "agent" THEN 1 END),
        escalated = COUNT(CASE WHEN status == "escalated" THEN 1 END),
        avg_resolve_hours = AVG(TIMESTAMP_DIFF(HOUR, created_at, updated_at)),
        p90_resolve_hours = PERCENTILE(TIMESTAMP_DIFF(HOUR, created_at, updated_at), 90),
        sla_breached_count = COUNT(CASE WHEN sla_breached == true THEN 1 END),
        avg_confidence = AVG(resolution_confidence)
    """

    category_query = f"""
    FROM support-tickets
    | WHERE created_at >= NOW() - {weeks_back * 7} days
    | STATS ticket_count = COUNT(*) BY category
    | SORT ticket_count DESC
    | LIMIT 10
    """

    metrics_result = run_esql(metrics_query)
    category_result = run_esql(category_query)

    metrics_cols = [c["name"] for c in metrics_result.get("columns", [])]
    metrics_vals = metrics_result.get("values", [[]])[0]
    metrics = dict(zip(metrics_cols, metrics_vals)) if metrics_vals else {}

    total = metrics.get("total_tickets", 0) or 1
    auto = metrics.get("auto_resolved", 0) or 0

    cat_cols = [c["name"] for c in category_result.get("columns", [])]
    categories = [dict(zip(cat_cols, row)) for row in category_result.get("values", [])]

    result = {
        "period_days": weeks_back * 7,
        "total_tickets": total,
        "auto_resolved": auto,
        "auto_resolution_rate": round(auto / total, 3),
        "escalation_rate": round((metrics.get("escalated", 0) or 0) / total, 3),
        "avg_resolve_hours": round(metrics.get("avg_resolve_hours", 0) or 0, 1),
        "p90_resolve_hours": round(metrics.get("p90_resolve_hours", 0) or 0, 1),
        "sla_breach_rate": round((metrics.get("sla_breached_count", 0) or 0) / total, 3),
        "avg_resolution_confidence": round(metrics.get("avg_confidence", 0) or 0, 3),
        "top_categories": categories,
    }

    if include_feedback:
        feedback_query = f"""
        FROM feedback
        | WHERE timestamp >= NOW() - {weeks_back * 7} days
        | STATS positive = COUNT(CASE WHEN score == 1 THEN 1 END),
                negative = COUNT(CASE WHEN score == -1 THEN 1 END),
                total_feedback = COUNT(*)
        """
        fb_result = run_esql(feedback_query)
        fb_cols = [c["name"] for c in fb_result.get("columns", [])]
        fb_vals = fb_result.get("values", [[0, 0, 0]])[0]
        fb = dict(zip(fb_cols, fb_vals))
        fb_total = fb.get("total_feedback", 0) or 1
        result["feedback"] = {
            "positive": fb.get("positive", 0),
            "negative": fb.get("negative", 0),
            "csat_score": round((fb.get("positive", 0) or 0) / fb_total, 3),
        }

    return result


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 9: kb_gap_detector
# Used by: Analyst Agent
# Finds ticket categories with NO knowledge base coverage
# ─────────────────────────────────────────────────────────────────────────────

TOOL_KB_GAP_DETECTOR = {
    "name": "kb_gap_detector",
    "description": "Identify ticket categories that have no matching knowledge base articles. These gaps cause low auto-resolution rates and high escalation. Returns categories ordered by ticket volume.",
    "parameters": {
        "type": "object",
        "properties": {
            "min_ticket_count": {"type": "integer", "default": 5, "description": "Only flag gaps with at least this many tickets"},
        },
    },
}


def kb_gap_detector(min_ticket_count: int = 5) -> dict:
    """Find categories with high ticket volume but no KB coverage."""

    # Get all categories with ticket counts
    ticket_cats_query = f"""
    FROM support-tickets
    | WHERE created_at >= NOW() - 30 days
    | STATS ticket_count = COUNT(*) BY category
    | WHERE ticket_count >= {min_ticket_count}
    | SORT ticket_count DESC
    """

    # Get all categories that have KB articles
    kb_cats_query = """
    FROM knowledge-base
    | WHERE draft == false
    | STATS article_count = COUNT(*) BY category
    """

    ticket_result = run_esql(ticket_cats_query)
    kb_result = run_esql(kb_cats_query)

    ticket_cols = [c["name"] for c in ticket_result.get("columns", [])]
    ticket_rows = [dict(zip(ticket_cols, row)) for row in ticket_result.get("values", [])]

    kb_cols = [c["name"] for c in kb_result.get("columns", [])]
    kb_cats = {row[0]: row[1] for row in kb_result.get("values", [])}  # category -> count

    gaps = []
    covered = []
    for row in ticket_rows:
        cat = row.get("category")
        count = row.get("ticket_count", 0)
        articles = kb_cats.get(cat, 0)
        if articles == 0:
            gaps.append({"category": cat, "ticket_count": count, "kb_articles": 0})
        else:
            covered.append({"category": cat, "ticket_count": count, "kb_articles": articles})

    return {
        "gaps": gaps,
        "covered": covered,
        "total_gaps": len(gaps),
        "coverage_rate": round(len(covered) / max(len(ticket_rows), 1), 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL REGISTRY — used by setup/04_workflows.py to register tools in Agent Builder
# ─────────────────────────────────────────────────────────────────────────────

ALL_TOOLS = [
    TOOL_FIND_SIMILAR_TICKETS,
    TOOL_GET_CUSTOMER_PROFILE,
    TOOL_SCORE_TICKET_PRIORITY,
    TOOL_DETECT_TICKET_SURGE,
    TOOL_CORRELATE_SPIKE_TO_DEPLOYMENT,
    TOOL_SEARCH_KNOWLEDGE_BASE,
    TOOL_SCORE_RESOLUTION_QUALITY,
    TOOL_WEEKLY_PERFORMANCE_METRICS,
    TOOL_KB_GAP_DETECTOR,
]

TOOL_FUNCTIONS = {
    "find_similar_tickets": find_similar_tickets,
    "get_customer_profile": get_customer_profile,
    "score_ticket_priority": score_ticket_priority,
    "detect_ticket_surge": detect_ticket_surge,
    "correlate_spike_to_deployment": correlate_spike_to_deployment,
    "search_knowledge_base": search_knowledge_base,
    "score_resolution_quality": score_resolution_quality,
    "weekly_performance_metrics": weekly_performance_metrics,
    "kb_gap_detector": kb_gap_detector,
}
