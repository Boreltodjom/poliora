#define MyAppName "Poliora"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Poliora"
#define MyAppExeName "Poliora.exe"

[Setup]
AppId={{B7E371D5-167B-4BA9-8EF5-96A723B4A9E1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Poliora
DefaultGroupName=Poliora
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\artifacts
OutputBaseFilename=Poliora-Setup-Windows
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\assets\poliora.ico
UninstallDisplayIcon={app}\{#MyAppExeName}


[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Poliora"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Poliora"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Poliora"; Flags: nowait postinstall skipifsilent