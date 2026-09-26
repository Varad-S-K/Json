import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


CHANNELS_URL = "https://raw.githubusercontent.com/Varad-S-K/Channel-Json/refs/heads/main/jtv_updated.json"
COOKIE_URL = "https://allinonereborn2.online/jstrweb2/cookies.json"
SPORTS_COOKIE_URL = "https://sonujson-v3.pages.dev/Data/sports.json"

PROJECT_DIR = Path(__file__).resolve().parent
M3U_FILE = PROJECT_DIR / "jtv.m3u"
JSON_FILE = PROJECT_DIR / "jtv.json"
LOCAL_CHANNELS_FILE = PROJECT_DIR / CHANNELS_URL.lstrip("/")

USER_AGENT = "Varad"


def get_json(url: str, default=None):
    """Fetch JSON from HTTP or local path. Returns `default` on 404/network errors."""
    if url.startswith("/"):
        try:
            with LOCAL_CHANNELS_FILE.open("r", encoding="utf-8") as source:
                return json.load(source)
        except Exception:
            return default if default is not None else {}

    fresh_url = f"{url}{'&' if '?' in url else '?'}t={int(time.time() * 1000)}"
    request = urllib.request.Request(
        fresh_url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                return default if default is not None else {}
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as err:
        print(f"Warning: Failed to fetch {url} ({err}). Using fallback values.")
        return default if default is not None else {}


def get_normal_cookie() -> str:
    """Fetch the cookie source and return only its cookie value."""
    data = get_json(COOKIE_URL, default="")

    if isinstance(data, str):
        return data.strip()

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("cookie"):
                return str(item["cookie"]).strip()
        return ""

    if isinstance(data, dict):
        return str(data.get("cookie") or "").strip()

    return ""


def get_sports_data():
    data = get_json(SPORTS_COOKIE_URL, default={})
    sports_urls = {}
    
    if not isinstance(data, dict):
        return sports_urls

    results = []
    results.extend(data.get("successful_results") or [])
    results.extend(data.get("failed_results") or [])

    for item in results:
        if not isinstance(item, dict) or not item.get("channel_id"):
            continue

        error_details = item.get("error_details") or {}
        final_url = item.get("final_url") or error_details.get("final_url") or ""
        if not final_url:
            continue

        final_url = re.sub(r"/output/", "/WDVLive/", final_url, count=1, flags=re.I)
        sports_urls[str(item["channel_id"])] = final_url

    return sports_urls


def extract_keys(channel):
    key_id = channel.get("keyId") or ""
    key = channel.get("key") or ""

    if not key_id and isinstance(channel.get("clearkey"), dict):
        key_id, key = next(iter(channel["clearkey"].items()), ("", ""))

    return key_id, key


def resolve_final_url(channel, sports_urls):
    channel_id = str(channel.get("id") or "")
    return sports_urls.get(channel_id) or channel.get("url") or ""


def create_channel_entry(channel, normal_cookie, sports_urls):
    channel_id = str(channel.get("id") or "")
    name = channel.get("name") or ""
    logo = channel.get("logo") or ""
    group = channel.get("group") or channel.get("category") or "Other"
    source_url = channel.get("url") or ""
    final_url = resolve_final_url(channel, sports_urls)
    key_id, key = extract_keys(channel)

    lines = [
        f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" '
        f'tvg-logo="{logo}" group-title="{group}",{name}'
    ]

    is_mpd = (
        channel.get("type") == "dash"
        or bool(re.search(r"\.mpd(?:\?|$)", final_url, re.I))
        or bool(re.search(r"\.mpd(?:\?|$)", source_url, re.I))
    )
    if is_mpd:
        lines.extend([
            "#KODIPROP:inputstream=inputstream.adaptive",
            "#KODIPROP:inputstream.adaptive.manifest_type=mpd",
        ])
        if key_id and key:
            lines.extend([
                "#KODIPROP:inputstream.adaptive.license_type=clearkey",
                f"#KODIPROP:inputstream.adaptive.license_key={key_id}:{key}",
            ])
        elif channel.get("license_url"):
            lines.extend([
                "#KODIPROP:inputstream.adaptive.license_type=clearkey",
                f"#KODIPROP:inputstream.adaptive.license_key={channel['license_url']}",
            ])

    if normal_cookie:
        lines.append(f"#EXTHTTP:{json.dumps({'cookie': normal_cookie})}")

    lines.extend([f"#EXTVLCOPT:http-user-agent={USER_AGENT}", final_url])
    return "\n".join(lines)


def build_channel_object(channel, normal_cookie, sports_urls):
    key_id, key = extract_keys(channel)
    return {
        "name": channel.get("name") or "",
        "id": str(channel.get("id") or ""),
        "category": channel.get("category") or channel.get("group") or "Other",
        "url": resolve_final_url(channel, sports_urls),
        "cookie": normal_cookie,
        "keyId": key_id,
        "key": key,
        "logo": channel.get("logo") or "",
    }


def write_atomically(path: Path, content: str):
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as output_file:
            output_file.write(content)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def generate_outputs():
    channels = get_json(CHANNELS_URL, default=[])
    if isinstance(channels, dict):
        channels = channels.get("channels") or channels.get("data") or []
    if not isinstance(channels, list):
        raise ValueError("Channel source must return a JSON array")

    normal_cookie = get_normal_cookie()
    sports_urls = get_sports_data()

    m3u_entries = [
        create_channel_entry(channel, normal_cookie, sports_urls)
        for channel in channels
    ]
    json_entries = [
        build_channel_object(channel, normal_cookie, sports_urls)
        for channel in channels
    ]

    m3u_content = "\n\n".join(["#EXTM3U", ""] + m3u_entries)
    json_content = json.dumps(json_entries, indent=2, ensure_ascii=False)
    return m3u_content, json_content


if __name__ == "__main__":
    try:
        m3u_content, json_content = generate_outputs()
        write_atomically(M3U_FILE, m3u_content)
        write_atomically(JSON_FILE, json_content)
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
        print(f"Update failed; existing files were kept: {error}")
        raise SystemExit(1)

    print(f"M3U saved to {M3U_FILE.name}")
    print(f"JSON saved to {JSON_FILE.name}")
