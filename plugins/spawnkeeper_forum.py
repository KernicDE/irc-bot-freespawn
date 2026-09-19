"""
Forum-RSS-Polling für den IRC-Bot.
Postet neue Diskussionen aus dem globalen, unauthentifizierten Feed in #freespawn.
Da der Feed ohne Login abgerufen wird, enthält er automatisch genau die
Diskussionen, die auch ein Gast ohne Anmeldung sehen könnte - unabhängig
davon, welche Tags gerade wie eingeschränkt sind.

Der Feed liefert pro Diskussion den jeweils neuesten Post (Eintrags-ID
enthält die Post-Nummer, z.B. https://freespawn.de/d/19/2). Jede neue Antwort
bekommt dadurch eine eigene Eintrags-ID und wird - gewollt - als eigener
Post gemeldet: es soll jeder einzelne Beitrag im Channel erscheinen, nicht
nur der erste eines Threads.

Die Meldung unterscheidet neuen Thread (Post-Nummer 1) von Antwort. Bei
einer Antwort wird zusätzlich per API kurz nachgeschaut, ob der Post
jemanden zitiert/erwähnt (Flarums Mention-Markup im gerenderten Post-HTML)
- falls ja, wird das Ziel genannt, falls nein, fällt dieser Teil der
Nachricht einfach weg, statt einen leeren Platzhalter zu zeigen.
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request

import feedparser
from sopel import module

IRC_CHANNEL = "#freespawn"
DB_PATH = "/bot/data/seen_entries.sqlite"
FEED_NAME = "all"
FORUM_BASE_URL = "https://freespawn.de"
FEED_URL = f"{FORUM_BASE_URL}/atom"
POLL_INTERVAL = 60  # Sekunden

_ENTRY_ID_RE = re.compile(r"/d/(\d+)/(\d+)/?$")


def _parse_entry_id(entry_id):
    """Liefert (discussion_id, post_number) aus einer Eintrags-ID wie
    'https://freespawn.de/d/19/2' -> ('19', 2). Liefert (None, None), falls das
    Format mal nicht passt (z.B. nach einem Feed-Wechsel)."""
    match = _ENTRY_ID_RE.search(entry_id)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def _mentioned_user(discussion_id, post_number, author):
    """Best-effort: Wen zitiert/erwähnt dieser Post? Holt dafür den
    gerenderten Post-Inhalt über die öffentliche API und sucht nach
    Flarums Mention-Markup. Gibt None zurück, wenn nichts gefunden wird
    oder die Anfrage fehlschlägt - das ist eine optionale Zusatzinfo,
    kein Grund, das Posten der eigentlichen Nachricht zu verhindern."""
    try:
        url = f"{FORUM_BASE_URL}/api/discussions/{discussion_id}?include=posts&page[near]={post_number}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        for post in data.get("included", []):
            if post.get("type") != "posts":
                continue
            attrs = post.get("attributes", {})
            if attrs.get("number") != post_number:
                continue
            match = re.search(r'data-username="([^"]+)"', attrs.get("contentHtml", ""))
            if match and match.group(1) != author:
                return match.group(1)
            break
    except Exception as exc:
        print(f"[spawnkeeper_forum] Konnte Mention nicht ermitteln (kein Problem, wird einfach weggelassen): {exc}")
    return None


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_entries (
            feed TEXT NOT NULL,
            entry_id TEXT NOT NULL,
            title TEXT,
            link TEXT,
            published TEXT,
            PRIMARY KEY (feed, entry_id)
        )
    """)
    conn.commit()
    conn.close()


def _is_seen(feed, entry_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "SELECT 1 FROM seen_entries WHERE feed = ? AND entry_id = ?",
        (feed, entry_id),
    )
    seen = cur.fetchone() is not None
    conn.close()
    return seen


def _mark_seen(feed, entry_id, title, link, published):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO seen_entries (feed, entry_id, title, link, published) VALUES (?, ?, ?, ?, ?)",
        (feed, entry_id, title, link, published),
    )
    conn.commit()
    conn.close()


def _poll_feed(bot):
    try:
        parsed = feedparser.parse(FEED_URL)
    except Exception as exc:
        # Nicht per bot.say melden: Ein dauerhafter Fehler würde sonst bei
        # jedem Poll-Intervall (60s) erneut in den Channel spammen.
        print(f"[spawnkeeper_forum] RSS-Fehler beim Abruf von {FEED_URL}: {exc}")
        return

    for entry in parsed.entries:
        entry_id = entry.get("id") or entry.get("link")
        if not entry_id:
            continue
        if _is_seen(FEED_NAME, entry_id):
            continue

        title = entry.get("title", "Neuer Beitrag")
        link = entry.get("link", FEED_URL)
        author = entry.get("author") or "jemand"
        published = entry.get("published", "")

        discussion_id, post_number = _parse_entry_id(entry_id)
        if post_number == 1:
            message = f"[Forum] Neuer Thread von {author}: {title} | {link}"
        elif discussion_id is not None:
            mentioned = _mentioned_user(discussion_id, post_number, author)
            if mentioned:
                message = f"[Forum] {author} antwortet an {mentioned} in {title} | {link}"
            else:
                message = f"[Forum] {author} antwortet in {title} | {link}"
        else:
            # Format der Eintrags-ID nicht erkannt - lieber die einfache,
            # garantiert korrekte Variante posten als raten.
            message = f"[Forum] {author}: {title} | {link}"

        # Erst posten, dann als gesehen markieren - schlägt bot.say fehl
        # (z.B. kurzer IRC-Disconnect), bleibt der Eintrag offen und wird
        # beim nächsten Poll erneut versucht, statt für immer verloren zu
        # gehen.
        try:
            bot.say(message, IRC_CHANNEL)
        except Exception as exc:
            print(f"[spawnkeeper_forum] Konnte Eintrag nicht posten, versuche es beim nächsten Poll erneut: {exc}")
            continue

        _mark_seen(FEED_NAME, entry_id, title, link, published)


def _poll_loop(bot):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _init_db()
    while True:
        try:
            _poll_feed(bot)
        except Exception as exc:
            # Ohne dieses Netz würde eine einzelne unerwartete Exception den
            # kompletten Poll-Thread lautlos für immer beenden (Container
            # läuft weiter, es wird aber nie wieder gepollt).
            print(f"[spawnkeeper_forum] Unerwarteter Fehler im Poll-Loop, mache beim nächsten Intervall weiter: {exc}")
        time.sleep(POLL_INTERVAL)


@module.event('001')
@module.event('376')
def start_forum_polling(bot, trigger):
    if "forum_poll_thread" not in bot.memory:
        thread = threading.Thread(target=_poll_loop, args=(bot,), daemon=True)
        thread.start()
        bot.memory["forum_poll_thread"] = thread
