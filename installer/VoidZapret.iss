; Inno Setup script для VoidZapret.
; Собирает onedir-сборку (dist\VoidZapret) в установщик с ярлыками на
; рабочем столе и в меню «Пуск». Компиляция: ISCC.exe VoidZapret.iss

#define MyAppName "VoidZapret"
#define MyAppVersion "3.2"
#define MyAppPublisher "VoidZapret"
#define MyAppExeName "VoidZapret.exe"
#define DistDir "..\dist\VoidZapret"

[Setup]
; Уникальный AppId — не менять между версиями (нужен для обновления/удаления).
AppId={{8F2A6C71-3D4B-4E9A-9C1E-0A1B2C3D4E5F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
OutputDir=..\dist\installer
OutputBaseFilename=VoidZapret-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\icon.ico
; Приложению нужны права администратора (winws/WinDivert), ставим в Program Files.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Намеренно кладём ярлык на персональный рабочий стол — глушим штатное предупреждение.
UsedUserAreasWarning=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#DistDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#DistDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Тихий bootstrapper WebView2 — ставится только если рантайма нет (см. [Code]).
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
; Ярлык кладём на рабочий стол ТЕКУЩЕГО пользователя, а не в общий (Public),
; иначе при админ-установке он попадает в C:\Users\Public\Desktop и может не
; отображаться на персональном рабочем столе.
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[InstallDelete]
; Чистая установка: при обнаружении прежней папки удаляем старое содержимое
; (включая распакованные обновления zapret), чтобы не оставалось битых/старых
; файлов. Конфиг (zapret_gui_config.json) НЕ трогаем — чтобы сохранить выбранные
; стратегии и настройки между переустановками.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\zapret"
Type: files; Name: "{app}\VoidZapret.exe"
; Убираем старый ярлык из общего рабочего стола (от прежних версий установщика).
Type: files; Name: "{commondesktop}\{#MyAppName}.lnk"

[Run]
; Сначала — тихая установка WebView2 (нужен для интерфейса), только если его нет.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Установка компонента WebView2..."; Flags: waituntilterminated; Check: WebView2Missing
; runascurrentuser обязателен: exe помечен requireAdministrator, и запуск его
; напрямую из повышенного установщика падает с ошибкой 740 ("требуется повышение").
; С этим флагом приложение стартует в сессии пользователя и поднимает права само.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent runascurrentuser

[Code]
function WebView2Missing: Boolean;
var v: string;
begin
  Result := True;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0') then
    Result := False
  else if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0') then
    Result := False
  else if RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0') then
    Result := False;
end;

[UninstallDelete]
; Удаляем то, что приложение могло дописать рядом (обновления zapret, конфиг).
Type: filesandordirs; Name: "{app}\zapret"
Type: files; Name: "{app}\zapret_gui_config.json"
