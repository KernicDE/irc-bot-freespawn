# irc-bot-freespawn (SpawnKeeper)

IRC-Bot der FreeSpawn-Community für `#freespawn` auf irc.libera.chat. Basis ist [Sopel](https://sopel.chat/) mit eigenen Plugins in `plugins/`.

## Plugins

| Plugin | Aufgabe |
|---|---|
| `spawnkeeper_commands` | Befehle: `!help`, `!users`/`!user` (Mumble-Nutzer nach Kanal), `!mumble`/`!voice`, `!forum`, `!irc`, `!rules`/`!regeln`, `!apply`/`!bewerben`, `!next` |
| `spawnkeeper_mumble` | Verbindung zum Mumble-Server (pymumble), meldet Ereignisse im Channel; verbindet sich als SuperUser, um Nutzer im AFK-Kanal automatisch stummzuschalten |
| `spawnkeeper_forum` | Meldet neue Beiträge aus dem öffentlichen Forum-RSS-Feed (jeder einzelne Beitrag, nicht nur der erste eines Threads) |
| `spawnkeeper_twitch` | Spiegelt den Twitch-Chat von `TWITCH_CHANNEL` als `[Twitch] USER: NACHRICHT` (anonym, nur lesend; leer = aus) |
| `spawnkeeper_topic` | Hält Nick, Op-Status und Topic dauerhaft aufrecht; holt den Nick nach einem Reconnect per ISON/NickServ REGAIN zurück; `!settopic` |

Sopels Plugins `help` und `tld` sind abgeschaltet (`sopel.cfg.template`). Die Anmeldung läuft per SASL.

## Konfiguration

`.env` (Vorlage `.env.example`):

| Variable | Bedeutung |
|---|---|
| `NICKSERV_PASSWORD` | Passwort des Bot-Accounts (SASL), wird beim Start in `sopel.cfg` eingesetzt |
| `MUMBLE_SUPERUSER_PASSWORD` | muss dem `MUMBLE_SUPERUSER_PASSWORD` des Mumble-Servers entsprechen (SuperUser umgeht alle ACLs) |
| `TWITCH_CHANNEL` | Twitch-Kanal für das Chat-Relay, leer = aus |
| `TWITCH_IGNORE_USERS`, `TWITCH_OWN_BOT_REGEX` | optional: Filter für das Relay |

## Betrieb

Docker-Image `ghcr.io/kernicde/freespawn-irc-bot:latest` (GitHub Actions, `.github/workflows/build.yml`), Python 3.11 wegen `pymumble`. Compose-Datei `compose.yaml` (Netzwerk `freespawn-net`, Daten und Logs in `./data`). Der Entrypoint erzeugt `sopel.cfg` aus dem Template und startet Sopel als `nobody`.

```bash
docker compose up -d
docker logs --tail 20 irc-bot-freespawn
```

## Tests

```bash
pytest tests/
```

Die Tests decken Mumble-Nutzerlisten, Nick-Rückgewinnung und das Twitch-Relay ab.
