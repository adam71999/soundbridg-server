"""
SoundBridg Core Engine
Shared logic for both Mac and Windows agents.
Handles: config, folder watching, FL Studio export, iCloud sync.
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import time
import platform
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Platform detection ────────────────────────────────────────────────────────

IS_MAC     = platform.system() == "Darwin"
IS_WINDOWS = platform.system() == "Windows"

# ── Paths ─────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path.home() / ".soundbridg_config.json"

def get_icloud_path() -> Path:
    """Return the iCloud Drive path for this platform."""
    if IS_MAC:
        p = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
        if p.exists():
            return p
        # Fallback: iCloud Drive in home
        return Path.home() / "iCloud Drive"
    elif IS_WINDOWS:
        # iCloud for Windows installs to %USERPROFILE%\iCloudDrive
        p = Path.home() / "iCloudDrive"
        if p.exists():
            return p
        # Some versions use a different path
        p2 = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "iCloudDrive"
        return p2
    return Path.home() / "SoundBridg"

def get_soundbridg_icloud_folder() -> Path:
    """The SoundBridg folder inside iCloud Drive."""
    return get_icloud_path() / "SoundBridg"

def get_fl_studio_cli() -> str:
    """Return the FL Studio CLI executable path for this platform."""
    if IS_MAC:
        return "/Applications/FL Studio.app/Contents/MacOS/FL64"
    elif IS_WINDOWS:
        # Common FL Studio install locations on Windows
        candidates = [
            r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe",
            r"C:\Program Files\Image-Line\FL Studio 20\FL64.exe",
            r"C:\Program Files (x86)\Image-Line\FL Studio 21\FL64.exe",
            r"C:\Program Files (x86)\Image-Line\FL Studio 20\FL64.exe",
        ]
        for c in candidates:
            if Path(c).exists():
                return c
        return r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe"
    return ""

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "watch_folders":    [],
    "export_trigger":   "on_save",   # "on_save", "interval", "both"
    "interval_minutes": 10,          # 5, 10, 15, 30, 60
    "export_format":    "mp3",       # "mp3", "wav", "both"
    "fl_studio_path":   "",          # override auto-detected path
    "enabled":          False,
}

INTERVAL_OPTIONS = [5, 10, 15, 30, 60]

def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

# ── Export logic ──────────────────────────────────────────────────────────────

def is_in_watch_folders(flp_path: str, watch_folders: list) -> bool:
    """Only export if the project is inside a configured watch folder."""
    p = Path(flp_path).resolve()
    for folder in watch_folders:
        try:
            p.relative_to(Path(folder).resolve())
            return True
        except ValueError:
            continue
    return False

def export_flp(flp_path: str, cfg: dict, on_progress=None) -> list:
    """
    Export a .flp to audio using FL Studio CLI.
    Returns list of output file paths that were successfully created.
    Only runs for projects inside configured watch folders.
    """
    if not is_in_watch_folders(flp_path, cfg["watch_folders"]):
        log(f"Skipped (not in watch folders): {flp_path}")
        return []

    fl_path = cfg.get("fl_studio_path") or get_fl_studio_cli()
    if not Path(fl_path).exists():
        log(f"FL Studio not found at: {fl_path}")
        log("Open SoundBridg settings and set your FL Studio path.")
        return []

    fmt            = cfg["export_format"]
    icloud_folder  = get_soundbridg_icloud_folder()
    icloud_folder.mkdir(parents=True, exist_ok=True)

    project_name = Path(flp_path).stem
    formats      = ["mp3", "wav"] if fmt == "both" else [fmt]
    outputs      = []

    for f in formats:
        out = str(icloud_folder / f"{project_name}.{f}")
        cmd = [fl_path, "-r", flp_path, f"-f{f}", f"-o{out}"]

        log(f"Exporting: {project_name}.{f}…")
        if on_progress:
            on_progress(f"Exporting {project_name}.{f}…")

        try:
            subprocess.run(
                cmd, timeout=300, check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log(f"✓ Exported to iCloud: {project_name}.{f}")
            outputs.append(out)
        except subprocess.TimeoutExpired:
            log(f"✗ Timeout exporting: {flp_path}")
        except subprocess.CalledProcessError as e:
            log(f"✗ FL Studio error ({flp_path}): {e}")
        except FileNotFoundError:
            log(f"✗ FL Studio not found at: {fl_path}")

    return outputs

def find_all_flp(watch_folders: list) -> list:
    """Find all .flp files inside configured watch folders."""
    found = []
    for folder in watch_folders:
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".flp"):
                    found.append(os.path.join(root, f))
    return found

# ── File watcher ──────────────────────────────────────────────────────────────

class FLPHandler(FileSystemEventHandler):
    """Debounced file watcher — only exports after 3s of no changes."""

    def __init__(self, cfg, on_export=None):
        self.cfg       = cfg
        self.on_export = on_export
        self._pending  = {}

    def _handle(self, path):
        if path.lower().endswith(".flp") and is_in_watch_folders(path, self.cfg["watch_folders"]):
            self._pending[path] = time.time()

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def flush(self):
        trigger = self.cfg.get("export_trigger", "on_save")
        if trigger not in ("on_save", "both"):
            self._pending.clear()
            return

        now   = time.time()
        ready = [p for p, t in list(self._pending.items()) if now - t > 3]
        for path in ready:
            del self._pending[path]
            log(f"Save detected: {path}")
            cfg_snap = dict(self.cfg)
            threading.Thread(
                target=self._do_export,
                args=(path, cfg_snap),
                daemon=True,
            ).start()

    def _do_export(self, path, cfg):
        outputs = export_flp(path, cfg)
        if outputs and self.on_export:
            self.on_export(Path(path).stem, outputs)

# ── Background engine ─────────────────────────────────────────────────────────

class SoundBridgEngine:
    """
    Platform-agnostic engine.
    The Mac and Windows UI layers call start()/stop() and receive callbacks.
    """

    def __init__(self, cfg: dict, on_export=None, on_status=None):
        self.cfg        = cfg
        self.on_export  = on_export   # callback(project_name, output_paths)
        self.on_status  = on_status   # callback(status_string)
        self._running   = False
        self._observer  = None
        self._handler   = None

    def start(self) -> bool:
        if not self.cfg["watch_folders"]:
            return False

        self._running = True
        self._handler = FLPHandler(self.cfg, on_export=self.on_export)
        self._observer = Observer()
        for folder in self.cfg["watch_folders"]:
            if os.path.isdir(folder):
                self._observer.schedule(self._handler, folder, recursive=True)
        self._observer.start()

        threading.Thread(target=self._loop, daemon=True).start()
        self._emit_status()
        return True

    def stop(self):
        self._running = False
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        self._emit_status()

    def _loop(self):
        last_interval = time.time()
        while self._running:
            time.sleep(1)
            if self._handler:
                self._handler.flush()

            trigger = self.cfg.get("export_trigger", "on_save")
            if trigger in ("interval", "both"):
                secs = self.cfg["interval_minutes"] * 60
                if time.time() - last_interval >= secs:
                    last_interval = time.time()
                    self._run_interval_export()

    def _run_interval_export(self):
        flps = find_all_flp(self.cfg["watch_folders"])
        if not flps:
            return
        log(f"Interval export: {len(flps)} project(s)")
        cfg_snap = dict(self.cfg)
        for flp in flps:
            threading.Thread(
                target=self._do_export_and_callback,
                args=(flp, cfg_snap),
                daemon=True,
            ).start()

    def _do_export_and_callback(self, flp, cfg):
        outputs = export_flp(flp, cfg)
        if outputs and self.on_export:
            self.on_export(Path(flp).stem, outputs)

    def _emit_status(self):
        if not self.on_status:
            return
        if self._running:
            trigger = self.cfg["export_trigger"]
            mins    = self.cfg["interval_minutes"]
            folders = len(self.cfg["watch_folders"])
            label   = {
                "on_save":  "On Save",
                "interval": f"Every {mins} min",
                "both":     f"On Save + Every {mins} min",
            }.get(trigger, trigger)
            self.on_status(f"● Running — {label} — {folders} folder(s)")
        else:
            self.on_status("Stopped")

    @property
    def running(self):
        return self._running

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"[SoundBridg] {msg}", flush=True)
