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

"""Phase 10 of the pywebview port: the in-app updater's download progress
popup. WebUpdateMixin(UpdateMixin) overrides _download_and_install_update
- a real, unmodified UI+logic-interleaved method (a Tk Toplevel/
Progressbar built inline, with the download worker's own progress
callback closing over those Tk objects directly) - per the port plan's own
budgeting for this method as a full rewrite, not a thin override.

Everything else update_mixin.py defines is reused completely unmodified:
- check_for_updates/_check_for_updates_worker and _show_update_check_error/
  _show_up_to_date/_prompt_update_available already work correctly against
  a non-Tk app, since Phase 1 routed their dialogs through
  _show_error_dialog/_show_info_dialog/_confirm_yes_no (web_messagebox.py's
  ctypes MessageBoxW).
- _finish_update_download takes `popup` as a PARAMETER, not self.popup -
  its real body only ever calls popup.grab_release()/popup.destroy(), both
  already wrapped in try/except. A real pywebview Window has a real
  .destroy() (so the progress window actually closes) and simply lacks
  .grab_release() (Tk-only; the AttributeError is silently caught, exactly
  like it would be for any other reason) - so this override passes the
  real pywebview window straight through, unmodified, rather than needing
  a stand-in object.
- _launch_installer_and_exit is already Tk-free end to end (subprocess/
  tempfile/self._show_error_dialog/self.on_closing - nothing Tk-specific
  at all) and needed no changes either.
"""

import json
import os
import tempfile
import threading
import urllib.request

from update_mixin import UpdateMixin, INSTALLER_ASSET_NAME, _USER_AGENT, _REQUEST_TIMEOUT_S

PROGRESS_HTML = r"""
<!doctype html><html><head><meta charset="utf-8"><title>Downloading Update</title>
<style>
:root{color-scheme:dark}
html,body{margin:0;background:#1E2228;color:#E5E7EB;
  font:14px/1.4 "Segoe UI",system-ui,sans-serif;overflow:hidden}
#wrap{padding:24px;box-sizing:border-box}
h3{margin:0 0 14px;font-size:14px;font-weight:700;color:#F3F4F6}
#track{height:14px;background:#14171C;border:1px solid #3A3A3A;border-radius:4px;overflow:hidden}
#fill{height:100%;width:0;background:#5B8FF7;transition:width 120ms linear}
#statusText{margin-top:10px;font-size:12px;color:#9CA3AF;text-align:center}
</style></head><body>
<div id="wrap">
  <h3>Downloading update...</h3>
  <div id="track"><div id="fill"></div></div>
  <div id="statusText">Connecting...</div>
</div>
<script>
function setProgress(pct, text){
  document.getElementById('fill').style.width = Math.max(0, Math.min(100, pct)) + '%'
  document.getElementById('statusText').textContent = text
}
</script></body></html>
"""


class WebUpdateMixin(UpdateMixin):
    def _download_and_install_update(self, asset_url):
        import webview

        # The download can't be meaningfully cancelled mid-stream (partial
        # installer files aren't useful) - matches the real popup's own
        # WM_DELETE_WINDOW-blocking intent by simply not giving this
        # window a close button at all (frameless), rather than needing a
        # closing-event veto.
        popup = webview.create_window(
            "Downloading Update",
            html=PROGRESS_HTML,
            width=360,
            height=140,
            resizable=False,
            frameless=True,
            on_top=True,
            background_color="#1E2228",
        )

        def push_progress(done_bytes, total_bytes):
            if total_bytes > 0:
                pct = min(100.0, done_bytes * 100.0 / total_bytes)
                text = f"{done_bytes // (1024 * 1024)} MB / {total_bytes // (1024 * 1024)} MB"
            else:
                pct = 0.0
                text = f"{done_bytes // (1024 * 1024)} MB downloaded"
            try:
                popup.evaluate_js(
                    "setProgress(%s, %s)" % (json.dumps(pct), json.dumps(text))
                )
            except Exception:
                pass

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
                                0, lambda d=done_bytes, t=total_bytes: push_progress(d, t)
                            )
            except Exception as exc:
                self.root.after(0, lambda: self._finish_update_download(popup, exc=exc))
                return
            self.root.after(
                0, lambda: self._finish_update_download(popup, installer_path=installer_path)
            )

        threading.Thread(target=worker, daemon=True).start()
