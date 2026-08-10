"""Checks a public, releases-only GitHub repo for newer Rhema builds and
installs them. Deliberately points at a separate public repo
(TopGradeTelecom/rhema-releases) that only ever holds release binaries,
never source - so the check needs no auth token, and there's nothing in
it that could expose this (private) repo's code if the app were
reverse-engineered.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from tkinter import messagebox

import ttkbootstrap as ttkb
import tkinter as tk

from version import APP_VERSION

GITHUB_RELEASES_REPO = "TopGradeTelecom/rhema-releases"
GITHUB_LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_RELEASES_REPO}/releases/latest"
)
INSTALLER_ASSET_NAME = "Rhema-Setup.exe"
_REQUEST_TIMEOUT_S = 10
_USER_AGENT = "Rhema-Update-Check"
_DIALOG_TITLE = "Check for Updates"


def _version_tuple(version_string):
    """Parses a dotted version string into a comparable tuple of ints,
    tolerant of a leading 'v' and non-numeric suffixes (e.g. "1.2.0-rc1"
    still compares sanely against "1.2.0").
    """
    parts = []
    for chunk in re.split(r"[.-]", str(version_string).strip().lstrip("vV")):
        match = re.match(r"\d+", chunk)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


class UpdateMixin:
    def check_for_updates(self, manual=True):
        """Kicks off an update check on a background thread. `manual`
        gates whether a result is surfaced when there's nothing to
        report (already up to date, or the check failed) - the
        button-triggered call always wants that feedback; a possible
        future silent startup check would pass manual=False to only
        ever surface a genuine update.
        """
        thread = threading.Thread(
            target=self._check_for_updates_worker, args=(manual,), daemon=True
        )
        thread.start()

    def _check_for_updates_worker(self, manual):
        try:
            request = urllib.request.Request(
                GITHUB_LATEST_RELEASE_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": _USER_AGENT,
                },
            )
            with urllib.request.urlopen(
                request, timeout=_REQUEST_TIMEOUT_S
            ) as response:
                release = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if manual:
                self.root.after(0, lambda: self._show_update_check_error(exc))
            return

        latest_version = str(release.get("tag_name", "")).strip()
        asset_url = None
        for asset in release.get("assets", None) or []:
            if asset.get("name") == INSTALLER_ASSET_NAME:
                asset_url = asset.get("browser_download_url")
                break

        if not latest_version or not asset_url:
            if manual:
                self.root.after(
                    0,
                    lambda: self._show_update_check_error(
                        RuntimeError(
                            "The latest release is missing a usable "
                            f"{INSTALLER_ASSET_NAME} asset."
                        )
                    ),
                )
            return

        if _version_tuple(latest_version) > _version_tuple(APP_VERSION):
            self.root.after(
                0, lambda: self._prompt_update_available(latest_version, asset_url)
            )
        elif manual:
            self.root.after(0, self._show_up_to_date)

    def _show_update_check_error(self, exc):
        messagebox.showerror(
            _DIALOG_TITLE,
            f"Couldn't check for updates:\n{exc}",
        )

    def _show_up_to_date(self):
        messagebox.showinfo(
            _DIALOG_TITLE,
            f"You're up to date (v{APP_VERSION}).",
        )

    def _prompt_update_available(self, latest_version, asset_url):
        proceed = messagebox.askyesno(
            "Update available",
            f"A new version of Rhema is available: {latest_version} "
            f"(you have v{APP_VERSION}).\n\n"
            "Download and install it now? Rhema will close during the "
            "update and reopen automatically once it's done.",
        )
        if proceed:
            self._download_and_install_update(asset_url)

    def _download_and_install_update(self, asset_url):
        parent = (
            self.settings_window
            if self.settings_window is not None and self.settings_window.winfo_exists()
            else self.root
        )
        palette = self._settings_palette()
        popup = tk.Toplevel(parent)
        popup.title("Downloading Update")
        popup.configure(bg=palette["section_bg"])
        popup.resizable(False, False)
        popup.transient(parent)
        # The download can't be meaningfully cancelled mid-stream from
        # here (partial installer files aren't useful), so block the
        # close button instead of leaving one that appears to hang.
        popup.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = tk.Frame(popup, bg=palette["section_bg"], padx=24, pady=18)
        frame.pack()
        tk.Label(
            frame,
            text="Downloading update...",
            bg=palette["section_bg"],
            fg=palette["text"],
            font=(self.ui_font_family, 11, "bold"),
        ).pack(pady=(0, 10))
        progress_var = tk.DoubleVar(value=0.0)
        progress = ttkb.Progressbar(
            frame, mode="determinate", length=280, variable=progress_var, maximum=100.0
        )
        progress.pack()
        status_var = tk.StringVar(value="Connecting...")
        tk.Label(
            frame,
            textvariable=status_var,
            bg=palette["section_bg"],
            fg=palette["muted_text"],
            font=(self.ui_font_family, 9),
        ).pack(pady=(8, 0))

        popup.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() - popup.winfo_width()) // 2
            py = parent.winfo_rooty() + (parent.winfo_height() - popup.winfo_height()) // 2
            popup.geometry(f"+{max(0, px)}+{max(0, py)}")
        except Exception:
            pass
        try:
            popup.grab_set()
        except Exception:
            pass

        def set_progress(done_bytes, total_bytes):
            if total_bytes > 0:
                progress_var.set(min(100.0, done_bytes * 100.0 / total_bytes))
                status_var.set(
                    f"{done_bytes // (1024 * 1024)} MB / "
                    f"{total_bytes // (1024 * 1024)} MB"
                )
            else:
                status_var.set(f"{done_bytes // (1024 * 1024)} MB downloaded")

        def worker():
            try:
                installer_dir = tempfile.mkdtemp(prefix="rhema_update_")
                installer_path = os.path.join(installer_dir, INSTALLER_ASSET_NAME)
                request = urllib.request.Request(
                    asset_url, headers={"User-Agent": _USER_AGENT}
                )
                with urllib.request.urlopen(
                    request, timeout=_REQUEST_TIMEOUT_S
                ) as response:
                    total_bytes = int(response.headers.get("Content-Length", 0) or 0)
                    done_bytes = 0
                    with open(installer_path, "wb") as installer_file:
                        while True:
                            chunk = response.read(1024 * 1024)
                            if not chunk:
                                break
                            installer_file.write(chunk)
                            done_bytes += len(chunk)
                            self.root.after(
                                0, lambda d=done_bytes, t=total_bytes: set_progress(d, t)
                            )
            except Exception as exc:
                self.root.after(0, lambda: self._finish_update_download(popup, exc=exc))
                return
            self.root.after(
                0, lambda: self._finish_update_download(popup, installer_path=installer_path)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_download(self, popup, installer_path=None, exc=None):
        try:
            popup.grab_release()
        except Exception:
            pass
        try:
            popup.destroy()
        except Exception:
            pass
        if exc is not None:
            messagebox.showerror(
                _DIALOG_TITLE,
                f"Couldn't download the update:\n{exc}",
            )
            return
        self._launch_installer_and_exit(installer_path)

    def _launch_installer_and_exit(self, installer_path):
        try:
            subprocess.Popen(
                [
                    installer_path,
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/CLOSEAPPLICATIONS",
                    "/RESTARTAPPLICATIONS",
                ],
                close_fds=True,
            )
        except Exception as exc:
            messagebox.showerror(
                _DIALOG_TITLE,
                f"Couldn't launch the installer:\n{exc}",
            )
            return
        self.on_closing()
