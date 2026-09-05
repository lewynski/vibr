"""Game engine tests.

The important one is test_hard_never_loses: it walks the entire game tree,
trying every human move against every move the bot considers optimal, and
asserts the human never reaches a win. That is a proof, not a spot check.
"""

from __future__ import annotations

import random

import pytest

from bot.cogs import tictactoe as ttt

DRAW = "draw"


def outcomes(board: str, turn: str, memo: dict) -> set[str]:
    """All results reachable from `board`: human tries everything, bot plays
    perfectly (branching over tied-best moves)."""
    key = (board, turn)
    if key in memo:
        return memo[key]

    won, _ = ttt.winner(board)
    if won:
        memo[key] = {won}
        return memo[key]
    if ttt.EMPTY not in board:
        memo[key] = {DRAW}
        return memo[key]

    if turn == ttt.HUMAN:
        moves = ttt.free_cells(board)
    else:
        ranked = ttt.ranked_moves(board, ttt.BOT)
        best = ranked[0][0]
        moves = [cell for value, cell in ranked if value == best]

    found: set[str] = set()
    for cell in moves:
        found |= outcomes(ttt.place(board, cell, turn), ttt.other(turn), memo)
    memo[key] = found
    return found


def test_hard_never_loses():
    reachable = outcomes(ttt.EMPTY_BOARD, ttt.HUMAN, {})
    assert ttt.HUMAN not in reachable, "hard mode is beatable"
    assert reachable == {DRAW, ttt.BOT}


def test_perfect_play_is_a_draw():
    for _ in range(25):
        board, turn = ttt.EMPTY_BOARD, ttt.HUMAN
        while not ttt.is_over(board):
            board = ttt.place(board, ttt.optimal_move(board, turn), turn)
            turn = ttt.other(turn)
        assert ttt.winner(board)[0] is None


def test_hard_blocks_an_immediate_threat():
    # Human threatens the top row and it is the bot's turn; cell 2 is forced.
    board = "XX.O....."
    assert ttt.is_legal_board(board)
    assert ttt.winning_cell(board, ttt.HUMAN) == 2
    assert ttt.choose_move(board, ttt.HARD) == 2


def test_bot_takes_a_win_over_a_block_at_every_difficulty():
    # Bot completes the middle row at cell 5; the human is threatening cell 2.
    board = "XX.OO...X"
    assert ttt.is_legal_board(board)
    assert ttt.winning_cell(board, ttt.BOT) == 5
    assert ttt.winning_cell(board, ttt.HUMAN) == 2
    for difficulty in (ttt.EASY, ttt.MEDIUM, ttt.HARD):
        picks = {ttt.choose_move(board, difficulty) for _ in range(60)}
        assert picks == {5}, f"{difficulty} passed up a winning move"


@pytest.mark.parametrize("difficulty", [ttt.EASY, ttt.MEDIUM, ttt.HARD])
def test_every_difficulty_plays_legal_moves(difficulty):
    rng = random.Random(1234)
    # choose_move draws from the module-global random for its tie-breaking, so
    # seeding only `rng` would leave the bot's side of this test unreproducible.
    random.seed(1234)
    for _ in range(200):
        board, turn = ttt.EMPTY_BOARD, ttt.HUMAN
        while not ttt.is_over(board):
            if turn == ttt.HUMAN:
                cell = rng.choice(ttt.free_cells(board))
            else:
                cell = ttt.choose_move(board, difficulty)
            assert board[cell] == ttt.EMPTY
            board = ttt.place(board, cell, turn)
            assert ttt.is_legal_board(board)
            turn = ttt.other(turn)


def test_winner_finds_every_line():
    for line in ttt.WIN_LINES:
        board = ttt.EMPTY_BOARD
        for cell in line:
            board = ttt.place(board, cell, ttt.BOT)
        assert ttt.winner(board) == (ttt.BOT, line)
    assert ttt.winner(ttt.EMPTY_BOARD) == (None, None)
    assert ttt.winner("XOXXOOOXX")[0] is None


@pytest.mark.parametrize(
    "board",
    [
        "",                # empty
        "XXXXXXXX",        # too short
        "XXXXXXXXXX",      # too long
        "XOXOXOXO!",       # bad character
        "XXX......",        # three X, no O — impossible
        "OOO......",        # O moved first
        "XXXOOO...",        # both sides have a line
        "OOOXXX...",        # same, but O's line is found first
        "XXXOOOX..",        # X wins twice over, O once
        "XXXOOOXO.",        # a line each, and the counts look plausible
        "OOOXX.X.X",        # O won, but on X's ply — parity rule for O
        "XXXOO..O.",        # X won, but on O's ply — parity rule for X
    ],
)
def test_is_legal_board_rejects_junk(board):
    assert not ttt.is_legal_board(board)


def test_two_winners_is_rejected_even_when_the_counts_look_right():
    # Regression: the count and parity rules alone accept this, because the
    # first line found is O's and o_count == x_count. Play stops the moment
    # someone wins, so it cannot occur.
    board = "OOOXXX..."
    assert board.count(ttt.HUMAN) == board.count(ttt.BOT)
    assert ttt.winning_marks(board) == {ttt.HUMAN, ttt.BOT}
    assert not ttt.is_legal_board(board)


@pytest.mark.parametrize("board", [ttt.EMPTY_BOARD, "X........", "XO.......", "XXXOO....", "OXXXOOOXX"])
def test_is_legal_board_accepts_real_positions(board):
    assert ttt.is_legal_board(board)


def test_parse_move_round_trip():
    board, difficulty, cell = "XO.......", ttt.HARD, 4
    payload = f"{ttt.CB_MOVE}{board}:{difficulty}:{cell}"
    assert ttt.parse_move(payload) == (board, difficulty, cell)


@pytest.mark.parametrize(
    "payload",
    [
        "ttt:mv:XO.......:h",            # missing the cell
        "ttt:mv:XO.......:h:4:extra",    # too many fields
        "ttt:mv:XO.......:z:4",          # unknown difficulty
        "ttt:mv:XO.......:h:9",          # cell out of range
        "ttt:mv:XO.......:h:-1",         # not a digit
        "ttt:mv:XO.......:h:²",          # isdigit() says yes, int() disagrees
        "ttt:mv:XO.......:h: 4",         # padded, and int() would accept it
        "ttt:mv:XXXXXXXXX:h:0",          # illegal board
    ],
)
def test_parse_move_rejects_bad_payloads(payload):
    assert ttt.parse_move(payload) is None


def test_parse_move_rejects_a_cell_too_long_for_int():
    # Regression: int() refuses a decimal string past CPython's 4300-digit
    # limit, so isdecimal() alone still let a hand-typed payload raise.
    assert ttt.parse_move(f"{ttt.CB_MOVE}XO.......:h:" + "1" * 4301) is None
    assert ttt.parse_move(f"{ttt.CB_MOVE}XO.......:h:04") is None


def test_board_keyboard_layout():
    kb = ttt.board_keyboard(ttt.EMPTY_BOARD, ttt.HARD)
    buttons = kb["Buttons"]
    assert len(buttons) == 11  # 9 cells + rematch + difficulty
    assert all(b["Silent"] for b in buttons)
    assert all(b["ActionType"] == "reply" for b in buttons[:9])
    # Viber fills a 6-column grid left to right, so every row must total 6 or
    # the board stops looking like a 3x3 grid.
    widths = [b["Columns"] for b in buttons]
    row, rows = 0, []
    for width in widths:
        row += width
        assert row <= 6, f"a row overflowed the 6-column grid: {widths}"
        if row == 6:
            rows.append(row)
            row = 0
    assert row == 0, f"the last row is short: {widths}"
    assert len(rows) == 4  # three board rows + one action row


def test_finished_board_has_inert_cells_and_highlights_the_win():
    board = "XXXOO...."
    _, line = ttt.winner(board)
    buttons = ttt.board_keyboard(board, ttt.HARD, win_line=line)["Buttons"]
    assert all(b["ActionType"] == "none" for b in buttons[:9])
    assert all(buttons[i]["BgColor"] == ttt.CELL_WIN_BG for i in line)
    assert buttons[3]["BgColor"] == ttt.CELL_O_BG


def test_render_board_is_three_rows():
    assert ttt.render_board("XOXOXOXOX").splitlines() == ["❌ ⭕ ❌", "⭕ ❌ ⭕", "❌ ⭕ ❌"]
