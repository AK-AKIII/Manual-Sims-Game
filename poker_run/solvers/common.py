"""Shared helpers for the example Poker Run solvers.

Every solver in this folder is built from the same pieces, collected here:

1. A fast scorer (`Scorer`). It computes exactly what `Game.evaluate` computes,
   but precomputes the per-card bonuses of the current engine so a search can
   score thousands of candidate plays cheaply. `self_test()` checks it against
   the simulator.

2. A light round state (`RoundState`) and a round context (`RoundCtx`). Inside a
   round nothing random happens: the pile order is known, so playing or
   discarding k cards always draws the next k cards of the pile. A round can
   therefore be searched exactly without cloning the whole `Game`. The context
   knows the round's target and turns a finished round into one number
   (`terminal_value`): clearing the round dominates everything, then money
   earned (worth `dollar` points per $), then points.

3. Move generators. `play_candidates` builds the plays worth considering
   (pairs, trips, two pair, full house, flushes, straights, high card, each
   optionally padded with junk "kickers" to cycle the hand), and
   `discard_candidates` builds discards aimed at the hands the known next cards
   make possible. `brute_force_plays` enumerates every subset, for comparison.

4. A fast greedy round policy (`greedy_round`) used for rollouts and for
   judging shop purchases, and a beam search (`beam_round`) used by several
   solvers to play the real rounds.

5. A shop policy (`Shop`). Shopping is where most of the score is: targets
   double every round, so the "engine" (hand levels, modifiers, deck edits) has
   to keep up. The shop proposes purchases (modifiers, levels, deck edits,
   handsize/discards), judges each by *simulating the next rounds with the
   known draw orders* using `greedy_round`, and greedily buys the best one
   until nothing is worth its price. Money kept also has a value (interest).

6. `solve()`: the driver that alternates round solver and shop, enforces the
   time budget, and checks every move on a real `Game`. If a solver ever
   returned an illegal move it is dropped and a safe move is played instead,
   so the output is always valid.

Run `python3 poker_run/solvers/common.py` to self-test the fast scorer and the
fast classifier against the simulator.

Every number of the rules (hand values, modifier amounts, prices) is read
from the game's ruleset (`game.rules`), so the solvers also play the other
rulesets: the environment variable POKER_RULES picks one (default classic).
"""

import math
import os
import sys
import time
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from poker_run.instance import parse_instance  # noqa: E402
from poker_run.rules import (  # noqa: E402
    HAND_CODES, MAX_HAND_SIZE, MIN_DECK, MODIFIERS, N_CARDS, SUITS, chips, get_ruleset,
)
from poker_run.sim import Game, IllegalMove, classify  # noqa: E402

BIG = 10 ** 12          # bonus for clearing a round: dominates points and money
DEFAULT_TIME = 1.7      # seconds of wall time a solver may use


def time_limit(default=DEFAULT_TIME):
    """TIME_LIMIT can be overridden with the environment variable POKER_TIME."""
    return float(os.environ.get("POKER_TIME", default))


def rules_from_env():
    """The ruleset to play: the environment variable POKER_RULES (default classic)."""
    return get_ruleset(os.environ.get("POKER_RULES") or None)


# ---------------------------------------------------------------------------
# 1. Fast scorer
# ---------------------------------------------------------------------------

def fast_classify(ranks, suits):
    """Same result as sim.classify (hand code, scored positions), about twice
    as fast because it branches on the number of cards and distinct ranks
    instead of sorting. self_test() checks that the two agree."""
    n = len(ranks)
    if n == 1:
        return "HC", [0]
    cnt = {}
    for r in ranks:
        cnt[r] = cnt.get(r, 0) + 1
    nd = len(cnt)
    if n == 5:
        s0 = suits[0]
        flush = suits[1] == s0 and suits[2] == s0 and suits[3] == s0 and suits[4] == s0
        if nd == 5:
            mx, mn = max(ranks), min(ranks)
            straight = mx - mn == 4 or (mx == 12 and mn == 0 and sum(ranks) == 18)
            if straight:
                return ("SF" if flush else "ST"), ALL5
            if flush:
                return "FL", ALL5
            return "HC", [ranks.index(mx)]
        if nd == 1:
            return ("F5" if flush else "FV"), ALL5
        top = max(cnt.values())
        if nd == 2:
            if top == 4:
                return "FK", [i for i in range(5) if cnt[ranks[i]] >= 2]
            return ("HF" if flush else "FH"), ALL5
        if flush:
            return "FL", ALL5
        scored = [i for i in range(5) if cnt[ranks[i]] >= 2]
        if top == 3:
            return "TK", scored
        return ("TP" if nd == 3 else "PR"), scored
    if nd == n:
        return "HC", [ranks.index(max(ranks))]
    scored = [i for i in range(n) if cnt[ranks[i]] >= 2]
    top = max(cnt.values())
    if top == 4:
        return "FK", scored
    if top == 3:
        return "TK", scored
    if len(scored) == 4:
        return "TP", scored
    return "PR", scored


ALL5 = [0, 1, 2, 3, 4]


class Scorer:
    """Scores plays for a fixed engine (card faces, levels, modifiers, deck size).

    score(cards, plays_left, mom_last, mom_streak, coll) -> (points, code, money)
    mirrors Game.evaluate exactly. The per-card parts of every modifier are
    folded into two tables: card_base[c] (chips + mirror + low_road + even_split)
    and card_mult[c] (rose_tint, coal_seam, high_court, odd_job).
    """

    def __init__(self, game):
        self.rank = game.rank[:]
        self.suit = game.suit[:]
        R = game.rules
        E = self.effects = R.effects
        self.level = {c: R.hand_value(c, game.levels[c]) for c in R.hand_codes}
        self.mods = [m for m, _ in game.mods]
        has = set(self.mods)
        self.has = has
        cb, cm = [0] * N_CARDS, [0] * N_CARDS
        for c in range(N_CARDS):
            r, s = self.rank[c], self.suit[c]
            b = chips(r) * (1 + E["mirror"] if "mirror" in has else 1)
            if "low_road" in has and r <= 4:
                b += E["low_road"]
            if "even_split" in has and r <= 8 and r % 2 == 0:
                b += E["even_split"]
            m = 0
            if "rose_tint" in has and s in (1, 2):
                m += E["rose_tint"]
            if "coal_seam" in has and s in (0, 3):
                m += E["coal_seam"]
            if "high_court" in has and 9 <= r <= 11:
                m += E["high_court"]
            if "odd_job" in has and r in (1, 3, 5, 7, 12):   # 3 5 7 9 A
                m += E["odd_job"]
            cb[c], cm[c] = b, m
        self.card_base, self.card_mult = cb, cm
        self.flat_mult = E["thin_deck"] * max(0, N_CARDS - game.deck_size) if "thin_deck" in has else 0
        self.twin = "twin_engine" in has
        self.short = "short_hand" in has
        self.spread = "full_spread" in has
        self.last_call = "last_call" in has
        self.momentum = "momentum" in has
        self.collector = "collector" in has
        self.tricolor = "tricolor" in has
        self.tri_max = 3 if R.tricolor_exact else 4   # most suits tricolor accepts
        self.ace_div = "ace_dividend" in has

    def score(self, cards, plays_left, mom_last="", mom_streak=0, coll=0):
        rank, suit = self.rank, self.suit
        ranks = [rank[c] for c in cards]
        code, idx = fast_classify(ranks, [suit[c] for c in cards])
        hb, hm = self.level[code]
        cb, cm = self.card_base, self.card_mult
        base, mult = hb, hm + self.flat_mult
        for i in idx:
            c = cards[i]
            base += cb[c]
            mult += cm[c]
        times = 1
        n = len(cards)
        E = self.effects
        if self.twin and len(idx) >= 2:
            sr = [ranks[i] for i in idx]
            mult += E["twin_engine"] * sum(1 for r in set(sr) if sr.count(r) >= 2)
        if self.short and n <= 3:
            times *= E["short_hand"]
        if self.spread and n == 5:
            base += E["full_spread"]
        if self.last_call and plays_left == 1:
            times *= E["last_call"]
        if self.momentum and mom_last == code:
            mult += E["momentum"] * mom_streak
        if self.collector:
            base += coll + (E["collector"] if code in ("ST", "SF") else 0)
        if self.tricolor and 3 <= len({suit[cards[i]] for i in idx}) <= self.tri_max:
            times *= E["tricolor"]
        money = E["ace_dividend"] * sum(1 for i in idx if ranks[i] == 12) if self.ace_div else 0
        return base * mult * times, code, money


def mod_state(game, name):
    for m, s in game.mods:
        if m == name:
            return s
    return None


# ---------------------------------------------------------------------------
# 2. Round state and context
# ---------------------------------------------------------------------------

class RoundState:
    """Everything that changes inside a round. `moves` is a linked list
    (parent_moves, move) so that copying a state is O(1)."""
    __slots__ = ("hand", "pos", "plays", "discards", "score", "mom_last", "mom_streak", "coll",
                 "money", "moves", "done")

    def copy(self):
        s = RoundState.__new__(RoundState)
        s.hand, s.pos, s.plays, s.discards = self.hand, self.pos, self.plays, self.discards
        s.score, s.mom_last, s.mom_streak, s.coll = self.score, self.mom_last, self.mom_streak, self.coll
        s.money, s.moves, s.done = self.money, self.moves, self.done
        return s

    def move_list(self):
        out, node = [], self.moves
        while node:
            node, m = node
            out.append(m)
        out.reverse()
        return out

    def key(self):
        return (frozenset(self.hand), self.pos, self.plays, self.discards, self.mom_streak, self.mom_last)


class RoundCtx:
    """The fixed facts of the round being played, from the Game at any point of it.

    dollar:    how many points one $ earned this round is worth (0 = ignore money).
    allow_end: whether `end` may be used to stop early once the target is met.
    """

    def __init__(self, game, dollar=0.0, allow_end=True):
        self.game = game
        self.sc = Scorer(game)
        self.pile = game.pile[:]
        self.hand_size = game.hand_size
        self.target = game.target
        self.final = game.round == game.inst.rounds - 1
        self.dollar = 0.0 if self.final else dollar
        self.allow_end = allow_end and not self.final
        self.rules = R = game.rules
        self.spare = R.effects["spare_hands"] if game.has_mod("spare_hands") else 0
        saving = game.has_mod("savings")
        self.cap = R.interest_cap + (R.effects["savings"] if saving else 0)
        self.per = R.savings_interest_per if saving else R.interest_per
        self.money0 = game.money
        self.cache = {}
        self.bp_cache = {}

    def start(self):
        g = self.game
        s = RoundState()
        s.hand = tuple(g.hand)
        s.pos, s.plays, s.discards, s.score = 0, g.plays_left, g.discards_left, g.round_score
        mom, col = mod_state(g, "momentum"), mod_state(g, "collector")
        s.mom_last, s.mom_streak = (mom["last"], mom["streak"]) if mom else ("", 0)
        s.coll = col["bonus"] if col else 0
        s.money, s.moves, s.done = 0, None, False
        return s

    # -- transitions ---------------------------------------------------------

    def _refill(self, s, kept):
        need = self.hand_size - len(kept)
        new = self.pile[s.pos:s.pos + need] if need > 0 else []
        s.pos += len(new)
        s.hand = tuple(kept) + tuple(new)

    def base_plays(self, hand):
        """base_plays(hand), memoised per round (same engine, same faces)."""
        r = self.bp_cache.get(hand)
        if r is None:
            r = self.bp_cache[hand] = base_plays(hand, self.sc)
        return r

    def score_play(self, s, cards):
        # Memoised: searches score the same plays over and over.
        key = (cards, s.plays, s.mom_streak, s.mom_last, s.coll)
        r = self.cache.get(key)
        if r is None:
            r = self.cache[key] = self.sc.score(cards, s.plays, s.mom_last, s.mom_streak, s.coll)
        return r

    def play(self, s, cards, scored=None):
        """New state after playing `cards` (a tuple of ids in s.hand)."""
        pts, code, money = scored or self.score_play(s, cards)
        t = s.copy()
        t.score += pts
        t.money += money
        if self.sc.momentum:
            if t.mom_last == code:
                t.mom_streak += 1
            else:
                t.mom_last, t.mom_streak = code, 1
        if self.sc.collector and code in ("ST", "SF"):
            t.coll += self.sc.effects["collector"]
        t.plays -= 1
        cs = set(cards)
        self._refill(t, [c for c in s.hand if c not in cs])
        t.moves = (s.moves, "play " + " ".join(map(str, cards)))
        if t.plays == 0 or not t.hand:
            t.done = True
        return t

    def discard(self, s, cards):
        t = s.copy()
        t.discards -= 1
        cs = set(cards)
        self._refill(t, [c for c in s.hand if c not in cs])
        t.moves = (s.moves, "discard " + " ".join(map(str, cards)))
        if not t.hand:
            t.done = True
        return t

    def can_end(self, s):
        return self.allow_end and not s.done and s.score >= self.target

    def end(self, s):
        t = s.copy()
        t.moves = (s.moves, "end")
        t.done = True
        return t

    def apply_move(self, s, move):
        """State after a move given as text ("play 1 2", "discard 3", "end")."""
        tok = move.split()
        cards = tuple(int(x) for x in tok[1:])
        if tok[0] == "play":
            return self.play(s, cards)
        if tok[0] == "discard":
            return self.discard(s, cards)
        return self.end(s)

    # -- values --------------------------------------------------------------

    def reward(self, s):
        """Money earned if the round ends cleared in state s."""
        R = self.rules
        money = self.money0 + s.money
        r = R.round_reward + R.play_bonus * s.plays + min(money // self.per, self.cap)
        if self.spare:
            r += self.spare * s.discards
        return r + s.money

    def terminal_value(self, s):
        """Value of a finished round: clearing >> money >> points."""
        if s.score >= self.target and not self.final:
            return BIG + s.score + self.dollar * self.reward(s)
        return s.score


# ---------------------------------------------------------------------------
# 3. Move generators
# ---------------------------------------------------------------------------

def keep_values(hand, rank, suit):
    """How much we want to keep each card: cards that pair up or share the
    hand's main suits are worth keeping, then high chips."""
    rc, sc = {}, {}
    for c in hand:
        rc[rank[c]] = rc.get(rank[c], 0) + 1
        sc[suit[c]] = sc.get(suit[c], 0) + 1
    return {c: 10 * (rc[rank[c]] - 1) + 2 * sc[suit[c]] + chips(rank[c]) / 11.0 for c in hand}


def base_plays(hand, sc, inner=False):
    """Plays (tuples of card ids) without kickers: every interesting structure.
    `sc` is the Scorer: we use its per-card bonuses to pick, among cards of
    the same rank or suit, the ones that score most (e.g. hearts with rose_tint)."""
    rank, suit = sc.rank, sc.suit
    cb, cm = sc.card_base, sc.card_mult
    hand = sorted(hand, key=lambda c: (cm[c], cb[c]), reverse=True)  # best cards first
    by_rank, by_suit = {}, {}
    for c in hand:
        by_rank.setdefault(rank[c], []).append(c)
        by_suit.setdefault(suit[c], []).append(c)
    out = []
    # High card: the highest card, and the best-scoring cards alone (with
    # modifiers a low heart can outscore an ace).
    out.append((max(hand, key=lambda c: rank[c]),))
    out.extend((c,) for c in hand[:2])
    # Rank groups: pairs, trips, quads, five of a kind.
    groups = sorted((g for g in by_rank.values() if len(g) >= 2),
                    key=lambda g: (len(g), rank[g[0]]), reverse=True)
    for g in groups:
        out.append(tuple(g[:5]))
        if len(g) >= 4 and sc.tricolor:
            # three of a kind of 3 suits keeps short_hand; offer it too
            out.append(tuple(_varied(g, suit, 3)))
    # Two pair and full house from the best groups.
    top = groups[:4]
    for i in range(len(top)):
        for j in range(len(top)):
            if i == j:
                continue
            a, b = top[i], top[j]
            if i < j:
                out.append(tuple(a[:2] + b[:2]))
            if len(a) >= 3:
                out.append(tuple(a[:3] + b[:2]))
    # Flushes: the best five of each suit, plus all fives if few enough.
    for s, cs in by_suit.items():
        if len(cs) < 5:
            continue
        if len(cs) <= 7:
            out.extend(combinations(cs, 5))
        else:
            out.append(tuple(cs[:5]))
            out.append(tuple(sorted(cs, key=lambda c: rank[c], reverse=True)[:5]))
            if not inner:  # flush houses / straight flushes inside the suit
                out.extend(p for p in base_plays(cs, sc, True) if len(p) == 5)
    # Straights: every window of 5 consecutive ranks present in the hand.
    present = set(by_rank)
    for w in STRAIGHT_WINDOWS:
        if all(r in present for r in w):
            # variant 1: the best-scoring card of each rank
            v1 = tuple(by_rank[r][0] for r in w)
            out.append(v1)
            # variant 2: one suit as much as possible (straight flush)
            cnt = {}
            for r in w:
                for c in by_rank[r]:
                    cnt[suit[c]] = cnt.get(suit[c], 0) + 1
            best_s = max(cnt, key=cnt.get)
            out.append(tuple(next((c for c in by_rank[r] if suit[c] == best_s), by_rank[r][0]) for r in w))
            # variant 3: as many suits as possible (tricolor)
            if sc.tricolor:
                used, v3 = set(), []
                for r in w:
                    c = next((c for c in by_rank[r] if suit[c] not in used), by_rank[r][0])
                    used.add(suit[c])
                    v3.append(c)
                out.append(tuple(v3))
    return out


STRAIGHT_WINDOWS = [[12, 0, 1, 2, 3]] + [list(range(lo, lo + 5)) for lo in range(9)]


def _varied(cards, suit, k):
    """k cards from `cards`, as many different suits as possible."""
    used, out = set(), []
    for c in cards:
        if suit[c] not in used:
            used.add(suit[c])
            out.append(c)
    out += [c for c in cards if c not in out]
    return out[:k]


def pad_plays(plays, hand, keep, short=False):
    """Add junk kickers: playing extra cards cycles them out of the hand for
    free (like a discard that costs nothing). Kickers are the cards we least
    want to keep. With short_hand we stop at 3 cards in one variant."""
    junk_order = sorted(hand, key=keep.get)
    out = set()
    for p in plays:
        out.add(p)
        if len(p) >= 5:
            continue
        ps = set(p)
        junk = [c for c in junk_order if c not in ps]
        if len(p) < 3:
            out.add(p + tuple(junk[:3 - len(p)]))
        out.add(p + tuple(junk[:5 - len(p)]))
    return list(out)


def play_candidates(ctx, s, pad=True):
    """Candidate plays in state s: list of (points, cards, code, money), best first."""
    sc = ctx.sc
    hand = s.hand
    plays = ctx.base_plays(hand)
    if pad:
        plays = pad_plays(plays, hand, keep_values(hand, sc.rank, sc.suit), sc.short)
    else:
        plays = list(set(plays))
    out = []
    for p in plays:
        pts, code, money = ctx.score_play(s, p)
        out.append((pts, p, code, money))
    out.sort(key=lambda x: (-x[0], -len(x[1])))
    return out


def brute_force_plays(ctx, s):
    """Every subset of 1-5 cards, scored. Exact but ~10x slower: use it to
    check that play_candidates doesn't miss good plays."""
    out = []
    for k in range(1, min(5, len(s.hand)) + 1):
        for p in combinations(s.hand, k):
            pts, code, money = ctx.score_play(s, p)
            out.append((pts, p, code, money))
    out.sort(key=lambda x: (-x[0], -len(x[1])))
    return out


def best_play_value(ctx, s):
    """Best points available right now (without kickers; fast)."""
    sc = ctx.sc
    best = 0
    for p in ctx.base_plays(s.hand):
        v = ctx.score_play(s, p)[0]
        if v > best:
            best = v
    if sc.short or sc.spread:   # kickers can change the multiplier: check padded too
        for v in play_candidates(ctx, s)[:1]:
            best = max(best, v[0])
    return best


def discard_candidates(ctx, s, top=3):
    """Discards worth considering. The pile is known: discarding k cards draws
    exactly pile[pos:pos+k]. So for each k we look at the best plays available
    in hand + those k cards, and discard k cards that those plays don't use.
    We also add 'goal' discards (keep one suit, keep the rank groups)."""
    if s.discards <= 0:
        return []
    sc = ctx.sc
    rank, suit = sc.rank, sc.suit
    hand = s.hand
    keep = keep_values(hand, rank, suit)
    order = sorted(hand, key=keep.get)
    out = set()
    remaining = len(ctx.pile) - s.pos
    if remaining <= 0:
        return []
    for k in range(1, min(5, len(hand)) + 1):
        new = ctx.pile[s.pos:s.pos + k]
        union = hand + tuple(new)
        cands = []
        for p in ctx.base_plays(union):
            cands.append((ctx.score_play(s, p)[0], p))
        cands.sort(reverse=True)
        seen = 0
        for _, p in cands:
            ps = set(p)
            rest = [c for c in order if c not in ps]
            if len(rest) >= k:
                out.add(tuple(sorted(rest[:k])))
                seen += 1
                if seen >= top:
                    break
    # goal discards
    by_suit = {}
    for c in hand:
        by_suit.setdefault(suit[c], []).append(c)
    for st, cs in by_suit.items():
        if 3 <= len(cs) < 5:
            off = [c for c in order if suit[c] != st][:5]
            if off:
                out.add(tuple(sorted(off)))
    rc = {}
    for c in hand:
        rc[rank[c]] = rc.get(rank[c], 0) + 1
    singles = [c for c in order if rc[rank[c]] == 1][:5]
    if singles:
        out.add(tuple(sorted(singles)))
    return list(out)


# ---------------------------------------------------------------------------
# 4. Round policies
# ---------------------------------------------------------------------------

def greedy_step(ctx, s, discard_gain=1.0):
    """One greedy decision: play the best play, unless the target isn't
    reachable that way and a discard gives a better hand (exact one-step
    look-ahead over discard candidates). Returns the next state."""
    if ctx.can_end(s) and ctx.dollar > 0:
        return ctx.end(s)
    cands = play_candidates(ctx, s)
    pts, cards, code, money = cands[0]
    need = ctx.target - s.score
    if ctx.final or not ctx.allow_end or need <= 0:
        need = float("inf")   # max mode: always try to improve
    if s.discards > 0 and pts * s.plays < need * 1.0 or (ctx.final and s.discards > 0) or \
            (not ctx.allow_end and s.discards > 0):
        best_d, best_v = None, pts * discard_gain
        for d in discard_candidates(ctx, s):
            t = ctx.discard(s, d)
            if not t.hand:
                continue
            v = best_play_value(ctx, t)
            if v > best_v:
                best_d, best_v = t, v
        if best_d is not None:
            return best_d
    if ctx.sc.last_call and s.plays == 2:
        # last_call triples the last play: pick the play that leaves the best
        # hand for it, not simply the best play now.
        best_t, best_v = None, -1
        for c in cands[:6]:
            t = ctx.play(s, c[1], c[:1] + c[2:])
            v = c[0] + (best_play_value(ctx, t) if not t.done else 0)
            if v > best_v:
                best_t, best_v = t, v
        return best_t
    return ctx.play(s, cards, (pts, code, money))


def greedy_round(ctx, s=None):
    """Play the round to the end with greedy_step. Returns the final state."""
    s = s or ctx.start()
    while not s.done:
        s = greedy_step(ctx, s)
    return s


def heuristic(ctx, s, best=None, fut=0.6, disc=0.15):
    """Estimated value of a non-terminal state, for beam search ordering."""
    if best is None:
        best = best_play_value(ctx, s)
    est = s.score + best
    if ctx.allow_end and est >= ctx.target:
        # one more play clears the round: value it like the terminal it leads to
        return BIG + est + ctx.dollar * (ctx.reward(s) - 1)
    return s.score + best * (1 + fut * (s.plays - 1) + disc * s.discards)


def children(ctx, s, max_plays=12):
    """All candidate successor states of s."""
    out = []
    if ctx.can_end(s):
        out.append(ctx.end(s))
    cands = play_candidates(ctx, s)
    for pts, cards, code, money in cands[:max_plays]:
        out.append(ctx.play(s, cards, (pts, code, money)))
    for d in discard_candidates(ctx, s):
        out.append(ctx.discard(s, d))
    return out


def beam_round(ctx, width=8, deadline=None, fut=0.6, disc=0.15):
    """Beam search over the moves of a round (see beam_search.py for the story).
    Returns the best finished state found."""
    start = ctx.start()
    best = greedy_round(ctx, start)          # always have an answer
    best_v = ctx.terminal_value(best)
    layer = [start]
    while layer:
        pool = {}
        for s in layer:
            for t in children(ctx, s):
                if t.done:
                    v = ctx.terminal_value(t)
                    if v > best_v:
                        best, best_v = t, v
                    continue
                k = t.key()
                old = pool.get(k)
                if old is None or t.score > old.score:
                    pool[k] = t
            if deadline and time.perf_counter() > deadline:
                break
        scored = sorted(pool.values(), key=lambda t: heuristic(ctx, t, None, fut, disc), reverse=True)
        layer = scored[:width]
        if deadline and time.perf_counter() > deadline:
            # finish the survivors greedily so we still return a complete round
            for s in layer:
                f = greedy_round(ctx, s)
                v = ctx.terminal_value(f)
                if v > best_v:
                    best, best_v = f, v
            break
    return best


# ---------------------------------------------------------------------------
# 5. The shop
# ---------------------------------------------------------------------------

class Shop:
    """Simulation-guided greedy shopping.

    Each shop we repeatedly list affordable purchases, apply each to a clone
    of the game, *simulate the next `horizon` rounds* (with the real, known
    draw orders) using the cheap greedy round player in "max points" mode, and
    buy the purchase with the best value gain per the formula below. We stop
    when no purchase is worth its price.

    value(game) = sum_j w_j * log2(points_j / target_j)   (j = next rounds)
                  - a big penalty if the very next round would not be cleared
                  + money_value * (dollars kept)          (interest, later buys)

    Deck edits are proposed in small bundles because a single removed card
    changes little: "remove the k least useful cards", "paint the k best
    off-suit cards into the main suit", "promote cards to join a big rank
    group".

    Parameters (all in `params`):
      horizon         rounds simulated per evaluation (4: next round + 3 more)
      w_next/w_later  weight of the next round / of later rounds in value()
      fail_penalty    extra cost if the next round would be failed
      money_value     value of $1 kept, in log2 units (0.02: $50 ~ one doubling)
      max_steps       purchases per shop. Small on purpose: a greedy shop that
                      spends everything early buys weak flat bonuses and has
                      no money for the multipliers offered later.
      early_rounds, early_steps
                      the shops after rounds 1..early_rounds allow only
                      early_steps purchases (1): +20% mean score
      final_spend     in the last shop money is useless: spend it all
      max_rerolls     how far down the (known) offer queue we look
      bundle          size of deck-edit bundles
      mod_bias        a prior bonus (log2 units) for multiplicative modifiers.
                      The simulation judges a modifier with *today's* engine,
                      where a flat +mult looks as good as x2; later, when the
                      engine has grown, the multiplier is worth far more.
      project         alternative fix for the same problem: simulate later
                      rounds as if every hand type had extra levels
      interest_rounds count interest on kept money for this many rounds
      future_shops    > 0: a $ kept is worth the best value/$ of modifiers the
                      next shops will offer (see future_money_value)
    The defaults are what worked best in our experiments on seeds 1001-1060
    (project, interest_rounds and future_shops did not help; try them!).
    """

    def __init__(self, params=None):
        p = dict(horizon=4, w_next=1.0, w_later=0.6, money_value=0.02, fail_penalty=4.0,
                 bundle=3, max_steps=3, min_gain=0.0, reserve=0, max_rerolls=1,
                 future_shops=0, future_discount=0.8, future_cap=0.08,
                 project=0.0, interest_rounds=1,
                 mod_bias={"last_call": 0.8, "tricolor": 0.5}, final_spend=True,
                 early_rounds=4, early_steps=1)
        p.update(params or {})
        self.p = p
        self.mv = p["money_value"]

    # -- evaluation ------------------------------------------------------------

    def rounds_points(self, game):
        """Points the greedy player makes in each of the next rounds with this engine."""
        g = game.clone()
        out = []
        for j in range(self.p["horizon"]):
            if g.round + 1 >= g.inst.rounds:
                break
            if j == 1 and self.p["project"] > 0:
                # Later rounds: pretend we will keep levelling our hands (we
                # will!). Against a stronger engine, flat bonuses like
                # odd_job matter less and multipliers like tricolor more.
                extra = int(self.p["project"] * (g.inst.rounds - 2 - game.round))
                for code in g.levels:
                    g.levels[code] += extra
            g._start_round(g.round + 1)  # jump to the next round, no shopping
            ctx = RoundCtx(g, dollar=0, allow_end=False)
            s = greedy_round(ctx)
            out.append((s.score, g.target))
        return out

    def value(self, game):
        return self.engine_value(game) + self.mv * self.money_worth(game)

    def engine_value(self, game, penalty=True):
        p = self.p
        pts = self.rounds_points(game)
        v = 0.0
        for j, (score, target) in enumerate(pts):
            ratio = max(score, 1) / target
            lr = math.log2(ratio)
            w = p["w_next"] if j == 0 else p["w_later"]
            v += w * min(lr, 3.0)
            if j == 0 and ratio < 1 and penalty:
                v -= p["fail_penalty"]
        return v

    def money_worth(self, game):
        """Money held, plus the interest it earns over the next rounds."""
        R = game.rules
        saving = game.has_mod("savings")
        cap = R.interest_cap + (R.effects["savings"] if saving else 0)
        per = R.savings_interest_per if saving else R.interest_per
        rounds = max(1, min(self.p["interest_rounds"], game.inst.rounds - 2 - game.round))
        return game.money + rounds * min(game.money // per, cap)

    # -- proposals -------------------------------------------------------------

    def proposals(self, game, usage):
        """Affordable shop move bundles (lists of move strings)."""
        out = []
        money = game.money
        price = game.rules.price
        offers = game.visible_offers()
        full = len(game.mods) >= game.inst.slots
        for name in offers:
            if game.has_mod(name) or price(name) > money:
                continue
            if not full:
                out.append([f"buy {name}"])
            else:
                for m, _ in game.mods:
                    if price(name) <= money + price(m) // 2:
                        out.append([f"sell {m}", f"buy {name}"])
        # reroll to later offers: we know the whole queue, so look ahead
        queue = game.inst.offers[game.round]
        cost, pos, rolls = 0, game.offer_pos, 0
        while pos + 3 < len(queue) and rolls < self.p["max_rerolls"]:
            cost += game.prices()["reroll"] + rolls
            pos += 3
            rolls += 1
            for name in queue[pos:pos + 3]:
                if game.has_mod(name) or price(name) + cost > money:
                    continue
                if not full:
                    out.append(["reroll"] * rolls + [f"buy {name}"])
                else:
                    for m, _ in game.mods:
                        if price(name) + cost <= money + price(m) // 2:
                            out.append([f"sell {m}"] + ["reroll"] * rolls + [f"buy {name}"])
        # levels: hand types used in the simulated next round, and the most common ones
        for code in usage:
            if game.level_price(code) <= money:
                out.append([f"level {code}"])
        pr = game.prices()
        if pr["handsize"] <= money and game.hand_size < game.rules.max_hand_size:
            out.append(["handsize"])
        if pr["discards"] <= money:
            out.append(["discards"])
        out.extend(self.deck_bundles(game, money))
        return out

    def deck_bundles(self, game, money):
        R = game.rules
        bundle = self.p["bundle"]
        k_remove = min(bundle, money // R.price_remove)
        k_paint = min(bundle, money // R.price_paint)
        k_promote = min(bundle, money // R.price_promote)
        if max(k_remove, k_paint, k_promote) <= 0:
            return []
        rank, suit = game.rank, game.suit
        deck = [c for c in range(N_CARDS) if game.in_deck[c]]
        sc, rc = [0] * 4, [0] * 13
        for c in deck:
            sc[suit[c]] += 1
            rc[rank[c]] += 1
        main = max(range(4), key=lambda s: sc[s])
        out = []
        # remove: low-value cards of the weakest suits / rarest ranks
        k = k_remove
        if k > 0 and game.deck_size - k >= R.min_deck:
            junk = sorted(deck, key=lambda c: (sc[suit[c]] + rc[rank[c]] * 2, chips(rank[c])))
            out.append([f"remove {c}" for c in junk[:k]])
            junk2 = sorted(deck, key=lambda c: (sc[suit[c]], chips(rank[c])))
            out.append([f"remove {c}" for c in junk2[:k]])
            junk3 = sorted(deck, key=lambda c: (rc[rank[c]], chips(rank[c])))
            out.append([f"remove {c}" for c in junk3[:k]])
        # paint: high cards of other suits into the main suit
        off = sorted((c for c in deck if suit[c] != main), key=lambda c: (-sc[suit[c]], -chips(rank[c])))
        if off and k_paint > 0:
            out.append([f"paint {c} {SUITS[main]}" for c in off[-k_paint:]])
        # promote: cards one rank below the most common rank join it
        for target in sorted(range(13), key=lambda r: -rc[r])[:2]:
            if target == 0:
                continue
            below = [c for c in deck if rank[c] == target - 1]
            if below and k_promote > 0:
                out.append([f"promote {c}" for c in below[:k_promote]])
        return out

    # -- main loop ---------------------------------------------------------------

    def usage(self, game):
        """Hand types the greedy player uses next round (most valuable first)."""
        g = game.clone()
        g._start_round(g.round + 1)
        ctx = RoundCtx(g, dollar=0, allow_end=False)
        s = ctx.start()
        used = {}
        while not s.done:
            t = greedy_step(ctx, s)
            if t.moves[1].startswith("play"):
                cards = tuple(map(int, t.moves[1].split()[1:]))
                pts, code, _ = ctx.score_play(s, cards)
                used[code] = used.get(code, 0) + pts
            s = t
        return sorted(used, key=used.get, reverse=True)[:3]

    def future_money_value(self, game):
        """What a dollar kept now is worth: the best value-per-dollar among the
        modifiers we know the next shops will offer (the input reveals every
        shop queue). If tricolor shows up next shop, a weak buy now must beat
        "save the money for tricolor"."""
        p = self.p
        if len(game.mods) >= game.inst.slots or p["future_shops"] <= 0:
            return 0.0
        base = self.engine_value(game, False)
        best = 0.0
        seen = set()
        for k in range(1, p["future_shops"] + 1):
            r = game.round + k
            if r >= game.inst.rounds - 1:
                break
            for pos, name in enumerate(game.inst.offers[r][:6]):
                if name in seen or game.has_mod(name):
                    continue
                seen.add(name)
                g = game.clone()
                g.mods.append([name, {"last": "", "streak": 0} if name == "momentum" else
                               {"bonus": 0} if name == "collector" else {}])
                gain = self.engine_value(g, False) - base
                price = game.rules.price(name) + (2 if pos >= 3 else 0)
                best = max(best, gain / price * p["future_discount"] ** k)
        return min(best, p["future_cap"])

    def __call__(self, game, deadline=None):
        p = self.p
        moves = []
        self.mv = max(p["money_value"], self.future_money_value(game))
        steps = p["max_steps"] if game.round >= p["early_rounds"] else p["early_steps"]
        if game.round == game.inst.rounds - 2 and p["final_spend"]:
            # Last shop: money has no use after it, so spend whatever helps.
            self.mv, steps = 0.0, 30
        cur = self.value(game)
        for _ in range(steps):
            if deadline and time.perf_counter() > deadline:
                break
            usage = self.usage(game)
            best = None
            for bundle in self.proposals(game, usage):
                g = game.clone()
                try:
                    for m in bundle:
                        g.apply(m)
                except IllegalMove:
                    continue
                if g.money < p["reserve"]:
                    continue
                v = self.value(g)
                gain = v - cur + sum(p["mod_bias"].get(m[4:], 0.0) for m in bundle if m.startswith("buy "))
                if gain > p["min_gain"] and (best is None or gain > best[0]):
                    best = (gain, bundle, g, v)
                if deadline and time.perf_counter() > deadline:
                    break
            if best is None:
                break
            _, bundle, g, cur = best
            for m in bundle:
                game.apply(m)
            moves += bundle
        game.apply("next")
        return moves + ["next"]


# ---------------------------------------------------------------------------
# 6. Driver
# ---------------------------------------------------------------------------

RESERVE = 0.05   # seconds kept back for each phase still to come


def solve(inst, round_solver, shop, start=None, budget=DEFAULT_TIME, rules=None):
    """Alternate round_solver(game, deadline) and shop(game, deadline) until
    the run is over. Both get a copy of the game and return move strings,
    which are checked here on the real game. rules: a ruleset or its name
    (default: the environment variable POKER_RULES, else classic).

    Time: the solvers are bounded by their own work limits (beam width,
    rollouts, ...), so their output does not depend on the machine. The
    deadline is only a safety net: a phase may use all the remaining time
    except RESERVE seconds for each phase still to come."""
    start = start or time.perf_counter()
    game = Game(inst, rules if rules is not None else rules_from_env())
    moves = []
    while not game.over:
        phases_left = 2 * (inst.rounds - game.round) - (2 if game.phase == "play" else 3)
        deadline = start + budget - RESERVE * phases_left
        g = game.clone()
        try:
            ms = round_solver(g, deadline) if game.phase == "play" else shop(g, deadline)
        except Exception as e:  # a bug in a solver must not cost the whole output
            print(f"solver error: {e!r}", file=sys.stderr)
            ms = []
        applied = 0
        for m in ms:
            try:
                game.apply(m)
            except IllegalMove as e:
                print(f"illegal move {m!r}: {e}", file=sys.stderr)
                break
            moves.append(m)
            applied += 1
            if game.over:
                break
        if applied == 0 and not game.over:
            # nothing usable came back: make one safe move and re-plan
            m = safe_move(game)
            game.apply(m)
            moves.append(m)
    return moves


def safe_move(game):
    """A move that is always legal: leave the shop, or play the best candidate."""
    if game.phase == "shop":
        return "next"
    ctx = RoundCtx(game)
    return "play " + " ".join(map(str, play_candidates(ctx, ctx.start())[0][1]))


def self_test(n=3000, seed=1, rules=None):
    """Check Scorer and fast_classify against the simulator on random cases."""
    import random
    from poker_run.instance import generate
    from poker_run.rules import MODIFIER_IDS
    rng = random.Random(seed)
    for _ in range(n):
        g = Game(generate(rng.randint(1, 100)), rules)
        g.mods = [[m, {"last": rng.choice(["", "PR", "FH"]), "streak": rng.randint(0, 3)} if m == "momentum"
                   else {"bonus": 10 * rng.randint(0, 3)} if m == "collector" else {}]
                  for m in rng.sample(MODIFIER_IDS, 5)]
        for c in range(N_CARDS):
            if rng.random() < 0.3:
                g.suit[c] = rng.randrange(4)
            if rng.random() < 0.3:
                g.rank[c] = rng.randrange(13)
        g.deck_size = rng.randint(g.rules.min_deck, N_CARDS)
        g.plays_left = rng.randint(1, 4)
        for code in HAND_CODES:
            g.levels[code] = rng.randint(1, 4)
        sc = Scorer(g)
        mom = mod_state(g, "momentum") or {"last": "", "streak": 0}
        col = mod_state(g, "collector") or {"bonus": 0}
        cards = rng.sample(range(N_CARDS), rng.randint(1, 5))
        e = g.evaluate(cards)
        got = sc.score(cards, g.plays_left, mom["last"], mom["streak"], col["bonus"])
        assert got == (e["points"], e["hand"], e["money"]), (cards, got, e)
        ranks, suits = [g.rank[c] for c in cards], [g.suit[c] for c in cards]
        assert fast_classify(ranks, suits) == classify(ranks, suits)
    print(f"self_test: {n} random plays agree with Game.evaluate ({get_ruleset(rules).name} rules)")


if __name__ == "__main__":
    from poker_run.rules import RULESETS
    for _name in RULESETS:
        self_test(rules=_name)
