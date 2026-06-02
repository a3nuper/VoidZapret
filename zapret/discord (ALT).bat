@echo off
chcp 65001 > nul
:: 65001 - UTF-8
:: VoidZapret: Discord - ALT ( max.ru,  stun-)

cd /d "%~dp0"
call service.bat status_zapret
call service.bat load_user_lists
echo:

set "BIN=%~dp0bin\"
set "LISTS=%~dp0lists\"
cd /d %BIN%

"%BIN%winws.exe" --wf-tcp=80,443 --wf-udp=443,50000-65535,19294-19344 ^
--filter-udp=443 --hostlist="%LISTS%list-general.txt" --hostlist="%LISTS%list-general-user.txt" --dpi-desync=fake --dpi-desync-repeats=8 --dpi-desync-fake-quic="%BIN%quic_initial_www_google_com.bin" --new ^
--filter-udp=50000-65535,19294-19344 --filter-l7=discord,stun --dpi-desync=fake --dpi-desync-repeats=8 --dpi-desync-fake-discord="%BIN%quic_initial_dbankcloud_ru.bin" --dpi-desync-fake-stun="%BIN%stun.bin" --new ^
--filter-tcp=443 --hostlist-domains=discord.media,discord.gg,discord.com,discordapp.com,discordapp.net,discord.app --dpi-desync=multisplit --dpi-desync-split-seqovl=652 --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_max_ru.bin" --new ^
--filter-tcp=80,443 --hostlist="%LISTS%list-general.txt" --hostlist="%LISTS%list-general-user.txt" --hostlist-exclude="%LISTS%list-exclude.txt" --hostlist-exclude="%LISTS%list-exclude-user.txt" --dpi-desync=multisplit --dpi-desync-split-seqovl=568 --dpi-desync-split-pos=1 --dpi-desync-split-seqovl-pattern="%BIN%tls_clienthello_4pda_to.bin"
