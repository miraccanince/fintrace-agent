import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from fintrace.llm_investigation import InvalidLLMFinding, LLMInvestigationFinding

# .env lives at the repository root, two levels above this package.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 5


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


def _is_transient(error: Exception) -> bool:
    return isinstance(error, errors.ServerError) or (isinstance(error, errors.ClientError) and error.code == 429)


def generate_with_tools(contents: list[dict], declarations: list[dict], system_instruction: str):
    """One Gemini call that may answer with a function call. Declarations are
    dicts with name, description and a JSON-Schema `parameters`. Automatic
    function calling is off: the caller's harness decides what runs.

    Only transient provider errors (5xx, 429) are retried, up to MAX_ATTEMPTS."""
    client = genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=d["name"], description=d["description"], parameters_json_schema=d["parameters"]
                    )
                    for d in declarations
                ]
            )
        ],
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client.models.generate_content(model=MODEL, contents=contents, config=config)
        except Exception as error:
            if not _is_transient(error) or attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(RETRY_DELAY_SECONDS)
