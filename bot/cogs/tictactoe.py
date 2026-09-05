"""Tic-tac-toe, playable against the bot at three difficulties.

Two halves, separated below: a pure game engine (no Viber types anywhere, so
it is trivially unit-testable) and the cog that wires it to Viber keyboards.

State: Vercel functions are stateless, so there is nowhere to keep a board
between requests. Instead the whole board is encoded into each button's
ActionBody and comes back with the tap. Buttons are marked Silent, so the
encoded string never appears in the conversation. No database needed.

Trade-off: the client hands us the board, so a determined user could edit a
payload and hand themselves a better position. The stake is bragging rights in
a tic-tac-toe game, so that is fine — but every incoming board is still
validated for legality (`is_legal_board`) so malformed input cannot crash the
handler or produce nonsense output.
"""

from __future__ import annotations

import logging
import random
from functools import lru_cache

from ..core import Cog, Context, callback, command
from ..viber import button, keyboard

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Game engine
# ---------------------------------------------------------------------------

EMPTY = "."
HUMAN = "X"  # always moves first
BOT = "O"
EMPTY_BOARD = EMPTY * 9

WIN_LINES = (
    (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),  # columns
    (0, 4, 8), (2, 4, 6),             # diagonals
)

EASY, MEDIUM, HARD = "e", "m", "h"
DIFFICULTY_NAMES = {EASY: "Easy", MEDIUM: "Medium", HARD: "Hard"}
DIFFICULTY_ALIASES = {
    "e": EASY, "easy": EASY, "baby": EASY,
    "m": MEDIUM, "med": MEDIUM, "medium": MEDIUM, "normal": MEDIUM,
    "h": HARD, "hard": HARD, "impossible": HARD, "unbeatable": HARD,
}
# Chance the medium bot plays the perfect move rather than a random one.
MEDIUM_ACCURACY = 0.7


def resolve_difficulty(text: str) -> str | None:
    return DIFFICULTY_ALIASES.get(text.strip().lower())


def other(mark: str) -> str:
    return BOT if mark == HUMAN else HUMAN


def place(board: str, index: int, mark: str) -> str:
    return board[:index] + mark + board[index + 1 :]


def free_cells(board: str) -> list[int]:
    return [i for i, cell in enumerate(board) if cell == EMPTY]


def winner(board: str) -> tuple[str | None, tuple[int, int, int] | None]:
    """Return (winning mark, winning line) or (None, None)."""
    for line in WIN_LINES:
        a, b, c = line
        if board[a] != EMPTY and board[a] == board[b] == board[c]:
            return board[a], line
    return None, None


def is_over(board: str) -> bool:
    return winner(board)[0] is not None or EMPTY not in board


def winning_marks(board: str) -> set[str]:
    """Every mark that completes at least one line. Real play yields 0 or 1."""
    return {
        board[a]
        for a, b, c in WIN_LINES
        if board[a] != EMPTY and board[a] == board[b] == board[c]
    }


def is_legal_board(board: str) -> bool:
    """Reject anything that could not have arisen from real play."""
    if not isinstance(board, str) or len(board) != 9:
        return False
    if any(cell not in (EMPTY, HUMAN, BOT) for cell in board):
        return False
    x_count, o_count = board.count(HUMAN), board.count(BOT)
    if x_count not in (o_count, o_count + 1):
        return False
    marks = winning_marks(board)
    # Play stops the instant someone wins, so two winners is impossible.
    # "OOOXXX..." satisfies every count rule and still cannot happen.
    if len(marks) > 1:
        return False
    won = next(iter(marks), None)
    # X moves first, so X can only have won on an odd-numbered ply.
    if won == HUMAN and x_count != o_count + 1:
        return False
    if won == BOT and x_count != o_count:
        return False
    return True


@lru_cache(maxsize=None)
def position_value(board: str, turn: str) -> int:
    """Minimax value of `board` with `turn` to move, from the bot's side.

    Positive favours the bot. Scores shrink with the number of moves played, so
    a win in three is worth more than a win in five and the bot prefers to
    finish quickly (and to lose as slowly as possible).
    """
    won, _ = winner(board)
    played = 9 - board.count(EMPTY)
    if won == BOT:
        return 10 - played
    if won == HUMAN:
        return played - 10
    if played == 9:
        return 0

    values = [
        position_value(place(board, i, turn), other(turn)) for i in free_cells(board)
    ]
    return max(values) if turn == BOT else min(values)


def ranked_moves(board: str, turn: str = BOT) -> list[tuple[int, int]]:
    """(value, cell) for every legal move, best first for `turn`."""
    scored = [
        (position_value(place(board, i, turn), other(turn)), i) for i in free_cells(board)
    ]
    scored.sort(key=lambda pair: pair[0], reverse=(turn == BOT))
    return scored


def optimal_move(board: str, turn: str = BOT) -> int:
    """A perfect move, chosen at random among equally perfect ones.

    The tie-break randomness is what stops 'hard' from replaying the identical
    game every time. It never costs anything: all the tied moves share the same
    minimax value.
    """
    scored = ranked_moves(board, turn)
    best = scored[0][0]
    return random.choice([cell for value, cell in scored if value == best])


def winning_cell(board: str, mark: str) -> int | None:
    """A cell that wins immediately for `mark`, if there is one."""
    for i in free_cells(board):
        if winner(place(board, i, mark))[0] == mark:
            return i
    return None


def choose_move(board: str, difficulty: str) -> int:
    """Pick the bot's move at the given difficulty."""
    cells = free_cells(board)
    if not cells:
        raise ValueError("no legal moves on a full board")

    if difficulty == HARD:
        return optimal_move(board)

    if difficulty == MEDIUM:
        if random.random() < MEDIUM_ACCURACY:
            return optimal_move(board)
        finisher = winning_cell(board, BOT)
        return finisher if finisher is not None else random.choice(cells)

    # Easy: random, but it will still take a win sitting in front of it.
    finisher = winning_cell(board, BOT)
    return finisher if finisher is not None else random.choice(cells)


# ---------------------------------------------------------------------------
# Presentation: button payloads and keyboards
# ---------------------------------------------------------------------------

CB = "ttt:"
CB_MOVE = "ttt:mv:"     # ttt:mv:<board>:<difficulty>:<cell>
CB_NEW = "ttt:new:"     # ttt:new:<difficulty>
CB_MENU = "ttt:menu"
CB_INERT = "ttt:-"

GLYPH = {HUMAN: "❌", BOT: "⭕", EMPTY: "▫️"}

BRAND = "#7360F2"
WHITE = "#FFFFFF"
MUTED = "#8E8E93"
CELL_FREE_BG = "#F2F2F7"
CELL_X_BG = "#FFEAE7"
CELL_O_BG = "#E8EEFF"
CELL_WIN_BG = "#D6F5DF"
SECONDARY_BG = "#EDEAFB"


def render_board(board: str) -> str:
    """Plain-text board, so a finished game stays readable in scrollback."""
    return "\n".join(" ".join(GLYPH[c] for c in board[r : r + 3]) for r in range(0, 9, 3))


def board_keyboard(
    board: str, difficulty: str, *, win_line: tuple[int, int, int] | None = None
) -> dict:
    """A 3x3 grid of buttons plus a Rematch / Difficulty row.

    Viber fills a 6-column grid left to right, so three Columns=2 buttons make
    one board row and two Columns=3 buttons make the action row.
    """
    over = is_over(board)
    buttons = []

    for i, mark in enumerate(board):
        if mark == EMPTY and not over:
            buttons.append(
                button(
                    str(i + 1),
                    f"{CB_MOVE}{board}:{difficulty}:{i}",
                    columns=2,
                    bg_color=CELL_FREE_BG,
                    text_color=MUTED,
                    text_size="large",
                )
            )
            continue
        bg = CELL_FREE_BG
        if win_line and i in win_line:
            bg = CELL_WIN_BG
        elif mark == HUMAN:
            bg = CELL_X_BG
        elif mark == BOT:
            bg = CELL_O_BG
        buttons.append(
            button(GLYPH[mark], CB_INERT, columns=2, bg_color=bg, text_size="large", action_type="none")
        )

    buttons.append(
        button(
            f"↻ {'Rematch' if over else 'Restart'}",
            f"{CB_NEW}{difficulty}",
            columns=3,
            bg_color=BRAND,
            text_color=WHITE,
        )
    )
    buttons.append(
        button(
            f"⚙ {DIFFICULTY_NAMES[difficulty]}",
            CB_MENU,
            columns=3,
            bg_color=SECONDARY_BG,
            text_color=BRAND,
        )
    )
    return keyboard(buttons)


def difficulty_keyboard() -> dict:
    labels = ((EASY, "🙂 Easy"), (MEDIUM, "😐 Medium"), (HARD, "😈 Hard"))
    return keyboard(
        [
            button(label, f"{CB_NEW}{code}", columns=2, bg_color=BRAND, text_color=WHITE)
            for code, label in labels
        ]
    )


def parse_move(payload: str) -> tuple[str, str, int] | None:
    """Decode a `ttt:mv:` payload, or None if it is malformed or illegal."""
    parts = payload[len(CB_MOVE) :].split(":")
    if len(parts) != 3:
        return None
    board, difficulty, raw_cell = parts
    # A cell is exactly one character. isdecimal (not isdigit — "²".isdigit() is
    # True while int("²") raises) plus the length check, because int() also
    # raises on a decimal string longer than sys.int_info.str_digits_check_
    # threshold, which a 4301-digit payload reaches.
    if difficulty not in DIFFICULTY_NAMES or len(raw_cell) != 1 or not raw_cell.isdecimal():
        return None
    cell = int(raw_cell)
    if not 0 <= cell <= 8 or not is_legal_board(board):
        return None
    return board, difficulty, cell


# ---------------------------------------------------------------------------
# The cog
# ---------------------------------------------------------------------------


class TicTacToe(Cog):
    name = "Tic-Tac-Toe"
    description = "Play tic-tac-toe against me. Hard mode cannot be beaten."

    @command(
        "ttt",
        aliases=("tictactoe", "xo"),
        usage="ttt [easy | medium | hard]",
        help="Play tic-tac-toe against me",
    )
    def start(self, ctx: Context) -> None:
        if not ctx.args:
            ctx.reply("Tic-tac-toe. How hard should I play?", difficulty_keyboard())
            return
        difficulty = resolve_difficulty(ctx.args[0])
        if difficulty is None:
            ctx.reply(
                f'"{ctx.args[0]}" is not a difficulty I know. Pick one:', difficulty_keyboard()
            )
            return
        self._new_game(ctx, difficulty)

    @callback(CB)
    def on_button(self, ctx: Context) -> None:
        payload = ctx.text
        if payload.startswith(CB_MOVE):
            self._play(ctx, payload)
        elif payload.startswith(CB_NEW):
            difficulty = payload[len(CB_NEW) :].strip()
            self._new_game(ctx, difficulty if difficulty in DIFFICULTY_NAMES else HARD)
        elif payload.startswith(CB_MENU):
            ctx.reply("How hard should I play?", difficulty_keyboard())
        elif payload.startswith(CB_INERT):
            pass  # An already-filled cell. ActionType "none" usually eats these.
        else:
            # Reachable by typing "ttt:whatever" by hand, or by tapping a
            # keyboard left on screen by an older deploy. Staying silent here
            # is indistinguishable from the bot being down.
            log.debug("unrecognised tictactoe payload %r", payload)
            ctx.reply("I don't recognise that button. Here's a fresh game:", difficulty_keyboard())

    def _new_game(self, ctx: Context, difficulty: str) -> None:
        ctx.reply(
            f"You're ❌, I'm ⭕. Difficulty: {DIFFICULTY_NAMES[difficulty]}.\nYou go first — tap a square.",
            board_keyboard(EMPTY_BOARD, difficulty),
        )

    def _play(self, ctx: Context, payload: str) -> None:
        parsed = parse_move(payload)
        if parsed is None:
            ctx.reply("I can't read that board. Let's start a fresh game:", difficulty_keyboard())
            return

        board, difficulty, cell = parsed
        if is_over(board):
            self._finish(ctx, board, difficulty, "That game's already over.")
            return
        if board[cell] != EMPTY:
            ctx.reply("That square is taken — pick an empty one.", board_keyboard(board, difficulty))
            return

        board = place(board, cell, HUMAN)
        if winner(board)[0] == HUMAN:
            self._finish(ctx, board, difficulty, f"You win! 🎉 On {DIFFICULTY_NAMES[difficulty].lower()}, too.")
            return
        if EMPTY not in board:
            self._finish(ctx, board, difficulty, "Draw. 🤝")
            return

        reply_cell = choose_move(board, difficulty)
        board = place(board, reply_cell, BOT)
        if winner(board)[0] == BOT:
            self._finish(ctx, board, difficulty, f"Square {reply_cell + 1} — and that's the game. 😎")
            return
        if EMPTY not in board:
            self._finish(ctx, board, difficulty, "Draw. 🤝")
            return

        ctx.reply(f"I take square {reply_cell + 1}. Your move.", board_keyboard(board, difficulty))

    def _finish(self, ctx: Context, board: str, difficulty: str, headline: str) -> None:
        _, line = winner(board)
        ctx.reply(
            f"{headline}\n\n{render_board(board)}",
            board_keyboard(board, difficulty, win_line=line),
        )


def setup(bot) -> None:
    bot.add_cog(TicTacToe(bot))


