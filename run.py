#!/usr/bin/env python3
"""Process JSON crawl jobs from jobs/incoming (up to 3 in parallel)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from sde_crawler.crawler import SeedCrawler
from sde_crawler.job import load_job_json, merge_job
from sde_crawler.s3upload import upload_job_artifacts

ROOT = Path(__file__).resolve().parent
DEFAULT_WORKERS = 3
DEFAULT_INCOMING = ROOT / "jobs" / "incoming"


def _load_env_files() -> None:
    for path in (Path("/etc/sde/env"), ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    for name in (
        "crawl4ai",
        "httpx",
        "httpcore",
        "asyncio",
        "urllib3",
        "playwright",
        "websockets",
        "botocore",
        "boto3",
        "s3transfer",
        "pypdf",
    ):
        logging.getLogger(name).setLevel(logging.ERROR if not verbose else logging.DEBUG)


def _run_one(
    *,
    job_path: Path,
    bucket: str | None,
    verbose: bool,
    done_dir: Path,
    failed_dir: Path,
    log_dir: Path,
) -> tuple[str, int, float, str]:
    name = job_path.stem
    worker_log = log_dir / f"{name}.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    try:
        payload = load_job_json(job_path)
        cfg = merge_job(payload, root=ROOT)
        cfg["verbose"] = verbose
        coll_id = cfg["collection_id"]

        with worker_log.open("w", encoding="utf-8") as out:
            out.write(
                f"# job={job_path.name} collection_id={coll_id}\n"
                f"# seed={cfg['seed']}\n"
                f"# started={datetime.now(timezone.utc).isoformat()}\n\n"
            )
            out.flush()

            cfg["job_log_file"] = out
            cfg["crawl4ai_base"] = str(ROOT / ".crawl4ai_runtime" / coll_id)
            asyncio.run(SeedCrawler(cfg).run())

            docs = Path(cfg["output"])
            fails = Path(cfg["failures_log"])
            summary = fails.with_name(fails.stem + "_summary.json")

            if bucket:
                uploaded = upload_job_artifacts(
                    bucket=bucket,
                    coll_id=coll_id,
                    documents_path=docs,
                    failures_path=fails,
                    summary_path=summary,
                )
                out.write("\n# s3 " + ", ".join(f"{k}={v}" for k, v in uploaded.items()) + "\n")
            else:
                out.write("\n# s3 skipped (no bucket)\n")

            elapsed = time.monotonic() - t0
            out.write(f"\n# exit=0 elapsed_s={elapsed:.1f}\n")

        done_dir.mkdir(parents=True, exist_ok=True)
        dest = done_dir / job_path.name
        if dest.exists():
            dest = done_dir / f"{job_path.stem}_{int(time.time())}{job_path.suffix}"
        shutil.move(str(job_path), str(dest))
        return coll_id, 0, elapsed, "ok"

    except Exception as exc:
        elapsed = time.monotonic() - t0
        with worker_log.open("a", encoding="utf-8") as out:
            out.write(f"\n# ERROR: {exc!r}\n# exit=1 elapsed_s={elapsed:.1f}\n")
        failed_dir.mkdir(parents=True, exist_ok=True)
        dest = failed_dir / job_path.name
        if dest.exists():
            dest = failed_dir / f"{job_path.stem}_{int(time.time())}{job_path.suffix}"
        try:
            shutil.move(str(job_path), str(dest))
        except OSError:
            pass
        return name, 1, elapsed, repr(exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SDE crawler")
    parser.add_argument("--jobs-dir", default=str(DEFAULT_INCOMING))
    parser.add_argument("--job", help="single JSON job file")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--bucket", default=os.environ.get("SDE_S3_BUCKET", ""))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _load_env_files()
    _configure_logging(args.verbose)

    bucket = args.bucket.strip() or os.environ.get("SDE_S3_BUCKET", "").strip() or None
    workers = max(1, int(args.workers))

    if args.job:
        job_path = Path(args.job)
        if not job_path.is_absolute():
            job_path = ROOT / job_path
        if not job_path.is_file():
            print(f"Job not found: {job_path}", file=sys.stderr)
            return 1
        coll_id, code, elapsed, detail = _run_one(
            job_path=job_path,
            bucket=bucket,
            verbose=args.verbose,
            done_dir=ROOT / "jobs" / "done",
            failed_dir=ROOT / "jobs" / "failed",
            log_dir=ROOT / "logs" / "jobs",
        )
        status = "ok" if code == 0 else f"FAIL {detail}"
        print(f"  {coll_id}: {status}  ({elapsed / 60:.1f} min)", flush=True)
        return code

    incoming = Path(args.jobs_dir)
    if not incoming.is_absolute():
        incoming = ROOT / incoming
    jobs = sorted(incoming.glob("*.json"))
    if not jobs:
        print(f"No jobs in {incoming}", flush=True)
        return 0

    done_dir = incoming.parent / "done" if incoming.name == "incoming" else incoming / "done"
    failed_dir = incoming.parent / "failed" if incoming.name == "incoming" else incoming / "failed"

    print(f"Jobs: {len(jobs)}  workers={workers}  bucket={bucket or '(local only)'}", flush=True)

    results: list[tuple[str, int, float, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                _run_one,
                job_path=jp,
                bucket=bucket,
                verbose=args.verbose,
                done_dir=done_dir,
                failed_dir=failed_dir,
                log_dir=ROOT / "logs" / "jobs",
            ): jp
            for jp in jobs
        }
        for fut in as_completed(futs):
            coll_id, code, elapsed, detail = fut.result()
            status = "ok" if code == 0 else f"FAIL {detail}"
            print(f"  {coll_id}: {status}  ({elapsed / 60:.1f} min)", flush=True)
            results.append((coll_id, code, elapsed, detail))

    print("SUMMARY", flush=True)
    for coll_id, code, elapsed, _ in sorted(results):
        print(f"  {coll_id:<32} exit={code}  {elapsed / 60:.1f} min", flush=True)

    return 1 if any(c != 0 for _, c, _, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
