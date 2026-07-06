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


class MonitorMixin:
    def get_monitors(self):
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                class RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                class MONITORINFOEXW(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", RECT),
                        ("rcWork", RECT),
                        ("dwFlags", wintypes.DWORD),
                        ("szDevice", wintypes.WCHAR * 32),
                    ]

                user32 = ctypes.windll.user32
                monitors = []

                MONITORENUMPROC = ctypes.WINFUNCTYPE(
                    wintypes.BOOL,
                    wintypes.HMONITOR,
                    wintypes.HDC,
                    ctypes.POINTER(RECT),
                    wintypes.LPARAM,
                )

                def _callback(h_monitor, hdc, lprc, lparam):
                    info = MONITORINFOEXW()
                    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
                    if user32.GetMonitorInfoW(h_monitor, ctypes.byref(info)):
                        monitors.append(
                            {
                                "left": info.rcMonitor.left,
                                "top": info.rcMonitor.top,
                                "right": info.rcMonitor.right,
                                "bottom": info.rcMonitor.bottom,
                                "device": info.szDevice,
                                "primary": bool(info.dwFlags & 1),
                            }
                        )
                    return True

                user32.EnumDisplayMonitors(
                    0,
                    0,
                    MONITORENUMPROC(_callback),
                    0,
                )

                if monitors:
                    return self._sync_monitor_indices(monitors)
            except Exception:
                pass

        try:
            self.root.update_idletasks()
            width = self.root.winfo_screenwidth()
            height = self.root.winfo_screenheight()
        except Exception:
            width = 1920
            height = 1080
        return self._sync_monitor_indices(
            [{"left": 0, "top": 0, "right": width, "bottom": height, "device": "", "primary": True}]
        )

    def set_dpi_awareness(self):
        if os.name != "nt":
            return
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
                return
            except Exception:
                pass
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        except Exception:
            pass

    def get_monitor_labels(self):
        labels = []
        for i, monitor in enumerate(self.monitors):
            width = monitor["right"] - monitor["left"]
            height = monitor["bottom"] - monitor["top"]
            origin = f'{monitor["left"]},{monitor["top"]}'
            primary = " primary" if monitor.get("primary") else ""
            labels.append(f"Monitor {i + 1} ({width}x{height} @ {origin}{primary})")
        return labels

    def _monitor_origin(self, monitor):
        return f'{monitor.get("left", 0)},{monitor.get("top", 0)}'

    def _find_monitor_index_for_point(self, x, y):
        for i, monitor in enumerate(self.monitors or []):
            if (
                monitor.get("left", 0) <= x < monitor.get("right", 0)
                and monitor.get("top", 0) <= y < monitor.get("bottom", 0)
            ):
                return i
        return None

    def _parse_geometry(self, geometry):
        if not geometry:
            return None
        match = re.match(r"(?:(\d+)x(\d+))?([+-]\d+)([+-]\d+)", str(geometry))
        if not match:
            return None
        width = int(match.group(1)) if match.group(1) else None
        height = int(match.group(2)) if match.group(2) else None
        x = int(match.group(3))
        y = int(match.group(4))
        return width, height, x, y

    def _monitor_identity_for_index(self, monitor_index):
        if not self.monitors:
            return "", ""
        idx = max(0, min(int(monitor_index or 0), len(self.monitors) - 1))
        monitor = self.monitors[idx]
        return monitor.get("device", ""), self._monitor_origin(monitor)

    def _resolve_monitor_index(self, saved_index, saved_device, saved_origin):
        if not self.monitors:
            return max(0, int(saved_index or 0))
        if saved_device:
            for i, monitor in enumerate(self.monitors):
                if monitor.get("device") == saved_device:
                    return i
        if saved_origin:
            for i, monitor in enumerate(self.monitors):
                if self._monitor_origin(monitor) == saved_origin:
                    return i
        return max(0, min(int(saved_index or 0), len(self.monitors) - 1))

    def _sync_monitor_indices(self, monitors):
        self.monitors = monitors or []
        if not self.monitors:
            return self.monitors
        self.monitor_index = self._resolve_monitor_index(
            self.monitor_index, self.monitor_device, self.monitor_origin
        )
        self.settings_monitor_index = self._resolve_monitor_index(
            self.settings_monitor_index,
            self.settings_monitor_device,
            self.settings_monitor_origin,
        )
        return self.monitors

    def show_monitor_ids(self, duration_ms=2000):
        self.monitors = self.get_monitors()
        if not self.monitors:
            return
        # Close any existing overlays.
        for win in tuple(self.monitor_id_windows):
            try:
                win.destroy()
            except Exception:
                pass
        self.monitor_id_windows = []

        for i, monitor in enumerate(self.monitors):
            width = max(1, monitor["right"] - monitor["left"])
            height = max(1, monitor["bottom"] - monitor["top"])
            x = monitor["left"]
            y = monitor["top"]
            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.configure(bg="#000000")
            overlay.geometry(f"{width}x{height}+{x}+{y}")
            label = tk.Label(
                overlay,
                text=str(i + 1),
                fg="#ffffff",
                bg="#000000",
                font=(self.font_family, max(80, int(min(width, height) * 0.2)), "bold"),
            )
            label.place(relx=0.5, rely=0.5, anchor="center")
            self.monitor_id_windows.append(overlay)

        def close_overlays():
            for win in tuple(self.monitor_id_windows):
                try:
                    win.destroy()
                except Exception:
                    pass
            self.monitor_id_windows = []

        self.root.after(duration_ms, close_overlays)

    def move_window_to_monitor(self, window, monitor_index, keep_size=True):
        self.monitors = self.get_monitors()
        if not self.monitors or window is None:
            return
        idx = max(0, min(monitor_index, len(self.monitors) - 1))
        monitor = self.monitors[idx]
        width = monitor["right"] - monitor["left"]
        height = monitor["bottom"] - monitor["top"]
        x = monitor["left"]
        y = monitor["top"]

        if keep_size:
            try:
                window.update_idletasks()
                w = window.winfo_width()
                h = window.winfo_height()
            except Exception:
                w, h = width, height
            x = monitor["left"] + max(0, (width - w) // 2)
            y = monitor["top"] + max(0, (height - h) // 2)
            window.geometry(f"{w}x{h}+{x}+{y}")
        else:
            window.geometry(f"{width}x{height}+{x}+{y}")

    def _move_settings_window_to_monitor(self, monitor_index):
        if self.settings_window is None or not self.settings_window.winfo_exists():
            return
        window = self.settings_window
        prev_state = self._safe_window_state(window)
        if prev_state in ("zoomed", "maximized"):
            self._normalize_settings_window_before_move(window)
            self._schedule_settings_window_move(window, monitor_index)
            return

        self.move_window_to_monitor(window, monitor_index, keep_size=True)

    def _safe_window_state(self, window):
        try:
            return window.state()
        except Exception:
            return None

    def _normalize_settings_window_before_move(self, window):
        try:
            window.state("normal")
            window.update_idletasks()
        except Exception:
            pass

    def _schedule_settings_window_move(self, window, monitor_index):
        window.after(60, lambda: self._finish_settings_window_move(window, monitor_index))

    def _finish_settings_window_move(self, window, monitor_index):
        if not window.winfo_exists():
            return
        self.move_window_to_monitor(window, monitor_index, keep_size=True)
        window.after(80, lambda: self._restore_settings_window_zoom(window))

    def _restore_settings_window_zoom(self, window):
        if not window.winfo_exists():
            return
        try:
            window.state("zoomed")
        except Exception:
            pass

    def enter_fullscreen(self):
        if not self.is_fullscreen:
            return
        if self.prev_geometry is None:
            self.prev_geometry = self.root.geometry()
        if self.use_custom_fullscreen:
            self._prepare_custom_fullscreen_state()
            self._apply_custom_fullscreen()
        else:
            self._prepare_borderless_fullscreen_state()
            self._apply_standard_fullscreen()
        self._apply_canvas_padding()

    def _prepare_borderless_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        try:
            self.root.overrideredirect(True)
        except Exception:
            pass

    def _prepare_custom_fullscreen_state(self):
        if self.prev_overrideredirect is None:
            try:
                self.prev_overrideredirect = bool(self.root.overrideredirect())
            except Exception:
                self.prev_overrideredirect = False
        if self.prev_topmost is None:
            try:
                self.prev_topmost = bool(self.root.attributes("-topmost"))
            except Exception:
                self.prev_topmost = False
        try:
            self.root.state("normal")
        except Exception:
            pass

    def _apply_custom_fullscreen(self):
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", bool(self.lock_output_focus))
        self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        # Apply twice to avoid position being offset by window manager.
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        if self.lock_output_focus:
            try:
                self.root.lift()
            except Exception:
                pass
            try:
                self.root.focus_force()
            except Exception:
                pass

    def _apply_standard_fullscreen(self):
        # Some window managers ignore geometry changes while fullscreen is active.
        if self.root.attributes("-fullscreen"):
            self.root.attributes("-fullscreen", False)
        self.move_window_to_monitor(self.root, self.monitor_index, keep_size=False)
        self.root.update_idletasks()
        self.root.attributes("-fullscreen", True)

    def exit_fullscreen(self):
        if self.use_custom_fullscreen:
            self.root.attributes("-fullscreen", False)
            if self.prev_topmost is not None:
                try:
                    self.root.attributes("-topmost", self.prev_topmost)
                except Exception:
                    pass
        else:
            self.root.attributes("-fullscreen", False)
        if self.prev_overrideredirect is not None:
            try:
                self.root.overrideredirect(self.prev_overrideredirect)
            except Exception:
                pass
        if self.prev_geometry:
            self.root.geometry(self.prev_geometry)
        self.prev_geometry = None
        self.prev_overrideredirect = None
        self.prev_topmost = None
        self._apply_canvas_padding()

    def get_audio_devices(self):
        device_infos, input_devices, output_devices = self._get_device_infos()
        self.device_sample_rates_by_index = {}
        for info in device_infos:
            rate = info.get("default_sample_rate")
            if rate:
                try:
                    self.device_sample_rates_by_index[info["index"]] = int(rate)
                except Exception:
                    pass
        self.available_host_apis = self._get_available_host_apis(device_infos)
        loopback_inputs = self._get_loopback_inputs(device_infos)
        self.recommended_host_api = self._pick_recommended_host_api(self.available_host_apis)
        return self._build_device_list(input_devices, output_devices, loopback_inputs)

    def _create_pyaudio(self):
        with self.portaudio_admin_lock:
            return pyaudio.PyAudio()

    def _terminate_pyaudio(self, pa_instance):
        if pa_instance is None:
            return
        with self.portaudio_admin_lock:
            pa_instance.terminate()

    def _open_microphone_source(self, device_index, sample_rate):
        with self.portaudio_admin_lock:
            mic = sr.Microphone(device_index=device_index, sample_rate=sample_rate)
            source = mic.__enter__()
        return mic, source

    def _close_microphone_source(self, mic):
        if mic is None:
            return
        with self.portaudio_admin_lock:
            mic.__exit__(None, None, None)

    def _get_device_infos(self):
        p = self._create_pyaudio()
        input_devices = []
        output_devices = []
        device_infos = []

        try:
            for i in range(p.get_device_count()):
                try:
                    device_info = p.get_device_info_by_index(i)
                except OSError:
                    continue
                host_api_name = self._get_host_api_name(p, device_info)
                if os.name == "nt" and not self._is_mme_host_api(host_api_name):
                    continue
                entry = {
                    "index": i,
                    "name": device_info.get("name", "Unknown"),
                    "max_input": device_info.get("maxInputChannels", 0),
                    "max_output": device_info.get("maxOutputChannels", 0),
                    "host_api": host_api_name,
                    "default_sample_rate": device_info.get("defaultSampleRate"),
                }
                device_infos.append(entry)
                if entry["max_input"] > 0:
                    input_devices.append(entry)
                elif entry["max_output"] > 0:
                    output_devices.append(entry)
        finally:
            self._terminate_pyaudio(p)
        return device_infos, input_devices, output_devices

    def _get_host_api_name(self, pyaudio_instance, device_info):
        host_api_index = device_info.get("hostApi")
        if host_api_index is None:
            return ""
        try:
            return pyaudio_instance.get_host_api_info_by_index(host_api_index).get("name", "")
        except Exception:
            return ""

    def _is_mme_host_api(self, host_api_name):
        return "mme" in (host_api_name or "").strip().lower()

    def _get_available_host_apis(self, device_infos):
        host_apis = {info.get("host_api") for info in device_infos if info.get("host_api")}
        return sorted(host_apis, key=lambda name: name.lower())

    def _first_host_api_keyword(self, lowered, keywords):
        for keyword in keywords:
            for key, value in lowered.items():
                if keyword in key:
                    return value
        return ""

    def _pick_host_api_windows(self, lowered):
        return self._first_host_api_keyword(lowered, ["wasapi", "asio", "wdm", "ks"])

    def _pick_host_api_darwin(self, lowered):
        for key, value in lowered.items():
            if "core" in key and "audio" in key:
                return value
        return ""

    def _pick_host_api_linux(self, lowered):
        return self._first_host_api_keyword(lowered, ["pipewire", "alsa", "pulse"])

    def _pick_recommended_host_api(self, host_api_values):
        values = [v for v in host_api_values if v and v != "Any"]
        lowered = {v.lower(): v for v in values}
        if os.name == "nt":
            return self._pick_host_api_windows(lowered)
        if sys.platform == "darwin":
            return self._pick_host_api_darwin(lowered)
        return self._pick_host_api_linux(lowered)

    def _normalize_host_api(self, name):
        return (name or "").strip().lower()

    def _is_loopback_name(self, name):
        lowered = name.lower()
        keywords = ["loopback", "stereo mix", "what u hear", "what you hear"]
        return any(k in lowered for k in keywords)

    def _is_loopback_label(self, label):
        if not label:
            return False
        lowered = label.lower()
        keywords = ["loopback", "stereo mix", "what u hear", "what you hear"]
        return any(k in lowered for k in keywords)

    def _normalize_device_name(self, name):
        lowered = name.lower()
        lowered = re.sub(r"\([^)]*\)", "", lowered)
        lowered = re.sub(r"\b(loopback|stereo mix|what u hear|what you hear)\b", "", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered.strip()

    def _sort_device_entries(self, entries):
        return sorted(
            entries,
            key=lambda entry: (
                (entry.get("host_api") or "").lower(),
                self._normalize_device_name(entry.get("name", "")),
                entry.get("index", 0),
            ),
        )

    def _extract_device_label_name(self, label):
        if not label:
            return ""
        if "]: " in label:
            return label.split("]: ", 1)[1]
        if ": " in label:
            return label.split(": ", 1)[1]
        return label

    def _resolve_preferred_device_label(self, preferred_label):
        if not preferred_label:
            return ""
        if preferred_label in self.devices:
            return preferred_label
        preferred_type = None
        if preferred_label.startswith("Input"):
            preferred_type = "input"
        elif preferred_label.startswith("Output"):
            preferred_type = "output"
        preferred_name = self._extract_device_label_name(preferred_label)
        preferred_norm = self._normalize_device_name(preferred_name)
        if not preferred_norm:
            return ""
        for label in self.devices:
            if preferred_type and self.device_types.get(label) != preferred_type:
                continue
            label_name = self._extract_device_label_name(label)
            if self._normalize_device_name(label_name) == preferred_norm:
                return label
        return ""

    def _get_loopback_inputs(self, device_infos):
        loopback_inputs = []
        for info in device_infos:
            if info["max_input"] > 0 and self._is_loopback_name(info["name"]):
                loopback_inputs.append(info)
        return loopback_inputs

    def _register_device_label(self, devices, label, idx, device_type):
        devices.append(label)
        self.device_indices[label] = idx
        self.device_types[label] = device_type

    def _format_device_label(self, device_type, entry):
        host_api = entry.get("host_api") or "Unknown"
        recommended = self._normalize_host_api(self.recommended_host_api)
        if recommended and self._normalize_host_api(host_api) == recommended:
            host_api = f"{host_api} (Recommended)"
        index = entry.get("index", 0)
        name = entry.get("name", "Unknown")
        return f"{device_type} ({index}) [{host_api}]: {name}"

    def _group_device_entries(self, entries):
        return sorted(entries, key=lambda item: item.get("index", 0))

    def _resolve_loopback_match(self, output_name, loopback_inputs):
        matched_input = None
        norm_out = self._normalize_device_name(output_name)
        for candidate in loopback_inputs:
            norm_in = self._normalize_device_name(candidate["name"])
            if norm_out and (norm_out in norm_in or norm_in in norm_out):
                matched_input = candidate["index"]
                break
        if matched_input is None and len(loopback_inputs) == 1:
            matched_input = loopback_inputs[0]["index"]
        return matched_input

    def _build_device_list(self, input_devices, output_devices, loopback_inputs):
        # Include input devices and optionally output devices (for loopback/monitor sources).
        devices = []
        self.device_indices = {}
        self.device_types = {}
        self.loopback_output_map = {}

        for entry in self._group_device_entries(self._sort_device_entries(input_devices)):
            label = self._format_device_label("Input", entry)
            self._register_device_label(devices, label, entry.get("index", 0), "input")

        if self.allow_loopback:
            for entry in self._group_device_entries(self._sort_device_entries(output_devices)):
                label = self._format_device_label("Output", entry)
                self._register_device_label(devices, label, entry.get("index", 0), "output")

                matched_input = self._resolve_loopback_match(entry.get("name", ""), loopback_inputs)
                if matched_input is not None:
                    self.loopback_output_map[label] = matched_input

        return devices if devices else ["No devices found"]
    
