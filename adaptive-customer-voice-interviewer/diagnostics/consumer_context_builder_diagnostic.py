
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agent.proposal_generator import build_consumer_context


# --------------------------------------------------
# SCENARIO 1: KNOWN PURCHASE FREQUENCY
# --------------------------------------------------

purchase_frequency_state = {
    "overall_sentiment": {
        "values": [
            {"value": "Very satisfied with the product"}
        ]
    },

    "purchase_intent": {
        "values": [
            {"value": "Purchases approximately twice a month"}
        ]
    }
}


# --------------------------------------------------
# SCENARIO 2: KNOWN PRODUCT SATISFACTION
# --------------------------------------------------

satisfaction_state = {
    "overall_sentiment": {
        "values": [
            {"value": "Very satisfied with the product"}
        ]
    }
}


# --------------------------------------------------
# SCENARIO 3: EMPTY CONSUMER KNOWLEDGE
# --------------------------------------------------

empty_state = {}


# --------------------------------------------------
# BUILD CONTEXTS
# --------------------------------------------------

purchase_context = build_consumer_context(
    purchase_frequency_state
)

satisfaction_context = build_consumer_context(
    satisfaction_state
)

empty_context = build_consumer_context(
    empty_state
)


# --------------------------------------------------
# DISPLAY
# --------------------------------------------------

print("\nPURCHASE FREQUENCY CONTEXT")
print("-" * 50)
print(purchase_context)

print("\nSATISFACTION CONTEXT")
print("-" * 50)
print(satisfaction_context)

print("\nEMPTY CONTEXT")
print("-" * 50)
print(empty_context)


# --------------------------------------------------
# VALIDATION
# --------------------------------------------------

assert "twice a month" in purchase_context.lower()

assert "very satisfied" in satisfaction_context.lower()

assert "no consumer-specific information" in empty_context.lower()

print("\nCONSUMER CONTEXT BUILDER TEST PASSED")
