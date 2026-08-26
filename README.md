# Geospatial on AWS, Part 1: Reverse-Geocoding with Strands and Location Service

## Introduction

Geospatial intelligence doesn't get the recognition it deserves in the tech community. Sure, AI and cloud infrastructure are changing the tech world at an unprecedented rate... but maps are just so COOL!

As a sector and as a tool for developers, geospatial intelligence has been evolving quickly. For one, launching satellites has become significantly cheaper over the last ten years, making scientific spacecraft that are the source of geospatial information cheaper. Secondly, Agentic AI has greatly streamlined both developing geospatial apps and simultaneously created an accessible method of automating systems. Now, a single person can deploy and monitor a service in a weekend that would’ve previously taken a team of developers weeks to deploy.

This has improved countless sectors, of course, but geospatial advancements impact our understanding of the physical world on a global scale. Satellite imagery can be parsed in real-time to identify areas suitable to develop a solar farm. Fleets of robotic semitrucks can be routed autonomously across geographic regions, and can have commands dispatched to them using an agent with an MCP server.

These are just some of the ambitions that excite me about this sector. For now, however, let’s start small. In a series of videos and blogs, I will show you how you can augment your software development toolbox by enabling location services for your apps. Today, we’re going to build a Strands agent that uses Amazon Location Service to help us explore New York City.

## Section 1: The App

First, and perhaps most importantly... what are we building? We’re going to build a map that we can talk to. Simple as that. When you complete the setup in the next section, it should look something like this:

This app is built with five components:

- **Strands Agents** ([https://strandsagents.com/](https://strandsagents.com/)) for the AI agent framework.
- **Amazon Bedrock** (Claude Haiku) as the underlying model.
- **Amazon Location Service** for reverse geocoding, nearby place search, and map tiles.
- **FastAPI** for the Python web server, which routes both local and outgoing API requests.
- **MapLibre GL JS** for rendering the interactive map in the browser, embedded directly in HTML.

A typical session goes like this:

1. Press **Follow me** and grant browser location access, or click a place you want to explore.
2. The map follows each fresh device fix immediately while the agent analyzes only meaningful movement.
3. The agent identifies the nearest notable place and sends back a report.
4. Ask follow-up questions about what's there, how tall it is, what's nearby, or what it costs.
5. Stop tracking or click elsewhere whenever you're ready to explore another location.

The data flow behind each of those steps looks like this:

```text
browser Geolocation --every fresh fix--> MapLibre live marker + accuracy + trail
          |
          '--meaningful movement--\
browser map click ---------------+--> FastAPI --> Strands Agent
browser follow-up question -------'    session     |- reverse_geocode    (Location Places)
       ^                                        |- find_nearby_places (Location Places)
       |                                        |- search_wikipedia   (Wikipedia)
       |                                        |- read_web_page      (the place's site)
       '-- POST SSE: steps, report, or text ----|- search_web         (capped fallback)

  basemap tiles: Amazon Location Maps, via an API key served by /api/map-config
```

The browser never waits for Bedrock to move the live marker. It coalesces movement while an agent turn is running and sends only the latest eligible point after that turn finishes. The agent then picks its own tool path. `ReverseGeocode` returns only the single closest match, so the agent also calls `SearchNearby` to avoid confidently naming a side entrance or nearby tour operator instead of the landmark the user meant.

The first turn is validated against a `PlaceReport` Pydantic model, so the UI never parses free text.

## Section 2: Clone the App

Now that we know what we’re building, lets get the app running on your machine.

### Prerequisites

- Python 3.10+
- An AWS account
- The AWS CLI installed and configured

No infrastructure will be provisioned. This is a script you run on your own machine; it calls Bedrock and Amazon Location's APIs, so your account is billed per request, not per hour. Total cost of following this whole guide and executing the whole click-and-chat workflow will be about a fraction of a cent.

### Setup

First, run the commands below in your terminal. This will clone the repo from GitLab, create a virtual environment for Python's config for this project, and install dependencies.

```bash
git clone https://gitlab.aws.dev/luswanso/Reverse-Geocode-Agent
cd reverse-geocode-agent
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Next, configure your AWS credentials in the AWS CLI:

```bash
aws configure
aws sts get-caller-identity     # confirms it worked
```

Enable Bedrock model access in the Bedrock console under **Model access** > **Manage model access**. Turn on a Claude model (the default is `us.anthropic.claude-haiku-4-5-20251001-v1:0`). This is a one-time, per-account, per-region setting. Skipping it produces an access-denied error on your first click that looks like a credentials problem but isn't.

### Required IAM Permissions

| Service | Used for | Actions |
| --- | --- | --- |
| Bedrock | The agent's model | `bedrock:InvokeModelWithResponseStream` |
| Location Places | Reverse geocoding and nearby search | `geo-places:ReverseGeocode`, `geo-places:SearchNearby` |
| Location | Creating and reading the basemap key | `geo:CreateKey`, `geo:DescribeKey`, `geo:DeleteKey` |

### Create the Basemap API Key

Run the `create_map_key.py` file:

```bash
.venv/bin/python create_map_key.py
```

This creates a key named `map-click-agent-demo-maps`, restricted to `geo-maps:*` (map rendering only, it cannot run place searches), expiring automatically in 90 days. The server fetches it by name at runtime with its own credentials and hands it to the browser through `/api/map-config`, so no credential is baked into `index.html`.

### Optional Environment Overrides

```bash
export AWS_REGION="us-east-1"
export MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"
export MAP_STYLE="Standard"        # Monochrome, Hybrid, Satellite
export MAP_COLOR_SCHEME="Dark"     # Light
export PORT="8000"
```

## Section 3: The Code

Now, let's review the files that build the app. Three files carry the application logic. Here's what each one does and how to run it.

### agent.py

This file contains the Strands agent, its five tools, the system prompt, and the `PlaceReport` Pydantic schema that structures the first-turn output.

The five tools:

- **reverse_geocode** calls Amazon Location's `ReverseGeocode` API to identify the closest place to a given lat/lon.
- **find_nearby_places** calls `SearchNearby` to get surrounding points of interest when the closest match isn't the most relevant one.
- **search_wikipedia** fetches Wikipedia summaries for landmarks and historical context.
- **read_web_page** fetches a specific website for prices, hours, or menus. It only accepts URLs that earlier tool output provided, and page text is passed to the model labelled as untrusted information rather than instructions.
- **search_web** is a last resort: an open web search the agent reaches for only after the other four have failed to surface a URL it needs, for example a transit station's fare page that Places and Wikipedia never link to. It's capped at two calls per conversation, enforced in code rather than left to the model's judgment, since an unbounded search tool is the one tool here whose cost isn't predictable per call.

The agent decides which tools to call and in what order. Every tool degrades to an error message the agent works around rather than crashing, so a throttled or failed lookup makes the summary vaguer instead of breaking the session.

In the UI, the outcome of the agent executing is as follows:

Run the agent standalone (no web layer), including one follow-up:

```bash
.venv/bin/python agent.py
```

### server.py

This is the FastAPI web server. It handles passcode authentication, session management, map configuration, streaming endpoints, and static file hosting.

Key behaviors:

- Dropping a pin or analyzing a live GPS fix creates a session holding a Strands Agent with its own history. Follow-ups build on the tool results from that report.
- Each analyzed location creates an independently addressable session. Selecting another location changes the browser's active conversation without invalidating another user's session.
- The process retains a bounded maximum of 100 recent sessions and evicts the oldest first.
- Tool calls stream to the UI as Server-Sent Events over a `fetch()` POST response.
- Coordinates, questions, and passcodes are carried in request bodies or headers, never API URLs.

The API endpoints:

| Endpoint | Behaviour |
| --- | --- |
| `GET /api/map-config` | Region, style, colour scheme, and the maps API key |
| `POST /api/describe/stream` | JSON `{latitude, longitude}`; starts a session and streams tool steps plus a report |
| `POST /api/ask/stream` | JSON `{session_id, question}`; streams a follow-up answer |
| `POST /api/describe` | Non-streaming first turn, returns `{session_id, report}` |

Run the server:

```bash
.venv/bin/python server.py
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000/), choose **Follow me** or click the map, and ask follow-ups. The first report takes roughly 6 to 10 seconds; follow-ups land in 2 to 4 depending on whether the agent reaches for a tool.

The server binds to `127.0.0.1` by default. The deployed version uses a passcode stored in SSM Parameter Store and CloudFront HTTPS. The maps key remains restricted to `geo-maps`, so it cannot run place searches on your account.

### Live location behavior

**Follow me** uses `navigator.geolocation.watchPosition()` with high-accuracy mode enabled. Every fresh GPS fix moves the MapLibre marker and accuracy circle immediately in the browser. Agent reports are intentionally slower and less frequent: the first fix must be accurate to roughly 75 metres, then a refresh requires at least 50 metres of meaningful movement and 15 seconds since the previous analysis. If the agent is busy, only the newest eligible fix is retained.

**Describe here now** bypasses the movement and time thresholds when the current fix is usable. **Stop tracking** calls `clearWatch()` and leaves the last location visible. GPS fixes and the bounded 100-point trail remain in browser memory only; only coordinates selected for analysis are sent to the server.

On Windows, use Chromium DevTools **Sensors** to simulate locations and routes. On iPhone, open the deployed CloudFront HTTPS URL in Safari and grant location access after pressing **Follow me**. Safari provides foreground tracking, but it can suspend updates when the tab is backgrounded or the screen locks. This web version does not claim background tracking.

### check_map.py

A diagnostic script verifies the browser's map rendering path end to end. Run it if tiles are not appearing:

```bash
.venv/bin/python check_map.py
```

It checks CDN assets, authenticated map configuration, the style descriptor, a sample tile, sprites, and glyphs.

## Section 4: Conversation Lifecycle

Explore mode preserves the original behavior: clicking a pin starts a conversation, and follow-ups reuse that location's tool results. Follow mode starts a new conversation whenever meaningful movement produces another report. A GPS fix received while an agent turn is running is coalesced rather than interrupting that turn.

Questions about prices, hours, tickets, or menus still use the place's own website only when the user asks. The agent may fetch only public URLs supplied by earlier tool output, and page content is treated as untrusted information.

Clicking the map exits Follow mode. Starting Follow mode clears the explored pin and requests location permission from an explicit button press. Sessions are isolated by unguessable IDs so separate viewers do not replace one another's active conversation; the in-memory store remains intentionally bounded for this demo.

## Section 5: Cleanup

The only persistent resource this project creates in your AWS account is the API key from the setup step. Everything else stops costing anything the moment you close the server.

To remove the API key:

```bash
aws location delete-key --key-name map-click-agent-demo-maps
```

Or leave it alone. An expired key stops working on its own and cannot be revived, so there's no ongoing risk in forgetting about it.

To confirm nothing else is left:

```bash
aws location list-keys
```

Any key you don't recognize by name is not something this project created.

## What's Next

This post covered the reverse-geocoding agent: clicking a map, identifying a place, and asking follow-up questions grounded in real tool output. The agent picks its own path through five tools, falling back to open web search only when the others come up empty, structures its first response with a Pydantic model, and streams its reasoning to the browser in real time.

In Part 2, we'll extend this foundation with forward geocoding (searching for places by name), route planning between pins, and isochrone visualization to show reachable areas within a given travel time. Same stack, same single-file deployment model, more spatial capability.

If you built the app and clicked around New York for a while, you already have everything you need to follow Part 2 when it drops. Keep the repo cloned and the venv intact.
