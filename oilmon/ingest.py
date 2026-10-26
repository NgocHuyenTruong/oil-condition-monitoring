"""Pull new and updated samples from the lab API into a raw landing area.

Incremental: only records updated since the last successful pull are requested
(watermark kept in output/ingest_state.json). Every pull is saved untouched in
data/raw/, so cleaning can always be re-run from the original data.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

import requests

from . import config

RETRY_STATUS = {429, 500, 502, 503, 504}


def _get(session: requests.Session, url: str, params: dict, attempts: int = 5) -> dict:
    for attempt in range(attempts):
        try:
            resp = session.get(url, params=params, timeout=15)
        except requests.ConnectionError:
            if attempt == attempts - 1:
                raise
        else:
            if resp.status_code not in RETRY_STATUS:
                resp.raise_for_status()  # 401/404 etc. are not worth retrying
                return resp.json()
        time.sleep(0.2 * 2 ** attempt)
    resp.raise_for_status()
    raise RuntimeError(f"giving up on {url} after {attempts} attempts")


def fetch_samples(base_url: str, api_key: str, modified_since: str | None = None,
                  page_size: int = 100) -> tuple[list[dict], int]:
    """Return (records, pages_fetched) for all pages of /v1/samples."""
    session = requests.Session()
    session.headers["X-Api-Key"] = api_key
    records, page, calls = [], 1, 0
    while page:
        params = {"page": page, "page_size": page_size}
        if modified_since:
            params["modified_since"] = modified_since
        body = _get(session, f"{base_url}/v1/samples", params)
        calls += 1
        records.extend(body["data"])
        page = body["meta"]["next_page"]
    return records, calls


def _load_state() -> dict:
    return json.loads(config.STATE_FILE.read_text()) if config.STATE_FILE.exists() else {}


def pull(base_url: str = config.API_BASE_URL, api_key: str = config.API_KEY, full: bool = False) -> int:
    """Fetch records changed since the last pull, save them to data/raw/. Returns the count."""
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if full:
        for old in config.RAW_DIR.glob("pull_*.json"):
            old.unlink()
    state = {} if full else _load_state()
    records, pages = fetch_samples(base_url, api_key, state.get("watermark"))
    if records:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        (config.RAW_DIR / f"pull_{stamp}.json").write_text(json.dumps(records), encoding="utf-8")
        state["watermark"] = max(r["updated_at"] for r in records)
    state["last_pull"] = datetime.now().isoformat(timespec="seconds")
    config.STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"Ingest: {len(records)} new/updated records in {pages} page(s); watermark {state.get('watermark')}")
    return len(records)


def load_raw_store() -> list[dict]:
    """All records from every saved pull, oldest pull first.

    A record already saved by an earlier pull (e.g. after the watermark was reset) is our own
    re-download, not a lab problem, so it is skipped here. Duplicates inside one pull are kept,
    so clean.py can log them as the lab sending the same record twice.
    """
    records, earlier = [], set()
    for path in sorted(config.RAW_DIR.glob("pull_*.json")):
        pull = json.loads(path.read_text(encoding="utf-8"))
        records.extend(r for r in pull if json.dumps(r, sort_keys=True) not in earlier)
        earlier.update(json.dumps(r, sort_keys=True) for r in pull)
    return records
