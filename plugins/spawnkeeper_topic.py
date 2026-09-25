"""
Hält Nick, Op-Status und Topic von #freespawn dauerhaft aufrecht.

Nick: Nach einem Ping-Timeout verbindet Sopel sich neu, während die alte
Verbindung serverseitig noch lebt - der Nick ist belegt und der Bot heißt
dann SpawnKeeper_. Sopel holt den Nick nur zurück, wenn es das QUIT der
alten Session sieht, was praktisch nie passiert (noch kein gemeinsamer
Channel). Deshalb prüft der Bot selbst per ISON, ob der konfigurierte Nick
frei ist: frei -> NICK, belegt -> NickServ REGAIN (wirft die alte Session
raus und benennt uns um; geht ohne Passwort, weil per SASL eingeloggt).
Bewusst kein blindes NICK: ein 433 würde Sopel zu SpawnKeeper__ umbenennen.

ChanServ vergibt Op über die FLAGS (+AO für den identifizierten Account)
eigentlich automatisch beim Join, aber das kann mit spürbarer Verzögerung
nach dem Identify passieren - dann liefe der Bot ohne Op durch die
Startphase. Deshalb fordert der Bot sich Op notfalls aktiv selbst über
ChanServ an ("/msg ChanServ OP"), statt nur passiv zu warten. Für TOPIC
braucht er zwingend Op, sonst schlägt der Befehl mit "482 You're not a
channel operator" fehl und wird stillschweigend ignoriert.

Voraussetzung: `/msg ChanServ FLAGS #freespawn <bot-nick> +AO` muss einmalig
vom Channel-Founder gesetzt sein, sonst antwortet ChanServ auf die
OP-Anfrage mit "You are not authorized to perform this operation."
"""
from sopel import module

CHANNEL = '#freespawn'
TOPIC = "#FreeSpawn | Forum: https://freespawn.de | Mumble: freespawn.de:64738"


def _ensure_nick(bot):
    if bot.nick != bot.make_identifier(bot.settings.core.nick):
        bot.write(['ISON', bot.settings.core.nick])


@module.event('001')  # RPL_WELCOME - Registrierung abgeschlossen
def on_welcome(bot, trigger):
    _ensure_nick(bot)


@module.event('303')  # RPL_ISON - Antwort auf unsere ISON-Anfrage
def on_ison(bot, trigger):
    wanted = bot.settings.core.nick
    if bot.nick == bot.make_identifier(wanted):
        return
    online = [bot.make_identifier(n) for n in trigger.args[-1].split()]
    if bot.make_identifier(wanted) in online:
        bot.say(f'REGAIN {wanted}', 'NickServ')
    else:
        bot.write(['NICK', wanted])


@module.event('NICK')
def on_nick(bot, trigger):
    # Je nach Reihenfolge der Handler hat Sopel bot.nick schon aktualisiert
    # oder noch nicht - beide Fälle abdecken, aber nicht auf Fremde reagieren.
    wanted = bot.make_identifier(bot.settings.core.nick)
    new = bot.make_identifier(trigger.args[-1])
    if new == wanted and (trigger.nick == bot.nick or bot.nick == wanted):
        if CHANNEL in bot.channels:
            _ensure_op_and_topic(bot)


def _ensure_op(bot):
    channel = bot.channels.get(CHANNEL)
    have_op = bool(channel) and channel.is_op(bot.nick)
    if channel and not have_op:
        bot.write(['PRIVMSG', 'ChanServ'], f'OP {CHANNEL} {bot.nick}')
        return False
    return True


def _ensure_topic(bot):
    channel = bot.channels.get(CHANNEL)
    current = channel.topic if channel else None
    if current != TOPIC:
        bot.write(['TOPIC', CHANNEL], TOPIC)


def _ensure_op_and_topic(bot):
    if _ensure_op(bot):
        _ensure_topic(bot)
    # Ist noch kein Op da, kommt gleich die MODE-Zeile von ChanServ rein,
    # die den on_mode_change-Handler unten auslöst und es dann erneut versucht.


@module.event('366')  # RPL_ENDOFNAMES - Channel ist vollständig gejoint
def on_join_complete(bot, trigger):
    if trigger.args[1] == CHANNEL:
        _ensure_op_and_topic(bot)


@module.event('MODE')
def on_mode_change(bot, trigger):
    # Reagiert u.a. auf die +o-Zeile von ChanServ, egal ob automatisch
    # oder als Antwort auf unsere eigene OP-Anfrage.
    if trigger.sender == CHANNEL:
        _ensure_op_and_topic(bot)


@module.interval(60)  # jede Minute selbst nachsehen, statt nur auf Events zu warten
def check_topic(bot):
    if not bot.connection_registered:
        return
    _ensure_nick(bot)
    if CHANNEL in bot.channels:
        _ensure_op_and_topic(bot)


@module.commands('settopic')
@module.require_admin('Nur der Bot-Owner darf das Topic manuell zurücksetzen.')
@module.example('!settopic')
def settopic_cmd(bot, trigger):
    _ensure_op_and_topic(bot)
    _ensure_nick(bot)
    bot.reply("Erledigt (Nick/Op ggf. angefragt, Topic gesetzt).")
