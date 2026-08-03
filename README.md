# Map Click Agent

Click a point on a map and an agent reverse-geocodes it, then reports back with a
one-sentence summary, the coordinates, and a relevant link if the spot is a point of interest.
Ask follow-up questions about that pin in the text box and the agent answers with the pin's
history in context. Tool calls stream to the UI as they happen, so the agent's reasoning steps
are visible on camera.

Built with [Strands Agents](https://strandsagents.com/), Amazon Bedrock, Amazon Location
Service, FastAPI, and MapLibre GL JS. The basemap and all place lookups come from Amazon
Location; Wikipedia and the place's own website fill in what the map data does not carry.

New to any of these pieces? `BUILD-GUIDE.md` walks through boto3, Amazon Location, and Strands
from scratch, using this project as the worked example.

## How it works

```
browser (MapLibre)  --click lat/lon-->  FastAPI  -->  Strands Agent
       ^             --follow-up Q-->   session       |- reverse_geocode    (Location Places)
       |                                             |- find_nearby_places (Location Places)
       |                                             |- search_wikipedia   (Wikipedia)
       '--- SSE: tool steps, then report or text ---  |- read_web_page      (the place's site)

  basemap tiles: Amazon Location Maps, via an API key served by /api/map-config
```

The agent picks its own path, which matters because `ReverseGeocode` returns only the single
closest match. Click the Eiffel Tower and the closest match is a sightseeing-tour listing four
metres away, so the agent calls `SearchNearby`, sees `Eiffel Tower` under a Tourist Attraction
category eighteen metres out, and reports that instead. Click open ocean and it gets nothing
back from either call, says so in one sentence, and offers no link. The first turn is validated
against the `PlaceReport` Pydantic model, so the UI never parses free text.

## Conversation lifecycle

One pin, one conversation. Dropping a pin creates a session holding a Strands `Agent` with its
own history, so follow-ups build on the tool results from the first turn instead of starting
cold: ask "how tall is it" and the agent reaches for Wikipedia, ask "what else is nearby" and
it reuses the place data it already has, ask "what does it cost" and it reads the website that
the place data handed it.

That last one only happens on follow-ups, never during the first report, and only for things a
place publishes itself: prices, hours, tickets, menus. The agent may only fetch URLs that
earlier tool output gave it, `read_web_page` refuses anything that is not a public web address,
and page text is passed to the model labelled as untrusted information rather than instructions.

Dropping a new pin discards that session. The browser clears the transcript, the server
replaces the agent, and any question aimed at the old session gets a `409`. There is exactly
one live session at a time, which is all a single-presenter demo needs.

## Quickstart

Needs Python 3.10+, an AWS account, and the AWS CLI configured (`aws configure`).

```bash
git clone <this-repo-url>
cd reverse-geocode-agent

python -m venv .venv                          # Windows: use py -3 if python is not on PATH
.venv/Scripts/python -m pip install -r requirements.txt    # macOS/Linux: .venv/bin/python
.venv/Scripts/python create_map_key.py                     # one-off, creates the basemap key
.venv/Scripts/python server.py
```

Then open http://127.0.0.1:8000. Commands below use the Windows path; on macOS and Linux swap
`.venv/Scripts/python` for `.venv/bin/python`.

Before the first run you need Bedrock model access enabled and a handful of IAM permissions,
both covered next.

## Setup detail

### AWS permissions

| Service | Used for | Actions |
| --- | --- | --- |
| Bedrock | the agent's model | `bedrock:InvokeModelWithResponseStream` on the model you pick |
| Location Places | reverse geocoding and nearby search, server-side | `geo-places:ReverseGeocode`, `geo-places:SearchNearby` |
| Location | fetching the basemap key by name | `geo:DescribeKey` |

Enable model access for your chosen model in the Bedrock console first.

### The basemap key

The browser renders Amazon Location basemap tiles, which needs an Amazon Location API key.
Create one, scoped to maps only:

```powershell
.\.venv\Scripts\python.exe create_map_key.py
```

That makes a key named `map-click-agent-demo-maps` restricted to `geo-maps:*`, expiring in 90
days. The server fetches it by name at runtime with its own credentials and hands it to the
browser through `/api/map-config`, so no credential is baked into `index.html`. To use a key
you already have, set `ALS_API_KEY` instead and skip the script.

Delete the key when you are done with it:

```powershell
aws location delete-key --key-name map-click-agent-demo-maps
```

### Optional overrides

```powershell
$env:AWS_REGION        = "us-east-1"    # Bedrock, Places, and the map key all use this
$env:MODEL_ID          = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
$env:MAP_STYLE         = "Standard"     # Monochrome, Hybrid, Satellite
$env:MAP_COLOR_SCHEME  = "Dark"         # Light
$env:PORT              = "8000"         # if 8000 is already taken
```

## Run

```powershell
.\.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8000, click the map, then ask follow-ups. The first turn takes roughly
6 to 10 seconds; follow-ups land in 2 to 4 depending on whether the agent reaches for a tool.

Run the agent on its own, no web layer, including one follow-up:

```powershell
.\.venv\Scripts\python.exe agent.py
```

Drive the endpoints against a running server, including session invalidation:

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

Check the browser's map path end to end, if tiles are not appearing:

```powershell
.\.venv\Scripts\python.exe check_map.py
```

## API

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/map-config` | Region, style, colour scheme, and the maps API key |
| `GET /api/describe/stream?latitude=&longitude=` | New pin. SSE `step` per tool call, then `report` carrying `{session_id, report}` |
| `GET /api/ask/stream?session_id=&question=` | Follow-up. SSE `step` and `delta` text chunks, then `done`. `409` if the pin was replaced |
| `POST /api/describe` | Non-streaming first turn, returns `{session_id, report}` |

## Files

| File | Purpose |
| --- | --- |
| `agent.py` | The agent, its four tools, prompts, and the `PlaceReport` schema |
| `server.py` | FastAPI: session handling, map config, streaming endpoints, static hosting |
| `static/index.html` | MapLibre map, live step list, result card, follow-up transcript |
| `create_map_key.py` | One-off: creates the maps-only Amazon Location API key |
| `smoke_test.py` | Fetch guards, pins, follow-ups, a website read, a stale-session check |
| `check_map.py` | Verifies CDN assets, the config endpoint, style descriptor, a tile, sprites, glyphs |
| `BUILD-GUIDE.md` | Beginner walkthrough of boto3, Amazon Location, and Strands |

## Recording notes

- Successful place lookups are cached in memory for the life of the process. Rehearsing a
  click makes the real take faster, and it does not bill you twice.
- Amazon Location place names come from commercial map data, so a landmark can appear under an
  unfamiliar title, and clutter like a test POI sometimes shows up in nearby results. The
  agent's prompt tells it to go by category rather than take the closest hit, which is what
  gets it to the Eiffel Tower rather than the tour operator parked next to it.
- Wikimedia rejects requests whose `User-Agent` carries no contact details, with a `403`. Put
  your own project URL or email in `USER_AGENT` in `agent.py`.
- Good follow-ups for a demo: "how tall is it and when was it built" answers straight from what
  the agent already read, "what else is worth seeing nearby" reuses the place data, and "how
  much does it cost" on a business sends it to read that place's website live on camera.
- Pin a business rather than a monument if you want to show the website read. Monuments tend to
  be answerable from Wikipedia, so the agent has no reason to fetch a page.
- Every tool degrades to an error message the agent works around rather than crashing, so a
  throttled or failed lookup makes the summary vaguer instead of breaking the take.
- The server binds to `127.0.0.1` and has no authentication. That is fine locally, but it
  should not be exposed to a network as-is. The maps key it serves is restricted to `geo-maps`,
  so it cannot be used to run place searches on your account.
