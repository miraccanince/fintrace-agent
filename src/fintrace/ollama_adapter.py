"""One local Ollama chat request with tools, via the native /api/chat endpoint.

No credentials are needed. Configuration comes from the environment:
OLLAMA_MODEL (default qwen3.5:9b) and OLLAMA_BASE_URL (default http://localhost:11434).
"""

import os
from typing import Any

import httpx
from dotenv import load_dotenv

# .env lives at the repository root, two levels above this package.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
NUM_CTX = 8192
REQUEST_TIMEOUT_SECONDS = 120  # covers loading the model on a cold start


def generate_with_tools(messages: list[dict], declarations: list[dict], system_instruction: str) -> dict[str, Any]:
    """Send one chat request and return Ollama's parsed JSON reply. Declarations are
    dicts with name, description and a JSON-Schema `parameters`. Nothing is retried:
    a failure (server down, timeout, HTTP error) is raised for the harness to report."""
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system_instruction}, *messages],
        "tools": [
            {"type": "function", "function": {"name": d["name"], "description": d["description"], "parameters": d["parameters"]}}
            for d in declarations
        ],
        "stream": False,
        "options": {"num_ctx": NUM_CTX},
    }
    response = httpx.post(f"{BASE_URL}/api/chat", json=body, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()
