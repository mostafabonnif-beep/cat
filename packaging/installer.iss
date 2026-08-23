; OUSSAMA Cutter — Inno Setup installer script
; Build with: iscc installer.iss   (Inno Setup 6.x, https://jrsoftware.org/isinfo.php)
; The CI workflow (build-exe.yml) runs this on the Windows runner and attaches
; the resulting setup.exe to the GitHub Release next to OUSSAMA-Cutter.exe.

#ifndef MyAppVersion
  #define MyAppVersion "6.16.1"
#endif

#define MyAppName "OUSSAMA Cutter"
#define MyAppPublisher "OUSSAMA Cutter"
#define MyAppURL "https://github.com/mostafabonnif-beep/cat"
#define MyAppExeName "OUSSAMA-Cutter.exe"

[Setup]
AppId={{8C3F5D1E-2A4B-4C6E-9A1B-VIRALCUTTER01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=OUSSAMA-Cutter-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The bundled ffmpeg/ffprobe/fpcalc + torch make the exe ~2GB; leave room.
MinVersion=10.0.17763
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; the app keeps user data in %USERPROFILE%\.viralcutter and its own VIRALS/
; folder next to the exe — only remove the program itself.
Type: filesandordirs; Name: "{app}"
