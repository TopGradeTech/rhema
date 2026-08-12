; Inno Setup script for Rhema.
;
; Prereq: build the onedir bundle first -
;   python -m PyInstaller --noconfirm --clean main.spec
; then compile this script -
;   ISCC.exe installer.iss
; Output lands in dist\Rhema-Setup.exe.
;
; User data (settings.json, logs) lives directly under {app}: the app's
; _get_app_data_dir (logging_mixin.py) returns the exe's own directory
; whenever it's writable, which it is here since this is a per-user install
; (see below) - it only falls back to %APPDATA%\Rhema for a hypothetical
; machine-wide/Program Files install, which this installer doesn't use.
; Uninstall deliberately leaves settings.json/logs (and the separate
; Hugging Face model cache) behind so they survive reinstalls/upgrades.
;
; Per-user install (PrivilegesRequired=lowest), not machine-wide: no admin
; rights or UAC prompt needed, since the target users may be running this
; on shared/managed computers (church, event AV laptops) where they don't
; have admin credentials. Installs to %LocalAppData%\Programs instead of
; Program Files; the {auto*} constants below resolve to the per-user
; equivalents automatically under this setting.

#define MyAppName "Rhema"
#define MyAppVersion "1.1.4"
#define MyAppPublisher "Top Grade Tech"
#define MyAppExeName "Rhema.exe"

[Setup]
; AppId identifies this app to Windows across versions - never change it,
; or upgrades will install side-by-side instead of in place.
AppId={{B3E18CC5-40CA-4697-BC5D-889707847075}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Rhema-Setup
; lzma2 without solid compression: solid would shave a little more off the
; download but makes both compiling this installer and per-file extraction
; much slower on a bundle this large (~2.5 GB uncompressed).
Compression=lzma2
SolidCompression=no
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; The in-app "Check for Updates" flow (update_mixin.py) closes Rhema
; itself before this installer's file-copy step, then relaunches the
; new exe itself once the install finishes - it doesn't depend on
; Restart Manager for either. CloseApplications stays on regardless, as
; a safety net for the brief window where the old process may not have
; fully released its file handles yet by the time this starts.
; RestartApplications is deliberately NOT set: relying on it in
; addition to the app's own self-relaunch raced against that relaunch
; and could double-launch Rhema after an update.
CloseApplications=yes
; The Rhema mark - Setup.exe's own icon, before anything is installed.
; TranslationApp.exe/Rhema.exe already carries the same icon embedded via
; main.spec's icon=, which Explorer/Start Menu/taskbar/Add-Remove Programs
; all pick up automatically from the exe itself - no separate reference
; needed for those.
SetupIconFile=assets\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\Rhema\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
