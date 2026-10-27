# -*- coding: utf-8 -*-
"""
DTW Auto Wave Selection - Launcher
- Checks environment health (venv + numpy)
- Starts watcher.py as background process
- Writes status file for external monitoring
- Captures watcher startup errors for diagnostics

Usage:
    python launcher.py              # Start watcher (default)
    python launcher.py --status     # Show current status
    python launcher.py --stop       # Stop running watcher
"""

import os
import sys
import subprocess
import time
from pathlib import Path

# ============================================================
# Config
# ============================================================
PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / "deji"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
WATCHER_SCRIPT = PROJECT_DIR / "watcher.py"
PID_FILE = PROJECT_DIR / "watcher.pid"
STATUS_DIR = Path(r"d:/data")
STATUS_FILE = STATUS_DIR / "watcher_status.txt"
ERROR_LOG = PROJECT_DIR / "logs" / "watcher_error.log"


def log(msg):
    print("[launcher] {}".format(msg))


# ============================================================
# Environment checks
# ============================================================
def venv_exists():
    return VENV_PYTHON.is_file()


def numpy_ok():
    """Check if numpy can be imported in the deji venv."""
    if not venv_exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import numpy"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def watcher_running():
    """Check if a watcher process is still alive via PID file."""
    if not PID_FILE.is_file():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


# ============================================================
# Status file
# ============================================================
def write_status(status, detail=""):
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        "status={}\ntime={}\ndetail={}".format(
            status,
            time.strftime("%Y-%m-%d %H:%M:%S"),
            detail
        ),
        encoding="utf-8"
    )
    log("Status: {}".format(status))


# ============================================================
# Start / Stop watcher
# ============================================================
def start_watcher():
    """Start watcher.py as background process, capture stderr for diagnostics."""
    if watcher_running():
        log("Watcher already running.")
        return True

    # Check prerequisites
    if not venv_exists():
        log("ERROR: Virtual env deji not found. Run install.bat first.")
        write_status("error", "venv missing - run install.bat")
        return False
    if not numpy_ok():
        log("ERROR: numpy import failed in deji venv.")
        write_status("error", "numpy import failed - check VC++ runtime")
        return False

    log("Starting watcher.py (background)...")

    cmd = [str(VENV_PYTHON), str(WATCHER_SCRIPT)]
    # guard.py 通过该环境变量传入监听目录，使 guard_config.ini 的 watch_dir 真正生效
    watch_dir = os.environ.get("DTW_WATCH_DIR")
    if watch_dir:
        cmd += ["--dir", watch_dir]
        log("Watch dir: {}".format(watch_dir))

    # Prepare error log (for crash diagnostics)
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    err_fh = open(str(ERROR_LOG), "w")
    err_fh.write("--- watcher startup {} ---\n".format(
        time.strftime("%Y-%m-%d %H:%M:%S")))

    creationflags = 0x08000000 if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=err_fh,
            stderr=err_fh,
            creationflags=creationflags,
            cwd=str(PROJECT_DIR)
        )
    finally:
        err_fh.close()

    PID_FILE.write_text(str(proc.pid))
    log("Watcher started (PID={})".format(proc.pid))
    time.sleep(2)

    if proc.poll() is not None:
        # Watcher exited immediately - read error log for diagnostics
        err_msg = "watcher exited code={}".format(proc.returncode)
        if ERROR_LOG.is_file():
            try:
                err_content = ERROR_LOG.read_text(encoding="utf-8", errors="replace")
                # Get last meaningful lines
                lines = [l.strip() for l in err_content.splitlines() if l.strip()]
                if lines:
                    err_msg += " | " + " // ".join(lines[-3:])
            except Exception:
                pass
        log("ERROR: {}".format(err_msg))
        write_status("error", err_msg)
        return False

    write_status("ready", "watcher running PID={}".format(proc.pid))
    return True


def stop_watcher():
    """Terminate a running watcher process."""
    if not watcher_running():
        log("Watcher not running.")
        write_status("stopped", "not running")
        return True

    try:
        pid = int(PID_FILE.read_text().strip())
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 0)
            ctypes.windll.kernel32.CloseHandle(handle)
            log("Watcher stopped (PID={})".format(pid))
    except Exception as e:
        log("Error stopping watcher: {}".format(e))

    if PID_FILE.is_file():
        PID_FILE.unlink()
    write_status("stopped", "manual stop")
    return True


# ============================================================
# Entry point
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DTW Auto Wave Launcher")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--stop", action="store_true",
                        help="Stop watcher (NOT recommended while guard.py runs: "
                             "the guard restarts it within seconds. Use run_guard.bat stop)")
    args = parser.parse_args()

    log("=" * 40)
    log("DTW Auto Wave Launcher")
    log("=" * 40)

    if args.status:
        ok = venv_exists() and numpy_ok()
        running = watcher_running()
        print("venv deji  : {}".format("OK" if ok else "MISSING"))
        print("watcher    : {}".format("running" if running else "stopped"))
        if STATUS_FILE.is_file():
            print("status file:\n{}".format(
                STATUS_FILE.read_text(encoding="utf-8")))
        sys.exit(0)

    if args.stop:
        stop_watcher()
        sys.exit(0)

    # Default: start watcher
    if start_watcher():
        log("")
        log("Watcher is running in background.")
        log("  Status: {}".format(STATUS_FILE))
        log("  Result: d:/data/selectwave.txt")
        log("  Errors: {}".format(ERROR_LOG))
    else:
        log("")
        log("Watcher failed to start!")
        log("Check error log: {}".format(ERROR_LOG))
        sys.exit(1)
