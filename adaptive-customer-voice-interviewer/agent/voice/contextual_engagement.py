
import json

from agent.llm import get_llm


def generate_contextual_engagement(
    decomposition: dict,
) -> str:
    """
    Generate a brief spoken engagement grounded in the
    consumer's answer decomposition.

    This function does not generate the next interview question
    or modify interview state.
    """

    if not isinstance(decomposition, dict) or not decomposition:
        return ""

    llm = get_llm(purpose="engagement")

    prompt = f"""
You are the conversational engagement component of a
customer research voice interviewer.

The consumer has just completed an answer. The research
system has decomposed that answer into structured evidence.

Your task is to produce one brief, natural spoken response
that actively comments on something meaningful from the
consumer's answer so they feel heard while the next question
is being prepared. This is not the next question.

RULES:

1. Use only information explicitly supported by the
   decomposition.
2. Sound warm, upbeat, and active, not flat or robotic.
3. Make it lightly creative and specific, not generic filler.
4. Keep the response to one short sentence.
5. Prefer approximately 5–12 words.
6. Do not ask a question.
7. Do not generate the next interview question.
8. Do not introduce new facts or assumptions.
9. Do not repeat the entire consumer answer.
10. Avoid generic filler such as "That's interesting."
11. Return only the spoken sentence, without quotation
    marks, labels, or explanations.

ANSWER DECOMPOSITION:

{json.dumps(decomposition, ensure_ascii=False, indent=2)}

Spoken response:
"""

    result = llm.invoke(prompt)

    content = result.content

    if not isinstance(content, str):
        return ""

    return content.strip().strip('"').strip()
