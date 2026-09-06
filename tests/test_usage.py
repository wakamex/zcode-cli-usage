import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import zcode_cli_usage as usage


# Actual quota response observed with API-key authentication on 2026-09-05.
LIVE_RESPONSE = {
    "code": 200, "success": True, "msg": "Operation successful",
    "data": {"level": "lite", "limits": [
        {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 2000,
         "currentValue": 0, "remaining": 2000, "percentage": 0},
        {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 10000,
         "currentValue": 43, "remaining": 9956, "percentage": 1,
         "nextResetTime": 1789097919995},
    ]},
}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {
            "XDG_CONFIG_HOME": self.temp.name, "XDG_CACHE_HOME": self.temp.name,
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_actual_response(self):
        data = usage.normalize(LIVE_RESPONSE)
        short, weekly = data["limits"]
        self.assertEqual(data["plan"], "lite")
        self.assertIsNone(short["resets_at"])
        self.assertEqual((weekly["used"], weekly["limit"], weekly["pct"], weekly["remaining"]), (43, 10000, 1, 9956))
        self.assertEqual(weekly["resets_at"], "2026-09-11T03:38:39.995000+00:00")

    def test_invalid_responses(self):
        for response in ({}, {"code": 401, "success": False}, {"code": 200, "success": True, "data": {"limits": []}}):
            with self.assertRaises(ValueError):
                usage.normalize(response)
        for value in (True, -1, float("nan"), "43", None):
            response = copy.deepcopy(LIVE_RESPONSE)
            response["data"]["limits"][0]["currentValue"] = value
            with self.assertRaises(ValueError):
                usage.normalize(response)

    def test_unknown_quota_preserved(self):
        response = copy.deepcopy(LIVE_RESPONSE)
        response["data"]["limits"][0].update(type="FUTURE_LIMIT", unit=42)
        item = usage.normalize(response)["limits"][0]
        self.assertIn("FUTURE_LIMIT", usage.label(item))

    def test_env_file_is_not_executed(self):
        path = Path(self.temp.name) / ".env"
        path.write_text('UNRELATED=ignored\nexport ZAI_API_KEY="literal$(false)" # comment\n')
        with patch.dict(os.environ, {"ZAI_API_KEY": ""}):
            self.assertEqual(usage.get_api_key(path), "literal$(false)")
        with patch.dict(os.environ, {"ZAI_API_KEY": "override"}):
            self.assertEqual(usage.get_api_key(path), "override")

    def test_cache_and_stale_fallback(self):
        data = usage.normalize(LIVE_RESPONSE)
        with patch.object(usage, "fetch_usage", return_value=data) as fetch:
            self.assertEqual(usage.load_usage(), data)
            self.assertEqual(usage.load_usage(cached=True), data)
            fetch.assert_called_once()
        with patch.object(usage, "fetch_usage", side_effect=RuntimeError("HTTP 401")):
            stale = usage.load_usage()
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["updated_at"], data["updated_at"])
        self.assertNotIn("stale", json.loads(usage.cache_path().read_text()))
        self.assertEqual(usage.cache_path().stat().st_mode & 0o777, 0o600)

    def test_entrypoints(self):
        for command in ([sys.executable, "-m", "zcode_cli_usage"], [str(Path(sys.executable).parent / "zcode-cli-usage")]):
            result = subprocess.run([*command, "--version"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["version"]
            self.assertEqual(result.stdout.strip(), expected)
            result = subprocess.run([*command, "daemon", "--interval", "0"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            env = {**os.environ, "ZAI_API_KEY": "", "ZCODE_USAGE_ENV_FILE": str(Path(self.temp.name) / "missing.env")}
            result = subprocess.run([*command, "refresh"], capture_output=True, env=env)
            self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
