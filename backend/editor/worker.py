"""
Worker process: `python -m editor.worker` (run from backend/).

Claims jobs from the queue one at a time, keeps a heartbeat going while each
runs, and records success or failure. Run several processes for parallelism.
"""

import argparse
import logging
import threading
import time
import traceback

from . import jobs
from .tasks import HANDLERS, JobError

log = logging.getLogger("editor.worker")

HEARTBEAT_SECONDS = 15
PROGRESS_MIN_INTERVAL = 1.0


def run_job(job: dict) -> None:
    handler = HANDLERS.get(job["kind"])
    if handler is None:
        jobs.fail(job["id"], f"Unknown job kind: {job['kind']}")
        return

    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            jobs.heartbeat(job["id"])

    last = {"t": 0.0}

    def progress(fraction: float, message: str | None = None) -> None:
        now = time.monotonic()
        if message is not None or now - last["t"] >= PROGRESS_MIN_INTERVAL or fraction >= 1:
            last["t"] = now
            jobs.heartbeat(job["id"], fraction, message)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        result = handler(job, progress)
        jobs.complete(job["id"], result)
        log.info("job %s (%s) succeeded", job["id"], job["kind"])
    except JobError as exc:
        jobs.fail(job["id"], str(exc))
        log.warning("job %s (%s) failed: %s", job["id"], job["kind"], exc)
    except Exception as exc:  # unexpected: keep the trace in the log, a summary in the job
        jobs.fail(job["id"], f"{type(exc).__name__}: {exc}")
        log.error("job %s (%s) crashed\n%s", job["id"], job["kind"], traceback.format_exc())
    finally:
        stop.set()
        t.join(timeout=1)


def run_once(kinds: tuple[str, ...] | None = None) -> bool:
    """Process at most one job. Returns True if a job was run."""
    jobs.recover_stale()
    job = jobs.claim(kinds)
    if job is None:
        return False
    log.info("job %s (%s) started, attempt %s", job["id"], job["kind"], job["attempts"])
    run_job(job)
    return True


def run_until_empty(kinds: tuple[str, ...] | None = None) -> int:
    n = 0
    while run_once(kinds):
        n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Video editor job worker")
    parser.add_argument("--kinds", help="Comma-separated job kinds to handle (default: all)")
    parser.add_argument("--poll", type=float, default=1.0, help="Seconds between polls when idle")
    args = parser.parse_args()
    kinds = tuple(k.strip() for k in args.kinds.split(",")) if args.kinds else None

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("worker started (kinds: %s)", ", ".join(kinds) if kinds else "all")
    from .tasks import queue_missing_proxies, refresh_video_dimensions

    fixed = refresh_video_dimensions()
    if fixed:
        log.info("corrected stored dimensions of %d rotated video(s)", fixed)
    queued = queue_missing_proxies()
    if queued:
        log.info("queued preview copies for %d earlier upload(s)", queued)
    try:
        while True:
            if not run_once(kinds):
                time.sleep(args.poll)
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
