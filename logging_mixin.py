import speech_recognition as sr
import tkinter as tk
from tkinter import messagebox
from tkinter import colorchooser
from tkinter import filedialog
from tkinter import font as tkfont
from threading import Thread, Lock, Event
import queue
import time
import re
import requests
import struct
import json
import pyaudio
from collections import deque, Counter
import os
import sys
import traceback
import io
import math
import statistics
import tempfile
import gc
import wave
import importlib.util
import ttkbootstrap as ttkb
from ttkbootstrap.constants import PRIMARY


class LoggingMixin:
    def _install_exception_hook(self):
        sys.excepthook = self._handle_unhandled_exception
        self._install_tk_exception_hook()
        self._install_thread_exception_hook()
        self._enable_fault_handler()
        self._register_process_exit_logger()

    def _handle_unhandled_exception(self, exc_type, exc, tb):
        self._write_unhandled_exception(exc_type, exc, tb)
        try:
            messagebox.showerror("Unhandled Error", f"{exc}")
        except Exception:
            pass

    def _write_unhandled_exception(self, exc_type, exc, tb):
        try:
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Unhandled Exception ---\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass

    def _install_tk_exception_hook(self):
        try:
            self.root.report_callback_exception = self._handle_unhandled_exception
        except Exception:
            pass

    def _install_thread_exception_hook(self):
        try:
            import threading

            threading.excepthook = self._handle_thread_exception
        except Exception:
            pass

    def _handle_thread_exception(self, args):
        try:
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Thread Exception ---\n")
                f.write(f"Thread: {args.thread.name}\n")
                traceback.print_exception(
                    args.exc_type,
                    args.exc_value,
                    args.exc_traceback,
                    file=f,
                )
        except Exception:
            pass

    def _enable_fault_handler(self):
        try:
            import faulthandler

            if not getattr(self, "_fault_log_file", None):
                self._fault_log_file = open(self.error_log_path, "a", encoding="utf-8")
            faulthandler.enable(self._fault_log_file, all_threads=True)
        except Exception:
            pass

    def _register_process_exit_logger(self):
        try:
            import atexit

            atexit.register(self._log_process_exit)
        except Exception:
            pass

    def _log_process_exit(self):
        try:
            self._log_status("Process exiting")
            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write("\n--- Process Exit ---\n")
                f.write("".join(traceback.format_stack(limit=10)))
        except Exception:
            pass

    def _get_app_data_dir(self):
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            if os.path.isdir(exe_dir) and os.access(exe_dir, os.W_OK):
                return exe_dir
            appdata_root = os.getenv("APPDATA") or os.path.expanduser("~")
            fallback_dir = os.path.join(appdata_root, "python-translation")
            try:
                os.makedirs(fallback_dir, exist_ok=True)
                return fallback_dir
            except Exception:
                return exe_dir
        return os.path.dirname(os.path.abspath(__file__))

    def _build_windows_startup_command(self):
        if getattr(sys, "frozen", False):
            return f"\"{sys.executable}\""
        script_path = os.path.abspath(__file__)
        return f"\"{sys.executable}\" \"{script_path}\""

    def _set_windows_startup_enabled(self, enabled):
        if os.name != "nt":
            return
        import winreg

        access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            self.WINDOWS_RUN_KEY_PATH,
            0,
            access,
        ) as key:
            if enabled:
                command = self._build_windows_startup_command()
                winreg.SetValueEx(
                    key,
                    self.WINDOWS_STARTUP_VALUE_NAME,
                    0,
                    winreg.REG_SZ,
                    command,
                )
            else:
                try:
                    winreg.DeleteValue(key, self.WINDOWS_STARTUP_VALUE_NAME)
                except FileNotFoundError:
                    pass

    def _sync_windows_startup_entry(self):
        try:
            self._set_windows_startup_enabled(bool(self.start_with_windows))
        except Exception:
            pass

    def _get_error_log_path(self):
        base_dir = self.app_data_dir
        if os.name == "nt":
            logs_dir = os.path.join(base_dir, "logs")
            try:
                os.makedirs(logs_dir, exist_ok=True)
            except Exception:
                pass
            timestamp = getattr(self, "log_session_timestamp", time.strftime("%Y%m%d-%H%M%S"))
            return os.path.join(logs_dir, f"error-{timestamp}.log")
        return os.path.join(base_dir, "error.log")

    def _get_transcript_trace_path(self):
        base_dir = self.app_data_dir
        logs_dir = os.path.join(base_dir, "logs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = getattr(self, "log_session_timestamp", time.strftime("%Y%m%d-%H%M%S"))
        return os.path.join(logs_dir, f"transcript-{timestamp}.log")

    def _get_finalized_transcript_path(self):
        base_dir = self.app_data_dir
        logs_dir = os.path.join(base_dir, "logs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = getattr(self, "log_session_timestamp", time.strftime("%Y%m%d-%H%M%S"))
        return os.path.join(logs_dir, f"finalized-{timestamp}.log")

    def _get_transcribed_text_path(self):
        return self._get_session_log_path("transcribed")

    def _get_translated_text_path(self):
        return self._get_session_log_path("translated")

    def _get_session_log_path(self, prefix):
        base_dir = self.app_data_dir
        logs_dir = os.path.join(base_dir, "logs")
        try:
            os.makedirs(logs_dir, exist_ok=True)
        except Exception:
            pass
        timestamp = getattr(self, "log_session_timestamp", time.strftime("%Y%m%d-%H%M%S"))
        return os.path.join(logs_dir, f"{prefix}-{timestamp}.log")

    def _prune_old_log_sessions(self):
        try:
            max_sessions = max(1, int(getattr(self, "log_retained_sessions", 5)))
        except Exception:
            max_sessions = 5
        try:
            logs_dir = os.path.join(self.app_data_dir, "logs")
            if not os.path.isdir(logs_dir):
                return
            prefixes = tuple(getattr(self, "session_log_prefixes", ()))
            if not prefixes:
                return
            prefix_pattern = "|".join(re.escape(prefix) for prefix in prefixes)
            pattern = re.compile(rf"^(?:{prefix_pattern})-(\d{{8}}-\d{{6}})\.log$")
            files_by_session = {}
            for name in os.listdir(logs_dir):
                match = pattern.match(name)
                if not match:
                    continue
                session = match.group(1)
                files_by_session.setdefault(session, []).append(os.path.join(logs_dir, name))
            current_session = getattr(self, "log_session_timestamp", "")
            if current_session:
                files_by_session.setdefault(current_session, [])
            keep_sessions = set(sorted(files_by_session.keys(), reverse=True)[:max_sessions])
            for session, paths in files_by_session.items():
                if session in keep_sessions:
                    continue
                for path in paths:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception:
            pass

    def _log_status(self, msg):
        if not self.status_log_enabled:
            return
        now = time.time()
        if msg == self.last_status_message:
            return
        self.last_status_message = msg
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            with self.status_log_lock:
                with open(self.error_log_path, "a", encoding="utf-8") as f:
                    f.write(f"[{timestamp}.{ms:03d}] STATUS: {msg}\n")
        except Exception:
            pass

    def _trace_pipeline(self, stage, text="", **meta):
        if not self.transcript_trace_enabled:
            return
        try:
            now = time.time()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            clean_text = "" if text is None else str(text).replace("\r", " ").replace("\n", " ")
            entry = {
                "ts": f"{timestamp}.{ms:03d}",
                "stage": str(stage),
                "chars": len(clean_text),
                "text": clean_text,
            }
            if meta:
                entry["meta"] = meta
            with self.transcript_trace_lock:
                with open(self.transcript_trace_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_finalized_sentence(self, text, **meta):
        if not getattr(self, "finalized_logs_enabled", True):
            return
        clean_text = "" if text is None else str(text).strip()
        if not clean_text:
            return
        try:
            now = time.time()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            entry = {
                "ts": f"{timestamp}.{ms:03d}",
                "text": clean_text.replace("\r", " ").replace("\n", " "),
                "chars": len(clean_text),
            }
            if meta:
                entry["meta"] = meta
            with self.finalized_transcript_lock:
                with open(self.finalized_transcript_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_transcribed_text(self, text, **meta):
        if not self.comparison_logs_enabled:
            return
        clean_text = "" if text is None else str(text).strip()
        if not clean_text:
            return
        try:
            now = time.time()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            with self.transcribed_text_lock:
                self.transcribed_log_sequence += 1
                entry = {
                    "id": self.transcribed_log_sequence,
                    "ts": f"{timestamp}.{ms:03d}",
                    "text": clean_text.replace("\r", " ").replace("\n", " "),
                    "chars": len(clean_text),
                }
                if meta:
                    entry["meta"] = meta
                with open(self.transcribed_text_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_translated_text(self, source_text, translated_text, **meta):
        if not self.comparison_logs_enabled:
            return
        clean_translation = "" if translated_text is None else str(translated_text).strip()
        if not clean_translation:
            return
        clean_source = "" if source_text is None else str(source_text).strip()
        try:
            now = time.time()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
            ms = int((now - int(now)) * 1000)
            with self.translated_text_lock:
                self.translated_log_sequence += 1
                entry = {
                    "id": self.translated_log_sequence,
                    "ts": f"{timestamp}.{ms:03d}",
                    "source_text": clean_source.replace("\r", " ").replace("\n", " "),
                    "source_chars": len(clean_source),
                    "text": clean_translation.replace("\r", " ").replace("\n", " "),
                    "chars": len(clean_translation),
                }
                if meta:
                    entry["meta"] = meta
                with open(self.translated_text_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

