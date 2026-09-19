"""Tests für !users (Mumble-User mit Kanal). Braucht `pip install sopel pytest`; Aufruf: pytest tests/."""
import importlib.util
import pathlib

PLUGINS = pathlib.Path(__file__).resolve().parent.parent / "plugins"


def load(name):
    spec = importlib.util.spec_from_file_location(name, PLUGINS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mumble = load("spawnkeeper_mumble")
commands = load("spawnkeeper_commands")

CHANNELS = {0: {"name": "FreeSpawn"}, 1: {"name": "Lounge"}, 2: {"name": "Gaming #1"}, 3: {"name": "AFK"}}


def users(*items):
    return {i: {"session": i, "name": n, "channel_id": c} for i, (n, c) in enumerate(items)}


def test_groups_by_channel_in_id_order_and_names_sorted():
    u = users(("zoe", 2), ("Anna", 1), ("bob", 2), ("Cy", 1))
    assert mumble.group_users_by_channel(u, CHANNELS) == [("Lounge", ["Anna", "Cy"]), ("Gaming #1", ["bob", "zoe"])]


def test_bot_is_left_out():
    u = users((mumble.MUMBLE_NICK, 3), ("Anna", 1))
    assert mumble.group_users_by_channel(u, CHANNELS) == [("Lounge", ["Anna"])]


def test_unknown_channel_gets_placeholder_name():
    assert mumble.group_users_by_channel(users(("Anna", 99)), CHANNELS) == [("Unbekannt", ["Anna"])]


def test_empty_server():
    assert mumble.group_users_by_channel(users((mumble.MUMBLE_NICK, 3)), CHANNELS) == []
    assert commands.format_users([]) == "Mumble ist aktuell leer."


def test_format_lists_count_and_channels():
    text = commands.format_users([("Lounge", ["Anna", "Cy"]), ("Gaming #1", ["bob"])])
    assert text == "Aktuell auf Mumble: 3 User – Lounge: Anna, Cy | Gaming #1: bob"


def test_format_without_connection():
    assert commands.format_users(None) == "Der Mumble-Status ist gerade nicht verfügbar."


def test_help_mentions_channel():
    class Bot:
        said = []
        def say(self, msg): self.said.append(msg)
    bot = Bot()
    commands.help_cmd(bot, None)
    assert "mit Kanal" in bot.said[0]
