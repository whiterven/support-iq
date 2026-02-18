"""
orchestration/a2a_client.py
A2A Protocol Client for SupportIQ.
Handles all communication with Elastic Agent Builder agents via the A2A protocol.

The A2A protocol has two key endpoints per agent:
  GET  /api/agent_builder/a2a/{agentId}.json  → Agent Card (metadata)
  POST /api/agent_builder/a2a/{agentId}       → Send message, get response
"""

import os
import json
import uuid
import time
import requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

KIBANA_URL = os.getenv("KIBANA_URL")
KIBANA_API_KEY = os.getenv("KIBANA_API_KEY")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {KIBANA_API_KEY}",
}

# Agent IDs — must match what was created in setup/03_agents.py
AGENT_IDS = {
    "watcher":  "supportiq_watcher",
    "judge":    "supportiq_judge",
    "solver":   "supportiq_solver",
    "critic":   "supportiq_critic",
    "analyst":  "supportiq_analyst",
}


class A2AClient:
    """
    Client for communicating with Elastic Agent Builder agents via the A2A protocol.
    Each agent exposes two A2A endpoints automatically when a2a_enabled=True.
    """

    def __init__(self, kibana_url: str = None, api_key: str = None):
        self.kibana_url = kibana_url or KIBANA_URL
        self.api_key = api_key or KIBANA_API_KEY
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}",
        }
        self._agent_cards = {}

    def get_agent_card(self, agent_name: str) -> dict:
        """
        Retrieve the A2A Agent Card for an agent.
        This is the 'business card' — describes capabilities, supported tasks, etc.
        """
        if agent_name in self._agent_cards:
            return self._agent_cards[agent_name]

        agent_id = AGENT_IDS.get(agent_name)
        if not agent_id:
            raise ValueError(f"Unknown agent: {agent_name}. Available: {list(AGENT_IDS.keys())}")

        url = f"{self.kibana_url}/api/agent_builder/a2a/{agent_id}.json"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()

        card = resp.json()
        self._agent_cards[agent_name] = card
        return card

    def send_message(self, agent_name: str, message: str,
                     context: Optional[dict] = None,
                     session_id: Optional[str] = None,
                     timeout: int = 120) -> dict:
        """
        Send a message to an agent via A2A protocol and get the response.

        The A2A protocol wraps messages in a standard envelope:
        {
          "jsonrpc": "2.0",
          "method": "message/send",
          "id": "<request-id>",
          "params": {
            "message": {
              "role": "user",
              "parts": [{"kind": "text", "text": "<message>"}],
              "messageId": "<message-id>"
            },
            "sessionId": "<session-id>"
          }
        }
        """
        agent_id = AGENT_IDS.get(agent_name)
        if not agent_id:
            raise ValueError(f"Unknown agent: {agent_name}")

        url = f"{self.kibana_url}/api/agent_builder/a2a/{agent_id}"

        # Build the message payload (may include context as additional parts)
        parts = [{"kind": "text", "text": message}]
        if context:
            parts.append({
                "kind": "text",
                "text": f"\n\nContext:\n{json.dumps(context, indent=2)}"
            })

        request_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        session = session_id or str(uuid.uuid4())

        payload = {
            "jsonrpc": "2.0",
            "method": "message/send",
            "id": request_id,
            "params": {
                "message": {
                    "role": "user",
                    "parts": parts,
                    "messageId": message_id,
                },
                "sessionId": session,
            }
        }

        start = time.time()
        resp = requests.post(url, headers=self.headers, json=payload, timeout=timeout)

        if resp.status_code != 200:
            raise RuntimeError(
                f"A2A call to '{agent_name}' failed: {resp.status_code} — {resp.text}"
            )

        duration_ms = int((time.time() - start) * 1000)
        result = resp.json()

        # Extract the agent's text response from A2A response envelope
        agent_response = self._extract_text(result)

        # Try to parse as JSON (agents always return JSON)
        parsed = None
        try:
            # Strip markdown code fences if present
            clean = agent_response.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed = json.loads(clean.strip())
        except (json.JSONDecodeError, IndexError):
            parsed = {"raw_response": agent_response}

        return {
            "agent": agent_name,
            "session_id": session,
            "request_id": request_id,
            "response_text": agent_response,
            "parsed": parsed,
            "duration_ms": duration_ms,
            "success": parsed is not None and "raw_response" not in parsed,
        }

    def _extract_text(self, a2a_response: dict) -> str:
        """Extract text content from A2A protocol response."""
        try:
            # A2A response structure: result.status.message.parts[].text
            result = a2a_response.get("result", {})
            if isinstance(result, dict):
                status = result.get("status", {})
                message = status.get("message", {})
                parts = message.get("parts", [])
                texts = [p.get("text", "") for p in parts if p.get("kind") == "text"]
                return " ".join(texts).strip()
        except (AttributeError, TypeError, KeyError):
            pass
        return str(a2a_response)

    def ping_all_agents(self) -> dict:
        """Health check: verify all 5 agents are responding."""
        results = {}
        for name in AGENT_IDS:
            try:
                card = self.get_agent_card(name)
                results[name] = {
                    "status": "online",
                    "name": card.get("name"),
                    "a2a_url": f"{self.kibana_url}/api/agent_builder/a2a/{AGENT_IDS[name]}",
                }
            except Exception as e:
                results[name] = {"status": "error", "error": str(e)}
        return results
