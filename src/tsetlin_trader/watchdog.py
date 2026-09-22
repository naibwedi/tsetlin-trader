"""Independent read-only heartbeat check. Schedule separately from the bot.

Returns nonzero and sends ALERT_WEBHOOK_URL on stale/error/late-position state.
Cannot submit orders. A separate host monitor is still needed for host failure.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .ops import alert


def problems(state: dict, now: datetime, max_age: float = 90) -> list[str]:
    issues = []
    stamp = state.get("updated_at")
    if not stamp:
        issues.append("No runner heartbeat")
    else:
        age = (now - datetime.fromisoformat(stamp)).total_seconds()
        if age > max_age or age < -30:
            issues.append("Runner heartbeat stale or clock invalid")
    if state.get("last_error"):
        issues.append("Runner reports an error; inspect private status")
    local = now.astimezone(ZoneInfo("America/New_York"))
    if local.strftime("%H:%M") >= "15:58" and (
        state.get("managed_position") or state.get("positions", {}).get("SPY", 0) != 0
    ):
        issues.append("SPY position remains after exit deadline")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        issues = problems(json.loads(args.state.read_text()), datetime.now(timezone.utc))
    except Exception:
        issues = ["Cannot read valid runner state"]
    for issue in issues:
        print(issue)
        alert("Paper bot watchdog: " + issue)
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__":
    main()
