"""One-off helper: create the maps-only Amazon Location API key for this demo.

Run once, then put the printed key in ALS_API_KEY. Delete the key when you are
done with it:  aws location delete-key --key-name map-click-agent-demo-maps
"""

import datetime
import os

import boto3
from botocore.exceptions import ClientError

KEY_NAME = os.getenv("ALS_KEY_NAME", "map-click-agent-demo-maps")
# Must match the region the server runs in, since the key is scoped to it.
REGION = os.getenv("AWS_REGION", "us-east-1")

client = boto3.client("location", region_name=REGION)

try:
    client.create_key(
        KeyName=KEY_NAME,
        Description="Maps-only key for the map click agent demo",
        # Scoped to map rendering only. Places calls run server-side with IAM
        # credentials, so this key cannot be used to search or geocode.
        Restrictions={
            "AllowActions": ["geo-maps:*"],
            "AllowResources": [f"arn:aws:geo-maps:{REGION}::provider/default"],
        },
        ExpireTime=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90),
    )
    print(f"Created {KEY_NAME}")
except ClientError as exc:
    if exc.response["Error"]["Code"] != "ConflictException":
        raise
    print(f"{KEY_NAME} already exists, reusing it")

described = client.describe_key(KeyName=KEY_NAME)
print(f"region:  {REGION}")
print(f"expires: {described['ExpireTime']:%Y-%m-%d}")
print(f"key:     {described['Key']}")
