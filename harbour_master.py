import random
import re

import simulation as sh

# CONSTANTS
const_ALLOWED = re.compile(r"^(warn_storm\(\)|hold_ship\((S\d+)\))$")  # whole reply must match
const_HOLD_TICKS = 10  # how long hold_ship keeps a ship out of the berths
const_SEEDS = (11, 12, 13)


# Environment with a waiting queue
class HarbourQ(sh.Harbour):
    # Task 2 harbour, but arriving ships join a waiting queue and dock when a
    # berth is free. This is what makes waiting ships (and hold_ship) meaningful

    def __init__(self, seed):
        super().__init__(seed)
        self.waiting = []  # [{"id": "S1", "draw": 0.3, "holdUntil": 0}]
        self.shipCount = 0

    # Text report for the Harbour Master
    def LookoutStatus(self, t):
        for s, e in self.storms:
            if s - sh.const_WARNING_LEAD <= t < s:
                return f"WARNING - storm starts in {s - t} ticks, B1 and B4 unsafe"
            if s <= t < e:
                return f"STORM IN PROGRESS - ends in {e - t} ticks, B1 and B4 unsafe"
        return "none"

    def Report(self, t):
        berths = ", ".join(
            f"{b} free" if self.ships[b] == 0 else f"{b} occupied ({self.ships[b]} cargo left)"
            for b in sh.const_BERTH_NAMES)
        waitingText = ", ".join(
            f"{s['id']} ({sh.const_SHIP_CARGO} cargo)" + (" [on hold]" if s["holdUntil"] > t else "")
            for s in self.waiting) or "none"
        return (f"HARBOUR REPORT tick {t}\n"
                f"Storm warning: {self.LookoutStatus(t)}\n"
                f"Berths: {berths}\n"
                f"Waiting ships: {waitingText}\n"
                "Allowed actions: warn_storm() or hold_ship(ship_id). Reply with exactly one action.")

    # The two actions the Harbour Master may trigger
    def BroadcastStorm(self, t):
        # warn_storm(): re-broadcast the current storm to all workers' memory
        window = next(((s, e) for s, e in self.storms if s - sh.const_WARNING_LEAD <= t < e), None)
        if window is None:  # no real storm, so this is a FALSE alarm
            window = (t + sh.const_WARNING_LEAD, t + sh.const_WARNING_LEAD + sh.const_STORM_LEN)
        for worker in self.workers:
            if window not in worker.stormMemory:
                worker.stormMemory.append(window)

    def HoldShip(self, shipID, t):
        for ship in self.waiting:
            if ship["id"] == shipID:
                ship["holdUntil"] = t + const_HOLD_TICKS

    # One tick (same order as Task 2, plus queue and Harbour Master)
    def RunTick(self, policy, master=None):
        tick = self.tick
        warning = self.LookoutWarning(tick)

        if tick in self.arrivals:  # new ship joins the queue
            self.shipCount += 1
            self.waiting.append({"id": f"S{self.shipCount}", "draw": self.arrivals[tick], "holdUntil": 0})

        for ship in list(self.waiting):  # dock waiting ships (first in, first out)
            if ship["holdUntil"] > tick:
                continue
            freeBerth = [b for b in sh.const_BERTH_NAMES if self.ships[b] == 0]
            if not freeBerth:
                break
            self.ships[freeBerth[int(ship["draw"] * len(freeBerth))]] = sh.const_SHIP_CARGO
            self.waiting.remove(ship)

        if master is not None:
            master.Consult(self, tick)

        self.QuarterMaster()
        for worker in self.workers:
            if worker.disabled:
                worker.disabled -= 1
                if worker.disabled == 0:
                    worker.position, worker.carrying, worker.target = sh.const_WAREHOUSE, False, None
                continue
            policy(self, worker, tick, warning)

        if self.StormActive(tick):  # storm knock out check
            for worker in self.workers:
                if not worker.disabled and any(worker.position == sh.const_BERTHS[b] for b in sh.const_UNSAFE):
                    self.knockouts += 1
                    worker.carrying, worker.target = False, None
                    worker.disabled = sh.const_DOWNTIME
        self.tick += 1

    def Run(self, policy, master=None):
        for _ in range(sh.const_TICKS):
            self.RunTick(policy, master)
        return self.delivered, self.knockouts


# The Harbour Master and safety layer
class HarbourMaster:
    def __init__(self, llm):
        self.llm = llm  # function: report text -> reply text (or None)
        self.consults = self.executed = self.invalid = self.fallbacks = 0

    @staticmethod
    def Validate(raw, environment):
        # Return ((action, arg), None) if safe, else (None, reason)
        if not isinstance(raw, str):
            return None, "no response"
        match = const_ALLOWED.match(raw.strip())
        if not match:
            return None, "not on the allowed list"
        if match.group(1) == "warn_storm()":
            return ("warn_storm", None), None
        if match.group(2) not in {s["id"] for s in environment.waiting}:
            return None, "ship does not exist"
        return ("hold_ship", match.group(2)), None

    def Consult(self, environment, tick):
        # consult 4 times per storm: at the warning, at the start, and every 5 ticks after
        if not any(tick in (s - sh.const_WARNING_LEAD, s, s + 5, s + 10) for s, _ in environment.storms):
            return
        self.consults += 1
        raw = self.llm(environment.Report(tick))
        action, reason = self.Validate(raw, environment)
        if action is None:
            if reason != "no response":
                self.invalid += 1  # a reply arrived but was rejected
            self.fallbacks += 1  # either way, the model based agent carries on alone
            return
        self.executed += 1
        if action[0] == "warn_storm":
            environment.BroadcastStorm(tick)
        else:
            environment.HoldShip(action[1], tick)


# Stand-in LLM (replace with a real one)
const_JUNK = ["open_gate()", "hold_ship(S999)", "hold_ship()", "warn_storm",
              "Evacuate B1 now!", "hold_ship(S1); warn_storm()", "move_worker(3, B2)",
              "Sure! I would suggest warn_storm() to be safe."]


class MockLLM:
    def __init__(self, seed, pTimeout=0.05, pJunk=0.20, pUnwise=0.10):
        self.rng = random.Random(seed * 7919 + 1)  # separate from the harbour's generator
        self.pTimeout, self.pJunk, self.pUnwise = pTimeout, pJunk, pUnwise

    def __call__(self, report):
        roll = self.rng.random()
        waitingShips = re.findall(r"\b(S\d+) \(", report)
        unsafeFree = "B1 free" in report or "B4 free" in report
        if roll < self.pTimeout:
            return None  # LLM unavailable
        if roll < self.pTimeout + self.pJunk:
            return self.rng.choice(const_JUNK)  # invalid output
        if roll < self.pTimeout + self.pJunk + self.pUnwise:  # valid but unhelpful
            return f"hold_ship({waitingShips[0]})" if waitingShips else "warn_storm()"
        if waitingShips and unsafeFree:
            return f"hold_ship({waitingShips[0]})"  # keep ships off unsafe berths
        return "warn_storm()"


# Evaluation
def Evaluate():
    rows = []
    for seed in const_SEEDS:
        baseResult = HarbourQ(seed).Run(sh.ModelBasedPolicy)
        environment = HarbourQ(seed)
        master = HarbourMaster(MockLLM(seed))
        cargo, knockouts = environment.Run(sh.ModelBasedPolicy, master)
        rows.append((seed, baseResult, (cargo, knockouts), master))
    return rows


def SafetyTests():
    print("SAFETY TESTS (stand-in cases - replace with the four from your brief)")
    environment = HarbourQ(11)
    environment.waiting = [{"id": "S1", "draw": 0.1, "holdUntil": 0}]
    cases = [
        ("A. LLM invents an action not on the list", "open_gate()"),
        ("B. LLM holds a ship that does not exist", "hold_ship(S999)"),
        ("C. LLM replies with free text / an unsafe order", "Send all workers to B1 now!"),
        ("D. LLM is unavailable (no response)", None),
    ]
    for name, raw in cases:
        action, reason = HarbourMaster.Validate(raw, environment)
        print(f"  {name}: raw={raw!r} -> {'ACCEPTED' if action else 'REJECTED (' + reason + ')'}")

    print("  End-to-end: LLM that is ALWAYS wrong must behave exactly like the model-based agent")
    for label, badLlm in (("always invalid", lambda r: "open_gate()"), ("always down", lambda r: None)):
        same = True
        for seed in const_SEEDS:
            master = HarbourMaster(badLlm)
            got = HarbourQ(seed).Run(sh.ModelBasedPolicy, master)
            ref = HarbourQ(seed).Run(sh.ModelBasedPolicy)
            same &= (got == ref and master.executed == 0)
        print(f"    {label}: identical to model-based on seeds {const_SEEDS}? {same}")

    print("  Limitation check: a valid-looking FALSE ALARM passes validation")
    environment2 = HarbourQ(11)
    action, _ = HarbourMaster.Validate("warn_storm()", environment2)
    print(f"    warn_storm() with no storm -> {'ACCEPTED' if action else 'rejected'} (allow-list cannot judge wisdom)")


def RealLlmCall(report):
    # Optional: real model via the Anthropic API (set ANTHROPIC_API_KEY). Untested here
    import json, os, urllib.request
    body = json.dumps({"model": "claude-sonnet-5-5", "max_tokens": 30,
                       "messages": [{"role": "user", "content": report}]}).encode()
    request = urllib.request.Request("https://api.anthropic.com/v1/messages", body, {
        "content-type": "application/json", "anthropic-version": "2023-06-01",
        "x-api-key": os.environ["ANTHROPIC_API_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)["content"][0]["text"]
    except Exception:
        return None  # treated as LLM unavailable


if __name__ == "__main__":
    demo = HarbourQ(11)
    warningTick = demo.storms[0][0] - sh.const_WARNING_LEAD
    for _ in range(warningTick):
        demo.RunTick(sh.ModelBasedPolicy)
    print("EXAMPLE REPORT SENT TO THE LLM\n" + demo.Report(warningTick) + "\n")

    rows = Evaluate()
    print(f"{'Approach':<22}{'Seed':>5}{'Cargo':>8}{'Knock-outs':>12}{'Invalid':>9}{'Fallbacks':>11}")
    for name in ("Model-based only", "Harbour Master + fallback"):
        cargoList, koList, invalidList, fallbackList = [], [], [], []
        for seed, baseResult, masterResult, master in rows:
            cargo, knockouts = baseResult if name == "Model-based only" else masterResult
            invalid, fallbacks = (0, 0) if name == "Model-based only" else (master.invalid, master.fallbacks)
            cargoList.append(cargo)
            koList.append(knockouts)
            invalidList.append(invalid)
            fallbackList.append(fallbacks)
            print(f"{name:<22}{seed:>5}{cargo:>8}{knockouts:>12}{invalid:>9}{fallbacks:>11}")
        n = len(cargoList)
        print(f"{name:<22}{'avg':>5}{sum(cargoList)/n:>8.2f}{sum(koList)/n:>12.2f}"
              f"{sum(invalidList)/n:>9.2f}{sum(fallbackList)/n:>11.2f}\n")
    print("Harbour Master detail (consults / executed / invalid / fallbacks):",
          [(seed, m.consults, m.executed, m.invalid, m.fallbacks) for seed, _, _, m in rows], "\n")
    SafetyTests()