
from dotenv import load_dotenv

from agent.llm import get_llm
from agent.state import InterviewState


load_dotenv()


def generate_question(state: InterviewState):
    """
    Generate the next interview question based on the interview
    goal and information collected so far.
    """

    research_goal = state["research_context"]["research_goal"]
    customer_profile = state.get("customer_profile")
    questions_asked = state.get("questions_asked", [])
    responses = state.get("responses", [])
    insights = state.get("insights", [])

    prompt = f"""
You are an expert customer researcher conducting a customer discovery interview.

Your goal is to understand the customer's genuine experiences, problems,
motivations, behaviors, and unmet needs.

Research goal:
{research_goal}

Customer profile:
{customer_profile}

Previous questions:
{questions_asked}

Customer responses:
{responses}

Insights discovered so far:
{insights}

Generate the single most valuable next interview question.

Rules:
- Ask only ONE question.
- Ask an open-ended question.
- Do not ask leading questions.
- Avoid repeating previous questions.
- Focus on discovering real experiences and behavior.
- Prefer questions about past behavior over hypothetical opinions.
- If an important insight needs clarification, ask a useful follow-up.
- Do not provide explanations.
- Return only the interview question.
"""

    response = get_llm().invoke(prompt)

    if not isinstance(response.content, str):
        raise TypeError("LLM response content is not a string.")

    question = response.content.strip()

    return {
        "current_question": question,
        "questions_asked": questions_asked + [question],
    }
