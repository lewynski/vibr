"""Framework tests: command routing, keyboard callbacks, listeners, signatures."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from bot.cogs import tictactoe as ttt
from bot.core import Bot, Cog, callback, command, listener
from bot.viber import verify_signature


class FakeClient:
    """Records outgoing messages instead of calling Viber."""

    name = "Test Bot"

    def __init__(self):
        self.sent: list[dict] = []

    @property
    def sender(self) -> dict:
        return {"name": self.name}

    def send_text(self, receiver, text, keyboard=None, tracking_data=None):
        self.sent.append({"receiver": receiver, "text": text, "keyboard": keyboard})
        return {"status": 0}

    def text_payload(self, text, keyboard=None):
        return {"type": "text", "sender": self.sender, "text": text, "keyboard": keyboard}

    @property
    def last(self) -> dict:
        assert self.sent, "the bot did not send anything"
        return self.sent[-1]


def make_bot(prefix: str = "") -> tuple[Bot, FakeClient]:
    client = FakeClient()
    bot = Bot(client, prefix=prefix)
    bot.load_cogs()
    return bot, client


def feed(bot: Bot, text: str, sender_id: str = "user-1"):
    return bot.handle(
        {
            "event": "message",
            "sender": {"id": sender_id, "name": "Tester"},
            "message": {"type": "text", "text": text},
        }
    )


def test_cogs_autoload():
    bot, _ = make_bot()
    assert "Tic-Tac-Toe" in bot.cogs
    assert "General" in bot.cogs
    for name in ("ttt", "help", "ping"):
        assert bot.get_command(name) is not None
    # aliases resolve to the same command object
    assert bot.get_command("tictactoe") is bot.get_command("ttt")


def test_duplicate_command_names_are_rejected():
    class Clash(Cog):
        name = "Clash"

        @command("ping")
        def ping(self, ctx):
            pass

    bot, _ = make_bot()
    with pytest.raises(ValueError, match="already taken"):
        bot.add_cog(Clash(bot))


def test_command_dispatch():
    bot, client = make_bot()
    feed(bot, "ping")
    assert "pong" in client.last["text"]


def test_command_is_case_insensitive_and_ignores_extra_spaces():
    bot, client = make_bot()
    feed(bot, "   PING   ")
    assert "pong" in client.last["text"]


def test_unknown_command_falls_back_to_help():
    bot, client = make_bot()
    feed(bot, "flurble")
    assert "flurble" in client.last["text"]
    assert "help" in client.last["text"]


def test_prefix_is_enforced_when_configured():
    bot, client = make_bot(prefix="!")
    feed(bot, "ping")  # no prefix: ordinary chatter, ignored entirely
    assert client.sent == []
    feed(bot, "!ping")
    assert "pong" in client.last["text"]


def test_keyboard_callbacks_bypass_the_prefix():
    # Buttons send raw payloads, so they must work whatever the prefix is.
    bot, client = make_bot(prefix="!")
    feed(bot, f"{ttt.CB_NEW}{ttt.HARD}")
    assert client.last["keyboard"] is not None


def test_non_text_message_is_answered_gracefully():
    bot, client = make_bot()
    bot.handle(
        {
            "event": "message",
            "sender": {"id": "user-1", "name": "Tester"},
            "message": {"type": "sticker", "sticker_id": 40100},
        }
    )
    assert "text" in client.last["text"].lower()


def test_conversation_started_replies_in_the_response_body():
    bot, client = make_bot()
    response = bot.handle(
        {"event": "conversation_started", "user": {"id": "user-1", "name": "Lewyn"}}
    )
    assert response is not None
    assert "Lewyn" in response["text"]
    assert response["sender"] == {"name": "Test Bot"}
    assert client.sent == [], "conversation_started must not call send_message"


def test_webhook_probe_is_a_no_op():
    bot, client = make_bot()
    assert bot.handle({"event": "webhook", "timestamp": 1}) is None
    assert client.sent == []


def test_unhandled_events_do_not_raise():
    bot, _ = make_bot()
    assert bot.handle({"event": "delivered", "user_id": "user-1"}) is None
    assert bot.handle({}) is None


def test_handler_errors_are_reported_not_raised():
    class Boom(Cog):
        name = "Boom"

        @command("boom")
        def boom(self, ctx):
            raise RuntimeError("intentional")

    bot, client = make_bot()
    bot.add_cog(Boom(bot))
    feed(bot, "boom")  # must not propagate
    assert "broke" in client.last["text"]


def test_verify_signature():
    token = "s3cret-token"
    body = b'{"event":"message"}'
    signature = hmac.new(token.encode(), body, hashlib.sha256).hexdigest()

    assert verify_signature(body, signature, token)
    assert verify_signature(body, signature.upper(), token), "hex case must not matter"
    assert not verify_signature(body, signature, "different-token")
    assert not verify_signature(b'{"event":"tampered"}', signature, token)
    assert not verify_signature(body, None, token)
    assert not verify_signature(body, "", token)
    assert not verify_signature(body, signature, "")


# --- end-to-end: a whole game driven through button payloads ---------------


def _first_move_button(keyboard: dict) -> str | None:
    for btn in keyboard["Buttons"]:
        if btn["ActionType"] == "reply" and btn["ActionBody"].startswith(ttt.CB_MOVE):
            return btn["ActionBody"]
    return None


def test_full_game_played_through_button_payloads():
    """Taps the first free square each turn until the game ends, exactly as a
    real client would: state only ever travels in the button payloads."""
    bot, client = make_bot()
    feed(bot, "ttt hard")
    assert "You go first" in client.last["text"]

    for _ in range(9):
        payload = _first_move_button(client.last["keyboard"])
        if payload is None:
            break
        feed(bot, payload)

    final = client.last["text"]
    assert "You win" not in final, "hard mode lost an end-to-end game"
    assert "that's the game" in final or "Draw" in final
    assert _first_move_button(client.last["keyboard"]) is None


def test_ttt_without_args_offers_the_difficulty_menu():
    bot, client = make_bot()
    feed(bot, "ttt")
    bodies = [btn["ActionBody"] for btn in client.last["keyboard"]["Buttons"]]
    assert bodies == [f"{ttt.CB_NEW}{code}" for code in (ttt.EASY, ttt.MEDIUM, ttt.HARD)]


def test_unrecognised_difficulty_offers_the_menu():
    bot, client = make_bot()
    feed(bot, "ttt nightmare")
    assert "nightmare" in client.last["text"]
    assert len(client.last["keyboard"]["Buttons"]) == 3


def test_taken_square_is_refused():
    bot, client = make_bot()
    feed(bot, f"{ttt.CB_MOVE}X........:h:0")
    assert "taken" in client.last["text"]


def test_tampered_board_is_refused():
    bot, client = make_bot()
    feed(bot, f"{ttt.CB_MOVE}XXXXXXXXX:h:0")
    assert "can't read that board" in client.last["text"]


def test_help_output():
    bot, client = make_bot()
    feed(bot, "help")
    assert "ttt" in client.last["text"]
    assert "Tic-Tac-Toe" in client.last["text"]

    feed(bot, "help ttt")
    assert "easy" in client.last["text"].lower()

    feed(bot, "help nosuchthing")
    assert "no command" in client.last["text"]


# --- regressions ------------------------------------------------------------
# Each test below pins a defect found by review or fuzzing. The theme is that
# nothing reachable from a webhook body may raise, because Viber unregisters a
# webhook that keeps failing.


def test_duplicate_callback_prefixes_are_rejected():
    class Clash(Cog):
        name = "Clash"

        @callback(ttt.CB)
        def on_button(self, ctx):
            pass

    bot, _ = make_bot()
    with pytest.raises(ValueError, match="already registered"):
        bot.add_cog(Clash(bot))


def test_a_rejected_cog_leaves_nothing_behind():
    """add_cog validates before it mutates, so a clash cannot half-install."""

    class Clash(Cog):
        name = "Clash"

        @command("brandnew")
        def brandnew(self, ctx):
            pass

        @command("ping")  # collides with General.ping
        def ping(self, ctx):
            pass

    bot, _ = make_bot()
    before = (dict(bot.commands), dict(bot._lookup), list(bot._callbacks), sorted(bot.cogs))
    with pytest.raises(ValueError):
        bot.add_cog(Clash(bot))
    assert "Clash" not in bot.cogs
    assert bot.get_command("brandnew") is None
    assert (dict(bot.commands), dict(bot._lookup), list(bot._callbacks), sorted(bot.cogs)) == before


def test_cog_properties_are_not_evaluated_during_registration():
    """Registration scans the class with getattr_static. A property that raises
    would otherwise blow up at import time and take down the cold start."""
    touched = []

    class Landmine(Cog):
        name = "Landmine"

        @property
        def trap(self):
            touched.append(1)
            raise RuntimeError("should never be evaluated")

        @command("safe")
        def safe(self, ctx):
            ctx.reply("fine")

    bot, client = make_bot()
    bot.add_cog(Landmine(bot))
    assert touched == []
    feed(bot, "safe")
    assert client.last["text"] == "fine"


def test_listener_errors_do_not_escape_dispatch():
    class Boom(Cog):
        name = "Boom"

        @listener("subscribed")
        def on_subscribed(self, ctx):
            raise RuntimeError("intentional")

    bot, client = make_bot()
    bot.add_cog(Boom(bot))
    # General also listens for "subscribed"; its reply must still go out.
    bot.handle({"event": "subscribed", "user": {"id": "user-1", "name": "Tester"}})
    assert client.sent, "a raising listener suppressed the working one"


@pytest.mark.parametrize("event", [None, 5, True, "hello", [{"event": "message"}]])
def test_handle_ignores_payloads_that_are_not_objects(event):
    bot, client = make_bot()
    assert bot.handle(event) is None
    assert client.sent == []


@pytest.mark.parametrize(
    "event",
    [
        {"event": 5},                                            # non-string event name
        {"event": None},                                         # missing event name
        {"event": "message", "sender": {"id": "u"}},             # no message key
        {"event": "message", "sender": {"id": "u"}, "message": "nope"},   # message not an object
        {"event": "message", "sender": "nope", "message": {"text": "ping"}},  # sender not an object
        {"event": "message", "message": {"text": "ping"}},       # no sender at all
    ],
)
def test_malformed_events_do_not_raise(event):
    bot, _ = make_bot()
    assert bot.handle(event) is None  # no exception is the assertion


def test_unrecognised_ttt_payload_gets_an_answer():
    # Silence is indistinguishable from a dead bot, so an unknown ttt: payload
    # replies with the difficulty menu instead of being dropped.
    bot, client = make_bot()
    feed(bot, "ttt:zzz")
    assert client.sent, "unrecognised payload produced no reply"
    assert len(client.last["keyboard"]["Buttons"]) == 3


def test_inert_cell_taps_stay_silent():
    bot, client = make_bot()
    feed(bot, ttt.CB_INERT)
    assert client.sent == []


def test_signature_header_with_non_ascii_is_rejected_not_fatal():
    # WSGI decodes headers as latin-1, so a hostile header can carry bytes that
    # hmac.compare_digest refuses to compare as str (TypeError -> HTTP 500).
    assert verify_signature(b"{}", "café", token="secret") is False
    assert verify_signature(b"{}", "\udcff\udcfe", token="secret") is False


def test_signature_accepts_an_uppercase_hex_digest():
    body = b'{"event":"message"}'
    digest = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, digest.upper(), token="secret") is True
    assert verify_signature(body, f"  {digest}  ", token="secret") is True


def test_main_menu_survives_the_tictactoe_cog_being_deleted():
    """The greeting builds its buttons from the registry, so cogs stay
    independently droppable — the point of the cogs/ layout."""
    from bot.cogs.general import main_menu

    client = FakeClient()
    bare = Bot(client, prefix="")
    from bot.cogs.general import setup as setup_general

    setup_general(bare)  # General only, no tictactoe
    bodies = [btn["ActionBody"] for btn in main_menu(bare)["Buttons"]]
    assert bodies == ["help"]


def test_a_finished_game_cannot_be_continued():
    # X has already won on this board. Without the is_over guard the payload
    # below would build "XXXOOX..." and carry on playing an illegal position.
    bot, client = make_bot()
    assert ttt.is_legal_board("XXXOO....")
    feed(bot, f"{ttt.CB_MOVE}XXXOO....:h:5")
    assert "already over" in client.last["text"]
    assert _first_move_button(client.last["keyboard"]) is None


def test_static_and_class_method_commands_register():
    """getattr_static returns the staticmethod/classmethod wrapper, and the
    decorator's marker sits on the inner function, so add_cog has to unwrap."""

    class Mixed(Cog):
        name = "Mixed"

        @staticmethod
        @command("stat")
        def stat(ctx):
            ctx.reply("static ok")

        @classmethod
        @command("klass")
        def klass(cls, ctx):
            ctx.reply("class ok")

    bot, client = make_bot()
    bot.add_cog(Mixed(bot))
    feed(bot, "stat")
    assert client.last["text"] == "static ok"
    feed(bot, "klass")
    assert client.last["text"] == "class ok"


def test_a_cog_whose_cog_load_raises_is_not_left_installed():
    class Fragile(Cog):
        name = "Fragile"

        @command("fragile")
        def fragile(self, ctx):
            pass

        def cog_load(self):
            raise RuntimeError("intentional")

    bot, _ = make_bot()
    with pytest.raises(RuntimeError):
        bot.add_cog(Fragile(bot))
    assert "Fragile" not in bot.cogs
    assert bot.get_command("fragile") is None


def test_one_broken_cog_does_not_take_the_bot_offline():
    """load_cogs runs at import time on Vercel. If it raised, every request
    would 500 and Viber would unregister the webhook."""
    client = FakeClient()
    bot = Bot(client, prefix="")
    bot.load_cogs("tests.brokencogs")
    assert "ping" in bot.commands, "the healthy cog did not load"
    assert "broken" in bot.failed_cogs
    assert "RuntimeError" in bot.failed_cogs["broken"]

