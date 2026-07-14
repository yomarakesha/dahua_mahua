; Kanagatly VMS SERVER — single double-click Windows installer (Inno Setup).
;
; Bundles the fully self-contained payload assembled by build-payload.ps1
; (embeddable CPython 3.12 with backend deps baked in, the go2rtc/ffmpeg/caddy/
; nssm binaries, the pre-built web UI, and runtime templates). The firm needs
; NOTHING preinstalled — no Python, Node, Git, or manual PowerShell.
;
; A post-install [Run] invokes postinstall.ps1 (elevated) which generates the
; .env secrets, renders configs, runs migrations, and registers + starts the
; three NSSM services, then prints the connect URL. [UninstallRun] stops and
; removes those services.
;
; Compile in CI:  iscc /DPayloadDir=<payload> /DAppVersion=<ver> installer.iss
; (see .github/workflows/windows-installer.yml). icon.ico is generated alongside.

#ifndef PayloadDir
  #define PayloadDir "payload"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{9F2C7A5E-3B41-4E9C-9C2E-7A1D5B0C4E21}
AppName=Kanagatly VMS Server
AppVersion={#AppVersion}
AppVerName=Kanagatly VMS Server {#AppVersion}
AppPublisher=Kanagatly
DefaultDirName={autopf}\Kanagatly VMS Server
DefaultGroupName=Kanagatly VMS Server
DisableProgramGroupPage=yes
UninstallDisplayName=Kanagatly VMS Server
UninstallDisplayIcon={app}\icon.ico
OutputDir=installer-output
OutputBaseFilename=KanagatlyVMS-Server-Setup
SetupIconFile=icon.ico
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; The whole payload folder → {app}, recursively (embeddable python\, bin\, www\,
; backend\, Caddyfile, go2rtc.base.yaml, postinstall.ps1).
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Configure + start the services. The installer is already elevated, so this
; PowerShell inherits admin. Visible window so the operator sees the connect URL.
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\postinstall.ps1"" -InstallDir ""{app}"""; \
  StatusMsg: "Configuring Kanagatly VMS services (this can take a minute)..."; \
  Flags: waituntilterminated

[UninstallRun]
; Stop + remove all three services before the files are deleted.
Filename: "{app}\bin\nssm.exe"; Parameters: "stop dahua-caddy";  Flags: runhidden; RunOnceId: "StopCaddy"
Filename: "{app}\bin\nssm.exe"; Parameters: "stop dahua-backend"; Flags: runhidden; RunOnceId: "StopBackend"
Filename: "{app}\bin\nssm.exe"; Parameters: "stop dahua-go2rtc"; Flags: runhidden; RunOnceId: "StopGo2rtc"
Filename: "{app}\bin\nssm.exe"; Parameters: "remove dahua-caddy confirm";  Flags: runhidden; RunOnceId: "RmCaddy"
Filename: "{app}\bin\nssm.exe"; Parameters: "remove dahua-backend confirm"; Flags: runhidden; RunOnceId: "RmBackend"
Filename: "{app}\bin\nssm.exe"; Parameters: "remove dahua-go2rtc confirm"; Flags: runhidden; RunOnceId: "RmGo2rtc"

[UninstallDelete]
; Runtime state generated after install (DB, logs, self-signed CA, rendered cfg).
Type: filesandordirs; Name: "{app}\.caddy"
Type: filesandordirs; Name: "{app}\.go2rtc"
Type: files;          Name: "{app}\dahua-*.log"
Type: files;          Name: "{app}\backend\dss.db"
