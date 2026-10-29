"""Canned deployment agent with no Bedrock or Location Places calls."""

import os
import re
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from report_links import safe_link

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


class PlaceReport(BaseModel):
    """Structured answer rendered by the web UI."""

    label: str = Field(description="Short name for the location")
    summary: str = Field(description="Exactly one factual sentence")
    latitude: float = Field(description="Latitude that was clicked")
    longitude: float = Field(description="Longitude that was clicked")
    link: Optional[str] = None
    link_title: Optional[str] = None

    @field_validator("link", mode="before")
    @classmethod
    def _only_http_links(cls, value: Any) -> Optional[str]:
        return safe_link(value)


@dataclass
class _Result:
    structured_output: Optional[PlaceReport] = None


def _coordinates(prompt: str) -> tuple[float, float]:
    match = re.search(r"latitude (-?\d+(?:\.\d+)?), longitude (-?\d+(?:\.\d+)?)", prompt)
    if not match:
        raise ValueError("Stub prompt did not contain coordinates")
    return float(match.group(1)), float(match.group(2))


def _report(prompt: str) -> PlaceReport:
    latitude, longitude = _coordinates(prompt)
    return PlaceReport(
        label="Stubbed location report",
        summary="This canned report confirms that the deployed streaming path is working without agent calls.",
        latitude=latitude,
        longitude=longitude,
    )

class FakeAgent:
    """Match the subset of the Strands Agent interface used by server.py."""

    def __call__(self, prompt: str, **kwargs) -> _Result:
        return _Result(structured_output=_report(prompt))

    async def stream_async(self, prompt: str, **kwargs):
        if kwargs.get("structured_output_model") is not None:
            yield {"current_tool_use": {"name": "reverse_geocode"}}
            yield {"current_tool_use": {"name": "find_nearby_places"}}
            yield {"data": "Preparing a canned report."}
            yield {"result": _Result(structured_output=_report(prompt))}
            return

        yield {"current_tool_use": {"name": "search_wikipedia"}}
        for chunk in (
            "This is a canned follow-up response. ",
            "The real agent is disabled, so no external tools were called.",
        ):
            yield {"data": chunk}
        yield {"result": _Result()}


def build_agent() -> FakeAgent:
    return FakeAgent()


def click_prompt(latitude: float, longitude: float) -> str:
    return f"The user clicked at latitude {latitude}, longitude {longitude}. Describe this location."


def follow_up_prompt(question: str) -> str:
    return f"Answer this follow-up question about the current location: {question}"
