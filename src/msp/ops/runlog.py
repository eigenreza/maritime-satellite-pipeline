"""Run log, freshness and alerts.

Every pipeline task writes one row per attempt to the `run_log` table: status, attempt number, run
time in seconds, records and bytes. Rows carry the replay clock (a time inside the study period), so
the pipeline can replay the period as if it were running live, round the clock.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from datetime import datetime, timedelta

from msp import db

log = logging.getLogger("msp")

# Freshness rules per source, in hours, measured against the replay clock
FRESHNESS_LIMIT_H = {
    "sentinel-1": 132.0,       # selected passes repeat weekly with gaps up to 5 days; alert after 5.5 days
    "sentinel-2": 96.0,        # an optical acquisition at least every 4 days
    "sentinel-3": 48.0,        # sea surface temperature at least every 2 days
    "ecmwf-forecast": 30.0,    # 2 runs a day, published ~8 h later, fetched every 6 h: normally <= 24 h old
    "metno-waves": 6.0,        # hourly model fields
    "ais": 1.0,                # AIS positions within the last hour
}


def _attempt() -> int:
    try:
        from prefect.runtime import task_run

        return int(task_run.run_count or 1)
    except Exception:
        return 1


def record(flow: str, task: str, status: str, replay_time: datetime | None = None, attempt: int = 1,
           duration_s: float | None = None, records: int | None = None, nbytes: int | None = None,
           message: str | None = None) -> None:
    try:
        db.execute("""INSERT INTO run_log (flow, task, replay_time, status, attempt, duration_s, records, bytes, message)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                   (flow, task, replay_time, status, attempt, duration_s, records, nbytes, (message or "")[:500]))
    except Exception as exc:  # the run log must never break the pipeline
        log.warning("run log write failed: %s", exc)


def logged(flow: str, max_attempts: int = 1):
    """Decorator: write a run_log row for every attempt of the wrapped task.

    The wrapped function may return a dict with 'records' and 'bytes'. The keyword argument
    `replay_time` (if present) is stored as the replay clock.
    """
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            attempt = _attempt()
            t0 = time.time()
            # Prefect may pass keyword parameters positionally, so bind them to find the replay clock
            replay = sig.bind_partial(*args, **kwargs).arguments.get("replay_time")
            try:
                out = fn(*args, **kwargs)
            except Exception as exc:
                status = "retried" if attempt < max_attempts else "failed"
                record(flow, fn.__name__, status, replay, attempt, round(time.time() - t0, 2),
                       message=f"{type(exc).__name__}: {exc}")
                raise
            info = out if isinstance(out, dict) else {}
            record(flow, fn.__name__, "recovered" if attempt > 1 else "completed", replay, attempt,
                   round(time.time() - t0, 2), info.get("records"), info.get("bytes"), info.get("message"))
            return out
        return wrapper
    return deco


def check_freshness(source: str, replay_time: datetime, latest: datetime | None,
                    against: datetime | None = None) -> dict:
    """Compare the newest data time with the replay clock (or with `against`, the moment the data is
    needed, such as a radar pass for AIS); store the result and alert when stale."""
    limit = FRESHNESS_LIMIT_H[source]
    if latest is None:
        status, age = "missing", None
    else:
        age = max(0.0, ((against or replay_time) - latest) / timedelta(hours=1))
        status = "fresh" if age <= limit else "stale"
    db.execute("""INSERT INTO freshness (source, replay_time, latest_data, age_hours, limit_hours, status)
                  VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (source, replay_time) DO UPDATE SET latest_data = EXCLUDED.latest_data,
                  age_hours = EXCLUDED.age_hours, limit_hours = EXCLUDED.limit_hours, status = EXCLUDED.status""",
               (source, replay_time, latest, age, limit, status))
    if status != "fresh":
        alert(source, f"{source} is {status}" + (f": newest data {age:.1f} h old (limit {limit:.0f} h)" if age else ""),
              replay_time)
    return {"source": source, "status": status, "age_hours": age}


def alert(source: str, message: str, replay_time: datetime | None = None) -> None:
    """Alerts are log entries (warning level) plus a run_log row with status 'alert'."""
    log.warning("ALERT %s: %s", source, message)
    record("ops", f"alert:{source}", "alert", replay_time, message=message)
