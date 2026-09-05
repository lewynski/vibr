"""Help, ping, and the greeting/fallback wiring.

Also the reference example: copy the shape of this file to add a command.
"""

from __future__ import annotations

from ..core import Cog, Context, command, listener

BRAND = "#7360F2"
BRAND_TINT = "#EDEAFB"
WHITE = "#FFFFFF"


def main_menu(bot) -> dict:
    """Buttons for the two things a new user should try first.

    Built from the command registry instead of hardcoded callback payloads. That
    keeps this cog independent of the others: deleting bot/cogs/tictactoe.py
    drops the button rather than breaking the greeting. Each button sends the
    command text itself and is Silent, so it runs through the normal parser
    without the text appearing in the chat.
    """
    from ..viber import button, keyboard  # local import keeps the engine import-light

    buttons = []
    if bot.get_command("ttt"):
        buttons.append(
            button("🎮 Play tic-tac-toe", f"{bot.prefix}ttt", columns=6, bg_color=BRAND, text_color=WHITE)
        )
    buttons.append(
        button("❔ What can you do?", f"{bot.prefix}help", columns=6, bg_color=BRAND_TINT, text_color=BRAND)
    )
    return keyboard(buttons)


class General(Cog):
    name = "General"
    description = "Help and basics."

    @command("help", aliases=("commands", "h"), usage="help [command]", help="List commands, or explain one")
    def help_command(self, ctx: Context) -> None:
        if ctx.args:
            found = ctx.bot.get_command(ctx.args[0])
            if found is None:
                ctx.reply(f'I have no command called "{ctx.args[0]}".', main_menu(ctx.bot))
                return
            prefix = ctx.bot.prefix
            body = [f"{prefix}{found.usage or found.name}"]
            if found.help:
                body.append(found.help)
            if found.aliases:
                body.append("Also: " + ", ".join(f"{prefix}{a}" for a in found.aliases))
            ctx.reply("\n".join(body))
            return

        ctx.reply(self._overview(ctx.bot), main_menu(ctx.bot))

    @command("ping", help="Check that I'm awake")
    def ping(self, ctx: Context) -> None:
        ctx.reply("pong 🏓")

    def _overview(self, bot) -> str:
        prefix = bot.prefix
        lines = ["Here's what I can do:"]
        for cog_name in bot.cogs:
            commands = sorted(
                (c for c in bot.commands.values() if c.cog_name == cog_name and not c.hidden),
                key=lambda c: c.name,
            )
            if not commands:
                continue
            lines.append("")
            lines.append(f"— {cog_name} —")
            for cmd in commands:
                label = f"{prefix}{cmd.usage or cmd.name}"
                lines.append(f"{label}{' · ' + cmd.help if cmd.help else ''}")
        return "\n".join(lines)

    # --- events -------------------------------------------------------------

    @listener("conversation_started")
    def on_conversation_started(self, ctx: Context):
        """Answered in the HTTP response body — the user hasn't subscribed yet,
        so send_message would be rejected at this point."""
        greeting = f"Hi {ctx.sender_name}! " if ctx.sender_name else "Hi! "
        return ctx.client.text_payload(
            greeting + "I'm a bot you can play games against. Want a round of tic-tac-toe?",
            main_menu(ctx.bot),
        )

    @listener("subscribed")
    def on_subscribed(self, ctx: Context) -> None:
        ctx.reply("Thanks for subscribing! Tap below to start.", main_menu(ctx.bot))

    @listener("unknown_command")
    def on_unknown_command(self, ctx: Context) -> None:
        prefix = ctx.bot.prefix
        ctx.reply(
            f'I don\'t know "{ctx.invoked_with}". Try {prefix}help for the list.',
            main_menu(ctx.bot),
        )

    @listener("non_text_message")
    def on_non_text_message(self, ctx: Context) -> None:
        ctx.reply("I only read text for now. Here's what I can do:", main_menu(ctx.bot))


def setup(bot) -> None:
    bot.add_cog(General(bot))
