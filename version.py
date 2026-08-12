"""Single source of truth for the app's version string, read by the
in-app update checker (update_mixin.py). Bump this alongside
installer.iss's MyAppVersion on every release - the two aren't linked
by tooling, so keeping them in sync is a manual step.
"""

APP_VERSION = "1.1.3"
