r"""Phase 1 verification: does webview_bridge.py's TkVariableInterpreter -
the hidden, mainloop'd tk.Tk() interpreter the port keeps in order to reuse
SettingsLogicMixin's dirty-tracking machinery and the NLLB status/message
vars as real tk.Variable/tk.Text objects, unmodified - survive real
concurrent writes AND reads from multiple worker threads, using only the
marshaling primitive it actually exposes (run_threadsafe()), with no crash
or corrupted state?

That primitive is not the one an earlier planning pass assumed. The first
version of this test called `var_root.after(0, lambda: var.set(v))`
directly from worker threads - the same shape every real NLLB status-var
call site in settings_ui_mixin.py uses successfully against `self.root`
today - and it failed immediately and reproducibly:
"RuntimeError: Calling Tcl from different apartment" and
"RuntimeError: main thread is not in main loop". The difference: in the
existing Tk app, `self.root.mainloop()` runs on the process's real main
thread, and only OTHER threads ever call `.after()` on it - the standard,
supported topology. A HIDDEN interpreter needs its own dedicated thread
instead (WebTranslationApp's real main thread is spoken for by pywebview's
own event loop), and calling that root's `.after()` directly from an
arbitrary outside thread is not safe in that topology. See
TkVariableInterpreter's docstring in webview_bridge.py for the full
finding and the corrected primitive (queue.Queue + a self-rescheduling
`.after()` poll loop that only ever runs on the interpreter's own thread).

Setup: nothing extra - tkinter is stdlib; webview_bridge.py has no
non-stdlib import at module scope (pywebview/pythonnet are only imported
lazily, inside functions that need them), so this test can import it
without pywebview installed.

Run:  .venv\Scripts\python.exe experiments\web_variable_shim_stress.py

Nothing here is imported by the app. Delete the folder and Rhema is
unchanged.
"""

import os
import random
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webview_bridge import TkVariableInterpreter  # noqa: E402

THREAD_COUNT = 16
WRITES_PER_THREAD = 500
VALID_VALUES = [f"status-{i}" for i in range(32)]


def main():
    log = []

    def report(label, ok, detail=""):
        status = "PASS" if ok else "FAIL"
        line = f"[{status}] {label}" + (f" - {detail}" if detail else "")
        print(line, flush=True)
        log.append((status, label, detail))

    interpreter = TkVariableInterpreter()
    report("interpreter started", interpreter.root is not None)

    errors = []
    interpreter.root.report_callback_exception = lambda *exc_info: errors.append(exc_info)

    status_var = tk.StringVar(master=interpreter.root, value="initial")
    read_results = []
    read_lock = threading.Lock()

    def worker(thread_id):
        rng = random.Random(thread_id)
        for _ in range(WRITES_PER_THREAD):
            value = rng.choice(VALID_VALUES)

            # The rule under test: every write to a shim-backed variable
            # goes through run_threadsafe(), never var.set() directly and
            # never interpreter.root.after() directly.
            interpreter.run_threadsafe(lambda v=value: status_var.set(v))

            # Interleave a marshaled read too - _capture_settings_snapshot
            # and friends read shim-backed vars from the same worker
            # threads that write NLLB status, so a real stress test should
            # cover both directions, not just writes.
            box = {}

            def _read(box=box):
                box["value"] = status_var.get()

            interpreter.run_threadsafe(_read)
            with read_lock:
                read_results.append(box)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - start

    report(
        "all worker threads completed",
        all(not t.is_alive() for t in threads),
        f"{elapsed:.2f}s for {THREAD_COUNT} threads x {WRITES_PER_THREAD} writes",
    )

    # Drain the queue: schedule one more marshaled no-op and wait for it,
    # so every earlier run_threadsafe() closure is guaranteed to have run
    # before this test inspects final state.
    drained = threading.Event()
    interpreter.run_threadsafe(drained.set)
    report("final marshaled callback drained", drained.wait(timeout=10))

    report("no exception surfaced from the Tcl interpreter", len(errors) == 0, str(errors[:3]))

    final_value = status_var.get()
    report(
        "final variable value is one of the values actually written (not corrupted)",
        final_value in VALID_VALUES,
        repr(final_value),
    )

    missing_reads = [r for r in read_results if "value" not in r]
    report(
        "every scheduled read actually ran and returned a valid value",
        not missing_reads and all(r["value"] in VALID_VALUES for r in read_results),
        f"{len(missing_reads)} missing of {len(read_results)}",
    )

    interpreter.shutdown()
    report("interpreter thread exited cleanly after shutdown()", not interpreter._thread.is_alive())

    ok = all(status == "PASS" for status, _, _ in log)
    print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
