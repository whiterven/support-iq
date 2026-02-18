"""
setup/01_inference_endpoint.py
Creates the Gemini 2.5 Pro inference endpoint in Elasticsearch via Vertex AI.
This is the LLM backbone all 5 agents will use.
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

ELASTIC_URL = os.getenv("ELASTIC_URL")
ELASTIC_API_KEY = os.getenv("ELASTIC_API_KEY")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")
GCP_LOCATION = os.getenv("GCP_LOCATION", "us-central1")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"ApiKey {ELASTIC_API_KEY}",
}


def create_gemini_inference_endpoint():
    """
    Creates the Gemini 2.5 Pro chat completion inference endpoint.
    Endpoint ID: supportiq-gemini-25-pro
    """
    endpoint_id = "supportiq-gemini-25-pro"
    url = f"{ELASTIC_URL}/_inference/chat_completion/{endpoint_id}"

    payload = {
        "service": "googlevertexai",
        "service_settings": {
            "project_id": GCP_PROJECT_ID,
            "location": GCP_LOCATION,
            "model_id": GEMINI_MODEL,
        },
        "task_settings": {
            "temperature": 0.1,          # Low temp = deterministic, reliable tool calls
            "max_tokens": 4096,
        },
    }

    print(f"Creating Gemini inference endpoint: {endpoint_id}")
    print(f"  Model: {GEMINI_MODEL}")
    print(f"  GCP Project: {GCP_PROJECT_ID} | Location: {GCP_LOCATION}")

    resp = requests.put(url, headers=HEADERS, json=payload)

    if resp.status_code in (200, 201):
        print(f"✅ Inference endpoint '{endpoint_id}' created successfully.")
    elif resp.status_code == 409:
        print(f"⚠️  Endpoint '{endpoint_id}' already exists. Skipping.")
    else:
        print(f"❌ Failed: {resp.status_code} — {resp.text}")
        raise RuntimeError("Inference endpoint creation failed.")

    return endpoint_id


def create_embedding_endpoint():
    """
    Creates a text embedding endpoint for semantic_text fields.
    Uses Gemini's text-embedding model for vector generation.
    """
    endpoint_id = "supportiq-embeddings"
    url = f"{ELASTIC_URL}/_inference/text_embedding/{endpoint_id}"

    payload = {
        "service": "googlevertexai",
        "service_settings": {
            "project_id": GCP_PROJECT_ID,
            "location": GCP_LOCATION,
            "model_id": "text-embedding-005",  # Latest Google embedding model
        },
    }

    print(f"\nCreating text embedding endpoint: {endpoint_id}")

    resp = requests.put(url, headers=HEADERS, json=payload)

    if resp.status_code in (200, 201):
        print(f"✅ Embedding endpoint '{endpoint_id}' created successfully.")
    elif resp.status_code == 409:
        print(f"⚠️  Endpoint '{endpoint_id}' already exists. Skipping.")
    else:
        print(f"❌ Failed: {resp.status_code} — {resp.text}")
        raise RuntimeError("Embedding endpoint creation failed.")

    return endpoint_id


def test_inference_endpoint(endpoint_id: str):
    """Quick smoke test: ask Gemini something simple."""
    url = f"{ELASTIC_URL}/_inference/chat_completion/{endpoint_id}/_perform"

    payload = {
        "input": [
            {
                "role": "user",
                "content": "Respond with exactly: SUPPORTIQ_ONLINE"
            }
        ]
    }

    print(f"\nTesting inference endpoint '{endpoint_id}'...")
    resp = requests.post(url, headers=HEADERS, json=payload)

    if resp.status_code == 200:
        content = resp.json().get("completion", [{}])[0].get("result", "")
        if "SUPPORTIQ_ONLINE" in content:
            print("✅ Gemini 2.5 Pro is responding correctly.")
        else:
            print(f"⚠️  Unexpected response: {content}")
    else:
        print(f"❌ Test failed: {resp.status_code} — {resp.text}")


if __name__ == "__main__":
    print("=" * 60)
    print("STEP 1: Configuring Gemini 2.5 Pro Inference Endpoints")
    print("=" * 60)

    llm_endpoint = create_gemini_inference_endpoint()
    embedding_endpoint = create_embedding_endpoint()
    test_inference_endpoint(llm_endpoint)

    print("\n✅ Step 1 complete. Endpoints ready.")
    print(f"   LLM Endpoint ID     : {llm_endpoint}")
    print(f"   Embedding Endpoint ID: {embedding_endpoint}")
