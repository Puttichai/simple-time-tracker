#!/usr/bin/env python3
"""
Run:
  python3 time_tracker.py

Dependencies: only Python stdlib + Tkinter (sudo apt-get install python3-tk if missing)
"""
from __future__ import annotations
import csv
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox

# ----------------- Config -----------------
APP_TITLE = "Time Tracker"
LOG_DIR = os.path.join(os.path.expanduser("~"), ".simple_time_tracker")
JSONL_PATH = os.path.join(LOG_DIR, "time_log.jsonl")
CSV_PATH = os.path.join(LOG_DIR, "time_log.csv")
AUTO_TOPMOST = True  # keep the tiny window always on top
REFRESH_MS = 200  # UI refresh interval (ms)
STATUSES = ["done", "in_progress", "blocked", "review", "cancelled", "other"]


# ----------------- Data model -----------------
@dataclass
class TimeEntry:
    id: str
    date: str
    start_iso: str
    end_iso: str
    duration_seconds: int
    duration_hms: str
    ticket_url: str
    note: str
    status: str

    def to_row(self):
        return [
            self.id,
            self.date,
            self.start_iso,
            self.end_iso,
            self.duration_seconds,
            self.duration_hms,
            self.ticket_url,
            self.note,
            self.status,
        ]


CSV_HEADER = [
    "id",
    "date",
    "start_iso",
    "end_iso",
    "duration_seconds",
    "duration_hms",
    "ticket_url",
    "note",
    "status",
]


# ----------------- Helpers -----------------
def ensure_paths():
    os.makedirs(LOG_DIR, exist_ok=True)
    # create CSV with header if new
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
    # touch JSONL file
    if not os.path.exists(JSONL_PATH):
        with open(JSONL_PATH, "w", encoding="utf-8") as f:
            pass


def fmt_hms(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ----------------- Entry dialog -----------------
class EntryDialog(tk.Toplevel):
    def __init__(self, master, duration_hms: str, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.title("Task details")
        self.resizable(False, False)
        self.grab_set()  # modal

        ttk.Label(self, text=f"Duration: {duration_hms}").grid(
            row=0, column=0, columnspan=2, pady=(8, 4), padx=10, sticky="w"
        )

        ttk.Label(self, text="Link (ticket / MR)").grid(row=1, column=0, sticky="e", padx=(10, 6), pady=4)
        self.link_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.link_var, width=42).grid(row=1, column=1, sticky="we", padx=(0, 10), pady=4)

        ttk.Label(self, text="Note").grid(row=2, column=0, sticky="e", padx=(10, 6), pady=4)
        self.note_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.note_var, width=42).grid(row=2, column=1, sticky="we", padx=(0, 10), pady=4)

        ttk.Label(self, text="Status").grid(row=3, column=0, sticky="e", padx=(10, 6), pady=4)
        self.status_var = tk.StringVar(value=STATUSES[0])
        ttk.Combobox(self, textvariable=self.status_var, values=STATUSES, state="readonly", width=39).grid(
            row=3, column=1, sticky="we", padx=(0, 10), pady=4
        )

        btns = ttk.Frame(self)
        btns.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btns, text="Save", command=self._save).pack(side=tk.RIGHT)

        self.result = None
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self._cancel())

    def _save(self):
        self.result = {
            "ticket_url": self.link_var.get().strip(),
            "note": self.note_var.get().strip(),
            "status": self.status_var.get().strip() or "other",
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ----------------- Main app -----------------
class TimeTrackerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.resizable(False, False)
        if AUTO_TOPMOST:
            self.wm_attributes("-topmost", 1)

        # State
        self.state = "idle"  # idle | running | paused
        self.session_start: float | None = None  # epoch seconds
        self.session_end: float | None = None
        self.accum_seconds = 0
        self._tick_job = None
        self._last_resume_at: float | None = None

        # UI
        main = ttk.Frame(self, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        self.elapsed_lbl = ttk.Label(main, text="00:00:00", font=("TkDefaultFont", 16, "bold"))
        self.elapsed_lbl.pack(pady=(0, 6))

        btns = ttk.Frame(main)
        btns.pack()
        self.play_btn = ttk.Button(btns, text="▶ Play", command=self.on_play, width=10)
        self.pause_btn = ttk.Button(btns, text="⏸ Pause", command=self.on_pause, width=10)
        self.stop_btn = ttk.Button(btns, text="⏹ Stop", command=self.on_stop, width=10)
        self.play_btn.grid(row=0, column=0, padx=3)
        self.pause_btn.grid(row=0, column=1, padx=3)
        self.stop_btn.grid(row=0, column=2, padx=3)

        self._update_buttons()

        # shortcuts
        self.bind("<space>", lambda e: self._toggle_play_pause())
        self.bind("<Control-s>", lambda e: self.on_stop())

        ensure_paths()

    # ----------- State helpers -----------
    def _toggle_play_pause(self):
        if self.state == "running":
            self.on_pause()
        else:
            self.on_play()

    def _update_buttons(self):
        self.play_btn.state(["!disabled"] if self.state in {"idle", "paused"} else ["disabled"])
        self.pause_btn.state(["!disabled"] if self.state == "running" else ["disabled"])
        self.stop_btn.state(["!disabled"] if self.state in {"running", "paused"} else ["disabled"])

    def _tick(self):
        total = self.accum_seconds
        if self.state == "running" and self._last_resume_at is not None:
            total += int(time.time() - self._last_resume_at)
        self.elapsed_lbl.config(text=fmt_hms(total))
        self._tick_job = self.after(REFRESH_MS, self._tick)

    # ----------- Button handlers -----------
    def on_play(self):
        now = time.time()
        if self.state == "idle":
            self.session_start = now
            self.accum_seconds = 0
        elif self.state == "paused":
            pass
        elif self.state == "running":
            return
        self.state = "running"
        self._last_resume_at = now
        if self._tick_job is None:
            self._tick()
        self._update_buttons()

    def on_pause(self):
        if self.state != "running":
            return
        now = time.time()
        if self._last_resume_at is not None:
            self.accum_seconds += int(now - self._last_resume_at)
        self._last_resume_at = None
        self.state = "paused"
        self._update_buttons()

    def on_stop(self):
        if self.state not in {"running", "paused"}:
            return
        now = time.time()
        # finalize duration
        if self.state == "running" and self._last_resume_at is not None:
            self.accum_seconds += int(now - self._last_resume_at)
        self._last_resume_at = None
        self.state = "idle"
        self.session_end = now

        total_sec = int(self.accum_seconds)
        duration_hms = fmt_hms(total_sec)
        self._update_buttons()

        if total_sec <= 0:
            messagebox.showinfo(APP_TITLE, "Nothing to save (duration is 0s).")
            self._reset_session()
            return

        # prompt details
        dlg = EntryDialog(self, duration_hms)
        self.wait_window(dlg)
        details = dlg.result
        if not details:
            # user canceled — keep elapsed so they can continue
            # roll back to paused state with previous accum time
            self.state = "paused"
            self._update_buttons()
            return

        # build entry
        start_dt = datetime.fromtimestamp(self.session_start or now, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(self.session_end or now, tz=timezone.utc)
        entry = TimeEntry(
            id=str(uuid.uuid4()),
            date=start_dt.astimezone().strftime("%Y-%m-%d"),
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            duration_seconds=total_sec,
            duration_hms=duration_hms,
            ticket_url=details.get("ticket_url", ""),
            note=details.get("note", ""),
            status=details.get("status", "other"),
        )

        # write logs
        try:
            with open(JSONL_PATH, "a", encoding="utf-8") as jf:
                jf.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
            with open(CSV_PATH, "a", newline="", encoding="utf-8") as cf:
                writer = csv.writer(cf)
                writer.writerow(entry.to_row())
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Failed to write logs: {e}")
            # return to paused state so user can retry
            self.state = "paused"
            self._update_buttons()
            return

        messagebox.showinfo(APP_TITLE, "Entry saved.")
        self._reset_session()

    def _reset_session(self):
        self.accum_seconds = 0
        self.session_start = None
        self.session_end = None
        self.state = "idle"
        self._update_buttons()
        # keep timer running for live clock update
        if self._tick_job is None:
            self._tick()

    def destroy(self):
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
        super().destroy()


if __name__ == "__main__":
    try:
        import _tkinter  # type: ignore  # noqa: F401
    except Exception:
        print("Tkinter is required. On Debian/Ubuntu: sudo apt-get install python3-tk")
        raise

    app = TimeTrackerApp()
    app.mainloop()
