"""Score an output file against an input file.

Output format: a line with S, the number of moves, then S lines, one move each.
The output may stop before the run is over; the score is the points so far.

Exit codes follow Harvest Craft's checker: 0 valid, 1 illegal move,
2 unparsable output, 3 bad input.
"""

from .instance import BadInput, parse_instance
from .sim import Game, IllegalMove

VALID, ILLEGAL, UNPARSABLE, BAD_INPUT = 0, 1, 2, 3


def parse_output(text):
    lines = [l.strip() for l in text.strip().splitlines()]
    if not lines or not lines[0]:
        raise ValueError("empty output")
    try:
        s = int(lines[0])
    except ValueError:
        raise ValueError("first line must be the number of moves") from None
    moves = [l for l in lines[1:] if l]
    if s < 0 or len(moves) != s:
        raise ValueError(f"first line says {s} moves, found {len(moves)}")
    return moves


def check(input_text, output_text):
    """Return (exit code, verdict dict)."""
    try:
        inst = parse_instance(input_text)
    except BadInput as e:
        return BAD_INPUT, {"valid": False, "score": 0, "error": f"bad input: {e}"}
    try:
        moves = parse_output(output_text)
    except ValueError as e:
        return UNPARSABLE, {"valid": False, "score": 0, "error": f"unparsable output: {e}"}
    game = Game(inst)
    for k, move in enumerate(moves, 1):
        try:
            game.apply(move)
        except IllegalMove as e:
            return ILLEGAL, {"valid": False, "score": 0, "error": f"move {k} ({move!r}): {e}",
                             "move": k}
    return VALID, verdict(game, len(moves))


def verdict(game, moves):
    return {
        "valid": True,
        "score": game.total,
        "cleared": game.cleared,
        "rounds": game.inst.rounds,
        "finished": game.over,
        "moves": moves,
        "money": game.money,
    }
