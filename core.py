"""The cog framework: registry, dispatch, and the decorators cogs use.

Modelled on discord.py so the mental model carries over:

    class Ping(Cog):
        @command("ping", help="Check the bot is alive")
        def ping(self, ctx):
            ctx.reply("pong")

    def setup(bot):
        bot.add_cog(Ping(bot))

Difference worth knowing: Viber is webhook-driven, not a websocket gateway.
There is no persistent process and no `await`. Each incoming message is one
HTTP request handled synchronously, so handlers are plain functions.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

COMMAND_ATTR = "__viber_command__"
CALLBACK_ATTR = "__viber_callback__"
LISTENER_ATTR = "__viber_listener__"


def _first_line(text: str | None) -> str:
    for line in (text or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def command(
    name: str | None = None,
    *,
    aliases: tuple[str, ...] = (),
    help: str = "",
    usage: str = "",
    hidden: bool = False,
) -> Callable:
    """Mark a Cog method as a text command."""

    def decorator(func: Callable) -> Callable:
        setattr(
            func,
            COMMAND_ATTR,
            {
                "name": (name or func.__name__).lower(),
                "aliases": tuple(a.lower() for a in aliases),
                "help": help or _first_line(func.__doc__),
                "usage": usage,
                "hidden": hidden,
            },
        )
        return func

    return decorator


def callback(prefix: str) -> Callable:
    """Mark a Cog method as the handler for keyboard button presses.

    Any incoming message whose text starts with `prefix` is routed here instead
    of the command parser. Button ActionBody strings are how a stateless bot
    remembers anything, so this is the main hook for interactive UI.
    """

    def decorator(func: Callable) -> Callable:
        setattr(func, CALLBACK_ATTR, prefix)
        return func

    return decorator


def listener(event: str) -> Callable:
    """Mark a Cog method as a handler for a raw Viber event.

    Events: subscribed, unsubscribed, conversation_started, delivered, seen,
    failed. A `conversation_started` listener may return a message payload dict,
    which is sent back as the webhook's HTTP response body.
    """

    def decorator(func: Callable) -> Callable:
        setattr(func, LISTENER_ATTR, event)
        return func

    return decorator


@dataclass
class Command:
    name: str
    aliases: tuple[str, ...]
    help: str
    usage: str
    hidden: bool
    handler: Callable
    cog_name: str


@dataclass
class Context:
    """Everything a handler needs about the message it is answering."""

    bot: "Bot"
    event: dict[str, Any]
    sender_id: str
    sender_name: str = ""
    text: str = ""
    invoked_with: str = ""
    args: list[str] = field(default_factory=list)

    @property
    def client(self):
        return self.bot.client

    @property
    def arg_string(self) -> str:
        return " ".join(self.args)

    def reply(self, text: str, keyboard: dict[str, Any] | None = None, tracking_data: str | None = None):
        return self.bot.client.send_text(self.sender_id, text, keyboard=keyboard, tracking_data=tracking_data)


class Cog:
    """Base class for a group of related commands.

    Set `name` and `description` — the help command reads them.
    """

    name: str | None = None
    description: str = ""

    def __init__(self, bot: "Bot"):
        self.bot = bot

    @property
    def qualified_name(self) -> str:
        return self.name or type(self).__name__

    def cog_load(self) -> None:
        """Optional hook, called once when the cog is registered."""


class Bot:
    """Command registry plus the webhook event dispatcher."""

    def __init__(self, client, prefix: str = ""):
        self.client = client
        self.prefix = prefix or ""
        self.cogs: dict[str, Cog] = {}
        self.failed_cogs: dict[str, str] = {}  # module name -> error, shown on /
        self.commands: dict[str, Command] = {}  # canonical name -> Command
        self._lookup: dict[str, Command] = {}  # name and aliases -> Command
        self._callbacks: list[tuple[str, Callable]] = []
        self._listeners: dict[str, list[Callable]] = {}

    # --- registration -------------------------------------------------------

    def add_cog(self, cog: Cog) -> None:
        name = cog.qualified_name
        if name in self.cogs:
            raise ValueError(f"cog {name!r} is already loaded")

        commands: list[Command] = []
        callbacks: list[tuple[str, Callable]] = []
        listeners: list[tuple[str, Callable]] = []

        for attr in dir(type(cog)):
            # getattr_static does not trigger descriptors. A plain getattr here
            # would evaluate every property on the cog, and since cogs load at
            # import time on Vercel, a property that raises would take down the
            # whole function on cold start.
            try:
                raw = inspect.getattr_static(cog, attr)
            except AttributeError:
                continue
            # staticmethod and classmethod wrap the real function, and the
            # decorator's marker attribute lives on that inner function. A
            # classmethod object is not even callable, so unwrap before testing.
            raw = getattr(raw, "__func__", raw)
            if not callable(raw):
                continue

            meta = getattr(raw, COMMAND_ATTR, None)
            prefix = getattr(raw, CALLBACK_ATTR, None)
            event = getattr(raw, LISTENER_ATTR, None)
            if meta is None and prefix is None and event is None:
                continue

            member = getattr(cog, attr)  # safe now: it is a decorated method
            if meta is not None:
                commands.append(Command(handler=member, cog_name=name, **meta))
            if prefix is not None:
                callbacks.append((prefix, member))
            if event is not None:
                listeners.append((event, member))

        # Validate everything before mutating any registry, so a rejected cog
        # cannot leave half of itself installed.
        claimed = {existing for existing, _ in self._callbacks}
        for prefix, _ in callbacks:
            if prefix in claimed:
                raise ValueError(f"callback prefix {prefix!r} is already registered")
            claimed.add(prefix)

        seen: set[str] = set()
        for cmd in commands:
            for key in (cmd.name, *cmd.aliases):
                if key in self._lookup or key in seen:
                    raise ValueError(f"command name {key!r} is already taken")
                seen.add(key)

        # Last thing that can fail, so it runs before anything is committed.
        cog.cog_load()

        for cmd in commands:
            self._register_command(cmd)
        self._callbacks.extend(callbacks)
        # Longest prefix wins, so "ttt:mv:" beats a broader "ttt:".
        self._callbacks.sort(key=lambda pair: len(pair[0]), reverse=True)
        for event_name, handler in listeners:
            self._listeners.setdefault(event_name, []).append(handler)

        self.cogs[name] = cog
        log.info("loaded cog %s", name)

    def _register_command(self, cmd: Command) -> None:
        for key in (cmd.name, *cmd.aliases):
            if key in self._lookup:
                raise ValueError(f"command name {key!r} is already taken by {self._lookup[key].cog_name}")
            self._lookup[key] = cmd
        self.commands[cmd.name] = cmd

    def load_cogs(self, package: str = "bot.cogs") -> None:
        """Import every module in `package` and call its `setup(bot)`.

        One broken cog must not take the bot offline. This runs at import time
        on Vercel, so an exception here would make every request 500 and Viber
        would eventually unregister the webhook — a syntax error in a brand new
        cog would silently kill tic-tac-toe too. Failures are recorded in
        `failed_cogs` instead, and the health page lists them.
        """
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_"):
                continue
            try:
                module = importlib.import_module(f"{package}.{info.name}")
                setup = getattr(module, "setup", None)
                if setup is None:
                    raise AttributeError("no setup(bot) function")
                setup(self)
            except Exception as exc:
                self.failed_cogs[info.name] = f"{type(exc).__name__}: {exc}"
                log.exception("cog %r failed to load and was skipped", info.name)

    def get_command(self, name: str) -> Command | None:
        return self._lookup.get(name.lower())

    # --- dispatch -----------------------------------------------------------

    def handle(self, event: Any) -> dict[str, Any] | None:
        """Process one webhook event.

        Returns a message payload to put in the HTTP response body, or None.
        Only `conversation_started` normally uses that channel.

        Takes Any rather than dict on purpose: the argument comes straight from
        a parsed request body, so it is whatever the caller sent.
        """
        if not isinstance(event, dict):
            log.warning("webhook payload was %s, not an object", type(event).__name__)
            return None
        name = str(event.get("event") or "").lower()
        if name == "webhook":
            return None  # Viber's one-off probe when you register the URL
        if name == "message":
            self._on_message(event)
            return None
        return self._dispatch(name, self._context(event))

    def _dispatch(self, event_name: str, ctx: Context) -> dict[str, Any] | None:
        handlers = self._listeners.get(event_name, [])
        if not handlers:
            log.debug("no listener registered for %r", event_name)
            return None
        result: dict[str, Any] | None = None
        for handler in handlers:
            # A listener that raises must not escape: the webhook has to answer
            # 200 or Viber eventually unregisters it.
            try:
                out = handler(ctx)
            except Exception:
                log.exception("listener for %r raised", event_name)
                continue
            if isinstance(out, dict) and result is None:
                result = out
        return result

    def _context(self, event: dict[str, Any], text: str = "") -> Context:
        # message/seen events carry "sender"; subscribed and
        # conversation_started carry "user"; unsubscribed only "user_id".
        sender_id, sender_name = "", ""
        for key in ("sender", "user"):
            data = event.get(key)
            if isinstance(data, dict):
                sender_id = str(data.get("id") or "")
                sender_name = str(data.get("name") or "")
                break
        else:
            sender_id = str(event.get("user_id") or "")
        return Context(
            bot=self, event=event, sender_id=sender_id, sender_name=sender_name, text=text
        )

    def parse(self, text: str) -> tuple[str | None, list[str]]:
        """Split raw text into (command_name, args).

        Returns (None, []) when a prefix is configured and the text does not
        start with it — that is normal chatter, not a failed command.
        """
        body = text
        if self.prefix:
            if not body.startswith(self.prefix):
                return None, []
            body = body[len(self.prefix) :]
        parts = body.strip().split()
        if not parts:
            return None, []
        return parts[0].lower(), parts[1:]

    def _on_message(self, event: dict[str, Any]) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            message = {}
        text = str(message.get("text") or "").strip()
        ctx = self._context(event, text)

        if not ctx.sender_id:
            log.warning("message event with no sender id, dropping")
            return

        if not text:
            # Sticker, image, location, contact... nothing to parse.
            self._dispatch("non_text_message", ctx)
            return

        # Keyboard button presses arrive as ordinary messages whose text is the
        # button's ActionBody, so callbacks are checked before command parsing.
        for prefix, handler in self._callbacks:
            if text.startswith(prefix):
                self._invoke(handler, ctx, label=prefix)
                return

        name, args = self.parse(text)
        if name is None:
            return

        ctx.invoked_with = name
        ctx.args = args

        cmd = self.get_command(name)
        if cmd is None:
            self._dispatch("unknown_command", ctx)
            return

        self._invoke(cmd.handler, ctx, label=cmd.name)

    def _invoke(self, handler: Callable, ctx: Context, label: str) -> None:
        try:
            handler(ctx)
        except Exception:
            log.exception("handler %r raised", label)
            try:
                ctx.reply("Something broke on my end. Try that again?")
            except Exception:
                log.exception("could not deliver the error notice either")

