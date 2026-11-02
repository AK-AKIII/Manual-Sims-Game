"""Game settings. Override any of them with a JSON file (see configs/)."""

import copy
import json

DEFAULT_CONFIG = {
    # "standard" = 52 distinct cards; "random" = deck_size cards drawn with
    # replacement from the 52 faces, so the deck changes from game to game.
    "deck": "standard",
    "deck_size": 52,
    "hand_size": 7,          # cards you see at once
    "max_hand_size": 10,     # cap for the hand_size upgrade
    "plays": 8,              # X: hands you may play
    "discards": 3,           # Y: discards you may use
    "max_play_cards": 5,
    "max_discard_cards": 5,
    "points": {
        "High Card": 5,
        "Pair": 10,
        "Two Pair": 20,
        "Three of a Kind": 30,
        "Straight": 40,
        "Flush": 45,
        "Full House": 60,
        "Four of a Kind": 90,
        "Straight Flush": 120,
        "Five of a Kind": 150,
        "Flush House": 160,
        "Flush Five": 200,
    },
    # Each game, every hand's points are scaled by a random factor in
    # [1 - v, 1 + v] (the same for everyone playing that seed). 0 = fixed.
    "points_variation": 0.0,
    # Each level above 1 adds this fraction of the hand's base points.
    "level_bonus": 0.5,
    "economy": True,
    "starting_money": 0,
    # Money earned for each hand type played.
    "money": {
        "High Card": 1,
        "Pair": 1,
        "Two Pair": 2,
        "Three of a Kind": 3,
        "Straight": 4,
        "Flush": 4,
        "Full House": 6,
        "Four of a Kind": 8,
        "Straight Flush": 10,
        "Five of a Kind": 12,
        "Flush House": 14,
        "Flush Five": 16,
    },
    # Base prices. Each purchase of the same thing raises its price:
    # level:<hand> costs level * current level; the others cost base * (times bought + 1).
    "shop": {"level": 4, "hand_size": 12, "discard": 6, "play": 15},
}


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None, **overrides):
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        with open(path) as f:
            _merge(config, json.load(f))
    _merge(config, {k: v for k, v in overrides.items() if v is not None})
    return config
