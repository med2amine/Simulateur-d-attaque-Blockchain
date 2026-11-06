import heapq
import random
import numpy as np
from scipy.stats import poisson

def bwh_sim(m_honest=19, honest_power=0.01, attacker_power=0.05, attacks=True,
            defend=False, r=200, steps=60000, dt=0.1, alpha=1e-6, seed=0):

    rng = np.random.default_rng(seed)
    pw = np.array([honest_power] * m_honest + [attacker_power])
    n, A = len(pw), len(pw) - 1
    shares = rng.poisson(pw * r * dt, size=(steps, n))
    cum = np.cumsum(shares, axis=0)
    submit = pw.copy()
    if attacks:
        submit[A] = 0.0                      
    k_per_step = rng.poisson(submit.sum() * dt, size=steps)
    reward = np.zeros(n)
    blocks_found = np.zeros(n)
    banned_at, last_idx = None, -1
    alive = np.ones(n, bool)
    for s in np.nonzero(k_per_step)[0]:
        k = k_per_step[s]
        block = cum[s] - (cum[last_idx] if last_idx >= 0 else 0)
        block = block * alive
        tot = block.sum()
        if tot > 0:
            reward += k * block / tot
        last_idx = s
        p_alive = submit * alive
        if p_alive.sum() > 0:
            finder = rng.choice(n, size=k, p=p_alive / p_alive.sum())
            for f in finder:
                blocks_found[f] += 1
        if defend and banned_at is None:
            S = cum[s] - 0
            mu = S / r
            pv = poisson.cdf(blocks_found, np.maximum(mu, 1e-12))
            flag = (pv < alpha) & (mu > 3)
            if flag.any():
                for i in np.nonzero(flag)[0]:
                    alive[i] = False
                banned_at = (s * dt, [int(i) for i in np.nonzero(flag)[0]])
    T = steps * dt
    return dict(
        honest_rev=float(reward[:m_honest].sum() / (m_honest * honest_power) / T),
        attacker_rev=float(reward[A] / pw[A] / T),
        attacker_share_of_pool_reward=float(reward[A] / max(reward.sum(), 1e-12)),
        banned_at=None if banned_at is None else banned_at[0],
        banned_ids=None if banned_at is None else banned_at[1],
        honest_banned=0 if banned_at is None else sum(i != A for i in banned_at[1]),
        attacker_banned=banned_at is not None and A in banned_at[1],
    )

class P2P:

    def __init__(self, n_h=200, n_s=0, k=8, n_prefix=200, sybil_prefix=4, seed=0):
        self.rng = random.Random(seed)
        self.n_h, self.n_s, self.k = n_h, n_s, k
        self.N = n_h + n_s
        self.prefix = [self.rng.randrange(n_prefix) for _ in range(n_h)] + \
                      [1000 + self.rng.randrange(sybil_prefix) for _ in range(n_s)]
        self.out = {i: [] for i in range(n_h)}
        self.lat = {}

    def pick_peers(self, i, cap=None, exclude=()):
        chosen, cnt = [], {}
        cand = list(range(self.N))
        self.rng.shuffle(cand)
        for j in cand:
            if j == i or j in exclude or j in chosen:
                continue
            if cap is not None and cnt.get(self.prefix[j], 0) >= cap:
                continue
            chosen.append(j)
            cnt[self.prefix[j]] = cnt.get(self.prefix[j], 0) + 1
            if len(chosen) == self.k:
                break
        return chosen

    def connect(self, cap=None):
        for i in range(self.n_h):
            self.out[i] = self.pick_peers(i, cap)
        self._rebuild()

    def _rebuild(self):
        self.adj = {i: set() for i in range(self.N)}
        for i, peers in self.out.items():
            for j in peers:
                self.adj[i].add(j)
                self.adj[j].add(i)
                key = (min(i, j), max(i, j))
                if key not in self.lat:
                    self.lat[key] = self.rng.uniform(0.05, 0.2)

    def propagate(self, origin):
        dist = {origin: 0.0}
        pq = [(0.0, origin)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, 1e18) or u >= self.n_h:  
                continue
            for v in self.adj[u]:
                nd = d + self.lat[(min(u, v), max(u, v))]
                if nd < dist.get(v, 1e18):
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        return dist

    def measure(self, n_blocks=20):
        reach = np.zeros(self.N)
        covs, t50s = [], []
        for _ in range(n_blocks):
            o = self.rng.randrange(self.n_h)
            dist = self.propagate(o)
            honest_d = sorted(d for v, d in dist.items() if v < self.n_h)
            covs.append(len(honest_d) / self.n_h)
            t50s.append(honest_d[len(honest_d) // 2])
            for v in dist:
                if v < self.n_h:         
                    reach[v] += 1
        return dict(coverage=float(np.mean(covs)), t_median=float(np.mean(t50s))), reach / n_blocks

    def relay_failure_detection(self, reach, thr=0.2):
        """Peer j is suspicious for node i if j delivered < thr of the blocks."""
        tp = fp = fn = 0
        flags = {}
        for i in range(self.n_h):
            flags[i] = [j for j in self.out[i] if reach[j] < thr]
            for j in self.out[i]:
                is_s = j >= self.n_h
                f = reach[j] < thr
                tp += f and is_s
                fp += f and not is_s
                fn += (not f) and is_s
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        return flags, prec, rec

    def diversity_detection(self, min_same=3):
        tp = fp = fn = 0
        for i in range(self.n_h):
            cnt = {}
            for j in self.out[i]:
                cnt[self.prefix[j]] = cnt.get(self.prefix[j], 0) + 1
            alert = max(cnt.values()) >= min_same
            at_risk = sum(j >= self.n_h for j in self.out[i]) >= self.k / 2
            tp += alert and at_risk
            fp += alert and not at_risk
            fn += (not alert) and at_risk
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 1.0
        return prec, rec

    def evict_and_reselect(self, flags, cap=None):
        for i, bad in flags.items():
            if bad:
                keep = [j for j in self.out[i] if j not in bad]
                banned = set(bad)
                cnt = {}
                for j in keep:
                    cnt[self.prefix[j]] = cnt.get(self.prefix[j], 0) + 1
                cand = list(range(self.N))
                self.rng.shuffle(cand)
                for j in cand:
                    if len(keep) >= self.k:
                        break
                    if j == i or j in keep or j in banned:
                        continue
                    if cap is not None and cnt.get(self.prefix[j], 0) >= cap:
                        continue
                    keep.append(j)
                    cnt[self.prefix[j]] = cnt.get(self.prefix[j], 0) + 1
                self.out[i] = keep
        self._rebuild()


def sybil_experiment(n_s, defense, n_h=200, seed=0):
    net = P2P(n_h=n_h, n_s=n_s, seed=seed)
    cap = 1 if defense != "none" else None
    net.connect(cap)
    m, reach = net.measure()
    flags, p_peer, r_peer = net.relay_failure_detection(reach)
    p_div, r_div = net.diversity_detection()
    if defense == "diversity+eviction":
        for _ in range(3):                      
            net.evict_and_reselect(flags, cap)
            m, reach = net.measure()
            flags, _, _ = net.relay_failure_detection(reach)
    m.update(peer_precision=p_peer, peer_recall=r_peer, div_precision=p_div, div_recall=r_div)
    return m