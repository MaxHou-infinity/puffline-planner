"""Run CPU-heavy analyses off the Tk thread and deliver results on that thread."""

from queue import Empty, Queue
from threading import Thread
from tkinter import ttk


class BackgroundJob:
    """A small Tk-owned poller; worker functions must never access widgets."""

    def __init__(self, owner, work, on_success, on_error, on_progress=None):
        self.owner = owner
        self.work = work
        self.on_success = on_success
        self.on_error = on_error
        self.on_progress = on_progress
        self.messages = Queue()

    def start(self):
        def run():
            try:
                result = self.work(lambda value: self.messages.put(("progress", value)))
                self.messages.put(("success", result))
            except Exception as exc:
                self.messages.put(("error", exc))

        Thread(target=run, daemon=True).start()
        self.owner.after(50, self._poll)

    def _poll(self):
        if not self.owner.winfo_exists():
            return
        finished = None
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except Empty:
                break
            if kind == "progress" and self.on_progress:
                self.on_progress(value)
            elif kind != "progress":
                finished = (kind, value)
        if finished:
            kind, value = finished
            if kind == "success":
                self.on_success(value)
            else:
                self.on_error(value)
        else:
            self.owner.after(50, self._poll)


def set_dialog_busy(dialog, busy, saved=None):
    """Temporarily disable conflicting inputs; return original widget states."""
    if not busy:
        for widget, state in (saved or {}).items():
            if widget.winfo_exists():
                widget.configure(state=state)
        return {}
    saved = {}
    pending = [dialog]
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox)):
            saved[widget] = str(widget.cget("state"))
            widget.configure(state="disabled")
    return saved
