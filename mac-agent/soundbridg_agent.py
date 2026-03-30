#!/usr/bin/env python3
"""
SoundBridg — Mac Menu Bar Agent
Watches FL Studio project folders and auto-exports to iCloud Drive.
"""

import rumps
import os
import json
import subprocess
import threading
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

CONFIG_FILE = Path.home() / ".soundbridg_config.json"
FL_STUDIO_CLI = "/Applications/FL Studio.app/Contents/MacOS/FL64"
INTERVAL_OPTIONS = [5, 10, 15, 30, 60]

DEFAULT_CONFIG = {
    "watch_folders": [],
    "export_trigger": "on_save",
    "interval_minutes": 10,
    "export_format": "mp3",
    "enabled": False,
}

def get_icloud_folder():
    p = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "SoundBridg"
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def is_in_watch_folders(flp_path, watch_folders):
    p = Path(flp_path).resolve()
    for folder in watch_folders:
        try:
            p.relative_to(Path(folder).resolve())
            return True
        except ValueError:
            continue
    return False

def export_flp(flp_path, cfg):
    if not is_in_watch_folders(flp_path, cfg["watch_folders"]):
        return []
    if not Path(FL_STUDIO_CLI).exists():
        print(f"[SoundBridg] FL Studio not found at: {FL_STUDIO_CLI}")
        return []

    fmt = cfg["export_format"]
    export_folder = get_icloud_folder()
    project_name = Path(flp_path).stem
    formats = ["mp3", "wav"] if fmt == "both" else [fmt]
    outputs = []

    for f in formats:
        out = str(export_folder / f"{project_name}.{f}")
        cmd = [FL_STUDIO_CLI, "-r", flp_path, f"-f{f}", f"-o{out}"]
        try:
            subprocess.run(cmd, timeout=300, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"[SoundBridg] Exported: {out}")
            outputs.append(out)
            rumps.notification("SoundBridg", f"Exported: {project_name}", f"{f.upper()} saved to iCloud", sound=False)
        except Exception as e:
            print(f"[SoundBridg] Export error: {e}")
    return outputs

def find_all_flp(watch_folders):
    found = []
    for folder in watch_folders:
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".flp"):
                    found.append(os.path.join(root, f))
    return found

class FLPHandler(FileSystemEventHandler):
    def __init__(self, cfg):
        self.cfg = cfg
        self._pending = {}

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
        now = time.time()
        ready = [p for p, t in list(self._pending.items()) if now - t > 3]
        for path in ready:
            del self._pending[path]
            threading.Thread(target=export_flp, args=(path, dict(self.cfg)), daemon=True).start()

class SoundBridgApp(rumps.App):
    def __init__(self):
        super().__init__("𝄞", quit_button=None)
        self.config = load_config()
        self.observer = None
        self.handler = None
        self._running = False

        self.toggle_item = rumps.MenuItem("▶  Start SoundBridg", callback=self.toggle)
        self.status_item = rumps.MenuItem("Stopped", callback=None)
        self.status_item.set_callback(None)

        self.trig_save     = rumps.MenuItem("  On Save",     callback=lambda _: self._set_trigger("on_save"))
        self.trig_interval = rumps.MenuItem("  On Interval", callback=lambda _: self._set_trigger("interval"))
        self.trig_both     = rumps.MenuItem("  Both",        callback=lambda _: self._set_trigger("both"))
        trigger_menu = rumps.MenuItem("Export Trigger")
        trigger_menu.update([self.trig_save, self.trig_interval, self.trig_both])

        interval_menu = rumps.MenuItem("Export Interval")
        self.interval_items = {}
        for m in INTERVAL_OPTIONS:
            item = rumps.MenuItem(f"  {m} min", callback=self._make_interval_cb(m))
            self.interval_items[m] = item
            interval_menu.add(item)

        self.fmt_mp3  = rumps.MenuItem("  MP3",  callback=lambda _: self._set_format("mp3"))
        self.fmt_wav  = rumps.MenuItem("  WAV",  callback=lambda _: self._set_format("wav"))
        self.fmt_both = rumps.MenuItem("  Both", callback=lambda _: self._set_format("both"))
        format_menu = rumps.MenuItem("Export Format")
        format_menu.update([self.fmt_mp3, self.fmt_wav, self.fmt_both])

        self.menu = [
            self.toggle_item,
            self.status_item,
            None,
            rumps.MenuItem("Add Watch Folder…",    callback=self.add_folder),
            rumps.MenuItem("Remove Watch Folder…", callback=self.remove_folder),
            rumps.MenuItem("Show iCloud Folder",   callback=self.show_icloud),
            None,
            trigger_menu,
            interval_menu,
            format_menu,
            None,
            rumps.MenuItem("Quit SoundBridg", callback=self.quit_app),
        ]

        self._refresh_checkmarks()
        if self.config["enabled"]:
            self._start()

    def toggle(self, _):
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        if not self.config["watch_folders"]:
            rumps.alert("No Folders Set", "Add at least one watch folder first.")
            return
        self._running = True
        self.config["enabled"] = True
        save_config(self.config)
        self.handler = FLPHandler(self.config)
        self.observer = Observer()
        for folder in self.config["watch_folders"]:
            if os.path.isdir(folder):
                self.observer.schedule(self.handler, folder, recursive=True)
        self.observer.start()
        threading.Thread(target=self._loop, daemon=True).start()
        self._update_status()

    def _stop(self):
        self._running = False
        self.config["enabled"] = False
        save_config(self.config)
        if self.observer:
            self.observer.stop()
            self.observer.join()
            self.observer = None
        self._update_status()

    def _loop(self):
        last_interval = time.time()
        while self._running:
            time.sleep(1)
            if self.handler:
                self.handler.flush()
            trigger = self.config.get("export_trigger", "on_save")
            if trigger in ("interval", "both"):
                secs = self.config["interval_minutes"] * 60
                if time.time() - last_interval >= secs:
                    last_interval = time.time()
                    flps = find_all_flp(self.config["watch_folders"])
                    cfg_snap = dict(self.config)
                    for flp in flps:
                        threading.Thread(target=export_flp, args=(flp, cfg_snap), daemon=True).start()

    def _update_status(self):
        if self._running:
            trigger = self.config["export_trigger"]
            mins = self.config["interval_minutes"]
            label = {"on_save": "On Save", "interval": f"Every {mins} min", "both": f"On Save + Every {mins} min"}.get(trigger)
            self.title = "𝄞●"
            self.toggle_item.title = "⏹  Stop SoundBridg"
            self.status_item.title = f"● Running — {label}"
        else:
            self.title = "𝄞"
            self.toggle_item.title = "▶  Start SoundBridg"
            self.status_item.title = "Stopped"

    def _refresh_checkmarks(self):
        t = self.config["export_trigger"]
        self.trig_save.title     = ("✓ On Save"     if t == "on_save"  else "  On Save")
        self.trig_interval.title = ("✓ On Interval" if t == "interval" else "  On Interval")
        self.trig_both.title     = ("✓ Both"        if t == "both"     else "  Both")
        m = self.config["interval_minutes"]
        for mins, item in self.interval_items.items():
            item.title = (f"✓ {mins} min" if mins == m else f"  {mins} min")
        f = self.config["export_format"]
        self.fmt_mp3.title  = ("✓ MP3"  if f == "mp3"  else "  MP3")
        self.fmt_wav.title  = ("✓ WAV"  if f == "wav"  else "  WAV")
        self.fmt_both.title = ("✓ Both" if f == "both" else "  Both")

    def _set_trigger(self, value):
        self.config["export_trigger"] = value
        save_config(self.config)
        self._refresh_checkmarks()
        self._update_status()

    def _set_format(self, value):
        self.config["export_format"] = value
        save_config(self.config)
        self._refresh_checkmarks()

    def _make_interval_cb(self, mins):
        def cb(_):
            self.config["interval_minutes"] = mins
            save_config(self.config)
            self._refresh_checkmarks()
            self._update_status()
        return cb

    def add_folder(self, _):
        result = subprocess.run(
            ["osascript", "-e", 'tell app "Finder" to POSIX path of (choose folder with prompt "Select your FL Studio projects folder:")'],
            capture_output=True, text=True)
        folder = result.stdout.strip()
        if folder and folder not in self.config["watch_folders"]:
            self.config["watch_folders"].append(folder)
            save_config(self.config)
            rumps.notification("SoundBridg", "Folder Added", folder, sound=False)
            if self._running:
                self._stop()
                self._start()

    def remove_folder(self, _):
        folders = self.config["watch_folders"]
        if not folders:
            rumps.alert("No Folders", "No watch folders added yet.")
            return
        options = ", ".join(f'"{Path(f).name}"' for f in folders)
        script = f'choose from list {{{options}}} with prompt "Select folder to remove:"'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        chosen = result.stdout.strip()
        if chosen and chosen != "false":
            to_remove = next((f for f in folders if Path(f).name == chosen), None)
            if to_remove:
                self.config["watch_folders"].remove(to_remove)
                save_config(self.config)
                if self._running:
                    self._stop()
                    self._start()

    def show_icloud(self, _):
        folder = get_icloud_folder()
        subprocess.run(["open", str(folder)])

    def quit_app(self, _):
        self._stop()
        rumps.quit_application()

if __name__ == "__main__":
    SoundBridgApp().run()
