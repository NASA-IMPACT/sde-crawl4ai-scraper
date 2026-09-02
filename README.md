# SDE Scraper : Crawl4AI v1

Seed URL in → same-site documents out. Optional `urls` list for targeted fetches.

> Detailed documentation and workflow diagrams for this v1 scraper are in the [SDE Crawler design notes](https://docs.google.com/document/d/1C-ntJbdYMe-yTp4MVlO7p7jhkqxbqapETMvGOag6zMc/edit?pli=1&tab=t.0).

A generic web crawler for the Science Discovery Engine (SDE). Given a public seed URL, it crawls that site with Crawl4AI (Playwright/Chromium), extracts readable text, and writes JSON documents plus failure logs. Optional upload to Amazon S3.

```text
jobs/incoming/*.json  →  watcher  →  run.py (≤3 sites)  →  documents + failures  →  S3
```

## Features

- Same-site BFS crawl (apex + `www` by default)
- Optional URL list (no BFS)
- HTML/JS via Chromium; PDF and plain text via HTTP extract
- Skips binaries, archives, media, FTP trees, and similar non-document assets
- Job queue is a filesystem folder (no SQS)
- Up to 3 collections in parallel; 1 URL at a time per site
- Documents checkpointed to S3 during the run (JSON array)
- Per-URL timeout (default 180s); optional post-pass retry for transient failures
- Failures logged as JSONL during the run; summary written at the end

## Requirements

- Python 3.11+
- Playwright Chromium (`playwright install chromium`)
- Linux with `inotify-tools` for the inbox watcher (On macOS, run `python run.py` directly)

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

mkdir -p jobs/incoming
cp jobs/examples/ligo.json jobs/incoming/
# or: echo '{"seed":"https://www.ligo.org","max_pages":20}' > jobs/incoming/test.json

python run.py
```

Outputs:

- `output/collections/<id>.json`
- `logs/collections/<id>_failures.jsonl`
- `logs/collections/<id>_failures_summary.json`
- `logs/jobs/<id>.log`

## Job format

Either `seed` or `urls` is required. Omitted fields use defaults; present fields override them.

```json
{"seed": "https://www.ligo.org"}
```

```json
{"seed": "https://espo.nasa.gov/", "collection_id": "espo", "max_pages": 5000}
```

```json
{"collection_id": "targeted", "urls": ["https://www.ligo.org/", "https://aurorasaurus.org/"]}
```

`urls`: fetch only those URLs (no BFS). Set `collection_id` when hosts differ.

| Field | Default | Description |
|-------|---------|-------------|
| `seed` | required unless `urls` | Start URL for BFS |
| `urls` | | Targeted list; no link discovery |
| `max_pages` | `100000` | Document ceiling (hard max 100000) |
| `depth_limit` | unlimited | Optional hop limit (BFS only) |
| `delay` | `0.25` | Seconds between requests |
| `concurrent_requests` | `1` | Parallelism within one job |
| `url_timeout` | `180` | Seconds per URL fetch+extract |
| `retry_failures` | `false` | After the first pass, retry retryable failures with `retry_url_timeout` |
| `retry_url_timeout` | `300` | Timeout for the post-pass retry |
| `checkpoint_pages` | `100` | S3 document flush every N docs |
| `checkpoint_seconds` | `300` | S3 document flush interval |
| `obey_robots` | `false` | Honor robots.txt when true |
| `include_subdomains` | `false` | Crawl `*.apex` when true |
| `collection_id` | derived from host | Output / S3 object name |

Example job files: `jobs/examples/`.

## Runtime architecture

```text
jobs/incoming/*.json
        │
        ▼
watch_inbox.sh          # inotify on *.json; flock → one run.py at a time
        │
        ▼
run.py                  # loads all inbox JSON; ThreadPoolExecutor(max_workers=3)
        │
        ├─ collection A (BFS, 1 URL at a time)
        ├─ collection B
        └─ collection C
                │
                ▼
        local output/logs → S3 (if configured) → jobs/done|failed
```

- **Queue:** the `jobs/incoming/` directory. There is no external message broker.
- **Watcher:** `watch_inbox.sh` uses `inotifywait` (`close_write` / `moved_to`). It does not crawl; it starts `run.py` under `flock`.
- **Concurrency:** one `run.py` process (via flock); up to three collections inside that process; when one finishes, the next inbox job in that batch starts.
- **S3:** set `SDE_S3_BUCKET`, or provide `/etc/sde/env` / `.env`. Local documents are removed after a successful upload. Objects:
  - `scraped_collections/<id>.json`
  - `failure_logs/<id>_failures.jsonl`
  - `failure_logs/<id>_failures_summary.json`

### Inbox watcher (Linux / EC2)

```bash
chmod +x watch_inbox.sh
nohup ./watch_inbox.sh >> logs/watch.log 2>&1 &
```

Drop `*.json` into `jobs/incoming/` to enqueue work.

### Document shape

```json
{
  "url": "https://example.com/page",
  "title": "...",
  "full_text": "...",
  "content_type": "text/html",
  "seed": "https://example.com",
  "host": "example.com",
  "depth": 1
}
```

### Failure reasons (examples)

| Reason | Meaning |
|--------|---------|
| `http_404` / `http_403` | HTTP error |
| `empty_extract` | Fetched but no usable text |
| `download_error` | File download failed |
| `crawl_unsuccessful` | Browser navigation / crawl error |
| `challenge_*` | Bot / CAPTCHA page (logged, not solved) |
| `url_timeout` | Fetch+extract exceeded `url_timeout` |
| `skipped_type` | URL list item skipped (asset/binary) |

## Deploy (AWS)

CDK stack + EC2 install scripts live under `infra/` and `scripts/`. See **[DEPLOY.md](DEPLOY.md)**.

## Layout

```text
run.py                 Process jobs/incoming (≤3 workers) → crawl → S3
watch_inbox.sh         inotify → flock → run.py
sde_crawler/           Crawl policy and I/O
scripts/               EC2 install / job submit helpers
infra/                 CDK (EC2, S3, IAM, VPC)
jobs/incoming|done|failed
jobs/examples/         Sample job JSON files
output/  logs/         Local artifacts (gitignored contents)
```
