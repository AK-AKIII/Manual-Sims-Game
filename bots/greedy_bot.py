"""Example bot with three strategies. Its score is the mean of the three.

Copy this file to start your own bot. A strategy gets the game state (a dict,
see the README) and returns one action:
    {"action": "play",    "cards": [hand indices]}
    {"action": "discard", "cards": [hand indices]}
    {"action": "buy",     "item": "hand_size" | "discard" | "play" | "level:<hand type>"}
Buying does not end your turn; you are asked again right after.
"""

from poker_hands import best_play, rank_of


def _junk(state, keep, limit):
    """Indices of the lowest cards not in `keep`."""
    rest = [i for i in range(len(state["hand"])) if i not in keep]
    rest.sort(key=lambda i: rank_of(state["hand"][i]))
    return rest[:limit]


def greedy(state):
    """Always play the best hand available right now."""
    cards, _, _ = best_play(state["hand"], state["points"], state["max_play_cards"])
    return {"action": "play", "cards": cards}


def fish(state):
    """Discard low cards while the best hand is weaker than two pair."""
    cards, _, pts = best_play(state["hand"], state["points"], state["max_play_cards"])
    if pts < state["points"]["Two Pair"] and state["discards_left"] > 0 and state["deck_remaining"] > 0:
        junk = _junk(state, cards, state["max_discard_cards"])
        if junk:
            return {"action": "discard", "cards": junk}
    return {"action": "play", "cards": cards}


def shopper(state):
    """Like fish, but spends money: bigger hand first, then levels for the hand it is about to play."""
    shop, money = state["shop"], state["money"]
    if "hand_size" in shop and money >= shop["hand_size"]:
        return {"action": "buy", "item": "hand_size"}
    _, hand_type, _ = best_play(state["hand"], state["points"], state["max_play_cards"])
    item = f"level:{hand_type}"
    if hand_type and item in shop and money >= shop[item] and state["plays_left"] > 1:
        return {"action": "buy", "item": item}
    return fish(state)


STRATEGIES = [greedy, fish, shopper]
