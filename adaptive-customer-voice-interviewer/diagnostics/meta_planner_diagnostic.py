
from datetime import date

from agent.meta_planner import create_meta_plan
from agent.session import create_initial_state, create_research_session


def main():
    session = create_research_session(
        company_id="apple",
        company_name="Apple",
        product_id="iphone",
        product_name="iPhone",
        product_category="Smartphone",
        research_goal=(
            "Understand consumer awareness, relationship, perception, "
            "sentiment, experiences, needs, pain points, aspirations, "
            "purchase intent, and constraints related to the product."
        ),
        start_date=date.today(),
    )

    initial_state = create_initial_state(session)
    result = create_meta_plan(initial_state)

    print(result["meta_plan"])


if __name__ == "__main__":
    main()
