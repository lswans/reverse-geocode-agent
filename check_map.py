"""Verify the browser's map path: CDN assets, config endpoint, style, one tile."""

import re

import httpx

# A missing CDN version returns a 404 as text/plain, which the browser refuses to
# run and which shows up only as a black map, so check the URLs the page asks for.
page = httpx.get("http://127.0.0.1:8000/", timeout=30)
page.raise_for_status()
for url in re.findall(r'https://cdn\.jsdelivr\.net/[^"\']+', page.text):
    asset = httpx.get(url, timeout=30, follow_redirects=True)
    kind = asset.headers.get("content-type", "").split(";")[0]
    expected = "text/css" if url.endswith(".css") else "application/javascript"
    status = "ok" if asset.is_success and kind == expected else f"BAD ({kind})"
    print(f"{status:16} {asset.status_code} {url}")

config = httpx.get("http://127.0.0.1:8000/api/map-config", timeout=60)
print(f"GET /api/map-config -> {config.status_code}")
config.raise_for_status()
data = config.json()
print({k: (v[:14] + "..." if k == "api_key" else v) for k, v in data.items()})

style_url = (
    f"https://maps.geo.{data['region']}.amazonaws.com/v2/styles/{data['style']}"
    f"/descriptor?key={data['api_key']}&color-scheme={data['color_scheme']}"
)
style = httpx.get(style_url, timeout=30)
print(f"style descriptor -> {style.status_code} {style.json().get('name')}")
style.raise_for_status()

tile_url = style.json()["sources"]["aws"]["tiles"][0]
tile_url = tile_url.replace("{z}", "13").replace("{x}", "2411").replace("{y}", "3080")
tile = httpx.get(tile_url, timeout=30)
print(f"sample tile -> {tile.status_code} {len(tile.content)} bytes")

for suffix in (".png", ".json"):
    sprite = httpx.get(style.json()["sprite"] + suffix, timeout=30)
    print(f"sprite{suffix} -> {sprite.status_code} {len(sprite.content)} bytes")

glyphs = style.json()["glyphs"].replace("{fontstack}", "Amazon%20Ember%20Regular").replace("{range}", "0-255")
print(f"glyphs -> {httpx.get(glyphs, timeout=30).status_code}")
