"""SCHEDULE: the Windows Task Scheduler task that runs the new-ad check every NEW_CHECK_EVERY_HOURS hours.

    install   creates the task: `main.py new --all --scheduled`, starting at 00:00
              (run install again after changing NEW_CHECK_EVERY_HOURS in .env)
    remove    deletes the task
    pause / resume   a switch in the database (settings.auto_check) that the scheduled check reads
                     before it starts and between pages, so pausing also stops a check that's running

Runs with pythonw.exe so no window pops up; the output goes to logs/new-checks.log.
"""
import os
import subprocess
import sys

from . import config, store

TASK_NAME = "Zameen Scrapper new ads"
# How often: any number of hours. No Zameen limit forces it (it can read back as far as needed);
# more often just means new ads reach the database sooner.
EVERY_HOURS = int(os.getenv("NEW_CHECK_EVERY_HOURS", "6"))


def _pythonw():
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def start_check_now():
    """Start `main.py new --all` in the background (the viewer's "Check for new ads now" button).
    No window; it logs to logs/new-checks.log and keeps running if the viewer is closed."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen([_pythonw(), os.path.join(config.ROOT, "main.py"), "new", "--all", "--background"],
                     cwd=config.ROOT, creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _quote(path):
    return f'"{path}"' if " " in path else path


def install():
    command = f"{_quote(_pythonw())} {_quote(os.path.join(config.ROOT, 'main.py'))} new --all --scheduled"
    # Windows counts hours only up to 23: a day or more is a daily schedule
    every = ["/SC", "DAILY", "/MO", str(EVERY_HOURS // 24)] if EVERY_HOURS >= 24 else \
        ["/SC", "HOURLY", "/MO", str(EVERY_HOURS)]
    r = subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/TR", command, *every,
                        "/ST", "00:00", "/F"], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def remove():
    r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout or r.stderr).strip()


def task_info():
    """{'installed': bool, 'next_run': str, 'last_run': str, 'last_result': str} from Windows."""
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"installed": False}
    fields = dict(line.split(":", 1) for line in r.stdout.splitlines() if ":" in line)
    fields = {k.strip(): v.strip() for k, v in fields.items()}
    return {"installed": True, "next_run": fields.get("Next Run Time"), "last_run": fields.get("Last Run Time"),
            "last_result": fields.get("Last Result")}


def is_paused(conn):
    with conn, conn.cursor() as cur:
        return store.get_setting(cur, "auto_check", "on") == "off"


def set_paused(conn, paused):
    with conn, conn.cursor() as cur:
        store.set_setting(cur, "auto_check", "off" if paused else "on")
