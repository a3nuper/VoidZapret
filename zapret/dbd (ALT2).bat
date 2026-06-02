@echo off
chcp 65001 > nul
:: 65001 - UTF-8
:: VoidZapret: Dead by Daylight - ALT2 ( max.ru,  )

cd /d "%~dp0"
call service.bat status_zapret
call service.bat load_user_lists
echo:

set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"
set "EOS=epicgames.com,epicgames.dev,unrealengine.com,ol.epicgames.com,live.ol.epicgames.com,fortnite.com"
cd /d %BIN%

"%BIN%winws.exe" --wf-tcp=80,443,1024-65535 --wf-udp=443,1024-65535 ^
--filter-udp=443 --hostlist="%LISTS%list-general.txt" --hostlist="%LISTS%list-general-user.txt" --dpi-desync=fake --dpi-desync-repeats=5 --dpi-desync-fake-quic="%BIN%quic_initial_dbankcloud_ru.bin" --new ^
--filter-udp=443 --hostlist-domains=%EOS% --dpi-desync=fake --dpi-desync-repeats=5 --dpi-desync-fake-quic="%BIN%quic_initial_dbankcloud_ru.bin" --new ^
--filter-tcp=443 --hostlist-domains=%EOS% --dpi-desync=multisplit --dpi-desync-split-seqovl=652 --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_max_ru.bin" --new ^
--filter-tcp=80,443 --hostlist="%LISTS%list-general.txt" --hostlist="%LISTS%list-general-user.txt" --hostlist-exclude="%LISTS%list-exclude.txt" --hostlist-exclude="%LISTS%list-exclude-user.txt" --dpi-desync=multisplit --dpi-desync-split-seqovl=652 --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_max_ru.bin" --new ^
--filter-tcp=1024-65535 --dpi-desync=multisplit --dpi-desync-any-protocol=1 --dpi-desync-cutoff=n3 --dpi-desync-split-seqovl=652 --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_max_ru.bin" --new ^
--filter-udp=1024-65535 --dpi-desync=fake --dpi-desync-repeats=6 --dpi-desync-any-protocol=1 --dpi-desync-fake-unknown-udp="%BIN%quic_initial_dbankcloud_ru.bin" --dpi-desync-cutoff=n2
