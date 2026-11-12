"""PACE: how often each city is checked for new ads, decided by what its own checks find.

The scheduled task still wakes up every NEW_CHECK_EVERY_HOURS hours (6), but each time it checks only
the cities that are due. After a city is checked, its interval is set from how fast new ads appear there:
  - aim for about TARGET_NEW new ads per check (one page): busy cities (Lahore: ~340 new ads in 6 h) stay
    at the shortest interval, quiet ones wait longer;
  - speeding up is immediate (a sudden burst brings the next check forward at once), slowing down is
    gradual: at most double the interval each time, because one quiet check proves little;
  - always a whole number of schedule steps, between NEW_CHECK_EVERY_HOURS and NEW_CHECK_MAX_HOURS.
Only brand-new ads count (ads we didn't have). A refreshed old ad comes back on top of Zameen's
newest-first list but isn't new to us, so it doesn't make a city look busier than it is.

Nothing is missed by waiting: every check reads back to that city's previous check, however long ago.
A city whose check failed, hit the safety limit or was stopped keeps its old interval and stays due,
so the next run tries it again. A city never checked this way is due straight away.

State per city in settings ('pace:<city>', JSON): every (hours), last and next check (ISO times), and
what the last check found (new ads, hours they took to appear). Checks started by hand (`new`, the viewer
button) check every city they're given, and their results set the pace too.
"""
import json
import logging
import math
import os
from datetime import datetime, timedelta

from . import config, store

log = logging.getLogger(__name__)

STEP_HOURS = int(os.getenv("NEW_CHECK_EVERY_HOURS", "6"))      # how often the scheduled task wakes up
MAX_HOURS = int(os.getenv("NEW_CHECK_MAX_HOURS", "24"))         # the longest any city waits
TARGET_NEW = 25                                                 # about one page of new ads per check


def _key(city):
    return f"pace:{city}"


def next_every(every, new, hours, step=None, longest=None):
    """The interval (hours) after a check that found `new` brand-new ads, posted over `hours` hours
    (since the city's previous check), when the interval was `every`."""
    step, longest = step or STEP_HOURS, max(longest or MAX_HOURS, step or STEP_HOURS)
    if hours <= 0:
        return every
    ideal = TARGET_NEW * hours / new if new else math.inf    # hours for TARGET_NEW new ads at this rate
    ideal = min(ideal, every * 2)                            # slow down one doubling at a time
    whole_steps = max(1, math.floor(ideal / step))           # round down: rather a bit early than late
    return min(whole_steps * step, longest // step * step)


def get(cur, city):
    """This city's pace: {"every", "last", "next", "new", "hours"}, or None if it was never set."""
    value = store.get_setting(cur, _key(city))
    if not value:
        return None
    state = json.loads(value)
    for k in ("last", "next"):
        state[k] = datetime.fromisoformat(state[k])
    return state


def is_due(cur, city, now=None, step=None):
    """Due when its next check falls before the middle of the coming schedule step: the task wakes up at
    fixed times, so a city due at 13:10 is checked at 12:00 rather than waiting until 18:00."""
    state = get(cur, city)
    if state is None:
        return True
    now = now or config.now()
    return state["next"] <= now + timedelta(hours=(step or STEP_HOURS) / 2)


def record(cur, city, checked_at, new, hours, step=None, longest=None):
    """Set the city's next interval after a successful check (started at `checked_at`). Returns the state."""
    old = get(cur, city)
    every = next_every(old["every"] if old else (step or STEP_HOURS), new, hours, step, longest)
    state = {"every": every, "last": checked_at.isoformat(timespec="seconds"),
             "next": (checked_at + timedelta(hours=every)).isoformat(timespec="seconds"),
             "new": new, "hours": round(hours, 1)}
    store.set_setting(cur, _key(city), json.dumps(state))
    log.info("%s: %d new ads in %.1f h -> checked every %d h%s, next %s", city, new, hours, every,
             f" (was {old['every']} h)" if old and old["every"] != every else "",
             (checked_at + timedelta(hours=every)).astimezone(config.PAKISTAN).strftime("%d %b %H:%M"))
    return get(cur, city)


def due_cities(cur, names, now=None):
    """(due, waiting): the cities to check now, and the others with their next check time."""
    due, waiting = [], []
    for name in names:
        if is_due(cur, name, now):
            due.append(name)
        else:
            waiting.append((name, get(cur, name)["next"]))
    return due, waiting
