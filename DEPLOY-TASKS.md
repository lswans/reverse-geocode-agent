# AWS Migration Task List: Frontend-First Demo, Agent Added Later

Goal: host the map UI on AWS behind a passcode, with the backend running as a container-image
Lambda (via AWS Lambda Web Adapter, so `server.py`'s existing FastAPI/SSE code needs no rewrite).
Phase 0-4 ship a demo with **no agent calls** (stubbed responses only). Phase 5 is the coworker's
track: swap the stub for the real `agent.py` and redeploy.

IaC: AWS SAM. Auth: shared passcode, entered client-side, checked server-side against an SSM
`SecureString` (never shipped in the frontend bundle or the template).

## Phase 0 — Repo prep

- [x] Add `agent_stub.py`: same public interface as `agent.py` (`AWS_REGION`, `PlaceReport`,
      `build_agent`, `click_prompt`, `follow_up_prompt`), but `build_agent()` returns a fake
      agent that emits the same SSE-shaped events (`current_tool_use`, `data`, `result`) with
      canned text and a placeholder `PlaceReport`. Zero Bedrock or Location Places calls.
- [x] Add a one-line indirection to `server.py`: import `agent_stub` or `agent` based on an
      `AGENT_MODULE` env var (default `agent_stub`), instead of a hardcoded `from agent import ...`.
- [x] Add `Dockerfile` at repo root: slim Python base image, copy the Lambda Web Adapter binary
      to `/opt/extensions/`, `pip install -r requirements.txt`, copy `server.py`, `agent.py`,
      `agent_stub.py`, `static/`, `CMD ["python", "server.py"]`.
- [x] Add `.dockerignore` (`.venv`, `__pycache__`, `.git`, `blog_images`).

## Phase 1 — Passcode gate

- [x] Add a small gate screen to `static/index.html`: the passcode stays in page memory only
      and is sent as an `X-Demo-Passcode` header. Streaming clients use `fetch()` POST bodies,
      so passcodes, coordinates, and questions never enter URLs.
- [x] Add passcode validation to `server.py`: a FastAPI dependency checks the header against a
      value fetched from SSM Parameter Store (`SecureString`) and cached in memory. Returns 401
      on mismatch.
- [x] Keep location conversations isolated by unguessable session IDs in a bounded 100-session
      in-memory store. Reserved concurrency remains 1 for predictable demo capacity, not to make
      a process-global active session safe.

## Phase 2 — SAM template (`template.yaml`)

- [x] `AWS::Serverless::Function`, `PackageType: Image`, `FunctionUrlConfig:
      {AuthType: NONE, InvokeMode: RESPONSE_STREAM}`.
- [x] `ReservedConcurrentExecutions: 1` — bounds demo capacity and cost. `server.py` now
      isolates conversations by session ID in a bounded in-memory store; DynamoDB remains out of
      scope for this disposable demo, so sessions can still expire on a cold start.
- [x] Function environment: `AWS_LWA_INVOKE_MODE=RESPONSE_STREAM`, `HOST=0.0.0.0`, `PORT=8080`,
      `AGENT_MODULE=agent_stub`, `ALS_KEY_NAME`, `PASSCODE_PARAM_NAME`.
- [x] IAM policy on the function role: `geo:DescribeKey` and the required scoped
      `ssm:GetParameter` only in stub mode. Leave `bedrock:InvokeModelWithResponseStream` and
      `geo-places:*` disabled until Phase 5.
- [x] S3 bucket (block all public access, Origin Access Control) for `static/`.
- [x] CloudFront distribution: default behavior → S3 origin (frontend); `/api/*` behavior →
      Lambda Function URL origin, forwarding query strings and the passcode header.
- [x] Do **not** put the passcode value in the template. It's created out-of-band (Phase 3) and
      referenced by name only.

## Phase 3 — One-time account setup

- [ ] `aws ssm put-parameter --type SecureString` to store the passcode.
- [ ] Run `create_map_key.py` once against the target account/region — the map needs a real
      Amazon Location key even when the agent is stubbed.

## Phase 4 — Build, deploy, verify the stub demo

- [x] `sam build` (container build, needs Docker running locally).
- [ ] `sam deploy --guided` (first run creates the ECR repo, prompts for stack name/region,
      confirms IAM changes).
- [ ] Upload `static/` to the S3 bucket from the stack outputs (or wire this into the SAM build
      if preferred — see the deploy guide).
- [ ] Open the CloudFront URL, enter the passcode, confirm the map renders, click a pin, confirm
      the stubbed report and stubbed follow-up text appear.

## Phase 5 — Coworker's track: enable the real agent

- [ ] Extend/adjust tool logic in `agent.py` as needed.
- [ ] Enable Bedrock model access for the chosen Claude model in the target account/region
      (console, one-time, per account/region).
- [ ] In `template.yaml`: flip `AGENT_MODULE` to `agent`, add the `bedrock:InvokeModelWithResponseStream`
      and `geo-places:ReverseGeocode`/`SearchNearby` IAM statements.
- [ ] `sam build && sam deploy`.
- [ ] Re-verify end to end with real tool calls (expect the first turn to take ~6-10s now,
      matching the local demo timing).

## Phase 6 — Teardown (after filming)

- [ ] `sam delete`
- [ ] `aws location delete-key --key-name map-click-agent-demo-maps`
- [ ] `aws ssm delete-parameter` for the passcode
