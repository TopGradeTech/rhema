; Inno Setup script for the Python Translation App.
;
; Prereq: build the onedir bundle first -
;   python -m PyInstaller --noconfirm --clean main.spec
; then compile this script -
;   ISCC.exe installer.iss
; Output lands in dist\TranslationApp-Setup.exe.
;
; User data (settings.json, logs) is NOT stored under {app}: the app's
; _get_app_data_dir (logging_mixin.py) falls back to
; %APPDATA%\python-translation when the exe dir isn't writable, which is
; always the case for a per-user install. Uninstall deliberately leaves
; that folder (and the Hugging Face model cache) behind so settings
; survive reinstalls/upgrades.
;
; Per-user install (PrivilegesRequired=lowest), not machine-wide: no admin
; rights or UAC prompt needed, since the target users may be running this
; on shared/managed computers (church, event AV laptops) where they don't
; have admin credentials. Installs to %LocalAppData%\Programs instead of
; Program Files; the {auto*} constants below resolve to the per-user
; equivalents automatically under this setting.

#define MyAppName "Python Translation App"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Top Grade Telecom"
#define MyAppExeName "TranslationApp.exe"

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
OutputBaseFilename=TranslationApp-Setup
; lzma2 without solid compression: solid would shave a little more off the
; download but makes both compiling this installer and per-file extraction
; much slower on a bundle this large (~2.5 GB uncompressed).
Compression=lzma2
SolidCompression=no
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\TranslationApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
