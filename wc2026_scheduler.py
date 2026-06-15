#!/usr/bin/env python3
"""
WC 2026 pre-kickoff scheduler.

Fetches FotMob fixture times, installs Windows scheduled tasks to run the
live prediction pipeline 60 minutes before each match, or runs as a daemon.

Usage:
    python wc2026_scheduler.py refresh          # cache fixture schedule
    python wc2026_scheduler.py install          # Windows Task Scheduler (T-60min)
    python wc2026_scheduler.py uninstall        # remove scheduled tasks
    python wc2026_scheduler.py prekickoff --match-id 4667798
    python wc2026_scheduler.py daemon             # poll every 5 min (keep running)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Project root = this file's directory
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import wc2026_simulation as sim  # noqa: E402

TASK_PREFIX = "WC2026_PreKickoff_"
PYTHON = sys.executable
SCHEDULER_SCRIPT = Path(__file__).resolve()


def refresh_schedule() -> List[dict]:
    """Download FotMob WC schedule and save locally."""
    print("[SCHEDULER] Fetching World Cup fixture schedule from FotMob …")
    schedule = sim.fetch_fotmob_world_cup_schedule()
    sim.save_fixture_schedule(schedule)
    upcoming = [f for f in schedule if not f.get("finished")]
    print(f"[SCHEDULER] Saved {len(schedule)} fixtures "
          f"({len(upcoming)} remaining) → {sim.FIXTURE_SCHEDULE_JSON}")
    return schedule


def _kickoff_local(fix: dict) -> Optional[datetime]:
    raw = fix.get("kickoff_utc")
    if not raw:
        return None
    kickoff = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return kickoff.astimezone()


def _run_time_local(fix: dict) -> Optional[datetime]:
    kickoff = _kickoff_local(fix)
    if not kickoff:
        return None
    return kickoff - timedelta(minutes=sim.PREKICKOFF_MINUTES)


def _task_name(match_id: int) -> str:
    return f"{TASK_PREFIX}{match_id}"


def install_windows_tasks(force: bool = False) -> int:
    """Create one Windows scheduled task per upcoming match (T-60 min local)."""
    if os.name != "nt":
        print("[SCHEDULER] Task install is Windows-only. Use `daemon` on other OS.")
        return 0

    schedule = sim.load_fixture_schedule()
    if not schedule:
        schedule = refresh_schedule()

    now = datetime.now().astimezone()
    created = 0

    for fix in schedule:
        if fix.get("finished"):
            continue
        run_at = _run_time_local(fix)
        if not run_at or run_at <= now:
            continue

        match_id = int(fix["match_id"])
        task = _task_name(match_id)
        cmd = (
            f'"{PYTHON}" "{SCHEDULER_SCRIPT}" prekickoff '
            f'--match-id {match_id}'
        )
        sd = run_at.strftime("%m/%d/%Y")
        st = run_at.strftime("%H:%M")

        schtasks = [
            "schtasks", "/Create", "/TN", task, "/TR", cmd,
            "/SC", "ONCE", "/SD", sd, "/ST", st,
        ]
        if force:
            schtasks.append("/F")

        print(f"  Task {task} → {run_at.strftime('%Y-%m-%d %H:%M')} local "
              f"({fix['home_team']} vs {fix['away_team']})")
        result = subprocess.run(schtasks, capture_output=True, text=True)
        if result.returncode == 0:
            created += 1
        else:
            print(f"    ⚠ Failed: {result.stderr.strip() or result.stdout.strip()}")

    print(f"[SCHEDULER] Created {created} scheduled task(s).")
    print("  Each task runs: lineups (FotMob) + live predictions + bet slip")
    return created


def uninstall_windows_tasks() -> int:
    if os.name != "nt":
        return 0
    removed = 0
    result = subprocess.run(
        ["schtasks", "/Query", "/FO", "LIST", "/V"],
        capture_output=True, text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("TaskName:") and TASK_PREFIX in line:
            task = line.split(":", 1)[1].strip().split("\\")[-1]
            subprocess.run(["schtasks", "/Delete", "/TN", task, "/F"],
                           capture_output=True)
            removed += 1
            print(f"  Removed {task}")
    print(f"[SCHEDULER] Removed {removed} task(s).")
    return removed


def _load_state() -> Dict[str, str]:
    if not sim.SCHEDULER_STATE_JSON.exists():
        return {}
    return json.loads(sim.SCHEDULER_STATE_JSON.read_text(encoding="utf-8"))


def _save_state(state: Dict[str, str]) -> None:
    sim.SCHEDULER_STATE_JSON.write_text(
        json.dumps(state, indent=2), encoding="utf-8")


def prekickoff(match_id: int) -> int:
    """Run lineup fetch + live pipeline for one match."""
    print(f"[SCHEDULER] Pre-kickoff run for match {match_id}")
    if not sim.refresh_lineups_for_match(match_id):
        print("[SCHEDULER] Lineups not confirmed yet — continuing with "
              "existing expected_lineups.json")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [PYTHON, str(ROOT / "wc2026_simulation.py"), "--live"]
    print(f"[SCHEDULER] Running: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


def run_daemon(poll_seconds: int = 300) -> None:
    """Poll schedule and trigger prekickoff ~60 min before each match."""
    print("[SCHEDULER] Daemon started — checking every "
          f"{poll_seconds // 60} min (Ctrl+C to stop)")
    state = _load_state()

    while True:
        schedule = sim.load_fixture_schedule()
        if not schedule:
            schedule = refresh_schedule()

        now = datetime.now(timezone.utc)
        for fix in schedule:
            if fix.get("finished"):
                continue
            raw = fix.get("kickoff_utc")
            if not raw:
                continue
            kickoff = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            match_id = str(fix["match_id"])
            if match_id in state:
                continue

            minutes_until = (kickoff - now).total_seconds() / 60
            if sim.PREKICKOFF_MINUTES - 5 <= minutes_until <= sim.PREKICKOFF_MINUTES + 5:
                print(f"\n[SCHEDULER] Triggering pre-kickoff: "
                      f"{fix['home_team']} vs {fix['away_team']}")
                prekickoff(int(match_id))
                state[match_id] = datetime.now().isoformat()
                _save_state(state)

        time.sleep(poll_seconds)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Schedule WC 2026 live predictions 60 min before kickoff",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("refresh", help="Fetch & save FotMob fixture schedule")

    p_install = sub.add_parser("install", help="Install Windows scheduled tasks")
    p_install.add_argument("--force", action="store_true")

    sub.add_parser("uninstall", help="Remove Windows scheduled tasks")

    p_pre = sub.add_parser("prekickoff", help="Run one pre-kickoff update")
    p_pre.add_argument("--match-id", type=int, required=True)

    p_daemon = sub.add_parser("daemon", help="Poll and run automatically")
    p_daemon.add_argument("--poll-seconds", type=int, default=300)

    args = parser.parse_args(argv)

    if args.command == "refresh":
        refresh_schedule()
        return 0
    if args.command == "install":
        refresh_schedule()
        install_windows_tasks(force=args.force)
        return 0
    if args.command == "uninstall":
        uninstall_windows_tasks()
        return 0
    if args.command == "prekickoff":
        return prekickoff(args.match_id)
    if args.command == "daemon":
        refresh_schedule()
        run_daemon(poll_seconds=args.poll_seconds)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
