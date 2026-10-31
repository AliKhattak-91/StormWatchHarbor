import random

# CONSTANTS
const_COLS = 24
const_ROWS = 16
const_TICKS = 600

# reason for choosing this layout is because it gives even spacing across the 24 wide edge
const_BERTHS = {"B1": (3, 0), "B2": (9, 0), "B3": (14, 0), "B4": (20, 0)}
const_BERTH_NAMES = list(const_BERTHS)
const_UNSAFE = {"B1", "B4"}  # the unsafe berths during the storm
const_WAREHOUSE = (0, 15)

const_STORM_LEN = 15
# lookout warns 5 ticks before a storm begins
const_WARNING_LEAD = 5
# the fixed cargo unit per ship
const_SHIP_CARGO = 8
# the ship arrives every 6-10 ticks
const_SHIP_INTERVAL = (6, 10)
# the fixed number of storms per run
const_NUM_STORMS = 3
# Total number of workers
const_NUM_WORKERS = 10
# The knocked out time for a worker
const_DOWNTIME = 30


def Distance(x, y):
    return abs(x[0] - y[0]) + abs(x[1] - y[1])


def MoveOneStep(position, goal, rowsFirst=False):
    # Move only one cell. Outbound (to a berth) closes the column gap first, then
    # the row gap, so a worker approaches the berth from the south. Returning with
    # cargo closes the row gap first, so it leaves the north edge at once.
    # Together this stops workers walking through other berth cells.
    col, row = position
    colsDone, rowsDone = col == goal[0], row == goal[1]
    moveRow = (rowsFirst and not rowsDone) or colsDone
    if moveRow and not rowsDone:
        row += 1 if goal[1] > row else -1
    elif not colsDone:
        col += 1 if goal[0] > col else -1
    return (col, row)


# Environment of the Harbor
class Worker:
    def __init__(self, workerID):
        self.id = workerID
        self.position = const_WAREHOUSE
        self.carrying = False
        self.target = None  # name of the berth or None
        self.disabled = 0
        # model based memory: list of (startTick, endTick) of known storms
        self.stormMemory = []


class Harbour:
    def __init__(self, seed):
        # One seeded generator, everything random is pre drawn from it so that
        # both policies face exactly the same ships and storms for a seed
        self.rng = random.Random(seed)

        # storm schedule: 3 storms, one in each third of the run,
        # random start time inside a window so they never overlap
        self.storms = []
        for i in range(const_NUM_STORMS):
            start = self.rng.randint(40 + 200 * i, 180 + 200 * i)
            self.storms.append((start, start + const_STORM_LEN))

        # ship arrival schedule: next ship 6-10 ticks after the previous one
        # the draw picks which free berth (so both policies see the same ships)
        self.arrivals = {}
        tick = self.rng.randint(*const_SHIP_INTERVAL)
        while tick < const_TICKS:
            self.arrivals[tick] = self.rng.random()
            tick += self.rng.randint(*const_SHIP_INTERVAL)
        self.turnedAway = 0

        # cargo left on ship (0 = free berth)
        self.ships = {berthName: 0 for berthName in const_BERTH_NAMES}
        self.workers = [Worker(i) for i in range(const_NUM_WORKERS)]
        self.delivered = 0
        self.knockouts = 0
        self.tick = 0

    # Storm helpers
    def StormActive(self, t):
        return any(s <= t < e for s, e in self.storms)

    def LookoutWarning(self, t):
        # Lookout: simple reflex agent, it warns 5 ticks before a storm
        return any(t == s - const_WARNING_LEAD for s, _ in self.storms)

    # Quartermaster: assigns idle workers to berths
    def QuarterMaster(self):
        for w in self.workers:
            if w.disabled or w.carrying or w.target is not None:
                continue
            bestBerth, bestDistance = None, None
            for berthName in const_BERTH_NAMES:
                cargo = self.ships[berthName]
                assigned = sum(1 for x in self.workers if x.target == berthName)
                if cargo > assigned:
                    dist = Distance(w.position, const_BERTHS[berthName])
                    if bestBerth is None or dist < bestDistance:
                        bestBerth, bestDistance = berthName, dist
            w.target = bestBerth

    # One tick
    def RunTick(self, policy):
        tick = self.tick
        warning = self.LookoutWarning(tick)

        # a scheduled ship arrives and takes one of the free berths
        if tick in self.arrivals:
            freeBerth = [b for b in const_BERTH_NAMES if self.ships[b] == 0]
            if freeBerth:
                choice = freeBerth[int(self.arrivals[tick] * len(freeBerth))]
                self.ships[choice] = const_SHIP_CARGO
            else:
                self.turnedAway += 1  # all berths are busy

        self.QuarterMaster()

        for worker in self.workers:
            if worker.disabled:
                worker.disabled -= 1
                if worker.disabled == 0:
                    worker.position, worker.carrying, worker.target = const_WAREHOUSE, False, None
                continue
            policy(self, worker, tick, warning)

        # storm knock out check (end of tick)
        if self.StormActive(tick):
            for worker in self.workers:
                if not worker.disabled and any(worker.position == const_BERTHS[b] for b in const_UNSAFE):
                    self.knockouts += 1
                    worker.carrying, worker.target = False, None  # cargo lost
                    worker.disabled = const_DOWNTIME

        self.tick += 1  # once per tick, outside all loops

    def Run(self, policy):
        for _ in range(const_TICKS):
            self.RunTick(policy)
        return self.delivered, self.knockouts


# Shared Movement
def BasicWorkStep(environment, worker):
    # Carry, Collect, Deliver, Walk. Used in both policies
    if worker.carrying:
        if worker.position == const_WAREHOUSE:
            environment.delivered += 1
            worker.carrying = False
            # keep the same berth if it still has cargo, else ask QuarterMaster
            if worker.target is not None and environment.ships[worker.target] == 0:
                worker.target = None
        else:
            worker.position = MoveOneStep(worker.position, const_WAREHOUSE, rowsFirst=True)
        return

    if worker.target is None:
        return  # idle, wait at current cell

    goal = const_BERTHS[worker.target]

    if worker.position == goal:
        if environment.ships[worker.target] > 0:
            environment.ships[worker.target] -= 1  # collect ONE unit
            worker.carrying = True
        else:
            worker.target = None  # ship already emptied
    else:
        worker.position = MoveOneStep(worker.position, goal)


# Policy A: Baseline
def BaselinePolicy(environment, worker, tick, warning):
    # Fixed rule: go to the assigned berth, collect, deliver, repeat.
    # Ignores the Lookout's warning completely
    BasicWorkStep(environment, worker)


# Policy B: Model Based
def ModelBasedPolicy(environment, worker, tick, warning):
    # Remembers storm warnings. Memory is a list of (start, end) windows.
    # A warning at tick t means the storm is active during (t+5, t+5+15).
    # The warning is broadcast only once, so memory is what lets the worker
    # still avoid the storm several ticks later
    if warning:
        worker.stormMemory.append((tick + const_WARNING_LEAD, tick + const_WARNING_LEAD + const_STORM_LEN))
    worker.stormMemory = [(s, e) for s, e in worker.stormMemory if e > tick]  # forget old storms

    def HitsWindow(arrivalTick):
        # worker arrives at arrivalTick, collects at +1 and steps off at +2
        leaveTick = arrivalTick + 2
        return any(leaveTick >= s and arrivalTick < e for s, e in worker.stormMemory)

    # 1. standing on an unsafe berth and a remembered storm is about to start:
    #    leave now (carrying cargo if we already have it)
    if worker.position in [const_BERTHS[b] for b in const_UNSAFE]:
        if any(s - 1 <= tick < e for s, e in worker.stormMemory):
            if worker.carrying:
                worker.position = MoveOneStep(worker.position, const_WAREHOUSE, rowsFirst=True)
            else:
                worker.position = (worker.position[0], worker.position[1] + 1)  # step off the berth
                worker.target = None
            return

    # 2. heading to an unsafe berth that will be hit by the storm: re-plan
    if not worker.carrying and worker.target in const_UNSAFE:
        eta = tick + Distance(worker.position, const_BERTHS[worker.target]) - 1
        if HitsWindow(eta):
            worker.target = None
            bestBerth, bestDistance = None, None
            for berthName in const_BERTH_NAMES:
                if berthName in const_UNSAFE:
                    continue
                assigned = sum(1 for x in environment.workers if x.target == berthName)
                if environment.ships[berthName] > assigned:
                    dist = Distance(worker.position, const_BERTHS[berthName])
                    if bestBerth is None or dist < bestDistance:
                        bestBerth, bestDistance = berthName, dist
            worker.target = bestBerth
            if worker.target is None:
                return

    BasicWorkStep(environment, worker)


def RunExperiment(seeds=(11, 12, 13, 14, 15)):
    results = {}
    for name, policy in (("Baseline", BaselinePolicy), ("Model Based", ModelBasedPolicy)):
        rows = []
        for seed in seeds:
            environment = Harbour(seed)
            rows.append((seed, *environment.Run(policy)))
        results[name] = rows
    return results


if __name__ == "__main__":
    # repeatability check: same seed, same policy should give identical results
    a = Harbour(11).Run(BaselinePolicy)
    b = Harbour(11).Run(BaselinePolicy)
    assert a == b, "run not repeatable"
    print(f"Repeatability check (seed 11, baseline): {a} == {b}  OK\n")

    res = RunExperiment()
    print(f"{'Policy':<13}{'Seed':>5}{'Cargo delivered':>17}{'Storm knock-outs':>18}")
    for name, rows in res.items():
        for s, cargo, ko in rows:
            print(f"{name:<13}{s:>5}{cargo:>17}{ko:>18}")
        avg_c = sum(r[1] for r in rows) / len(rows)
        avg_k = sum(r[2] for r in rows) / len(rows)
        print(f"{name:<13}{'avg':>5}{avg_c:>17.2f}{avg_k:>18.2f}\n")