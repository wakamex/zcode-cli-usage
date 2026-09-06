"""Read Z.ai Coding Plan quotas using an existing API key."""

import argparse
import json
import math
import os
from pathlib import Path
import shlex
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib.metadata import version

API_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
INTERVAL = 300


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "zcode-cli-usage/config.json"


def cache_path():
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "zcode-cli-usage/usage.json"


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".usage-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def get_api_key(env_file=None):
    key = os.environ.get("ZAI_API_KEY", "").strip()
    if key:
        return key
    source = env_file or os.environ.get("ZCODE_USAGE_ENV_FILE") or (read_json(config_path()) or {}).get("env_file")
    if source:
        try:
            for line in Path(source).expanduser().read_text().splitlines():
                name, sep, value = line.strip().removeprefix("export ").partition("=")
                if sep and name.strip() == "ZAI_API_KEY":
                    parts = shlex.split(value, comments=True)
                    if len(parts) == 1 and parts[0].strip():
                        return parts[0].strip()
                    raise ValueError("Invalid ZAI_API_KEY assignment")
        except (OSError, ValueError):
            raise RuntimeError("Could not read ZAI_API_KEY from the configured env file") from None
    raise RuntimeError("Set ZAI_API_KEY or configure an env file with zcode-cli-usage configure --env-file PATH")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid quota response: expected a nonnegative finite number")
    return value


def normalize(payload):
    if not isinstance(payload, dict) or payload.get("code") != 200 or payload.get("success") is not True:
        raise ValueError("Usage API reported an unsuccessful response")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("limits"), list) or not data["limits"]:
        raise ValueError("Invalid quota response: missing limits")
    limits = []
    for item in data["limits"]:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise ValueError("Invalid quota response: missing quota type")
        unit = number(item.get("unit"))
        count = number(item.get("number"))
        reset = item.get("nextResetTime")
        limits.append({
            "type": item["type"], "unit": unit, "number": count,
            "used": number(item.get("currentValue")),
            "limit": number(item.get("usage")),
            "remaining": number(item.get("remaining")),
            "pct": number(item.get("percentage")),
            "resets_at": datetime.fromtimestamp(number(reset) / 1000, timezone.utc).isoformat() if reset is not None else None,
        })
    return {"plan": data.get("level", "unknown"), "source": "api", "updated_at": datetime.now(timezone.utc).isoformat(), "limits": limits}


def fetch_usage(env_file=None):
    request = urllib.request.Request(API_URL, headers={"Authorization": "Bearer " + get_api_key(env_file), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return normalize(json.load(response))
    except urllib.error.HTTPError as exc:
        # Never include response bodies or request headers in diagnostics.
        raise RuntimeError(f"Usage request failed (HTTP {exc.code})") from None
    except urllib.error.URLError:
        raise RuntimeError("Could not connect to the usage API") from None


def load_usage(env_file=None, cached=False):
    previous = read_json(cache_path())
    if cached and previous:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(previous["updated_at"])).total_seconds()
            if 0 <= age < INTERVAL:
                return previous
        except (KeyError, TypeError, ValueError):
            pass
    try:
        data = fetch_usage(env_file)
    except (RuntimeError, ValueError, OverflowError):
        if not previous:
            raise
        return {**previous, "stale": True}
    atomic_json(cache_path(), data)
    return data


def label(item):
    if item["unit"] == 3:
        return f"{item['number']:g}-hour"
    if item["unit"] == 6 and item["number"] == 1:
        return "Weekly"
    return f"{item['type']} (unit {item['unit']:g}, number {item['number']:g})"


def display(data, compact=False):
    parts = []
    for item in data["limits"]:
        suffix = " credits" if item["type"] == "CREDIT_LIMIT" else f" {item['type']}"
        text = f"{label(item)}: {item['pct']:g}% ({item['used']:g}/{item['limit']:g}{suffix})"
        if item["resets_at"]:
            reset = datetime.fromisoformat(item["resets_at"]).astimezone()
            text += f" resets {reset:%Y-%m-%d %H:%M %Z}"
        parts.append(text)
    if data.get("stale"):
        parts.append(f"Cached, live fetch failed; updated {data['updated_at']}")
    print(" | ".join(parts) if compact else f"Plan: {data['plan']}\n" + "\n".join(parts))


def positive_interval(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("interval must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=version("zcode-cli-usage"))
    sub = parser.add_subparsers(dest="command")
    for name in ("status", "json", "refresh", "statusline"):
        sub.add_parser(name)
    daemon = sub.add_parser("daemon", help="Refresh the cache every five minutes")
    daemon.add_argument("--interval", "-i", type=positive_interval, default=INTERVAL)
    configure = sub.add_parser("configure", help="Remember an existing env file path")
    configure.add_argument("--env-file", required=True)
    args = parser.parse_args()
    try:
        if args.command == "configure":
            path = str(Path(args.env_file).expanduser().resolve())
            # Validate the selected file even if a key is present in the environment.
            existing = os.environ.pop("ZAI_API_KEY", None)
            try:
                get_api_key(path)
            finally:
                if existing is not None:
                    os.environ["ZAI_API_KEY"] = existing
            atomic_json(config_path(), {"env_file": path})
            print(f"Configured {config_path()}")
        elif args.command == "daemon":
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            backoff = args.interval
            while True:
                try:
                    data = fetch_usage()
                    atomic_json(cache_path(), data)
                    display(data, compact=True)
                    backoff = args.interval
                except (RuntimeError, ValueError, OSError, OverflowError) as exc:
                    print(str(exc), file=sys.stderr, flush=True)
                    backoff = min(backoff * 2, max(args.interval, 3600))
                sys.stdout.flush()
                time.sleep(backoff)
        elif args.command == "refresh":
            atomic_json(cache_path(), fetch_usage())
            print(f"Updated {cache_path()}")
        else:
            data = load_usage(cached=args.command == "statusline")
            if args.command == "json":
                print(json.dumps(data, indent=2))
            else:
                display(data, compact=args.command == "statusline")
    except (RuntimeError, ValueError, OSError, OverflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0
