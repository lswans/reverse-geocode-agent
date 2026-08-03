"""Local demo server: serves the map UI, runs the agent, and keeps one
conversation alive per dropped pin.

Endpoints:
  GET  /api/map-config      - Amazon Location region, style, and map API key
  GET  /api/describe/stream - new pin: streams tool steps, then the PlaceReport
  GET  /api/ask/stream      - follow-up: streams tool steps, then the answer text
  POST /api/describe        - same first turn without streaming, for scripts

Dropping a new pin replaces the live session, so the previous conversation and
its history are discarded. This is a single-user demo: there is exactly one
session at a time and there is no authentication. Loopback only, do not expose.
"""

import asyncio
import functools
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from strands import Agent

from agent import AWS_REGION, PlaceReport, build_agent, click_prompt, follow_up_prompt

app = FastAPI(title="Map Click Agent Demo")

MAX_QUESTION_CHARS = 500

# The browser renders Amazon Location basemap tiles, which needs an Amazon
# Location API key. Set ALS_API_KEY to supply one directly, or let the server
# look up a key by name with its own AWS credentials. The key is scoped to
# geo-maps only: Places calls stay server-side under IAM.
ALS_API_KEY = os.getenv("ALS_API_KEY")
ALS_KEY_NAME = os.getenv("ALS_KEY_NAME", "map-click-agent-demo-maps")
MAP_STYLE = os.getenv("MAP_STYLE", "Standard")
MAP_COLOR_SCHEME = os.getenv("MAP_COLOR_SCHEME", "Dark")

TOOL_LABELS = {
    "reverse_geocode": "Reverse-geocoding with Amazon Location",
    "find_nearby_places": "Searching Amazon Location for nearby places",
    "search_wikipedia": "Reading up on it on Wikipedia",
    "read_web_page": "Reading the place's own website",
    "PlaceReport": "Writing up the report",
}


@dataclass
class Session:
    """One pin's conversation. Replaced whenever a new pin is dropped."""

    id: str
    latitude: float
    longitude: float
    agent: Agent
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_session: Optional[Session] = None


def _start_session(latitude: float, longitude: float) -> Session:
    """Drop the old conversation and begin a new one."""
    global _session
    _session = Session(
        id=uuid.uuid4().hex,
        latitude=latitude,
        longitude=longitude,
        agent=build_agent(),
    )
    return _session


@functools.lru_cache(maxsize=1)
def _map_api_key() -> str:
    """The API key the browser uses for basemap tiles."""
    if ALS_API_KEY:
        return ALS_API_KEY
    return boto3.client("location", region_name=AWS_REGION).describe_key(KeyName=ALS_KEY_NAME)["Key"]


class MapConfig(BaseModel):
    region: str
    style: str
    color_scheme: str
    api_key: str


@app.get("/api/map-config", response_model=MapConfig)
async def map_config() -> MapConfig:
    """Everything the browser needs to point MapLibre at Amazon Location."""
    try:
        key = await asyncio.to_thread(_map_api_key)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No Amazon Location map key available ({exc}). "
                "Run create_map_key.py, or set ALS_API_KEY."
            ),
        ) from exc
    return MapConfig(
        region=AWS_REGION,
        style=MAP_STYLE,
        color_scheme=MAP_COLOR_SCHEME,
        api_key=key,
    )


class ClickRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DescribeResponse(BaseModel):
    session_id: str
    report: PlaceReport


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _run(session: Session, prompt: str, structured: bool) -> AsyncIterator[str]:
    """Stream one agent turn as SSE frames: tool steps, then the result."""
    announced: set[str] = set()
    kwargs = {"structured_output_model": PlaceReport} if structured else {}

    try:
        async for event in session.agent.stream_async(prompt, **kwargs):
            name = (event.get("current_tool_use") or {}).get("name")
            if name and name not in announced:
                announced.add(name)
                yield _sse("step", {"tool": name, "label": TOOL_LABELS.get(name, name)})

            # Free-text answers arrive as deltas. Structured turns emit none.
            delta = event.get("data")
            if delta:
                yield _sse("delta", {"text": delta})

            result = event.get("result")
            if result is None:
                continue
            report = getattr(result, "structured_output", None)
            if report is not None:
                yield _sse("report", {"session_id": session.id, "report": report.model_dump()})
            elif not structured:
                yield _sse("done", {"session_id": session.id})
    except Exception as exc:
        yield _sse("failed", {"detail": f"Agent failed: {exc}"})


async def _guarded(session: Session, prompt: str, structured: bool) -> AsyncIterator[str]:
    """Serialize turns so overlapping requests cannot interleave into one history."""
    async with session.lock:
        if session is not _session:
            yield _sse("failed", {"detail": "That pin was replaced. Drop a new pin to continue."})
            return
        async for frame in _run(session, prompt, structured):
            yield frame


def _stream(session: Session, prompt: str, structured: bool) -> StreamingResponse:
    return StreamingResponse(
        _guarded(session, prompt, structured),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/describe/stream")
async def describe_stream(
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
) -> StreamingResponse:
    """New pin. Starts a fresh conversation and streams the first turn."""
    session = _start_session(latitude, longitude)
    return _stream(session, click_prompt(latitude, longitude), structured=True)


@app.get("/api/ask/stream")
async def ask_stream(
    session_id: str = Query(min_length=1, max_length=64),
    question: str = Query(min_length=1, max_length=MAX_QUESTION_CHARS),
) -> StreamingResponse:
    """Follow-up question about the pin that is currently live."""
    session = _session
    if session is None or session.id != session_id:
        raise HTTPException(status_code=409, detail="That pin was replaced. Drop a new pin to continue.")
    return _stream(session, follow_up_prompt(question.strip()), structured=False)


@app.post("/api/describe", response_model=DescribeResponse)
async def describe(click: ClickRequest) -> DescribeResponse:
    """Non-streaming first turn. Also starts a session you can ask follow-ups on."""
    session = _start_session(click.latitude, click.longitude)
    try:
        # The agent call is blocking, so keep the event loop free.
        result = await asyncio.to_thread(
            session.agent,
            click_prompt(click.latitude, click.longitude),
            structured_output_model=PlaceReport,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent failed: {exc}") from exc
    return DescribeResponse(session_id=session.id, report=result.structured_output)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    # Loopback by default on purpose. There is no authentication here, so binding
    # to 0.0.0.0 would expose the agent to your whole network.
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
