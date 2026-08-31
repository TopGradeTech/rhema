# Rhema - live speech transcription and translation, run locally.
# Copyright (C) 2026 Zachary Price
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

import multiprocessing
import os
import sys
import traceback


class AppLifecycleMixin:
    """Shutdown watchdog, extracted verbatim from TranslationApp.on_closing
    (main.py) during the pywebview port so the Tk app (TranslationApp) and
    the pywebview app (WebTranslationApp) share one copy - hand-duplicating
    this hard-won, safety-critical logic risked a shutdown bugfix landing in
    one copy and not the other.

    Every method this touches (_log_status, error_log_path,
    _force_kill_realtime_stt_processes, save_settings, _stop_realtime_stt,
    stop_video_feed) comes from a mixin both apps already share unchanged
    (LoggingMixin/SettingsMixin/RealtimeSttMixin/VideoCaptureMixin), so this
    extraction changes nothing about the Tk app's behavior.

    NOTE for the pywebview port: this was tuned against Tk's specific hang
    modes (mainloop teardown, RealtimeSTT subprocess joins). pywebview's
    WebView2 disposal path is a different failure surface (.NET object
    disposal, thread-affinity requirements for window destruction) - expect
    WebTranslationApp to need its own additional teardown steps (destroying
    its windows) layered on top of this, not assume this base is already
    sufficient. See the port plan's Phase 3 note. self.root.quit() below is
    a Tk-specific call already guarded by try/except - harmless (silently
    no-ops) against a non-Tk self.root."""

    def on_closing(self):
        try:
            self._log_status("App closing requested")
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Close Requested ---\n")
                f.write("".join(traceback.format_stack(limit=10)))
        except Exception:
            pass

        # RealtimeSTT's own shutdown_recorder() joins its recording/realtime
        # threads with no timeout, so if a subprocess died uncleanly (e.g.
        # from the same Ctrl+C break event this process received) that join
        # can block forever. Arm a watchdog that force-exits regardless, so
        # closing the app is never held hostage by that internal hang. It
        # also force-kills RealtimeSTT's child processes by PID first
        # (_force_kill_realtime_stt_processes) rather than relying solely on
        # os._exit(0) plus Windows' Job Object cleanup to take them down -
        # that cleanup isn't reliable enough in practice: if the graceful
        # shutdown below is still stuck when this fires, shutdown_event was
        # never set, so the transcription subprocess's poll_connection loop
        # has no way to notice and just spins on BrokenPipeError forever
        # once its parent pipe breaks, showing up as an orphaned process the
        # user has to end from Task Manager.
        import os as _os
        import threading as _threading

        def _watchdog_force_exit():
            try:
                self._force_kill_realtime_stt_processes()
            except Exception:
                pass
            _os._exit(0)

        watchdog = _threading.Timer(3.0, _watchdog_force_exit)
        watchdog.daemon = True
        watchdog.start()

        # Persist window geometry/maximized state on the way out. Until this
        # was added, save_settings() only ran on Apply (plus audio-device
        # change and Hardware Autodetect), so resizing or maximizing either
        # window and then just closing the app silently discarded it - the
        # window layout is the one thing here the user adjusts without ever
        # touching Apply. Placed after the watchdog is armed so a stuck
        # write cannot wedge the close, and before any teardown so both
        # windows can still be asked for their state. Only already-applied
        # self.* state is serialized (save_settings never reads the Tk
        # vars), so this cannot commit edits left pending without Apply.
        try:
            self.save_settings()
        except Exception:
            pass

        self.listening = False
        try:
            self._stop_realtime_stt()
        except Exception:
            pass
        try:
            self._force_kill_realtime_stt_processes()
        except Exception:
            pass
        try:
            self.stop_video_feed()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        # Hard-exit so RealtimeSTT's multiprocessing child processes are also
        # killed. root.quit() alone only exits the Tkinter loop; os._exit()
        # terminates the entire process tree immediately.
        _os._exit(0)


def bootstrap_and_run(app_class):
    """Shared entry-point bootstrap, extracted verbatim from main.py's
    `if __name__ == "__main__":` block during the pywebview port - both
    main.py and main_webview.py call this instead of each having their own
    copy of the freeze_support()/CWD-pinning logic to drift out of sync."""
    # Must be the very first thing here, before anything else runs. RealtimeSTT
    # spawns its transcription worker via multiprocessing.Process; in a frozen
    # (PyInstaller) build, a child spawned that way re-invokes this same exe
    # with special multiprocessing bootstrap arguments instead of a normal
    # python.exe + script invocation. Without freeze_support() to recognize
    # those arguments and run only the child worker, the "child" falls through
    # to this same block and launches a whole second app instead - which
    # spawns its own child the same way, recursively, spawning dozens of
    # windows and processes within seconds (confirmed 2026-07-23: froze the
    # dev machine, required a hard reboot).
    multiprocessing.freeze_support()
    # Some launch paths (e.g. the in-app updater's silent relaunch via
    # `start`, update_mixin.py) can hand this process a working
    # directory of C:\Windows\System32 instead of its own install
    # folder. RealtimeSTT opens its own debug log via a relative path
    # ('realtimesst.log', not something this app controls), which then
    # fails with a permission error writing into System32 as a
    # non-elevated user. Pinning CWD to the app's own directory up
    # front fixes that regardless of how the process was launched.
    # __file__ here is this module's own path, not main.py's/
    # main_webview.py's - equivalent only because every module in this repo
    # lives in the same directory (no subpackages). If that ever changes,
    # this needs the caller's __file__ passed in instead.
    try:
        os.chdir(
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__))
        )
    except Exception:
        pass
    return app_class()
