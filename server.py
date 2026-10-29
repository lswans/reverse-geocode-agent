"""Serve the map UI, authenticate API requests, and manage location sessions."""

import asyncio
import functools
import importlib
import json
import logging
import os
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

agent_module = importlib.import_module(os.getenv("AGENT_MODULE", "agent_stub"))
AWS_REGION = agent_module.AWS_REGION
PlaceReport = agent_module.PlaceReport
build_agent = agent_module.build_agent
click_prompt = agent_module.click_prompt
follow_up_prompt = agent_module.follow_up_prompt

app = FastAPI(title="Map Click Agent Demo")
logger = logging.getLogger("map_click_agent")

MAX_QUESTION_CHARS = 500
AGENT_FAILED_DETAIL = "The agent could not finish that request. Try again in a moment."
ALS_API_KEY = os.getenv("ALS_API_KEY")
ALS_KEY_NAME = os.getenv("ALS_KEY_NAME", "map-click-agent-demo-maps")
MAP_STYLE = os.getenv("MAP_STYLE", "Standard")
MAP_COLOR_SCHEME = os.getenv("MAP_COLOR_SCHEME", "Dark")
PASSCODE_PARAM_NAME = os.getenv("PASSCODE_PARAM_NAME")

TOOL_LABELS = {
    "reverse_geocode": "Reverse-geocoding with Amazon Location",
    "find_nearby_places": "Searching Amazon Location for nearby places",
    "search_wikipedia": "Reading up on it on Wikipedia",
    "read_web_page": "Reading the place's own website",
    "search_web": "Searching the web as a last resort",
    "PlaceReport": "Writing up the report",
}


@functools.lru_cache(maxsize=1)
def _configured_passcode() -> str:
    """Fetch the encrypted demo passcode once per execution environment."""
    if not PASSCODE_PARAM_NAME:
        raise RuntimeError("PASSCODE_PARAM_NAME is not configured")
    response = boto3.client("ssm", region_name=AWS_REGION).get_parameter(
        Name=PASSCODE_PARAM_NAME,
        WithDecryption=True,
    )
    return response["Parameter"]["Value"]


async def require_passcode(
    x_demo_passcode: Optional[str] = Header(default=None, alias="X-Demo-Passcode"),
) -> None:
    """Require the demo passcode in a request header, never in a URL."""
    if not x_demo_passcode:
        raise HTTPException(status_code=401, detail="Passcode required")
    try:
        configured = await asyncio.to_thread(_configured_passcode)
    except (ClientError, BotoCoreError, RuntimeError, KeyError) as exc:
        raise HTTPException(status_code=503, detail="Passcode validation is unavailable") from exc
    if not secrets.compare_digest(x_demo_passcode, configured):
        raise HTTPException(status_code=401, detail="Invalid passcode")


@dataclass
class Session:
    """One independently addressable location conversation."""

    id: str
    latitude: float
    longitude: float
    agent: Any
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


MAX_SESSIONS = 100
_sessions: dict[str, Session] = {}


def _start_session(latitude: float, longitude: float) -> Session:
    """Create an isolated conversation and evict the oldest one at the bound."""
    session = Session(
        id=uuid.uuid4().hex,
        latitude=latitude,
        longitude=longitude,
        agent=build_agent(),
    )
    _sessions[session.id] = session
    while len(_sessions) > MAX_SESSIONS:
        oldest_id = next(iter(_sessions))
        _sessions.pop(oldest_id, None)
    return session


@functools.lru_cache(maxsize=1)
def _map_api_key() -> str:
    """Return the browser's maps-only API key."""
    if ALS_API_KEY:
        return ALS_API_KEY
    return boto3.client("location", region_name=AWS_REGION).describe_key(KeyName=ALS_KEY_NAME)["Key"]


class MapConfig(BaseModel):
    region: str
    style: str
    color_scheme: str
    api_key: str


_api_auth = [Depends(require_passcode)]


@app.get("/api/map-config", response_model=MapConfig, dependencies=_api_auth)
async def map_config() -> MapConfig:
    """Return the configuration MapLibre needs for Amazon Location."""
    try:
        key = await asyncio.to_thread(_map_api_key)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=503,
            detail="No Amazon Location map key is available. Run create_map_key.py or set ALS_API_KEY.",
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


class AskRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


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
    except Exception:
        # The exception text can carry ARNs, account IDs, or tool internals, so it
        # goes to the server log only. The client gets a generic message.
        logger.exception("Agent turn failed for session %s", session.id)
        yield _sse("failed", {"detail": AGENT_FAILED_DETAIL})


async def _guarded(session: Session, prompt: str, structured: bool) -> AsyncIterator[str]:
    """Serialize turns within one conversation without blocking other users."""
    async with session.lock:
        if _sessions.get(session.id) is not session:
            yield _sse("failed", {"detail": "That location session expired. Choose a location again."})
            return
        async for frame in _run(session, prompt, structured):
            yield frame


def _stream(session: Session, prompt: str, structured: bool) -> StreamingResponse:
    return StreamingResponse(
        _guarded(session, prompt, structured),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _describe_stream(latitude: float, longitude: float) -> StreamingResponse:
    session = _start_session(latitude, longitude)
    return _stream(session, click_prompt(latitude, longitude), structured=True)


def _ask_stream(session_id: str, question: str) -> StreamingResponse:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=409, detail="That location session expired. Choose a location again.")
    clean_question = question.strip()
    if not clean_question:
        raise HTTPException(status_code=422, detail="Question cannot be blank")
    return _stream(session, follow_up_prompt(clean_question), structured=False)


@app.post("/api/describe/stream", dependencies=_api_auth)
async def describe_stream_post(click: ClickRequest) -> StreamingResponse:
    """Start a conversation from coordinates in a request body and stream its first turn."""
    return _describe_stream(click.latitude, click.longitude)


@app.post("/api/ask/stream", dependencies=_api_auth)
async def ask_stream_post(request: AskRequest) -> StreamingResponse:
    """Stream a follow-up whose session and question are carried in a request body."""
    return _ask_stream(request.session_id, request.question)


@app.post("/api/describe", response_model=DescribeResponse, dependencies=_api_auth)
async def describe(click: ClickRequest) -> DescribeResponse:
    """Run a non-streaming first turn and create a follow-up session."""
    session = _start_session(click.latitude, click.longitude)
    try:
        result = await asyncio.to_thread(
            session.agent,
            click_prompt(click.latitude, click.longitude),
            structured_output_model=PlaceReport,
        )
    except Exception as exc:
        logger.exception("Agent turn failed for session %s", session.id)
        raise HTTPException(status_code=502, detail=AGENT_FAILED_DETAIL) from exc
    return DescribeResponse(session_id=session.id, report=result.structured_output)


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
