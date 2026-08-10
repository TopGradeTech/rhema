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
#define MyAppVersion "1.0.1"
#define MyAppPublisher "Top Grade Telecom"
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
; Explicit even though both already default to yes in Inno 6: the
; in-app "Check for Updates" flow (update_mixin.py) launches this
; installer with /VERYSILENT while Rhema itself is still running, and
; relies on Restart Manager to close it (its files are locked) and
; relaunch it afterward rather than requiring the app to have fully
; exited before the installer even starts.
CloseApplications=yes
RestartApplications=yes
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
