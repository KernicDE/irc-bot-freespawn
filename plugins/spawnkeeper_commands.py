"""
Öffentliche IRC-Befehle für #freespawn.
"""
from sopel import module


@module.commands('help')
@module.example('!help')
def help_cmd(bot, trigger):
    bot.say(
        "Verfügbare Befehle: "
        "!users/!user (wer ist in Mumble, mit Kanal), !mumble/!voice (Server-Info), !forum (Forum-Link), "
        "!irc (Channel-Info), !rules/!regeln (Regeln), !apply/!bewerben (Bewerbung), !next (nächster Termin)"
    )


@module.commands('mumble', 'voice')
@module.example('!mumble')
def mumble_cmd(bot, trigger):
    bot.say("Mumble-Server: freespawn.de:64738 | Verbinde dich und stell dich kurz vor.")


@module.commands('forum')
@module.example('!forum')
def forum_cmd(bot, trigger):
    bot.say("Forum: https://freespawn.de")


@module.commands('irc')
@module.example('!irc')
def irc_cmd(bot, trigger):
    bot.say("Wir sind auf irc.libera.chat im Channel #freespawn. Registriere deinen Nick mit /msg NickServ REGISTER.")


@module.commands('rules', 'regeln')
@module.example('!rules')
def rules_cmd(bot, trigger):
    bot.say("1. Sei respektvoll. 2. Kein Spam/Trolling. 3. Bewerbungen nur im Forum im Tag 'applications'.")


@module.commands('apply', 'bewerben')
@module.example('!apply')
def apply_cmd(bot, trigger):
    bot.say("Bewirb dich im Forum unter https://freespawn.de/t/applications (nur für registrierte Mitglieder sichtbar).")


@module.commands('next')
@module.example('!next')
def next_cmd(bot, trigger):
    bot.say("Der nächste Clan-Termin wird im Forum im Tag 'news' angekündigt.")


def format_users(channels):
    """Text für !users. channels: Liste aus (Kanalname, [Namen]) oder None (keine Verbindung)."""
    if channels is None:
        return "Der Mumble-Status ist gerade nicht verfügbar."
    count = sum(len(names) for _, names in channels)
    if count == 0:
        return "Mumble ist aktuell leer."
    parts = [f"{channel}: {', '.join(names)}" for channel, names in channels]
    return f"Aktuell auf Mumble: {count} User – " + " | ".join(parts)


@module.commands('users', 'user')
@module.example('!users')
def users_cmd(bot, trigger):
    bot.say(format_users(bot.memory.get('mumble_user_channels')))
