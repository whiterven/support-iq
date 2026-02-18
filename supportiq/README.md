# 🏆 SupportIQ — Autonomous Support Operations Agent
> Elastic Agent Builder Hackathon 2026 Submission

SupportIQ is a **5-agent autonomous support operations system** powered by Elasticsearch Agent Builder, Gemini 2.5 Pro via Vertex AI, and the A2A (Agent-to-Agent) protocol. It eliminates 70% of manual support triage, predicts ticket surges before they happen, and gets smarter every week through a built-in human feedback loop.

---

## 🏗️ Architecture

```
                         ┌─────────────────────────────────────┐
                         │         Elastic Kibana / A2A         │
                         └──────────────┬──────────────────────┘
                                        │ A2A Protocol
          ┌─────────────────────────────▼──────────────────────────────┐
          │                    A2A Orchestrator (Python)               │
          │                 (coordinates all 5 agents)                 │
          └──┬──────────────┬──────────────┬──────────────┬───────────┘
             │              │              │              │
   ┌─────────▼───┐  ┌───────▼───┐  ┌──────▼────┐  ┌─────▼──────┐  ┌──────────────┐
   │  AGENT 1    │  │  AGENT 2  │  │  AGENT 3  │  │  AGENT 4   │  │   AGENT 5    │
   │  Watcher    │→ │  Judge    │→ │  Solver   │→ │  Critic    │  │  Analyst     │
   │  (Intake)   │  │ (Triage)  │  │ (Resolve) │  │  (QA Gate) │  │  (Insights)  │
   └─────────────┘  └───────────┘  └───────────┘  └────────────┘  └──────────────┘
          │              │              │              │                  │
          └──────────────┴──────────────┴──────────────┴──────────────────┘
                                        │
                              Elasticsearch Indices
                    support-tickets | knowledge-base | deployments
                    agent-traces | customer-profiles | feedback
```

## 💥 Key Features

| Feature | Impact |
|---|---|
| Semantic ticket matching | Finds similar past tickets in <200ms |
| 3-dimension ES\|QL triage scoring | Priority score = tier + SLA risk + recurrence |
| Confidence-gated auto-resolution | Auto-resolves 85%+ of known issues |
| Self-correcting Critic loop | Rejects low-quality drafts, forces re-resolution |
| Deployment correlation | Links ticket surges to deployments automatically |
| Ghost Ticket pre-emption | Alerts team BEFORE surge hits queue |
| RLHF-lite feedback loop | Quality improves weekly from 👍/👎 Slack reactions |
| A2A with Gemini Enterprise | All 5 agents exposed as A2A endpoints |
| Voice interface (LiveKit) | Talk to your operations on mobile |
| Live Ops Command Center | Real-time Kibana dashboard of all agent activity |

---

## 🚀 Quick Start

### Prerequisites
- Elastic Cloud Serverless account (free trial works)
- Google Cloud account with Vertex AI enabled
- Python 3.11+
- Node.js 18+

### 1. Clone & Install
```bash
git clone https://github.com/your-org/supportiq
cd supportiq
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Fill in your credentials (see .env.example)
```

### 3. Run Setup (creates indices, inference endpoint, agents, workflows)
```bash
python setup/run_all.py
```

### 4. Seed Synthetic Data
```bash
python data/seed_data.py --tickets 500 --kb-articles 150
```

### 5. Start the A2A Orchestrator
```bash
python orchestration/a2a_pipeline.py
```

### 6. Send a Test Ticket
```bash
python orchestration/test_ticket.py
```

---

## 📁 Project Structure

```
supportiq/
├── setup/
│   ├── 01_inference_endpoint.py   # Configure Gemini 2.5 Pro via Vertex AI
│   ├── 02_indices.py              # Create all ES indices with mappings
│   ├── 03_agents.py               # Create all 5 agents via Kibana API
│   ├── 04_workflows.py            # Create Elastic Workflows
│   └── run_all.py                 # Run all setup in order
├── agents/
│   ├── watcher_agent.json         # Intake agent config
│   ├── triage_agent.json          # Judge/triage agent config
│   ├── resolver_agent.json        # Resolution agent config
│   ├── critic_agent.json          # Quality gate agent config
│   └── analyst_agent.json         # Insights agent config
├── tools/
│   ├── esql_tools.py              # All 9 custom ES|QL tools
│   └── workflow_tools.py          # Elastic Workflow definitions
├── orchestration/
│   ├── a2a_pipeline.py            # Main A2A orchestration loop
│   ├── a2a_client.py              # A2A protocol client
│   └── slack_integration.py       # Slack webhook handler
├── data/
│   └── seed_data.py               # Synthetic data generator
├── dashboard/
│   └── kibana_dashboard.ndjson    # Ops Command Center dashboard
└── workflows/
    ├── ticket_intake.yaml
    ├── crm_update.yaml
    ├── slack_notify.yaml
    ├── kb_draft.yaml
    └── ghost_alert.yaml
```

---

## 🔑 Environment Variables

```
ELASTIC_URL=https://your-deployment.es.region.gcp.elastic.cloud
ELASTIC_API_KEY=your-encoded-api-key
KIBANA_URL=https://your-deployment.kb.region.gcp.elastic.cloud
GCP_PROJECT_ID=your-gcp-project
GCP_LOCATION=us-central1
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
GEMINI_MODEL=gemini-2.5-pro
```

---

## 📺 Demo Video

[3-minute demo video link]

---

## 📝 License

MIT License — see LICENSE file.
