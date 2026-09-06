# zcode-cli-usage

Monitor your Z.ai Coding Plan's 5-hour and weekly quotas from the terminal. Uses your existing `ZAI_API_KEY`, with no browser login or external Python dependencies.

```text
Plan: lite
5-hour: 0% (0/2000 credits)
Weekly: 1% (43/10000 credits) resets 2026-09-10 23:38 EDT
```

## Install

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv tool install zcode-cli-usage
```

## Authentication

Export `ZAI_API_KEY` in your environment, or point the tool at an existing `.env` file:

```sh
zcode-cli-usage configure --env-file /path/to/.env
zcode-cli-usage
```

Configuration stores only the file path under `~/.config/zcode-cli-usage/config.json`. The tool reads `ZAI_API_KEY` from that file without executing shell code. It supports plain or quoted assignments, optional `export`, and comments; shell variable expansion is not supported. Credentials are never included in usage output or the cache.

An exported `ZAI_API_KEY` takes precedence. Set `ZCODE_USAGE_ENV_FILE` to override the configured file path. `XDG_CONFIG_HOME` and `XDG_CACHE_HOME` are supported.

## Commands

```sh
zcode-cli-usage              # Fetch and display current quotas
zcode-cli-usage status       # Same as above
zcode-cli-usage json         # Normalized JSON, including all reported quotas
zcode-cli-usage refresh      # Fetch and update the cache; fail if unavailable
zcode-cli-usage daemon       # Refresh every five minutes, with error backoff
zcode-cli-usage statusline   # Compact output; reuse a cache less than five minutes old
zcode-cli-usage --version
python -m zcode_cli_usage --help
```

Successful fetches write `~/.cache/zcode-cli-usage/usage.json` atomically. Status, JSON, and statusline commands fall back to cached data when a request fails. Text output explicitly labels stale data; JSON includes `"stale": true` and the original `updated_at`. `refresh` exits unsuccessfully when it cannot fetch current data.

Reset times display in your local timezone. The API's reported percentages and remaining balances are preserved, because rounded credit values can differ from arithmetic on the displayed used and total values. An absent reset time stays absent.

## Background refresh on Linux

After configuring authentication, save this as `~/.config/systemd/user/zcode-cli-usage.service`, adjusting the executable path if needed:

```ini
[Unit]
Description=Z.ai Coding Plan quota monitor
After=network-online.target

[Service]
ExecStart=%h/.local/bin/zcode-cli-usage daemon
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now zcode-cli-usage.service
```

## API

Quota data comes from `GET https://api.z.ai/api/monitor/usage/quota/limit`, authenticated with `Authorization: Bearer <ZAI_API_KEY>`. API-key authentication was verified against the live service on September 5, 2026. The website's separate `credit-usage/usage-detail` endpoint supplies historical reports and is not used by this tool.

The normalized JSON maps `currentValue` to `used`, `usage` to `limit`, `percentage` to `pct`, and `nextResetTime` from Unix milliseconds to a UTC `resets_at` string. It retains each quota's `type`, `unit`, `number`, and `remaining`. The current API uses unit `3` for hours and `6` for weeks.

## Development

```sh
uv run --locked python -m unittest discover -s tests
uv run --locked zcode-cli-usage --help
```
