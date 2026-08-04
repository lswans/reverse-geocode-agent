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

The data flow looks like this:

browser (MapLibre)  --click lat/lon-->  FastAPI  -->  Strands Agent
       ^             --follow-up Q-->   session       |- reverse_geocode    (Location Places)
       |                                             |- find_nearby_places (Location Places)
       |                                             |- search_wikipedia   (Wikipedia)
       '--- SSE: tool steps, then report or text ---  |- read_web_page      (the place's site)

  basemap tiles: Amazon Location Maps, via an API key served by /api/map-config

The agent picks its own path. ReverseGeocode returns only the single closest match. Click the Empire State Building and the closest match is a sightseeing-tour listing four meters away, so the agent calls SearchNearby, sees "Empire State Building" under a Tourist Attraction category eighteen metres out, and reports that instead. Click open ocean and it gets nothing back from either call, says so in one sentence, and offers no link.

The first turn is validated against a PlaceReport Pydantic model, so the UI never parses free text.

## Section 2: Build the App

Now that we know what we’re building, lets get the app running on your machine.

### Prerequisites

- Python 3.10+
- An AWS account
- The AWS CLI installed and configured

No infrastructure will be provisioned. This is a script you run on your own machine; it calls Bedrock and Amazon Location's APIs, so your account is billed per request, not per hour. Total cost of following this whole guide and executing the whole click-and-chat workflow will be about a fraction of a cent.

### Setup

First, run the commands below in your terminal. This will clone the repo from GitLab, create a virtual environment for Python's config for this project, and install dependencies.

git clone <https://gitlab.aws.dev/luswanso/Reverse-Geocode-Agent#>
cd reverse-geocode-agent
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

Next, configure your AWS credentials in the AWS CLI:

aws configure
aws sts get-caller-identity     # confirms it worked

Enable Bedrock model access in the Bedrock console under **Model access** > **Manage model access**. Turn on a Claude model (the default is us.anthropic.claude-haiku-4-5-20251001-v1:0). This is a one-time, per-account, per-region setting. Skipping it produces an access-denied error on your first click that looks like a credentials problem but isn't.

### Required IAM Permissions

| Service | Used for | Actions |
| --- | --- | --- |
| Bedrock | The agent's model | bedrock:InvokeModelWithResponseStream |
| Location Places | Reverse geocoding and nearby search | geo-places:ReverseGeocode, geo-places:SearchNearby |
| Location | Creating and reading the basemap key | geo:CreateKey, geo:DescribeKey, geo:DeleteKey |

### Create the Basemap API Key

Run the create_map_key.py file:

.venv/bin/python create_map_key.py

This creates a key named map-click-agent-demo-maps, restricted to geo-maps:* (map rendering only, it cannot run place searches), expiring automatically in 90 days. The server fetches it by name at runtime with its own credentials and hands it to the browser through /api/map-config, so no credential is baked into index.html.

### Optional Environment Overrides

export AWS_REGION="us-east-1"
export MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"
export MAP_STYLE="Standard"        # Monochrome, Hybrid, Satellite
export MAP_COLOR_SCHEME="Dark"     # Light
export PORT="8000"

## Section 3: The Code

Now, let's review the files that build the app. Three files carry the application logic. Here's what each one does and how to run it.

### agent.py

This file contains the Strands agent, its four tools, the system prompt, and the PlaceReport Pydantic schema that structures the first-turn output.

The four tools:

- **reverse_geocode** calls Amazon Location's ReverseGeocode API to identify the closest place to a given lat/lon.
- **find_nearby_places** calls SearchNearby to get surrounding points of interest when the closest match isn't the most relevant one.
- **search_wikipedia** fetches Wikipedia summaries for landmarks and historical context.
- **read_web_page** fetches a place's own website for prices, hours, or menus. It only accepts URLs that earlier tool output provided, and page text is passed to the model labelled as untrusted information rather than instructions.

The agent decides which tools to call and in what order. Every tool degrades to an error message the agent works around rather than crashing, so a throttled or failed lookup makes the summary vaguer instead of breaking the session.

In the UI, the outcome of the agent executing is as follows:

Run the agent standalone (no web layer), including one follow-up:

.venv/bin/python agent.py

### server.py

This is the FastAPI web server. It handles session management, map configuration, streaming endpoints, and static file hosting.

Key behaviors:

- Dropping a pin creates a session holding a Strands Agent with its own history. Follow-ups build on the tool results from the first turn instead of starting cold.
- Dropping a new pin discards the previous session. The browser clears the transcript, the server replaces the agent, and any question aimed at the old session gets a 409.
- There is exactly one live session at a time.
- Tool calls stream to the UI as Server-Sent Events, so the agent's reasoning steps are visible in real time.

The API endpoints:

| Endpoint | Behaviour |
| --- | --- |
| GET /api/map-config | Region, style, colour scheme, and the maps API key |
| GET /api/describe/stream?latitude=&longitude= | New pin. SSE step per tool call, then report with {session_id, report} |
| GET /api/ask/stream?session_id=&question= | Follow-up. SSE step and delta text chunks, then done. 409 if the pin was replaced |
| POST /api/describe | Non-streaming first turn, returns {session_id, report} |

Run the server:

.venv/bin/python server.py

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000/), click the map, and ask follow-ups. The first turn takes roughly 6 to 10 seconds; follow-ups land in 2 to 4 depending on whether the agent reaches for a tool.

The server binds to 0.0.0.0 and has no authentication. That is fine locally, but it should not be exposed to a network as-is. The maps key it serves is restricted to geo-maps, so it cannot be used to run place searches on your account.

### check_map.py

A diagnostic script that verifies the browser's map rendering path end to end. Run it if tiles are not appearing:

.venv/bin/python check_map.py

It checks:

- CDN assets are reachable
- The /api/map-config endpoint returns valid configuration
- The style descriptor loads
- A sample tile fetches successfully
- Sprites and glyphs resolve

If any of these fail, the map won't render in the browser, and this script tells you which link in the chain broke.

## Section 4: Conversation Lifecycle

The session model is simple. Dropping a pin creates a session. Follow-ups build on the tool results from the first turn: ask "how tall is it" and the agent reaches for Wikipedia, ask "what else is nearby" and it reuses the place data it already has, ask "what does it cost" and it reads the website that the place data handed it.

That last behavior only happens on follow-ups, never during the first report, and only for things a place publishes itself: prices, hours, tickets, menus. The agent may only fetch URLs that earlier tool output gave it. read_web_page refuses anything that is not a public web address, and page text is passed to the model labelled as untrusted information.

Dropping a new pin discards the session entirely. One session at a time is all a single-presenter demo needs.

## Section 5: Cleanup

The only persistent resource this project creates in your AWS account is the API key from the setup step. Everything else stops costing anything the moment you close the server.

To remove the API key:

aws location delete-key --key-name map-click-agent-demo-maps

Or leave it alone. An expired key stops working on its own and cannot be revived, so there's no ongoing risk in forgetting about it.

To confirm nothing else is left:

aws location list-keys

Any key you don't recognize by name is not something this project created.

## What's Next

This post covered the reverse-geocoding agent: clicking a map, identifying a place, and asking follow-up questions grounded in real tool output. The agent picks its own path through four tools, structures its first response with a Pydantic model, and streams its reasoning to the browser in real time.

In Part 2, we'll extend this foundation with forward geocoding (searching for places by name), route planning between pins, and isochrone visualization to show reachable areas within a given travel time. Same stack, same single-file deployment model, more spatial capability.

If you built the app and clicked around New York for a while, you already have everything you need to follow Part 2 when it drops. Keep the repo cloned and the venv intact.