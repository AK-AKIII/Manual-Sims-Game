"""Baseline: play 1-5 random cards every turn. Never discards or shops."""

import random


def play(state):
    n = random.randint(1, min(state["max_play_cards"], len(state["hand"])))
    return {"action": "play", "cards": random.sample(range(len(state["hand"])), n)}
