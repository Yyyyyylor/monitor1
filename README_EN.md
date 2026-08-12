# Steam CS2 Inventory Monitor v2.4.0

> [简体中文](README.md) · **English**

Monitor Steam CS2 inventory changes, with storage-unit activity detection, daily history archives, and a web dashboard.

## Quick Start (Windows)

### 1. Double-click `install.bat` after extracting

It automatically: creates a virtual environment → installs dependencies → initializes the database

### 2. Edit `.env`

```
STEAM_IDS=YourSteam64ID
STEAM_HOSTS_OVERRIDE=127.0.0.1:443
```

> Steam++ requires **hosts acceleration** mode enabled to accelerate Steam Community

## Security and Deployment (v2.4.0)

- Store the dashboard password only as an scrypt verifier in `WEB_PASSWORD_HASH`. Use
  `scripts/migrate_web_password.py --password-env <environment-variable>` to update it without writing plaintext back to `.env`.
- Sessions use short-lived, revocable `HttpOnly` cookies. Logging out invalidates the current session immediately.
- Imports are validated for Steam IDs, file size, nesting depth, and inventory/history limits before one atomic database transaction.
- Docker publishes only `127.0.0.1:8080` by default and runs non-root with a read-only root filesystem. Put any public deployment behind HTTPS.

## WebUI and Image Loading (v2.4.0)

- The responsive operations-console UI adds calmer spacing, tiered stat cards, category pills, unified modals, and restrained motion. It automatically minimizes motion when the operating system requests reduced motion.
- Inventory images still load directly from Steam CDN, but now use six high-priority/auto first-screen requests followed by a bounded six-request queue (four on mobile), content-viewport prefetching, asynchronous decoding, and fade-in. This improves first-screen and scroll performance without risky unlimited CDN concurrency.

## Runtime Reliability and Responsiveness

- Status and user-list refreshes coalesce overlapping polling, live-update, and user-initiated requests. Fast switches between users, the management panel, and the recycle bin cancel stale page requests so old responses cannot replace the current view.
- The inventory renders before its independent change history and archive requests. Network, server, or deleted-snapshot errors show a retryable state, while an empty inventory still shows its normal empty state.
- Monitoring uses a bounded worker queue while preserving the configured concurrency limit and random jitter. Stopping monitoring or the web service waits for workers, archive jobs, WebSockets, and shared network resources to shut down cleanly.
- Successful runs no longer rewrite an already-clear failure state; bulk import preloads existing records to avoid repeated queries; Steam 429 waits honor `Retry-After` but are capped by `STEAM_RETRY_AFTER_MAX_SECONDS` (300 seconds by default).

### 3. Double-click `start.bat`

The browser opens `http://localhost:8080` automatically

---

## Features

### Web Dashboard
- 🎯 Category browsing (Rifles / Pistols / Knives / Gloves, etc.)
- 🖼️ Item image display
- ⚡ First-screen-prioritized, viewport-prefetched inventory images
- 🎨 Wear color bar + quality badge + StatTrak marker
- 🔍 Search by name, sort by wear / name
- 📋 Item detail modal (stickers / wear / pattern seed / phase)
- 🛒 One-click jump to Steam Market

### Monitoring
- ⏱️ Scheduled fetching (default 5 minutes)
- ⚡ Concurrent fetching (default 3-way concurrency + random jitter per user, to avoid tripping Steam rate limits)
- 📦 Storage-unit activity detection (based on `total_inventory_count` delta)
- 🔄 Swap detection (pairing items of the same type)
- 📜 Change-event recording (added / removed / modified / swapped)
- 📅 Daily archive snapshots (retained 90 days)
- 📢 Dual-channel notifications (user notifications + admin alerts)
- 🖥️ Runs 24/7 after the web server starts; can be stopped manually at any time

---

## Project Structure

```
├── install.bat           # One-click install
├── start.bat             # One-click launch
├── run_web.py            # Web dashboard entry
├── src/
│   ├── main.py           # Monitoring-only entry (no web)
│   ├── config.py         # pydantic-settings config
│   ├── models/item.py    # Core data models
│   ├── db/               # Database layer
│   ├── crawler/          # Paginated fetching + attribute parsing
│   ├── detector/         # Change detection + swap detection + activity classification
│   ├── notifications/    # Notification services
│   ├── scheduler/        # Monitoring scheduler
│   ├── health/           # Health checks
│   └── web/              # Web dashboard
│       ├── app.py        # API routes
│       └── static/
│           └── index.html
├── tests/                # Tests (71 cases)
├── requirements.lock     # Locked dependencies (reproducible install)
├── CHANGELOG.md          # Changelog
├── README.md             # Documentation (Chinese)
├── LICENSE               # MIT license
├── .env.example          # Config template
├── Dockerfile
└── docker-compose.yml
```

## Manual Run

```bash
pip install -e .
python run_web.py
```

## Docker

```bash
docker compose up -d
```

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## Dependency Locking

`requirements.lock` records the exact dependency versions (including transitive dependencies) verified in the current working environment, for reproducible installs and dependency vulnerability audits:

```bash
# Reproduce an install at the locked versions
pip install -r requirements.lock

# Dependency vulnerability scan
pip install pip-audit
pip-audit -r requirements.lock
```

> For the first deployment in a new environment, `pip install -e .` is still recommended; when you need to update the lock file, run `venv/Scripts/python -m pip freeze --exclude-editable > requirements.lock`.

---

## License

This project is licensed under the [MIT License](LICENSE).

Copyright (c) 2026 Cheney

When using, modifying, distributing, or redistributing this project, you must retain the above copyright notice and license terms (see the full [LICENSE](LICENSE) file).
