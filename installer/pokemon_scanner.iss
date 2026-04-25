; CardLens — Inno Setup installer script
; Requires Inno Setup 6.x  (https://jrsoftware.org/isinfo.php)
;
; Prerequisites:
;   1. Run .\build.ps1 first — this script expects dist\CardLens\ to exist.
;   2. Open this file in the Inno Setup Compiler (ISCC) or run:
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\pokemon_scanner.iss
; Output: installer\Output\CardLensSetup.exe

#define AppName    "CardLens"
#define AppVersion "0.5.0"
#define AppPublisher "CardLens Dev"
#define AppURL "https://github.com/webster586/cardlens"
#define AppExeName "CardLens.exe"
#define DistDir    "..\dist\CardLens"

[Setup]
AppId={{A3F7B2D1-4E9C-4A1B-8F62-7D3E5C9A0B24}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
; Install to %LOCALAPPDATA% — no UAC prompt required
DefaultDirName={localappdata}\CardLens
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installer output
OutputDir=Output
OutputBaseFilename=CardLensSetup
Compression=lzma2/ultra64
SolidCompression=yes
; Cosmetics
WizardStyle=modern
WizardSmallImageFile=
; Architecture
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; Uninstaller
Uninstallable=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; Don't allow running an older installer over a newer installed version
VersionInfoVersion={#AppVersion}
; Minimal privileges — runs as the current user, no admin needed
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=

[Languages]
Name: "german";   MessagesFile: "compiler:Languages\German.isl"
Name: "english";  MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; ---------- Main application (PyInstaller --onedir output) ----------
; All files from dist\PokemonCardScanner\ go into {app}\
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch after install
Filename: "{app}\{#AppExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove user-data folder only if it is empty (don't delete the user's collection!)
Type: dirifempty; Name: "{userappdata}\CardLens\data\catalog_images"
Type: dirifempty; Name: "{userappdata}\CardLens\data"
Type: dirifempty; Name: "{userappdata}\CardLens\runtime"
Type: dirifempty; Name: "{userappdata}\CardLens\logs"
Type: dirifempty; Name: "{userappdata}\CardLens\cache"
Type: dirifempty; Name: "{userappdata}\CardLens\exports"
Type: dirifempty; Name: "{userappdata}\CardLens\crashes"
; IMPORTANT: The database (data\pokemon_scanner.sqlite3) and downloaded card
; images are intentionally NOT deleted on uninstall — the user's collection is
; preserved. A manual cleanup note is shown below.

[Code]
// LGPL v3 compliance note: PySide6/Qt DLLs are located in {app}\PySide6\
// Users may replace them with a modified version to comply with LGPL v3.
// Source code: https://github.com/webster586/cardlens

// Show a note during uninstall that user data is preserved.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox(
      'Deine Sammlung (Datenbank + Kartenbilder) unter' + #13#10 +
      ExpandConstant('{userappdata}\CardLens\') + #13#10#13#10 +
      'wurde NICHT gelöscht. Du kannst diesen Ordner manuell entfernen, ' +
      'wenn du alle Daten löschen möchtest.',
      mbInformation, MB_OK
    );
  end;
end;
