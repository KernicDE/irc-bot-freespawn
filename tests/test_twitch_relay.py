"""Tests für plugins/spawnkeeper_twitch.py (braucht `pip install sopel pytest`; Aufruf: pytest tests/)."""
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "spawnkeeper_twitch", pathlib.Path(__file__).resolve().parent.parent / "plugins" / "spawnkeeper_twitch.py")
tw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tw)

IGN = tw.ignored_users("kernicnet", raw=None)


def relay(line, ignore=IGN):
    return tw.format_relay(tw.parse_line(line), ignore)


def test_normal_message():
    assert relay("@display-name=Ada :ada!ada@ada.tmi.twitch.tv PRIVMSG #kernicnet :hallo welt : test") == "[Twitch] Ada: hallo welt : test"


def test_falls_back_to_nick_without_display_name():
    assert relay(":ada!ada@ada.tmi.twitch.tv PRIVMSG #kernicnet :moin") == "[Twitch] ada: moin"


def test_action_message():
    assert relay("@display-name=Ada :ada!a@a PRIVMSG #c :\x01ACTION winkt\x01") == "[Twitch] * Ada winkt"


def test_commands_bots_and_channel_owner_are_skipped():
    assert relay("@display-name=Ada :ada!a@a PRIVMSG #c :!forum") is None
    assert relay("@display-name=Nightbot :nightbot!a@a PRIVMSG #c :Hi") is None
    assert relay("@display-name=KernicNET :kernicnet!a@a PRIVMSG #c :Willkommen im Chat") is None


def test_ignore_list_can_be_emptied_or_changed():
    assert tw.ignored_users("kernicnet", raw="") == set()
    assert relay("@display-name=KernicNET :kernicnet!a@a PRIVMSG #c :hi", tw.ignored_users("kernicnet", raw="")) == "[Twitch] KernicNET: hi"
    assert tw.ignored_users("kernicnet", raw=" Foo, bar ,") == {"foo", "bar"}


def test_non_chat_lines_are_skipped():
    assert relay(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!") is None
    assert relay("@msg-id=raid :tmi.twitch.tv USERNOTICE #c") is None
    assert relay("PING :tmi.twitch.tv") is None


def test_control_characters_and_irc_colors_are_removed():
    out = relay("@display-name=Ada :ada!a@a PRIVMSG #c :\x02fett\x0304rot\x0f\x01 text\x07")
    assert out == "[Twitch] Ada: fettrot text"
    assert "\r" not in out and "\n" not in out


def test_long_message_is_truncated():
    out = relay("@display-name=Ada :ada!a@a PRIVMSG #c :" + "x" * 1000)
    assert len(out) <= len("[Twitch] Ada: ") + tw.MAX_TEXT and out.endswith("…")


def test_escaped_tag_values():
    assert relay(r"@display-name=Ada\sB :ada!a@a PRIVMSG #c :hi") == "[Twitch] Ada B: hi"


def test_queue_is_bounded():
    class Bot: pass
    r = tw.TwitchRelay(Bot(), "kernicnet")
    for i in range(tw.QUEUE_SIZE + 20):
        r.handle_line(f"@display-name=U{i} :u{i}!a@a PRIVMSG #c :msg")
    assert r.outbox.qsize() == tw.QUEUE_SIZE


def test_reconnect_is_detected_by_command_not_by_text():
    assert tw.parse_line(":tmi.twitch.tv RECONNECT")["command"] == "RECONNECT"
    assert tw.parse_line("@display-name=Ada :ada!a@a PRIVMSG #c :bitte RECONNECT")["command"] == "PRIVMSG"
