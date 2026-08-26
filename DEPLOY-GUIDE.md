# Deploy & Demo Guide

Short version: deploy the UI on AWS with the agent stubbed out, film that, then your coworker
swaps in real agent code and you redeploy. Full task breakdown is in `DEPLOY-TASKS.md`.

## Prerequisites

- AWS CLI configured against the target account (`aws sts get-caller-identity` to confirm)
- SAM CLI and Docker installed and running
- Bedrock model access is **not** required yet — only needed once the real agent is enabled

## Part 1 — One-time account setup

```powershell
# Amazon Location map key (the map needs this even with the agent stubbed)
python create_map_key.py

# Store the demo passcode as a secret, never in code or the template
aws ssm put-parameter --name "/map-click-agent/passcode" --type SecureString --value "<choose one>"
```

## Part 2 — Deploy the stub demo (no agent functionality)

```powershell
sam build      # container build; Docker must be running
sam deploy --guided
```

On first deploy, SAM will prompt for a stack name, region, and confirm IAM role creation —
accept the defaults unless you have a naming convention to follow. Note the two stack outputs:
the **CloudFront URL** (what you'll open in the browser) and the **S3 bucket name**.

Upload the frontend to that bucket:

```powershell
aws s3 sync static/ s3://<bucket-name-from-output>/ --delete
```

Open the CloudFront URL and enter the passcode. Verify both interaction modes:

1. Click a pin and confirm the canned report and follow-up response.
2. On a physical phone, press **Follow me**, grant location permission, and confirm the live marker,
   accuracy circle, and status update. Walk far enough to produce another throttled report, then
   press **Stop tracking** and confirm the marker stops moving.
3. Background Safari briefly and return. The page must recover, but it does not promise background
   updates while suspended.

The stub makes no Bedrock or Location Places calls, so these checks validate CloudFront HTTPS,
browser geolocation, streaming transport, and responsive layout without model cost. This is what
to film first.

## Part 3 — Coworker enables the real agent

1. Write/extend the agent logic in `agent.py` (already has the five tools from the local demo).
2. Enable Bedrock model access once, in the target account/region: Bedrock console →
   **Model access** → **Manage model access** → turn on the Claude model in use.
3. In `template.yaml`, change the function's `AGENT_MODULE` environment variable from
   `agent_stub` to `agent`, and uncomment/add the IAM statements for
   `bedrock:InvokeModelWithResponseStream` and `geo-places:ReverseGeocode` / `SearchNearby`.
4. Redeploy:

   ```powershell
   sam build
   sam deploy
   ```

5. Reload the CloudFront URL (same passcode, same map) and confirm real reports and follow-ups
   come back — first click takes roughly 6-10 seconds, matching the local demo.

## Teardown (after filming)

```powershell
sam delete
aws location delete-key --key-name map-click-agent-demo-maps
aws ssm delete-parameter --name "/map-click-agent/passcode"
```

`sam delete` removes the CloudFront distribution, S3 bucket, Lambda function, and ECR repo it
created. Nothing in this project provisions anything that costs money while idle beyond the
Location API key, which expires on its own after 90 days regardless.
