"""
Twitch-Chat -> IRC-Relay.

Liest den Chat des Twitch-Kanals aus TWITCH_CHANNEL (anonym, nur lesend, ohne
Login) und schreibt jede Nachricht als "[Twitch] USER: NACHRICHT" nach #freespawn.
Ist TWITCH_CHANNEL nicht gesetzt, tut das Plugin nichts.

Optionale Umgebungsvariablen (.env):
  TWITCH_CHANNEL        Login-Name des Kanals, z.B. KernicNET (leer = aus)
  TWITCH_IGNORE_USERS   Komma-Liste von Twitch-Namen, die nicht weitergegeben
                        werden (Standard: gängige Chat-Bots). Leerer Wert = niemanden.
  TWITCH_OWN_BOT_REGEX  Regex für Texte des Kanal-Accounts, die NICHT weitergegeben
                        werden: das OBS-Overlay postet seine Bot-Antworten (Begrüßung,
                        Raid-Dank, !freespawn/!forum/... ) unter dem Konto des Streamers.
                        Standard passt auf genau diese Texte; leerer Wert = alles
                        weitergeben.

Nicht weitergegeben werden ausserdem Befehle (Nachrichten mit "!" am Anfang).
Damit ein Chat-Ansturm den IRC-Channel nicht flutet, geht höchstens eine Zeile
pro Sekunde raus und die Warteschlange ist begrenzt (überzählige Zeilen
werden verworfen).
"""
import os
import queue
import random
import re
import socket
import ssl
import threading
import time

from sopel import module
from sopel.tools import get_logger

LOGGER = get_logger("spawnkeeper_twitch")

IRC_CHANNEL = "#freespawn"
TWITCH_HOST = "irc.chat.twitch.tv"
TWITCH_PORT = 6697
MAX_TEXT = 350          # Zeichen pro IRC-Zeile (IRC erlaubt 512 Byte inkl. Protokoll-Präfix)
RELAY_GAP = 1.0         # Sekunden zwischen zwei Zeilen im IRC
QUEUE_SIZE = 100
READ_TIMEOUT = 420      # Twitch pingt ca. alle 5 Minuten; länger still = Verbindung tot
DEFAULT_IGNORED = "nightbot,streamelements,streamlabs,moobot,fossabot,wizebot"
# Texte, die das OBS-Overlay (chat_bot_logic.py / chat_commands.csv) unter dem Konto des Streamers postet.
DEFAULT_OWN_BOT_REGEX = (r"^(Willkommen im Chat, |Danke für den Raid, |FreeSpawn(-| im |: )|"
                         r"https?://\S+$|Lecker Soft|Entdecke die Welt)")

_IRC_COLOR = re.compile(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_TAG_UNESCAPE = {"s": " ", ":": ";", "\\": "\\", "r": "", "n": ""}


def _unescape_tag(value):
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(_TAG_UNESCAPE.get(value[i + 1], value[i + 1]))
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_line(line):
    """Zerlegt eine Twitch-IRC-Zeile in {tags, nick, command, params, text}."""
    tags = {}
    if line.startswith("@"):
        raw, _, line = line.partition(" ")
        for pair in raw[1:].split(";"):
            key, _, value = pair.partition("=")
            tags[key] = _unescape_tag(value)
    prefix = ""
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    command, _, rest = line.partition(" ")
    text = None
    if rest.startswith(":"):
        middle, text = "", rest[1:]
    elif " :" in rest:
        middle, _, text = rest.partition(" :")
    else:
        middle = rest
    return {"tags": tags, "nick": prefix.split("!", 1)[0], "command": command,
            "params": middle.split(), "text": text}


def _clean(text):
    """Steuerzeichen (inkl. IRC-Farbcodes, CR/LF) entfernen, Leerraum glätten, kürzen."""
    text = " ".join(_CONTROL_CHARS.sub(" ", _IRC_COLOR.sub("", text)).split())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1].rstrip() + "…"
    return text


def ignored_users(raw=None):
    """Menge der ignorierten Twitch-Namen (klein geschrieben)."""
    raw = os.environ.get("TWITCH_IGNORE_USERS") if raw is None else raw
    if raw is None:
        raw = DEFAULT_IGNORED
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def own_bot_pattern(raw=None):
    """Kompiliertes Muster für Bot-Texte des Kanal-Accounts (None = nichts filtern)."""
    raw = os.environ.get("TWITCH_OWN_BOT_REGEX") if raw is None else raw
    if raw is None:
        raw = DEFAULT_OWN_BOT_REGEX
    return re.compile(raw) if raw else None


def decide(msg, ignore, channel="", own_bot=None):
    """Entscheidet über eine geparste Zeile: (IRC-Text, None) oder (None, Grund).

    Grund None = keine Chat-Nachricht (still übergehen), sonst kurzer Text fürs Log.
    """
    if msg["command"] != "PRIVMSG" or not msg["text"]:
        return None, None
    nick = msg["nick"].lower()
    name = _clean(msg["tags"].get("display-name") or msg["nick"])
    if not name:
        return None, "kein Name"
    if nick in ignore or name.lower() in ignore:
        return None, "Nutzer ignoriert"
    text = msg["text"]
    action = text.startswith("\x01ACTION ") and text.endswith("\x01")
    if action:
        text = text[len("\x01ACTION "):-1]
    text = _clean(text)
    if not text:
        return None, "leer"
    if text.startswith("!"):
        return None, "Befehl"
    if own_bot and nick == channel.lower() and own_bot.search(text):
        return None, "Bot-Antwort des Overlays"
    return (f"[Twitch] * {name} {text}" if action else f"[Twitch] {name}: {text}"), None


def format_relay(msg, ignore, channel="", own_bot=None):
    """Nur der IRC-Text aus decide(), oder None."""
    return decide(msg, ignore, channel, own_bot)[0]


class TwitchRelay:
    """Anonyme Twitch-Chat-Verbindung mit Reconnect und gedrosseltem IRC-Versand."""

    def __init__(self, bot, channel):
        self.bot = bot
        self.channel = channel.lower().lstrip("#")
        self.ignore = ignored_users()
        self.own_bot = own_bot_pattern()
        self.outbox = queue.Queue(maxsize=QUEUE_SIZE)

    def handle_line(self, line):
        """Verarbeitet eine Zeile vom Twitch-Server; gibt den eingereihten Text zurück (oder None)."""
        msg = parse_line(line)
        text, reason = decide(msg, self.ignore, self.channel, self.own_bot)
        if reason:
            LOGGER.info("Twitch-Nachricht von %s übersprungen: %s", msg["nick"], reason)
        if text:
            try:
                self.outbox.put_nowait(text)
            except queue.Full:
                LOGGER.warning("Relay-Warteschlange voll, Nachricht verworfen")
                return None
            LOGGER.info("Twitch -> IRC: %s", text)
        return text

    def start(self):
        threading.Thread(target=self._read_loop, name="twitch-relay-read", daemon=True).start()
        threading.Thread(target=self._send_loop, name="twitch-relay-send", daemon=True).start()

    def _send_loop(self):
        while True:
            text = self.outbox.get()
            try:
                self.bot.say(text, IRC_CHANNEL, max_messages=1)
            except Exception as exc:
                LOGGER.error("bot.say fehlgeschlagen: %s", exc)
            time.sleep(RELAY_GAP)

    def _read_loop(self):
        delay = 5
        while True:
            sock = None
            try:
                raw = socket.create_connection((TWITCH_HOST, TWITCH_PORT), timeout=15)
                sock = ssl.create_default_context().wrap_socket(raw, server_hostname=TWITCH_HOST)
                sock.settimeout(READ_TIMEOUT)

                def send(line):
                    sock.sendall((line + "\r\n").encode("utf-8"))

                send("CAP REQ :twitch.tv/tags twitch.tv/commands")
                send("PASS SCHMOOPIIE")                       # anonymer Zugang, Twitch verlangt irgendein Passwort
                send(f"NICK justinfan{random.randint(10000, 99999)}")
                send(f"JOIN #{self.channel}")
                LOGGER.info("Twitch-Relay verbunden: #%s", self.channel)
                delay = 5
                buffer = ""
                while True:
                    data = sock.recv(4096)
                    if not data:
                        raise ConnectionError("Verbindung von Twitch geschlossen")
                    buffer += data.decode("utf-8", errors="replace")
                    while "\r\n" in buffer:
                        line, buffer = buffer.split("\r\n", 1)
                        if line.startswith("PING"):
                            send(line.replace("PING", "PONG", 1))
                        elif parse_line(line)["command"] == "RECONNECT":
                            raise ConnectionError("Twitch bittet um Reconnect")
                        else:
                            self.handle_line(line)
            except Exception as exc:
                LOGGER.warning("Twitch-Relay getrennt (%s), neuer Versuch in %ss", exc, delay)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            time.sleep(delay)
            delay = min(delay * 2, 60)


@module.event('001')
@module.event('376')
def start_twitch_relay(bot, trigger):
    """Starte den Relay, sobald der Bot mit IRC verbunden ist (nur wenn TWITCH_CHANNEL gesetzt ist)."""
    channel = os.environ.get("TWITCH_CHANNEL", "").strip()
    if not channel:
        return
    if "twitch_relay" not in bot.memory:
        relay = TwitchRelay(bot, channel)
        bot.memory["twitch_relay"] = relay
        relay.start()
