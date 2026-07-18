; Inno Setup script for the Receipt Print Agent.
;
; Compiles dist/receipt-print-agent.exe (build it first -- see README.md) into a
; single installer.exe that a shop owner double-clicks once. It installs the
; agent, starts it immediately, opens the printer-selection page, and adds it
; to Startup so it's already running the next time the till boots -- no other
; setup, ever again.
;
; Build the agent .exe first:
;   pyinstaller --onefile --windowed --name receipt-print-agent --add-data "public;public" app.py
;
; Then compile this script (Inno Setup 6, https://jrsoftware.org/isinfo.php):
;   ISCC.exe installer.iss
; Output lands in installer_output\ReceiptPrintAgentSetup.exe -- that's the one
; file you actually hand to shop owners.

#define MyAppName "Receipt Print Agent"
#define MyAppVersion "1.0.0"
#define MyAppExeName "receipt-print-agent.exe"

[Setup]
AppId={{B7E1F5C2-4A3D-4E8B-9F1A-2D6C8E0A5B3F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Eventry POS
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=ReceiptPrintAgentSetup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut -- mainly so it's easy to find/relaunch manually if ever needed.
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; The actual point of this installer: launches quietly on every boot, for whichever
; Windows account logs into this till -- {commonstartup} (not {userstartup}) since the
; install itself runs elevated and a till is often shared/logged into by different staff.
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
; Start it right after install, then open the setup page so the printer can be
; picked immediately -- "install once, forget it" only works if this happens
; without the shop owner needing to know where to look.
Filename: "{app}\{#MyAppExeName}"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent
Filename: "http://127.0.0.1:19100/"; Flags: postinstall shellexec skipifsilent runasoriginaluser; Description: "Open printer setup page"

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#MyAppExeName} /F"; Flags: runhidden; RunOnceId: "StopAgent"

[Code]
// Stop any already-running instance before copying files. Without this, an
// upgrade install fails with "file in use" if the agent is running (which,
// by design, it always is), and the old process would otherwise keep running
// the previous version's code until the next reboot.
procedure KillRunningAgent;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM {#MyAppExeName} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    KillRunningAgent;
end;
