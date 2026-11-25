"""Desktop launcher: click the shortcut, the reader starts and the browser opens.

Runs under pythonw.exe, so there is no console window. Three behaviours:

  (no args)  start the server if it is not already up, wait for the port, open the browser
  --stop     stop the server
  --status   print whether it is running (for scripts/tests)

Everything is logged to logs/server.log; if startup fails a message box appears with the tail of
that log, because a silent failure from pythonw is indistinguishable from "nothing happened".
"""
import ctypes
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
# pythonw cannot own a console at all, so no window exists that could be closed and take the
# server down with it. That is the classic run.bat failure.
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
LOG = ROOT / "logs" / "server.log"
CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008


def port_open(timeout=0.4):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def pids_on_port():
    """PIDs listening on PORT, via netstat (no psutil in this venv)."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and f":{PORT}" in parts[1] \
                and parts[3].upper() == "LISTENING":
            pids.add(parts[4])
    return sorted(pids)


def stop():
    pids = pids_on_port()
    if not pids:
        return False
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    for _ in range(40):
        if not port_open():
            return True
        time.sleep(0.25)
    return not port_open()


def serve():
    """Run uvicorn in this process — the scheduled task's action.

    Task Scheduler owns the process tree, so nothing in a shell or a tool call can take it down. Under
    pythonw there is no stdout, so logging is pointed at logs/server.log before uvicorn starts.
    """
    import io
    LOG.parent.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    stream = io.TextIOWrapper(open(LOG, "ab", buffering=0), encoding="utf-8", write_through=True)
    sys.stdout = sys.stderr = stream
    print(f"\n--- serve start {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()}) ---")
    import uvicorn
    try:
        uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level="info")
    finally:
        # if this line is missing from the log next time, the process was killed, not shut down
        print(f"--- serve end {time.strftime('%Y-%m-%d %H:%M:%S')} (pid {os.getpid()}) ---")


TASK_NAME = "ThreePassReader"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "ThreePassReader"


def task_exists():
    r = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME], capture_output=True, text=True)
    return r.returncode == 0


def install_task():
    """Register a per-user logon task. No elevation needed for an onlogon task of the current user."""
    cmd = f'"{PYW if PYW.exists() else PY}" "{ROOT / "launcher.py"}" --serve'
    r = subprocess.run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", cmd, "/sc", "onlogon",
                        "/rl", "limited", "/f"], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def remove_task():
    r = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def install_autostart():
    """HKCU Run entry: starts the server at logon with no console and no admin rights needed."""
    import winreg
    cmd = f'"{PYW if PYW.exists() else PY}" "{ROOT / "launcher.py"}" --serve'
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
    return True, cmd


def remove_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_VALUE)
        return True, "removed"
    except FileNotFoundError:
        return True, "was not set"


def autostart_value():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return ""


def log_tail(n=25):
    try:
        return "\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log yet)"


def say(title, text):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        print(f"{title}: {text}", file=sys.stderr)


def start():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    py = PYW if PYW.exists() else PY
    if not py.exists():
        say("Three-Pass Reader",
            f"The virtualenv is missing:\n{py}\n\nRecreate it with:\n"
            f"  cd {ROOT}\n  python -m venv .venv\n  .venv\\Scripts\\pip install -r requirements.txt")
        return 1
    with open(LOG, "ab") as log:
        log.write(f"\n--- launcher start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode())
        log.flush()
    if task_exists():
        # Task Scheduler owns the process tree: it survives every shell, tool call and window.
        subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], capture_output=True, text=True)
    else:
        # same code path as the logon autostart, so every launch writes the same markers to the log
        subprocess.Popen(
            [str(py), str(ROOT / "launcher.py"), "--serve"],
            cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=CREATE_NO_WINDOW | DETACHED, close_fds=True,
        )
    for _ in range(120):                       # ~30 s; first run also scans the archive
        if port_open(0.3):
            break
        time.sleep(0.25)
    else:
        say("Three-Pass Reader didn't start",
            f"The server never opened port {PORT}.\n\nLast lines of {LOG}:\n\n{log_tail()}")
        return 2
    # A port answering is not proof of a working app: if something kills the child (a job object on
    # the parent, an antivirus, a crash on the first request), the failure must not be silent.
    time.sleep(2.5)
    if not port_open(0.5):
        say("Three-Pass Reader started and then stopped",
            f"The server opened port {PORT} but was gone moments later.\n\n"
            f"Last lines of {LOG}:\n\n{log_tail()}")
        return 4
    return 0


def main(argv):
    if "--status" in argv:
        print("running" if port_open() else "stopped")
        return 0 if port_open() else 1
    if "--install-autostart" in argv:
        ok, msg = install_autostart()
        print(("autostart installed: " if ok else "autostart failed: ") + str(msg))
        return 0 if ok else 5
    if "--uninstall-autostart" in argv:
        ok, msg = remove_autostart()
        print(msg)
        return 0 if ok else 5
    if "--serve" in argv:
        serve()
        return 0
    if "--install-task" in argv:
        ok, msg = install_task()
        if not ok:
            print("install failed:", msg)
        else:
            print(f"task '{TASK_NAME}' installed (starts at logon, runs the server with no window)")
        return 0 if ok else 5
    if "--uninstall-task" in argv:
        ok, msg = remove_task()
        print(msg)
        return 0 if ok else 5
    if "--restart" in argv:
        stop()
        rc = start()
        if rc == 0:
            webbrowser.open(URL)
        return rc
    if "--stop" in argv:
        ok = stop()
        if "--status" in argv or ok:
            print("stopped" if ok else "was not running")
        return 0
    if port_open():                            # already up: just show it
        webbrowser.open(URL)
        return 0
    rc = start()
    if rc == 0:
        webbrowser.open(URL)
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:                   # never die silently under pythonw
        say("Three-Pass Reader :: launcher error", f"{type(exc).__name__}: {exc}")
        sys.exit(3)
