; Socksicle Windows Installer Script (Inno Setup)
; Compiles the Nuitka standalone output into a standalone setup executable.

#define MyAppName "Socksicle"
#ifndef MyAppVersion
#define MyAppVersion "0.1.2-rc.1"
#endif
#ifndef OutputBaseFilename
#define OutputBaseFilename "Socksicle-Setup-" + MyAppVersion + "-x64"
#endif
#ifndef MySourceDir
#define MySourceDir "..\..\dist\Socksicle.dist"
#endif
#define MyAppPublisher "iwtsd"
#define MyAppURL "https://github.com/socksicle/desktop"
#define MyAppExeName "Socksicle.exe"

[Setup]
AppId={{E5D4B2A1-8C3F-4F91-9A7E-2B6C8D1E3F5A}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\..\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64
OutputDir=..\..\dist-installer
OutputBaseFilename={#OutputBaseFilename}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Copy all files compiled by Nuitka
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
