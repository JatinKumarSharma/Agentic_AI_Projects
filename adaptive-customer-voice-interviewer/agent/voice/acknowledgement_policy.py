import random


ACKNOWLEDGEMENTS = (
    "I see.",
    "Got it.",
    "Right, go on.",
    "That makes sense.",
    "Thanks for that.",
    "I'm with you.",
)


_last_acknowledgement: str | None = None


def should_acknowledge(
    speech_duration_ms: int,
    pause_duration_ms: int,
) -> bool:
    # Acknowledge only longer answers; short replies don't need a verbal filler.
    return (
        speech_duration_ms >= 6000
        and pause_duration_ms >= 700
    )


def select_acknowledgement() -> str:
    global _last_acknowledgement

    choices = [
        item for item in ACKNOWLEDGEMENTS
        if item != _last_acknowledgement
    ] or list(ACKNOWLEDGEMENTS)
    selected = random.choice(choices)
    _last_acknowledgement = selected
    return selected
