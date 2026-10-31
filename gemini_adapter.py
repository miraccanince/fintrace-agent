import os

from google import genai
from google.genai import types

from llm_investigation import InvalidLLMFinding, LLMInvestigationFinding

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def generate_finding(prompt: str) -> LLMInvestigationFinding:
    """Call the Gemini API and return a validated LLMInvestigationFinding.

    Requires GEMINI_API_KEY in the environment (read automatically by
    genai.Client()). Uses Gemini's structured output (response_schema) so
    the model's JSON is already schema-validated before this function sees
    it; gemini-2.0-flash supports this on the free tier.
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
