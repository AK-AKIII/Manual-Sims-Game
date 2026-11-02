"""Play the game yourself in the terminal. It is just another strategy function."""

from .engine import check_action
from .hands import classify

HELP = """commands:
  p 0 2 3        play the cards at those positions
  d 1 4          discard them
  ? 0 2 3        preview what those cards would score
  b hand_size    buy from the shop (also: b discard, b play, b level:Flush)
  shop           list prices
  h              this help"""


def _show(state):
    print()
    print(f"score {state['score']}   money {state['money']}   plays {state['plays_left']}"
          f"   discards {state['discards_left']}   deck {state['deck_remaining']}")
    for o in state["opponents"]:
        print(f"  seat {o['seat']}: score {o['score']}, plays {o['plays_left']}")
    print("hand: " + "  ".join(f"{i}:{c}" for i, c in enumerate(state["hand"])))


def _indices(parts):
    try:
        return [int(x) for x in parts]
    except ValueError:
        return None


def human_strategy(state):
    _show(state)
    last = [e for e in state["history"] if e["action"] == "play"][-1:]
    if last:
        e = last[0]
        print(f"last play: seat {e['seat']} {' '.join(e['cards'])} = {e['hand_type']} (+{e['points']})")
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            raise SystemExit
        parts = line.split()
        if not parts or parts[0] in ("h", "help"):
            print(HELP)
            continue
        cmd, rest = parts[0], parts[1:]
        if cmd == "shop":
            for item, cost in sorted(state["shop"].items(), key=lambda x: x[1]):
                print(f"  {item:<26} {cost}")
            if not state["shop"]:
                print("  the shop is off in this game")
            continue
        if cmd == "?":
            idx = _indices(rest)
            if idx and all(0 <= i < len(state["hand"]) for i in idx) and len(idx) <= 5:
                t = classify([state["hand"][i] for i in idx], state["points"])
                print(f"  {t}: {state['points'][t]} points")
            else:
                print("  give 1 to 5 card positions from your hand")
            continue
        if cmd in ("p", "d"):
            action = {"action": "play" if cmd == "p" else "discard", "cards": _indices(rest)}
        elif cmd == "b" and rest:
            action = {"action": "buy", "item": " ".join(rest)}
        else:
            print(HELP)
            continue
        err = check_action(state, action)
        if err:
            print(f"  can't do that: {err}")
            continue
        return action
