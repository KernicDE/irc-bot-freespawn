"""Tests für die Nick-Rückgewinnung in spawnkeeper_topic. Aufruf: pytest tests/."""
import importlib.util
import pathlib
from types import SimpleNamespace

PLUGINS = pathlib.Path(__file__).resolve().parent.parent / "plugins"
spec = importlib.util.spec_from_file_location("spawnkeeper_topic", PLUGINS / "spawnkeeper_topic.py")
topic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(topic)


class FakeBot:
    def __init__(self, nick):
        self.nick = nick
        self.settings = SimpleNamespace(core=SimpleNamespace(nick="SpawnKeeper"))
        self.channels = {}
        self.sent = []

    def make_identifier(self, name):
        return name.lower()

    def write(self, args, text=None):
        self.sent.append((tuple(args), text))

    def say(self, text, target):
        self.sent.append((("PRIVMSG", target), text))


def ison_reply(bot, nicks):
    return SimpleNamespace(args=[bot.nick, nicks])


def test_no_ison_when_nick_is_correct():
    bot = FakeBot("spawnkeeper")
    topic._ensure_nick(bot)
    assert bot.sent == []


def test_asks_ison_when_nick_is_wrong():
    bot = FakeBot("spawnkeeper_")
    topic._ensure_nick(bot)
    assert bot.sent == [(("ISON", "SpawnKeeper"), None)]


def test_regain_when_nick_still_online():
    bot = FakeBot("spawnkeeper_")
    topic.on_ison(bot, ison_reply(bot, "SpawnKeeper"))
    assert bot.sent == [(("PRIVMSG", "NickServ"), "REGAIN SpawnKeeper")]


def test_plain_nick_change_when_nick_free():
    bot = FakeBot("spawnkeeper_")
    topic.on_ison(bot, ison_reply(bot, ""))
    assert bot.sent == [(("NICK", "SpawnKeeper"), None)]


def test_requests_op_after_regaining_nick():
    bot = FakeBot("spawnkeeper_")
    bot.channels[topic.CHANNEL] = SimpleNamespace(is_op=lambda nick: False, topic=topic.TOPIC)
    topic.on_nick(bot, SimpleNamespace(nick="spawnkeeper_", args=["SpawnKeeper"]))
    assert bot.sent == [(("PRIVMSG", "ChanServ"), "OP #freespawn spawnkeeper_")]


def test_ignores_other_users_nick_changes():
    bot = FakeBot("spawnkeeper_")
    bot.channels[topic.CHANNEL] = SimpleNamespace(is_op=lambda nick: False, topic=topic.TOPIC)
    topic.on_nick(bot, SimpleNamespace(nick="someone", args=["other"]))
    assert bot.sent == []
