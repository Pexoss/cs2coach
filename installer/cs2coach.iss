; Instalador (Inno Setup 6). Gera installer\Output\CS2Coach-Setup.exe
; Instala so para o usuario atual (nao pede senha de administrador).
#define Nome "CS2 Coach"
#ifndef Versao
  #define Versao "5.0.0"
#endif

[Setup]
AppId={{6E0B4C1B-3D63-4F5B-9C2A-0C52C0AC4001}
AppName={#Nome}
AppVersion={#Versao}
DefaultDirName={localappdata}\Programs\CS2Coach
DefaultGroupName={#Nome}
PrivilegesRequired=lowest
OutputBaseFilename=CS2Coach-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icone.ico
UninstallDisplayIcon={app}\CS2Coach.exe
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no
LanguageDetectionMethod=none

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktop"; Description: "Criar atalho na Área de Trabalho"; Flags: unchecked

[Files]
Source: "..\dist\CS2Coach\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#Nome}"; Filename: "{app}\CS2Coach.exe"; AppUserModelID: "Pexoss.CS2Coach"
Name: "{autodesktop}\{#Nome}"; Filename: "{app}\CS2Coach.exe"; Tasks: desktop; AppUserModelID: "Pexoss.CS2Coach"

[Run]
; atualiza o cache de icones do Windows (senao o icone antigo pode continuar aparecendo)
Filename: "{sys}\ie4uinit.exe"; Parameters: "-show"; Flags: runhidden nowait skipifdoesntexist
Filename: "{app}\CS2Coach.exe"; Description: "Abrir o CS2 Coach agora"; Flags: nowait postinstall

[UninstallDelete]
; Os seus dados (%APPDATA%\CS2Coach) NAO sao apagados ao desinstalar.
Type: filesandordirs; Name: "{app}"
