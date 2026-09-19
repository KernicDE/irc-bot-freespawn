"""
Mumble-Integration für den IRC-Bot.
Verbindet sich mit dem Murmur-Server und postet Events in #freespawn.
"""
import os
import threading
import time

from sopel import module

try:
    # Das PyPI-Paket heißt "pymumble", das importierbare Modul aber
    # "pymumble_py3" (siehe top_level.txt im Wheel) - "import pymumble"
    # schlägt immer fehl.
    import pymumble_py3 as pymumble
    from pymumble_py3 import constants as pymumble_constants
    from pymumble_py3.errors import UnknownChannelError
    PYMUMBLE_AVAILABLE = True
except ImportError:
    PYMUMBLE_AVAILABLE = False
    UnknownChannelError = KeyError  # nur Platzhalter, ohne pymumble läuft das Plugin nicht

MUMBLE_HOST = "mumble-freespawn"
MUMBLE_PORT = 64738
# Der Bot verbindet sich als SuperUser (Passwort aus MUMBLE_SUPERUSER_PASSWORD,
# muss mit mumble-freespawn/.env übereinstimmen), um User im AFK-Channel
# automatisch muten zu können - SuperUser umgeht dafür alle ACL-Prüfungen.
# Bewusster Tradeoff: einfacher als ein dediziertes Bot-Konto mit
# Minimal-Rechten, dafür hat der Bot damit volle Admin-Macht über den
# gesamten Server. Hinweis: Mumble kennt keinen "unsichtbar verbunden"-Modus,
# der Bot erscheint im Mumble-Client selbst ganz normal als "SuperUser" im
# Channel-Baum - wir filtern ihn nur aus den eigenen Bot-Ausgaben (!users,
# Join/Leave/AFK-Meldungen) heraus.
MUMBLE_NICK = "SuperUser"
MUMBLE_PASSWORD = os.environ.get("MUMBLE_SUPERUSER_PASSWORD", "")
MUMBLE_CHANNEL = "FreeSpawn"
RECONNECT_DELAY = 20  # Sekunden zwischen Neuaufbau-Versuchen der Mumble-Sitzung
AFK_CHANNEL_NAME = "AFK"
# Der eigens versteckte "SuperUser"-Channel wurde wieder entfernt - Mumble
# schickt die komplette Channel-Struktur ohnehin an jeden Client, egal
# welche ACL-Rechte er hat (Traverse steuert nur Zutritt, nicht Sichtbarkeit).
# Ein "unsichtbarer" Channel war also nie erreichbar. Der Bot parkt sich
# stattdessen einfach in AFK - dort ist er wenigstens thematisch am
# richtigen Platz statt sichtbar in Lounge/Gaming rumzustehen.
IRC_CHANNEL = "#freespawn"

# Wer direkt in einen dieser Channel wechselt, landet automatisch in einem
# nummerierten Unterkanal (z.B. "Gaming #1") statt im Sammelkanal selbst -
# bestehende Unterkanäle mit noch freien Plätzen werden bevorzugt aufgefüllt,
# erst wenn alle voll sind, wird ein neuer temporärer Unterkanal angelegt.
AUTOSPLIT_PARENTS = ("On-Air", "Gaming")
AUTOSPLIT_MAX_USERS = 6


def group_users_by_channel(users, channels, skip_name=MUMBLE_NICK):
    """Liste aus (Kanalname, [Usernamen]) für die aktuell verbundenen User.

    Der Bot selbst (skip_name) wird ausgelassen. Kanäle stehen in Reihenfolge ihrer
    ID (entspricht dem Baum, temporäre Unterkanäle kommen hinten), Namen alphabetisch.
    """
    grouped = {}
    for user in users.values():
        name = user.get("name", "Unbekannt")
        if name == skip_name:
            continue
        grouped.setdefault(user.get("channel_id", 0), []).append(name)
    result = []
    for channel_id in sorted(grouped):
        channel = channels.get(channel_id) or {}
        result.append((channel.get("name") or "Unbekannt", sorted(grouped[channel_id], key=str.lower)))
    return result


class MumbleClientThread(threading.Thread):
    def __init__(self, bot):
        super().__init__(daemon=True)
        self.bot = bot
        self.mumble = None
        self.known_users = {}
        self.afk_users = set()
        self.muted_in_afk = set()  # session_ids, die wir wegen AFK-Channel gemutet haben
        self.pending_autosplit = {}  # neuer Channel-Name -> wartende session_id
        self.announced_down = False  # Ausfall wurde schon im IRC gemeldet

    def run(self):
        """Hält die Mumble-Verbindung dauerhaft aufrecht.

        Früher lief alles in einem einzigen try-Block: Warf irgendetwas beim
        Neuverbinden eine Ausnahme (z.B. während der Mumble-Server neu startet,
        etwa nach einem Zertifikats-Sync), endete der Thread für immer und der
        Bot blieb bis zum nächsten Container-Neustart getrennt. Jetzt baut ein
        Supervisor die Sitzung bei jedem Fehler oder dauerhaftem Verbindungs-
        verlust mit einem frischen Client neu auf.
        """
        if not PYMUMBLE_AVAILABLE:
            self.bot.say("[Mumble] pymumble ist nicht installiert.", IRC_CHANNEL)
            return

        while True:
            try:
                self._run_session()
                reason = "Verbindung zum Mumble-Server verloren"
            except Exception as exc:  # pragma: no cover
                reason = f"Fehler: {exc!r}"
            print(f"[spawnkeeper_mumble] {reason}, neuer Versuch in {RECONNECT_DELAY}s")
            if not self.announced_down:
                # nur einmal pro Störung melden, sonst spammt der Bot bei längerem Ausfall
                self.bot.say("[Mumble] Verbindung unterbrochen, ich versuche es erneut.", IRC_CHANNEL)
                self.announced_down = True
            self._stop_client()
            self.bot.memory["mumble_user_channels"] = None  # !users: "nicht verfügbar" statt veralteter Liste
            time.sleep(RECONNECT_DELAY)

    def _stop_client(self):
        try:
            if self.mumble:
                self.mumble.stop()
        except Exception as exc:  # pragma: no cover
            print(f"[spawnkeeper_mumble] Konnte alten Client nicht sauber beenden: {exc!r}")
        self.mumble = None

    def _run_session(self):
        self.mumble = pymumble.Mumble(
            MUMBLE_HOST, MUMBLE_NICK, port=MUMBLE_PORT,
            password=MUMBLE_PASSWORD, reconnect=True,
        )
        self.mumble.start()
        self.mumble.is_ready()
        self.mumble.set_bandwidth(96000)

        self._ensure_parked_in_afk()

        # WICHTIG: mumble.callbacks(...) ist NICHT die Registrierung, sondern
        # ein __call__-Shortcut für call_callback() (löst bereits registrierte
        # Callbacks aus!). Ohne registrierte Funktion ist das ein stiller No-op -
        # deshalb feuerten Join/Leave/AFK-Meldungen nie. Registrieren geht über
        # set_callback()/add_callback() auf dem callbacks-Objekt.
        self.mumble.callbacks.set_callback(pymumble_constants.PYMUMBLE_CLBK_USERCREATED, self._user_created)
        self.mumble.callbacks.set_callback(pymumble_constants.PYMUMBLE_CLBK_USERREMOVED, self._user_removed)
        self.mumble.callbacks.set_callback(pymumble_constants.PYMUMBLE_CLBK_USERUPDATED, self._user_updated)
        # CONNECTED feuert bei JEDEM (Re-)Connect, auch bei pymumbles
        # eigener reconnect=True-Logik nach einem Mumble-Server-Neustart -
        # ohne diesen Hook landet der Bot nach so einem Reconnect im
        # DEFAULTCHANNEL (Lounge) und bleibt dort für alle sichtbar hängen,
        # weil move_in() sonst nur einmal beim allerersten Start läuft.
        self.mumble.callbacks.set_callback(pymumble_constants.PYMUMBLE_CLBK_CONNECTED, self._on_connected)
        self.mumble.callbacks.set_callback(pymumble_constants.PYMUMBLE_CLBK_CHANNELCREATED, self._channel_created)

        self._update_user_list()
        if self.announced_down:
            self.bot.say("[Mumble] Verbindung wiederhergestellt.", IRC_CHANNEL)
            self.announced_down = False

        down_since = None
        while True:
            time.sleep(5)
            if self.mumble.connected == pymumble_constants.PYMUMBLE_CONN_STATE_CONNECTED:
                down_since = None
                try:
                    self._ensure_parked_in_afk()
                    self._update_user_list()
                except (KeyError, TypeError, AttributeError) as exc:
                    # Kurz nach einem Reconnect sind Kanal-/Userlisten noch leer -
                    # das darf die Sitzung nicht beenden, der nächste Durchlauf klappt.
                    print(f"[spawnkeeper_mumble] Zwischenzustand nach (Re-)Connect, ignoriert: {exc!r}")
            else:
                down_since = down_since or time.time()
                if time.time() - down_since > 90:
                    return  # pymumbles eigener Reconnect hat es nicht geschafft: frischer Client

    def _find_channel(self, name):
        """Kanal per Name oder None.

        pymumble wirft UnknownChannelError ("Channel AFK does not exists"), statt None
        zu liefern - besonders direkt nach einem (Re-)Connect, solange die Kanalliste
        noch leer ist. Genau diese Ausnahme hat früher den ganzen Mumble-Thread beendet.
        """
        try:
            return self.mumble.channels.find_by_name(name)
        except UnknownChannelError:
            return None

    def _ensure_parked_in_afk(self):
        if not self.mumble:
            return
        afk = self._find_channel(AFK_CHANNEL_NAME)
        if not afk:
            return
        try:
            current = self.mumble.users.myself["channel_id"]
        except (KeyError, TypeError):
            return
        if current != afk["channel_id"]:
            self.mumble.users.myself.move_in(afk["channel_id"])

    def _on_connected(self):
        self._ensure_parked_in_afk()

    def _move_other_user(self, session, channel_id):
        # pymumble_py3s User.move_in() bewegt IMMER den Bot selbst
        # (nutzt hartkodiert mumble_object.users.myself_session, ignoriert
        # das User-Objekt auf dem es aufgerufen wird) - für andere User
        # muss der rohe MoveCmd mit expliziter session direkt ausgeführt
        # werden. SuperUser umgeht dafür die nötige Move-ACL.
        self.mumble.execute_command(pymumble.messages.MoveCmd(session, channel_id))

    def _channel_user_count(self, channel_id):
        return sum(1 for u in self.mumble.users.values() if u.get("channel_id") == channel_id)

    def _handle_autosplit(self, session, parent):
        parent_id = parent["channel_id"]
        parent_name = parent["name"]
        children = sorted(
            (c for c in self.mumble.channels.values() if c.get("parent") == parent_id),
            key=lambda c: c["name"],
        )

        # Ersten Unterkanal mit noch freiem Platz nehmen, falls vorhanden.
        for child in children:
            if self._channel_user_count(child["channel_id"]) < AUTOSPLIT_MAX_USERS:
                self._move_other_user(session, child["channel_id"])
                return

        # Alle voll (oder noch keiner da) - neuen temporären Unterkanal anlegen.
        existing_names = {c["name"] for c in children}
        n = 1
        while f"{parent_name} #{n}" in existing_names:
            n += 1
        new_name = f"{parent_name} #{n}"
        self.pending_autosplit[new_name] = session
        self.mumble.channels.new_channel(parent_id, new_name, True)

    def _channel_created(self, channel):
        name = channel.get("name")
        session = self.pending_autosplit.pop(name, None)
        if session is not None and session in self.mumble.users:
            self._move_other_user(session, channel["channel_id"])

    def _afk_channel_id(self):
        channel = self._find_channel(AFK_CHANNEL_NAME)
        return channel["channel_id"] if channel else None

    def _update_user_list(self):
        if not self.mumble:
            return
        # Bot selbst wird nicht mitgezählt (siehe group_users_by_channel)
        channels = group_users_by_channel(self.mumble.users, self.mumble.channels)
        names = [name for _, members in channels for name in members]
        self.bot.memory["mumble_user_channels"] = channels
        self.bot.memory["mumble_user_count"] = len(names)
        self.bot.memory["mumble_user_names"] = names

    def _user_created(self, user, action=None):
        name = user.get("name", "Jemand")
        if name != MUMBLE_NICK:
            self.bot.say(f"[Mumble] {name} ist dem Server beigetreten.", IRC_CHANNEL)
        self._update_user_list()

    def _user_removed(self, user, action=None):
        name = user.get("name", "Jemand")
        if name != MUMBLE_NICK:
            self.bot.say(f"[Mumble] {name} hat den Server verlassen.", IRC_CHANNEL)
        session = user.get("session")
        self.muted_in_afk.discard(session)
        self._update_user_list()

    def _user_updated(self, user, action=None):
        name = user.get("name", "Jemand")
        if name == MUMBLE_NICK:
            # Murmur bewegt den Ersteller eines temporären Channels
            # automatisch mit rein - da der Bot der Ersteller ist (Gaming/
            # On-Air-Autosplit legt Unterkanäle an), landet er sonst kurz
            # sichtbar dort statt in AFK. Sofort reagieren statt auf den
            # nächsten periodischen Check zu warten, damit das nicht sichtbar
            # "springt".
            self._ensure_parked_in_afk()
            return

        session = user.get("session")
        current_channel_id = user.get("channel_id")

        # Landet jemand direkt im Sammelkanal (On-Air/Gaming, nicht in einem
        # bereits existierenden Unterkanal davon), automatisch in einen
        # nummerierten Unterkanal weiterleiten.
        for parent_name in AUTOSPLIT_PARENTS:
            parent = self._find_channel(parent_name)
            if parent and current_channel_id == parent["channel_id"]:
                self._handle_autosplit(session, parent)
                break

        afk_channel_id = self._afk_channel_id()
        in_afk_channel = afk_channel_id is not None and user.get("channel_id") == afk_channel_id

        # Automatisch muten/deafen beim Betreten von AFK, wieder freigeben beim Verlassen.
        # Erfordert MuteDeafen-Rechte - der Bot verbindet dafür als SuperUser.
        if in_afk_channel and session not in self.muted_in_afk:
            self.muted_in_afk.add(session)
            if not user.get("mute") or not user.get("deaf"):
                self.mumble.users[session].mute()
                self.mumble.users[session].deafen()
        elif not in_afk_channel and session in self.muted_in_afk:
            self.muted_in_afk.discard(session)
            if user.get("mute") or user.get("deaf"):
                self.mumble.users[session].unmute()
                self.mumble.users[session].undeafen()

        # AFK ist man nur im AFK-Channel. Selbst stummgeschaltet (Mikro/Ton aus) heisst
        # nicht abwesend: wer nur das Mikro oder die Ausgabe ausmacht, bleibt aktiv.
        is_afk = in_afk_channel
        if is_afk and name not in self.afk_users:
            self.afk_users.add(name)
            self.bot.say(f"[Mumble] {name} ist jetzt AFK.", IRC_CHANNEL)
        elif not is_afk and name in self.afk_users:
            self.afk_users.discard(name)
            self.bot.say(f"[Mumble] {name} ist nicht mehr AFK.", IRC_CHANNEL)
        self._update_user_list()


@module.event('001')
@module.event('376')
def connect_mumble(bot, trigger):
    """Starte Mumble-Thread, sobald der Bot mit IRC verbunden ist."""
    if "mumble_thread" not in bot.memory:
        thread = MumbleClientThread(bot)
        thread.start()
        bot.memory["mumble_thread"] = thread
