"""Create or reuse the maps-only Amazon Location API key for this demo.

The key value is intentionally not printed. The deployed server retrieves it by
name with its execution role.

The key is served to the browser, so anyone who opens the page can copy it.
Two restrictions limit what a copied key is worth:
  - AllowActions/AllowResources: map rendering only, no Places search or geocode.
  - AllowReferers: requests must come from pages on the listed origins.

Set ALS_ALLOWED_REFERERS to a comma-separated list (at most 5) of referer
patterns before running. The default covers the local server only; add your
CloudFront URL for a deployed demo, for example:

    $env:ALS_ALLOWED_REFERERS = "https://d111111abcdef8.cloudfront.net/*,http://127.0.0.1:8000/*"
    python create_map_key.py --update-restrictions

Without --update-restrictions an existing key is reused as-is. With it, the
existing key's restrictions are replaced by the ones below.
"""

import argparse
import datetime
import os

import boto3
from botocore.exceptions import ClientError

KEY_NAME = os.getenv("ALS_KEY_NAME", "map-click-agent-demo-maps")
# Must match the region the server runs in, since the key is scoped to it.
REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_REFERERS = "http://127.0.0.1:8000/*,http://localhost:8000/*"
MAX_REFERERS = 5


def allowed_referers() -> list[str]:
    raw = os.getenv("ALS_ALLOWED_REFERERS", DEFAULT_REFERERS)
    referers = [item.strip() for item in raw.split(",") if item.strip()]
    if not referers:
        raise SystemExit("ALS_ALLOWED_REFERERS is empty; list at least one referer pattern.")
    if len(referers) > MAX_REFERERS:
        raise SystemExit(f"Amazon Location allows at most {MAX_REFERERS} referers, got {len(referers)}.")
    return referers


def restrictions(referers: list[str]) -> dict:
    return {
        # Scoped to map rendering only. Places calls run server-side with IAM
        # credentials, so this key cannot be used to search or geocode.
        "AllowActions": ["geo-maps:*"],
        "AllowResources": [f"arn:aws:geo-maps:{REGION}::provider/default"],
        "AllowReferers": referers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update-restrictions",
        action="store_true",
        help="Replace an existing key's restrictions with the current referer list.",
    )
    args = parser.parse_args()

    referers = allowed_referers()
    client = boto3.client("location", region_name=REGION)

    try:
        client.create_key(
            KeyName=KEY_NAME,
            Description="Maps-only key for the map click agent demo",
            Restrictions=restrictions(referers),
            ExpireTime=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90),
        )
        print(f"Created {KEY_NAME}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConflictException":
            raise
        if args.update_restrictions:
            # ForceUpdate is required when the key was used in the last 7 days.
            client.update_key(KeyName=KEY_NAME, Restrictions=restrictions(referers), ForceUpdate=True)
            print(f"{KEY_NAME} already exists, restrictions updated")
        else:
            print(f"{KEY_NAME} already exists, reusing it unchanged (pass --update-restrictions to apply referers)")

    described = client.describe_key(KeyName=KEY_NAME)
    print(f"region:   {REGION}")
    print(f"expires:  {described['ExpireTime']:%Y-%m-%d}")
    print(f"referers: {', '.join(described['Restrictions'].get('AllowReferers') or ['(any)'])}")


if __name__ == "__main__":
    main()
