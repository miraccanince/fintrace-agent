import os

from google import genai
from google.genai import types

from llm_investigation import InvalidLLMFinding, LLMInvestigationFinding

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def generate_finding(prompt: str) -> LLMInvestigationFinding:
    """Call the Gemini API and return a validated LLMInvestigationFinding.

    Requires GEMINI_API_KEY in the environment (read automatically by
    genai.Client()). Uses the installed google-genai SDK's structured
    output fields (`response_mime_type` + `response_schema` on
    GenerateContentConfig) so the model's JSON is already schema-validated
    before this function sees it. gemini-2.0-flash (the model this adapter
    originally used) has since been shut down; gemini-3.8-flash is the
    current free-tier model confirmed (via ai.google.dev, 2026-10-31) to
    support structured output.
    """
    client = genai.Client()
    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=LLMInvestigationFinding,
        ),
    )

    if response.parsed is None:
        raise InvalidLLMFinding("Gemini response did not match the expected schema")

    return response.parsed
