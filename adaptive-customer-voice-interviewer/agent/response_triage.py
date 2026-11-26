import re
from typing import Any, Dict

from dotenv import load_dotenv

from agent.llm import get_llm
from agent.observability import get_logger
from agent.response_outcomes import append_outcome
from agent.state import InterviewState

logger = get_logger(__name__)


load_dotenv()


def build_triage_prompt(question: str, response: str) -> str:
    return f"""
You are a response-triage classifier for a consumer research interview.

Your ONLY task is to classify whether the consumer's response is
relevant and usable for the current interview question.

INTERVIEW QUESTION:
{question}

CONSUMER RESPONSE:
{response}

Choose exactly one status:

valid:
- The response meaningfully addresses the question or a relevant
  part of it.
- A partial answer can be valid.
- Relevant examples, preferences, goals, needs, expectations,
  experiences, concerns, and reasons count as answers when they
  relate to the question.
- The response does not need to repeat the question's wording.
- If the response contains relevant information, do not classify
  it as unrelated merely because it also contains general examples
  or introductory statements.

unrelated:
- The consumer gives a response that has no meaningful connection
  to the question.

non_responsive:
- The response contains no meaningful conversational answer,
  such as random words, unintelligible content, or obvious noise
  represented as text.

unclear:
- The consumer appears to be attempting an answer, but the
  response is too ambiguous or incomplete to determine whether
  it is relevant.

DECISION RULES:
1. When in doubt between valid and unclear, prefer valid.
2. Do not require the response to address every part of the question.
3. Use unrelated only when the response clearly does not address the question.
4. Return only the classification label: valid, unrelated, non_responsive, or unclear.

Classification:
"""


def _deterministic_triage_fallback(question: str, response: str) -> str:
    text = response.strip()
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    words = [w for w in re.findall(r"[a-z0-9']+", normalized) if w]

    if not words:
        return "no_response"

    non_responsive = {
        "uh", "um", "hmm", "mm", "mmm", "hm", "er", "ah",
        "noise", "inaudible", "unintelligible",
    }
    if all(word in non_responsive for word in words):
        return "non_responsive"

    # If we have a real transcript with substantive content, prefer accepting it
    # over asking the user to repeat. Small local models often over-classify as unclear.
    if len(words) >= 4 or len(text) >= 20:
        return "valid"

    question_words = {
        w for w in re.findall(r"[a-z0-9']+", question.lower())
        if len(w) > 3
    }
    if question_words and question_words.intersection(words):
        return "valid"

    return "unclear"


def triage_response(state: InterviewState) -> Dict[str, Any]:
    question = state.get("current_question")
    response = state.get("latest_response")

    if not question:
        status = "unclear"
        return {
            "response_status": status,
            **append_outcome(state, status, "response_triage"),
        }

    if not response or not response.strip():
        status = "no_response"
        return {
            "response_status": status,
            **append_outcome(state, status, "response_triage"),
        }

    try:
        result = get_llm().invoke(
            build_triage_prompt(question, response)
        )
        status = str(getattr(result, "content", result)).strip().lower()
    except Exception:
        logger.exception("response_triage_invoke_failed")
        status = _deterministic_triage_fallback(question, response)

    allowed_statuses = {
        "valid",
        "unrelated",
        "non_responsive",
        "unclear",
    }

    if status not in allowed_statuses:
        logger.info(
            "debug_payload",
            extra={
                "parts": [
                    "WARNING: Unexpected triage output:",
                    repr(status),
                ]
            },
        )
        status = _deterministic_triage_fallback(question, response)

    # Local/small-model guard: don't punish a clear transcript with repeat loops.
    if status == "unclear":
        fallback = _deterministic_triage_fallback(question, response)
        if fallback == "valid":
            logger.info(
                "response_triage_unclear_overridden",
                extra={
                    "question": question,
                    "response": response,
                },
            )
            status = "valid"

    logger.info(
        "debug_payload",
        extra={
            "parts": [
                "DEBUG triage result:",
                {
                    "question": question,
                    "response": response,
                    "status": status,
                },
            ]
        },
    )
    return {
        "response_status": status,
        **append_outcome(state, status, "response_triage"),
    }
