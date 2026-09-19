"""
Hält Op-Status und Topic von #freespawn dauerhaft aufrecht.

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
TOPIC = "FreeSpawn Clan Channel | Forum: https://freespawn.de | Mumble: freespawn.de:64738"


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


@module.interval(300)  # alle 5 Minuten selbst nachsehen, statt nur auf Events zu warten
def check_topic(bot):
    if bot.connection_registered and CHANNEL in bot.channels:
        _ensure_op_and_topic(bot)


@module.commands('settopic')
@module.require_admin('Nur der Bot-Owner darf das Topic manuell zurücksetzen.')
@module.example('!settopic')
def settopic_cmd(bot, trigger):
    _ensure_op_and_topic(bot)
    bot.reply("Erledigt (Op ggf. bei ChanServ angefragt, Topic gesetzt).")
