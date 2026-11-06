import math
import time


class AdaptiveDetector:
    adaptive = True

    def __init__(self, sim, W=60, step=10, alpha=0.1, theta=5.0, warm=200,
                 deep=5, orphan_thr=0.05, react=True):
        self.sim, self.W, self.step, self.alpha = sim, W, step, alpha
        self.theta, self.warm, self.deep = theta, warm, deep
        self.orphan_thr, self.react = orphan_thr, react
        self.log, self.n, self.strikes = [], 0, 0
        self.stats = {}               
        self.first_alert = None        
        self.cpu = 0.0

    def features(self):
        sim = self.sim
        win = self.log[-self.W:]
        chain = sim.main_chain()
        main = {b.id for b in chain}
        tip_h = chain[-1].height if chain else 0
        settled = [(b, t) for b, t in win if b.height <= tip_h - 2]
        orphan = (1 - sum(b.id in main for b, _ in settled) / len(settled)) if settled else 0.0
        lags = {}
        for b, t in win:
            if b.miner != sim.observer.id:
                lags.setdefault(b.miner, []).append(t - b.time)
        per = [sum(v) / len(v) for v in lags.values() if len(v) >= 3]
        allv = [x for v in lags.values() for x in v]
        lag = max(per) if per else (sum(allv) / len(allv) if allv else 0.0)
        t0 = win[0][1]
        depth = max([d for tt, d in sim.reorgs if tt >= t0], default=0)
        return dict(orphan=orphan, lag=lag, depth=depth)

    def observe(self, b, t):
        self.log.append((b, t))
        self.n += 1
        if self.n >= self.W and self.n % self.step == 0:
            c0 = time.perf_counter()
            self.evaluate(t)
            self.cpu += time.perf_counter() - c0

    def evaluate(self, t):
        f = self.features()
        z = {}
        for k in ("orphan", "lag"):
            mu, var = self.stats.get(k, (f[k], 0.0))
            floor = (0.3 * abs(mu) + (0.01 if k == "orphan" else 1.0)) ** 2
            z[k] = (f[k] - mu) / math.sqrt(max(var, floor))
        deep = f["depth"] >= self.deep
        warmed = self.n >= self.warm
        if self.adaptive:
            hit = warmed and (deep or (z["lag"] > self.theta and z["orphan"] > 1.0))
        else:
            hit = warmed and (deep or f["orphan"] > self.orphan_thr)
        if not hit or not warmed:     
            for k in ("orphan", "lag"):
                mu, var = self.stats.get(k, (f[k], 0.0))
                d = f[k] - mu
                mu += self.alpha * d
                var = (1 - self.alpha) * (var + self.alpha * d * d)
                self.stats[k] = (mu, var)
        self.strikes = self.strikes + 1 if hit else 0
        if self.first_alert is None and (deep or self.strikes >= 2):
            kind = "reorg_51" if deep else "selfish"
            self.first_alert = (t, kind)
            if self.react and kind == "selfish":
                self.sim.tie_break = "fresh"


class StaticDetector(AdaptiveDetector):
    adaptive = False
    def __init__(self, sim, **kw):
        kw.setdefault("react", False)
        super().__init__(sim, **kw)