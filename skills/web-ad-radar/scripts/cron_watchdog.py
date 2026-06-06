#!/usr/bin/env python3
"""JOSINT cron watchdog.

Designed to be invoked by system cron every 10 minutes. It checks the
last-run state and decides whether to trigger a bounded retry.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows local compatibility
    fcntl = None

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from radar.env import load_env
from radar.state_io import atomic_write_json, today_business_iso
from notify_feishu import format_failure_message, send_text


log = logging.getLogger("cron_watchdog")

MAX_RETRIES = 3


def _state_path(workspace: Path) -> Path:
    return workspace / "runtime" / "cron_state.json"


def _lock_path(workspace: Path) -> Path:
    return workspace / "runtime" / ".cron_state.lock"


def load_state(workspace: Path) -> dict:
    path = _state_path(workspace)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read cron state: %s", exc)
        return {}


def should_retry(state: dict, today: str) -> bool:
    """Decide if radar_cron.py should be run right now."""
    if not state:
        return False
    if state.get("last_run_status") == "success":
        return False
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    if state.get("last_run_date") != yesterday:
        return False
    return state.get("retry_count", 0) < MAX_RETRIES


def trigger_cron(workspace: Path, env: dict[str, str]) -> int:
    """Spawn radar_cron.py and bump retry_count in state."""
    lock_path = _lock_path(workspace)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                log.info("Another watchdog is already running, skipping: %s", exc)
                return 0

        state = load_state(workspace)
        state["retry_count"] = state.get("retry_count", 0) + 1
        atomic_write_json(_state_path(workspace), state)

        log.info("Triggering radar_cron.py (retry %d/%d)", state["retry_count"], MAX_RETRIES)
        cmd = [sys.executable, str(workspace / "scripts" / "radar_cron.py"), "--workspace", str(workspace)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        log.info("radar_cron.py finished: exit=%d", result.returncode)
        if result.stdout:
            log.info("stdout: %s", result.stdout[-500:])
        if result.stderr:
            log.info("stderr: %s", result.stderr[-500:])
        return result.returncode
    except subprocess.TimeoutExpired:
        log.error("radar_cron.py timed out after 900s")
        return 2
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        lock_fd.close()


def send_final_failure(env: dict[str, str], state: dict, today: str) -> None:
    """Send a final failure message when retry_count reaches MAX_RETRIES."""
    notify_open_id = env.get("FEISHU_NOTIFY_OPEN_ID", "")
    if not notify_open_id:
        return
    msg = format_failure_message(
        run_date=today,
        stage="retry_exhausted",
        error=f"已重试 {state.get('retry_count', 0)} 次仍失败。last_error={state.get('last_error', '?')}",
        log_path=str(Path(env.get("WORKSPACE", ".")) / "runtime" / "cron.log"),
    )
    try:
        send_text(notify_open_id, msg, env["FEISHU_APP_ID"], env["FEISHU_APP_SECRET"])
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send final-failure notification: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="JOSINT cron watchdog")
    parser.add_argument("--workspace", default=".", help="Skill workspace path")
    parser.add_argument("--env", default=".env", help="Env file path relative to workspace unless absolute")
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    workspace = Path(args.workspace).resolve()
    env_path = workspace / args.env if not Path(args.env).is_absolute() else Path(args.env)
    env = load_env(env_path)
    env["WORKSPACE"] = str(workspace)

    today = today_business_iso(env.get("JOSINT_TIMEZONE", "Asia/Shanghai"))
    state = load_state(workspace)
    log.info(
        "Watchdog: today=%s state=%s",
        today,
        {k: state.get(k) for k in ("last_run_date", "last_run_status", "retry_count")},
    )

    if state.get("last_run_status") == "success" and state.get("last_run_date") == today:
        log.info("Today's run already succeeded. No-op.")
        return

    if not should_retry(state, today):
        if state.get("retry_count", 0) >= MAX_RETRIES and state.get("last_run_status") != "success":
            send_final_failure(env, state, today)
        log.info("Nothing to retry.")
        return

    trigger_cron(workspace, env)


if __name__ == "__main__":
    main()
