# Viber Bot

A Viber bot organised the way a discord.py bot is: commands live in `bot/cogs/`,
one file per feature, auto-discovered at startup. Adding a command means adding
a file — nothing else in the repo changes.

First command is tic-tac-toe, played on a keyboard of buttons, with easy, medium
and hard difficulty. Hard runs full minimax and cannot be beaten.

Runs on Vercel's free tier. No database.

## How this differs from a Discord bot

Discord gives you a gateway: one long-lived websocket, so your process stays up
and can just hold state in a dictionary. Viber gives you a webhook: Viber POSTs
each event to an HTTPS URL and you answer. Three consequences shape this repo.

Handlers are plain synchronous functions, not coroutines. There is no `await`,
no event loop, no `bot.run()` — a request comes in, you reply, the process may
die immediately after.

Nothing survives between messages. On Vercel each event can hit a cold process,
so a `games = {}` dict at module level is not a reliable place to keep a board.
Instead the board is encoded into every button's payload and comes back with the
tap. See `bot/cogs/tictactoe.py` for the encoding. Buttons are sent with
`Silent: true`, so the encoded string never shows up in the chat.

There is no `!` prefix convention. In a one-to-one chat with a bot, every
message is aimed at the bot, so `COMMAND_PREFIX` defaults to empty and users
type `ttt hard`. Set it to `!` if you prefer `!ttt hard`.

## Setup

### 1. Create the bot on Viber

Go to [partners.viber.com](https://partners.viber.com), sign in with your Viber
account, and choose **Create Bot Account**. You need a Viber account on a real
phone number.

Copy the auth token it gives you. It is shown once and it is the only credential
for the bot — treat it like a password. If you leak it, regenerate it from the
same page.

### 2. Deploy

```bash
git clone https://github.com/<you>/viber-bot.git
cd viber-bot
vercel            # or import the repo at vercel.com/new
```

Then set the environment variables in Vercel under **Settings → Environment
Variables**:

| Variable | Value |
| --- | --- |
| `VIBER_AUTH_TOKEN` | the token from step 1 |
| `VIBER_BOT_NAME` | display name on outgoing messages |
| `VIBER_BOT_AVATAR` | optional public image URL |
| `COMMAND_PREFIX` | leave empty, or `!` |

Redeploy after adding them — Vercel only injects env vars at build time.

Visit your deployment URL in a browser. You should get JSON with
`"ok": true` and your loaded commands. If `missing_config` lists
`VIBER_AUTH_TOKEN`, the env var did not make it into the deployment.

### 3. Register the webhook

```bash
pip install -r requirements-dev.txt
cp .env.example .env        # put the same token in here
python scripts/set_webhook.py https://your-app.vercel.app/
```

Viber immediately POSTs a probe to that URL and only saves it if the probe gets
HTTP 200 — so deploy before running this. If the endpoint later starts failing,
Viber unregisters the webhook; fix the error and run the command again.

### 4. Say hi

Your bot's chat link is on its page in the Viber admin panel, as
`viber://pa?chatURI=<your-uri>`. Open it on your phone, send `ttt`, and pick a
difficulty.

## Local development

Viber requires a public HTTPS URL with a real certificate, so `localhost` will
not do. Tunnel it:

```bash
python local_server.py                 # serves on 127.0.0.1:8000
ngrok http 8000                        # in a second terminal
python scripts/set_webhook.py https://<subdomain>.ngrok-free.app/
```

Point the webhook back at your Vercel URL when you are done — only one webhook
can be registered per bot at a time, so a live tunnel steals traffic from
production.

## Adding a command

Drop a file in `bot/cogs/`. The loader imports every module there and calls its
`setup(bot)`, so this is the entire contract:

```python
# bot/cogs/dice.py
import random
from ..core import Cog, Context, command


class Dice(Cog):
    name = "Dice"
    description = "Roll dice."

    @command("roll", aliases=("d",), usage="roll [sides]", help="Roll a die")
    def roll(self, ctx: Context) -> None:
        sides = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 6
        ctx.reply(f"🎲 {random.randint(1, sides)} (d{sides})")


def setup(bot) -> None:
    bot.add_cog(Dice(bot))
```

That is it — `roll` now works and `help` lists it automatically. Duplicate
command names raise at startup rather than silently shadowing each other, and so
do duplicate `@callback` prefixes. Registration validates the whole cog before it
touches any registry, so a rejected cog cannot leave half of itself installed.

Three decorators are available. `@command` registers a text command. `@callback`
claims a payload prefix, which is how you handle keyboard button presses:

```python
@callback("dice:")
def on_button(self, ctx: Context) -> None:
    sides = ctx.text.removeprefix("dice:")
    ...
```

`@listener` hooks a raw Viber event — `subscribed`, `unsubscribed`,
`conversation_started`, plus two synthetic ones this framework adds,
`unknown_command` and `non_text_message`. A `conversation_started` listener is
special: return a message payload and it goes out as the HTTP response body,
because the user has not subscribed yet and `send_message` would be refused.

`Context` carries `sender_id`, `sender_name`, `text`, `args`, `invoked_with`, the
raw `event`, and `reply(text, keyboard=None)`.

Cogs do not import each other. The greeting keyboard in `general.py` is built by
asking the registry whether a command exists, so deleting `bot/cogs/tictactoe.py`
drops the play button instead of breaking the greeting.

## Commands

| Command | What it does |
| --- | --- |
| `ttt [easy\|medium\|hard]` | Play tic-tac-toe. Aliases: `tictactoe`, `xo` |
| `help [command]` | List commands, or explain one. Aliases: `commands`, `h` |
| `ping` | Check the bot is awake |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The load-bearing test walks the entire game tree — every human move against
every move the bot rates as optimal — and asserts the human never reaches a win.
That is a proof that hard mode is unbeatable, not a spot check.

The rest covers command routing, prefix handling, malformed button payloads, the
signature gate, and one full game played end to end through button payloads. A
block of regression tests at the bottom of `tests/test_bot.py` and
`tests/test_webhook.py` pins one rule: nothing reachable from a webhook body may
raise. Anything that gets past the signature check — a body of `5`, a `message`
field that is a string, an event name that is a number, a cell index of `²` — has
to come back as HTTP 200, because Viber unregisters a webhook that keeps failing.

`tests/test_config.py` exists for one reason: `VIBER_VERIFY_SIGNATURE` is the
whole authentication story, so an env value the parser does not recognise has to
fall back to *on*. It asserts that `maybe`, `2`, an empty string and a stray pair
of quotes all leave the default intact.

For reference, against a human playing uniformly at random over 200,000 games:
easy loses about 40% of the time, medium about 12%, hard 0%.

## Security notes

**The signature check is the only thing guarding the webhook.** Viber requires
the URL to be publicly reachable, so anyone who learns it can POST to it. Viber
signs each request body with HMAC-SHA256 keyed by your auth token, and
`api/index.py` rejects anything that does not match with a 403. Leave
`VIBER_VERIFY_SIGNATURE=true`. Turning it off means anyone can impersonate any
user to your bot.

**Never commit the token.** `.env` is gitignored; the token belongs in Vercel's
environment variables. If it ends up in a commit, regenerate it at
partners.viber.com — rewriting git history is not enough, since the old value is
already public.

**Board state is client-supplied.** Because there is no database, the board
travels in the button payload, and a determined user could hand-craft a payload
to give themselves a better position. The stake is bragging rights in
tic-tac-toe, so this is an accepted trade rather than a bug. Every incoming board
is still checked for legality (`is_legal_board`), so a malformed or impossible
payload gets rejected instead of crashing the handler or producing nonsense. If
you later add something where cheating actually matters — a score table, a wager,
anything persistent — move state server-side first. Upstash Redis has a free
tier and works from Vercel functions.

**The `GET /` health page is public.** It returns the bot name and the list of
loaded commands, no secrets. If you would rather it were not enumerable, gate it
behind a query token or delete the `health()` branch.

## Repo layout

```
api/index.py            Vercel entrypoint: Flask app, signature gate, dispatch
bot/core.py             Cog base class, decorators, registry, event dispatch
bot/viber.py            Viber REST client, HMAC verification, keyboard builders
bot/config.py           Environment variables
bot/cogs/general.py     help, ping, greeting, unknown-command fallback
bot/cogs/tictactoe.py   Game engine (pure) + the cog that renders it
scripts/set_webhook.py  Register your URL with Viber
local_server.py         Dev server for use behind ngrok
tests/                  pytest suite
.python-version         Pins Vercel's interpreter to 3.12
```

## Troubleshooting

`set_webhook` fails with a status message about the webhook URL — the URL is not
returning 200 yet. Open it in a browser first; you should see the health JSON.

Health page shows `missing_config: ["VIBER_AUTH_TOKEN"]` — the env var is not in
that deployment. Add it in Vercel settings and redeploy.

Every request 403s — the token in your Vercel env does not match the token the
webhook was registered with. Re-check both, then re-run `set_webhook.py`.

Bot goes quiet after working for a while — Viber unregistered the webhook after
repeated failures. Check the Vercel function logs, fix, and register again.

Buttons do nothing — the ActionBody prefix does not match any `@callback`. The
tictactoe cog claims the single prefix `ttt:` and sorts out `ttt:mv:`, `ttt:new:`
and the rest itself, so check that first character-for-character. If two cogs
register prefixes where one is a prefix of the other, the longer one is matched
first.

Tapping a square posts a string like `ttt:mv:XO.......:h:4` into the chat as if
you had typed it — your Viber client is ignoring the `Silent` button flag. The
game still works, it is only cosmetic. `Silent` is well supported on current
iOS and Android clients, so update the app first. If you need it gone for good,
the state has to move server-side (see the note about Upstash above) so the
payload can shrink to just a game id and a cell number.

## License

MIT.


