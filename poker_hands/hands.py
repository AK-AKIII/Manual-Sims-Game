"""Hand classification and a brute-force best-play helper for bots."""

from collections import Counter
from itertools import combinations

from .cards import rank_of, suit_of

# Lowest to highest under the default points table. The last three only
# happen with duplicate cards, i.e. with a random deck.
HAND_TYPES = [
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
    "Five of a Kind",
    "Flush House",
    "Flush Five",
]


def hand_types(cards):
    """Every hand type that a set of 1-5 played cards makes."""
    n = len(cards)
    counts = sorted(Counter(rank_of(c) for c in cards).values(), reverse=True)
    ranks = {rank_of(c) for c in cards}
    flush = n == 5 and len({suit_of(c) for c in cards}) == 1
    straight = n == 5 and len(ranks) == 5 and (
        max(ranks) - min(ranks) == 4 or ranks == {14, 2, 3, 4, 5}
    )
    full_house = counts[:2] == [3, 2]

    found = {"High Card"}
    if counts[0] >= 2:
        found.add("Pair")
    if len(counts) >= 2 and counts[0] >= 2 and counts[1] >= 2:
        found.add("Two Pair")
    if counts[0] >= 3:
        found.add("Three of a Kind")
    if counts[0] >= 4:
        found.add("Four of a Kind")
    if counts[0] == 5:
        found.add("Five of a Kind")
    if full_house:
        found.add("Full House")
    if straight:
        found.add("Straight")
    if flush:
        found.add("Flush")
    if straight and flush:
        found.add("Straight Flush")
    if full_house and flush:
        found.add("Flush House")
    if counts[0] == 5 and flush:
        found.add("Flush Five")
    return found


def classify(cards, points):
    """The hand type the cards score as: the applicable type worth the most points."""
    return max(hand_types(cards), key=lambda t: (points[t], HAND_TYPES.index(t)))


def best_play(hand, points, max_cards=5, prefer="fewest"):
    """Highest-scoring set of cards to play from `hand`.

    Returns (indices, hand_type, points). On ties, prefer="fewest" keeps more
    cards in hand; prefer="most" dumps extra cards along with the scoring ones.
    """
    best = None
    for k in range(1, min(max_cards, len(hand)) + 1):
        for idx in combinations(range(len(hand)), k):
            t = classify([hand[i] for i in idx], points)
            size = -k if prefer == "fewest" else k
            key = (points[t], size)
            if best is None or key > best[0]:
                best = (key, list(idx), t)
    if best is None:
        return [], None, 0
    return best[1], best[2], points[best[2]]
