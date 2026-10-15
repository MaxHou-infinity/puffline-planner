"""Run CPU-heavy analyses off the Tk thread and deliver results on that thread."""

from queue import Empty, Queue
from concurrent.futures import CancelledError
from threading import Event, Thread
from tkinter import ttk


class JobProgress:
    """Progress reporter and cooperative cancellation token for a worker."""

    def __init__(self, messages, cancel_event):
        self.messages = messages
        self.cancel_event = cancel_event

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise CancelledError()

    def __call__(self, value):
        self.check_cancelled()
        self.messages.put(("progress", value))


class BackgroundJob:
    """A small Tk-owned poller; worker functions must never access widgets."""

    def __init__(self, owner, work, on_success, on_error, on_progress=None,
                 on_cancel=None):
        self.owner = owner
        self.work = work
        self.on_success = on_success
        self.on_error = on_error
        self.on_progress = on_progress
        self.messages = Queue()
        self.cancel_event = Event()
        self.on_cancel = on_cancel or (lambda: None)

    def cancel(self):
        """Request cancellation; a cancelled job never delivers a success."""
        self.cancel_event.set()

    def start(self):
        def run():
            try:
                progress = JobProgress(self.messages, self.cancel_event)
                progress.check_cancelled()
                result = self.work(progress)
                progress.check_cancelled()
                self.messages.put(("success", result))
            except CancelledError:
                self.messages.put(("cancelled", None))
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
            if kind == "progress" and self.on_progress and not self.cancel_event.is_set():
                self.on_progress(value)
            elif kind != "progress":
                finished = (kind, value)
        if finished:
            kind, value = finished
            if self.cancel_event.is_set() or kind == "cancelled":
                self.on_cancel()
            elif kind == "success":
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
        if (isinstance(widget, (ttk.Button, ttk.Entry, ttk.Combobox))
                and not getattr(widget, "allow_while_busy", False)):
            saved[widget] = str(widget.cget("state"))
            widget.configure(state="disabled")
    return saved
