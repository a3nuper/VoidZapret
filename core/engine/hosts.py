"""Таргетинг по хостам для VoidEngine: десинк только для нужных SNI.

Список встроен в движок (независим от Flowseal и переживает обновления zapret),
плюс при наличии подмешиваются zapret/lists. Так чистый трафик не трогаем →
ниже пинг, а покрытие сайтов/приложений — широкое.
"""

from config import get_zapret_dir

# Встроенный набор популярных заблокированных/замедляемых ресурсов и приложений.
BUILTIN = set("""
youtube.com youtu.be ytimg.com youtube-nocookie.com youtubei.googleapis.com
googlevideo.com gvt1.com gvt2.com gvt3.com ggpht.com googleusercontent.com
yt3.ggpht.com jnn-pa.googleapis.com googleapis.com gstatic.com
discord.com discord.gg discord.media discord.app discordapp.com discordapp.net
discordcdn.com dis.gd discord.dev discordstatus.com
instagram.com cdninstagram.com facebook.com fbcdn.net fb.com fbsbx.com
facebook.net meta.com threads.net whatsapp.com whatsapp.net wa.me
twitter.com x.com twimg.com t.co
tiktok.com tiktokcdn.com tiktokv.com byteoversea.com ibyteimg.com muscdn.com
twitch.tv ttvnw.net jtvnw.net kick.com
spotify.com scdn.co spotifycdn.com soundcloud.com sndcdn.com
deezer.com bandcamp.com last.fm
dailymotion.com dai.ly vimeo.com vimeocdn.com rumble.com
openai.com chatgpt.com oaistatic.com oaiusercontent.com
anthropic.com claude.ai perplexity.ai huggingface.co poe.com
copilot.microsoft.com character.ai midjourney.com civitai.com
epicgames.com epicgames.dev unrealengine.com fortnite.com ol.epicgames.com
proton.me protonmail.com protonvpn.com protonvpn.net
signal.org element.io matrix.org torproject.org mullvad.net riseup.net
reddit.com redd.it redditstatic.com redditmedia.com
medium.com substack.com telegra.ph graph.org
linkedin.com licdn.com pinterest.com pinimg.com
telegram.org t.me telegram.me telesco.pe cdn-telegram.org
tumblr.com bsky.app mastodon.social imgur.com 9gag.com
bbc.com bbc.co.uk dw.com meduza.io currenttime.tv svoboda.org
rutracker.org rutracker.net nnmclub.to kinozal.tv 1337x.to thepiratebay.org rutor.info
patreon.com patreonusercontent.com boosty.to
notion.so notion.site figma.com canva.com archive.org nyaa.si
chess.com lichess.org
cloudflare-ech.com encryptedsni.com
""".split())


def load_targets() -> set[str]:
    doms = set(BUILTIN)
    lists_dir = get_zapret_dir() / "lists"
    for fname in ("list-general.txt", "list-general-user.txt", "list-google.txt"):
        p = lists_dir / fname
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    doms.add(line)
        except OSError:
            pass
    return doms


def matches(sni: str, targets: set[str]) -> bool:
    """SNI попадает под обход, если совпадает с доменом или его поддоменом."""
    if not targets:
        return True
    sni = sni.lower().rstrip(".")
    if sni in targets:
        return True
    return any(sni.endswith("." + d) for d in targets)
