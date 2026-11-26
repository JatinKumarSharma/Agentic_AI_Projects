from typing import Literal

from pydantic import BaseModel


class ResponseTriageSchema(BaseModel):
    status: Literal[
        "valid",
        "unrelated",
        "non_responsive",
        "unclear",
    ]
    