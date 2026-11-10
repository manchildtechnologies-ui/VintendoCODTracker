; Vintendo COD Tracker - per-user installer (Inno Setup 6).
;
; Stage the files first (see installer/README-INSTALLER.md):
;   installer\stage\VintendoCODTracker.exe   (from: python build.py)
;   ..\tracker.ico                           (repo root, used by the shortcuts)
;
; Then compile:  iscc installer\VintendoCODTracker.iss
;
; Per-user install: no admin rights needed. User data (matches, settings,
; screenshots) lives in
; %LOCALAPPDATA%\Vintendo COD Tracker and survives uninstall/reinstall.

#define MyAppName "Vintendo COD Tracker"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Vintendo"

[Setup]
AppId={{B4C7E2A1-5F38-4D6E-9A0B-7C1D2E3F4A5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Vintendo COD Tracker
DefaultGroupName=Vintendo COD Tracker
PrivilegesRequired=lowest
OutputDir=..\dist-installer
OutputBaseFilename=Setup-VintendoCODTracker-{#MyAppVersion}
SetupIconFile=..\tracker.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
CloseApplications=yes
CloseApplicationsFilter=VintendoCODTracker.exe
UninstallDisplayName={#MyAppName}

[Files]
Source: "stage\VintendoCODTracker.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\tracker.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Vintendo COD Tracker"; Filename: "{app}\VintendoCODTracker.exe"; IconFilename: "{app}\tracker.ico"
Name: "{group}\Uninstall Vintendo COD Tracker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Vintendo COD Tracker"; Filename: "{app}\VintendoCODTracker.exe"; IconFilename: "{app}\tracker.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\VintendoCODTracker.exe"; Description: "Launch Vintendo COD Tracker"; Flags: nowait postinstall skipifsilent unchecked
