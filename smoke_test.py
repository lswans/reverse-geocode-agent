"""Exercise authenticated endpoints against a running server."""

import json
import os
import time

import httpx

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PASSCODE = os.environ.get("DEMO_PASSCODE", "")
HEADERS = {"X-Demo-Passcode": PASSCODE}


def read_sse(url: str, body: dict) -> dict:
    """Consume one POST SSE stream, printing steps and collecting the outcome."""
    outcome: dict = {"text": ""}
    with httpx.stream("POST", url, headers=HEADERS, json=body, timeout=180) as response:
        response.raise_for_status()
        event = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if event == "step":
                    print(f"  .. {payload['label']}")
                elif event == "delta":
                    outcome["text"] += payload["text"]
                else:
                    outcome[event] = payload
    return outcome


def drop_pin(name: str, lat: float, lon: float) -> str:
    started = time.time()
    print(f"\n=== pin: {name} ===")
    outcome = read_sse(f"{BASE}/api/describe/stream", {"latitude": lat, "longitude": lon})
    if "failed" in outcome:
        raise SystemExit(f"  failed: {outcome['failed']['detail']}")
    print(f"  {json.dumps(outcome['report']['report'], ensure_ascii=False)}")
    print(f"  ({time.time() - started:.1f}s)")
    return outcome["report"]["session_id"]


def ask(session_id: str, question: str) -> None:
    started = time.time()
    print(f"\n=== ask: {question} ===")
    outcome = read_sse(
        f"{BASE}/api/ask/stream",
        {"session_id": session_id, "question": question},
    )
    if "failed" in outcome:
        print(f"  failed: {outcome['failed']['detail']}")
    else:
        print(f"  {outcome['text'].strip()}")
    print(f"  ({time.time() - started:.1f}s)")


def check_fetch_guards() -> None:
    """read_web_page must refuse anything that is not a public web address."""
    from agent import read_web_page

    blocked = [
        "file:///C:/Windows/win.ini",
        "http://localhost:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
    ]
    print("\n=== read_web_page guards ===")
    for url in blocked:
        error = read_web_page(url).get("error")
        print(f"  {'blocked' if error else 'ALLOWED (BAD)':14} {url}")


if __name__ == "__main__":
    if not PASSCODE:
        raise SystemExit("Set DEMO_PASSCODE before running this script")

    check_fetch_guards()
    print(f"\nGET / -> {httpx.get(BASE, timeout=30).status_code}")
    missing = httpx.get(f"{BASE}/api/map-config", timeout=30)
    wrong = httpx.get(f"{BASE}/api/map-config", headers={"X-Demo-Passcode": "wrong"}, timeout=30)
    print(f"auth guards -> missing={missing.status_code} wrong={wrong.status_code}")
    if (missing.status_code, wrong.status_code) != (401, 401):
        raise SystemExit("Passcode guards did not return 401")

    config = httpx.get(f"{BASE}/api/map-config", headers=HEADERS, timeout=60)
    summary = {k: v for k, v in config.json().items() if k != "api_key"} if config.is_success else config.text
    print(f"GET /api/map-config -> {config.status_code} {summary} key_present={'api_key' in config.text}")

    first = drop_pin("Eiffel Tower", 48.8584, 2.2945)
    ask(first, "How tall is it and when was it built?")
    second = drop_pin("Open Pacific", -30.0, -140.0)
    ask(second, "What is the nearest land from here?")
    ask(first, "Is my first location session still available?")

    get_transport = httpx.get(
        f"{BASE}/api/describe/stream",
        headers=HEADERS,
        params={"latitude": 47.6205, "longitude": -122.3212},
        timeout=30,
    )
    print(f"\n=== removed GET stream -> {get_transport.status_code} ===")

    response = httpx.post(
        f"{BASE}/api/describe",
        headers=HEADERS,
        json={"latitude": 47.6205, "longitude": -122.3212},
        timeout=180,
    )
    print(f"\n=== POST /api/describe -> {response.status_code} ===")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
