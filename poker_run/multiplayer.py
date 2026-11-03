"""Several players on one deal: a port of web/multiplayer.js (PokerMulti.Match).

The online rooms (platform/server/prplatform/rooms.py) use this as the
authority: every move a client sends goes through Match.apply, which checks
whose turn it is, the shared-shop draft rules and the solo rules of the
player's own Game. web/MULTIPLAYER.md has the rules; tests/test_poker_run_multiplayer.py
drives this module and multiplayer.js with the same move sequences (via Node)
and checks that they agree move for move.

    m = Match(inst, "draft", "classic", [{"name": "Ann"}, {"name": "Bot", "bot": "normal"}])
    m.turn                  # {"seat", "stage": "play" | "shop", "round"} or None when over
    m.try_move("play 1 2", seat)   # None, or why the move is illegal
    m.apply("play 1 2", seat)      # raises IllegalMove; the bots then move by themselves
    m.leave(seat)           # online only: the player stops; their run scores what it has

Modes: race (everyone plays on their own; bots play their whole run at once),
rounds (turns go round by round in seat order), draft (rounds with a shared
shop: everyone plays round r, then the shop is visited one player at a time,
lowest total first, and a modifier another player owns can't be bought).

`leave` is the one addition to the JS layer (whose matches are all on one
device): a player who left is skipped like one whose run is over. It is logged
as the pseudo-move LEAVE so a match replays from its log.
"""

from . import bot as Bot
from .rules import get_ruleset
from .sim import Game, IllegalMove

MODES = ("race", "rounds", "draft")
MAX_PLAYERS = 4
LEAVE = "!leave"
INF = float("inf")


class Player:
    __slots__ = ("seat", "name", "bot", "game", "moves", "rounds", "hands", "left")

    def __init__(self, seat, name, bot, game):
        self.seat, self.name, self.bot, self.game = seat, name, bot, game
        self.moves = []      # a valid output file for the seed
        self.rounds = []     # per round: {round, score, cleared, total, money, plays, discards, moves, shop}
        self.hands = {}      # hand code -> times played (bots level their most played hand)
        self.left = False

    def clone(self):
        p = Player(self.seat, self.name, self.bot, self.game.clone())
        p.moves = list(self.moves)
        p.rounds = [dict(r, plays=list(r["plays"]), moves=list(r["moves"]), shop=list(r["shop"])) for r in self.rounds]
        p.hands = dict(self.hands)
        p.left = self.left
        return p

    @property
    def out(self):
        """Not taking part any more: the run is over or the player left."""
        return self.left or self.game.over


class Match:
    def __init__(self, inst, mode, rules=None, players=(), log=()):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode}: use one of {', '.join(MODES)}")
        players = list(players)
        if not 1 <= len(players) <= MAX_PLAYERS:
            raise ValueError(f"1 to {MAX_PLAYERS} players")
        self.inst = inst
        self.mode = mode
        self.rules = get_ruleset(rules).name
        self.setup = [{"name": str(p.get("name") or f"Player {i + 1}"),
                       "bot": Bot.level(p["bot"]) if p.get("bot") else None} for i, p in enumerate(players)]
        self.players = [Player(seat, p["name"], p["bot"], Game(inst, self.rules)) for seat, p in enumerate(self.setup)]
        self.history = []    # (seat, move, round, phase) in the order made
        for seat, move in log:
            if move == LEAVE:
                self._leave(seat)
            else:
                self._apply(seat, move)
        self.run_bots()

    def clone(self):
        m = Match.__new__(Match)
        m.inst, m.mode, m.rules, m.setup = self.inst, self.mode, self.rules, self.setup
        m.players = [p.clone() for p in self.players]
        m.history = list(self.history)
        return m

    # ---- whose turn

    def _step(self, pl):
        if pl.out:
            return INF
        g = pl.game
        return g.round if self.mode == "rounds" else 2 * g.round + (1 if g.phase == "shop" else 0)

    @property
    def over(self):
        return all(p.out for p in self.players)

    @property
    def turn(self):
        if self.mode == "race":
            p = next((q for q in self.players if not q.bot and not q.out), None)
            return {"seat": p.seat, "stage": p.game.phase, "round": p.game.round} if p else None
        step = min(self._step(p) for p in self.players)
        if step == INF:
            return None
        if self.mode == "draft" and step % 2 == 1:
            r = (step - 1) // 2
            pick = next(self.players[s] for s in self.shop_order(r) if self._step(self.players[s]) == step)
        else:
            pick = next(p for p in self.players if self._step(p) == step)
        return {"seat": pick.seat, "stage": pick.game.phase, "round": pick.game.round}

    def can_act(self, seat):
        if not 0 <= seat < len(self.players):
            return False
        pl = self.players[seat]
        if pl.out:
            return False
        if self.mode == "race":
            return True
        t = self.turn
        return t is not None and t["seat"] == seat

    def shop_order(self, r):
        """Draft: the seats visiting the shop after round r, lowest total after round r first;
        ties in seat order starting with seat r mod N."""
        n = len(self.players)
        rows = []
        for p in self.players:
            if p.game.cleared > r and r + 1 < self.inst.rounds:
                total = sum(x["score"] for x in p.rounds[:r + 1])
                rows.append((total, (p.seat - r) % n, p.seat))
        rows.sort()
        return [seat for _, _, seat in rows]

    # ---- the shared shop

    def owner_of(self, name, seat):
        """Draft: the seat of another player who owns this modifier, or -1."""
        if self.mode != "draft":
            return -1
        for p in self.players:
            if p.seat != seat and p.game.has_mod(name):
                return p.seat
        return -1

    def owners(self):
        """Draft: {modifier: seat} for every owned modifier."""
        if self.mode != "draft":
            return {}
        return {name: p.seat for p in self.players for name, _ in p.game.mods}

    def try_move(self, move, seat):
        """None if this player may make this move now, else why not."""
        if not self.can_act(seat):
            pl = self.players[seat] if 0 <= seat < len(self.players) else None
            if pl is not None and pl.game.over:
                return "the run is already over"
            if pl is not None and pl.left:
                return "you left this match"
            return "it is not this player's turn"
        tok = str(move).split()
        g = self.players[seat].game
        if len(tok) == 2 and tok[0] == "buy" and g.phase == "shop":
            owner = self.owner_of(tok[1], seat)
            if owner >= 0:
                return f"{tok[1]} was drafted by {self.players[owner].name}"
        return _try(g, move)

    # ---- moves

    def _apply(self, seat, move):
        move = str(move)
        if not self.can_act(seat):
            pl = self.players[seat] if 0 <= seat < len(self.players) else None
            if pl is None:
                raise IllegalMove(f"no player {seat}")
            raise IllegalMove(f"{pl.name}'s run is already over" if pl.game.over else
                              f"{pl.name} left the match" if pl.left else f"it is not {pl.name}'s turn")
        err = self.try_move(move, seat)
        if err:
            raise IllegalMove(err)
        pl = self.players[seat]
        g = pl.game
        r, phase = g.round, g.phase
        tok = move.split()
        res = g.evaluate([int(x) for x in tok[1:]]) if tok[0] == "play" else None
        g.apply(move)
        pl.moves.append(move)
        self.history.append((seat, move, r, phase))
        while len(pl.rounds) <= r:
            pl.rounds.append({"round": len(pl.rounds), "score": 0, "cleared": None, "total": 0, "money": 0,
                              "plays": [], "discards": 0, "moves": [], "shop": []})
        rec = pl.rounds[r]
        if phase == "play":
            rec["moves"].append(move)
            if res:
                rec["plays"].append({"hand": res["hand"], "points": res["points"]})
                pl.hands[res["hand"]] = pl.hands.get(res["hand"], 0) + 1
            if tok[0] == "discard":
                rec["discards"] += 1
            rec["score"] = g.round_score
            rec["total"] = g.total
            if g.phase != "play":
                rec["cleared"] = g.cleared > r
        else:
            rec["shop"].append(move)
        rec["money"] = g.money

    def apply(self, move, seat=None):
        """A human player's move (default: whose turn it is); then the bots move."""
        if seat is None:
            t = self.turn
            if t is None:
                raise IllegalMove("the match is over")
            seat = t["seat"]
        if 0 <= seat < len(self.players) and self.players[seat].bot:
            raise IllegalMove(f"{self.players[seat].name} is a bot")
        self._apply(seat, move)
        self.run_bots()

    def _leave(self, seat):
        pl = self.players[seat]
        if pl.out:
            raise IllegalMove(f"{pl.name} is not playing any more")
        pl.left = True
        self.history.append((seat, LEAVE, pl.game.round, pl.game.phase))

    def leave(self, seat):
        """The player stops for good (left, or removed for being away): skipped from now on."""
        self._leave(seat)
        self.run_bots()

    def run_bots(self):
        def stage(p):
            g = p.game.clone()
            hands = dict(p.hands)
            if g.phase == "play":
                return Bot.round_moves(g, p.bot, hands)
            return Bot.shop_moves(g, p.bot, hands, lambda name: self.owner_of(name, p.seat) < 0)

        if self.mode == "race":
            for p in self.players:
                while p.bot and not p.out:
                    for m in stage(p):
                        self._apply(p.seat, m)
            return
        t = self.turn
        while t is not None and self.players[t["seat"]].bot:
            for m in stage(self.players[t["seat"]]):
                self._apply(t["seat"], m)
            t = self.turn

    # ---- results

    @property
    def log(self):
        return [(seat, move) for seat, move, _, _ in self.history]

    def standings(self):
        """Players best first (total, then rounds cleared), with places (ties share a place)."""
        rows = [{"seat": p.seat, "name": p.name, "bot": p.bot, "total": p.game.total, "cleared": p.game.cleared,
                 "over": p.game.over, "left": p.left, "round": p.game.round, "phase": p.game.phase,
                 "money": p.game.money} for p in self.players]
        rows.sort(key=lambda r: (-r["total"], -r["cleared"], r["seat"]))
        for i, row in enumerate(rows):
            prev = rows[i - 1] if i else None
            row["place"] = prev["place"] if prev and prev["total"] == row["total"] and prev["cleared"] == row["cleared"] \
                else i + 1
        return rows

    def winners(self):
        if not self.over:
            return []
        return [r["seat"] for r in self.standings() if r["place"] == 1]


def _try(game, move):
    g = game.clone()
    try:
        g.apply(move)
    except IllegalMove as e:
        return str(e)
    return None
