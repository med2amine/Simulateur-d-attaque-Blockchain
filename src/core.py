import heapq
import math
import random
from dataclasses import dataclass


@dataclass
class Block:
    id: int
    parent: int
    height: int
    miner: int
    time: float          
    tx: str = ""         



def attacker_success(q, z):
    if q >= 0.5:
        return 1.0
    if z <= 0:
        return 1.0
    p = 1.0 - q
    s = sum(math.comb(m + z - 1, m) * (p ** z * q ** m - p ** m * q ** z)
            for m in range(z + 1))
    return max(0.0, 1.0 - s)


def adaptive_confirmations(q_hat, base=6, eps=1e-3, zmax=60):
    if q_hat >= 0.5:
        return 10 ** 9
    z = base
    while z < zmax and attacker_success(q_hat, z) > eps:
        z += 1
    return z

class Node:
    is_attacker = False

    def __init__(self, nid, share, sim):
        self.id, self.share, self.sim = nid, share, sim
        self.known = {0}
        self.tip = 0
        self.pending = {}          
        self.recv = {0: 0.0}       
        self.speed = 1.0          

    def broadcast(self, b, t):
        for o in self.sim.nodes:
            if o.id != self.id:
                self.sim.push(t + self.speed * self.sim.delay(), (o.id, b.id))

    def on_receive(self, b, t):
        if b.id in self.known:
            return
        if b.parent not in self.known:          
            self.pending.setdefault(b.parent, []).append(b)
            return
        self.known.add(b.id)
        self.recv[b.id] = t
        self.consider(b, t)
        if self is self.sim.observer:
            self.sim.observe(b, t)
        for c in self.pending.pop(b.id, []):
            self.on_receive(c, t)

    def consider(self, b, t):
        sim = self.sim
        cur = sim.blocks[self.tip]
        if b.height > cur.height:
            d = sim.reorg_depth(self.tip, b.id)
            if sim.max_reorg is not None and d > sim.max_reorg:
                sim.rejected += 1               
                return
            self.set_tip(b.id, t, d)
        elif b.height == cur.height and b.id != self.tip:
            tb = sim.tie_break
            if (tb == "random" and sim.rng.random() < 0.5) or \
               (tb == "fresh" and b.time > cur.time):
                self.set_tip(b.id, t, sim.reorg_depth(self.tip, b.id))

    def set_tip(self, bid, t, depth):
        self.tip = bid
        if self is self.sim.observer:
            if depth > 0:
                self.sim.reorgs.append((t, depth))
            self.sim.check_shipping(t)

    def mine(self, t):
        sim = self.sim
        tx = ""
        if sim.pay_pending and not self.is_attacker:
            tx, sim.pay_pending = "PAY", False
        b = sim.new_block(self.tip, self.id, t, tx)
        if tx:
            sim.pay_block = b.id
        self.known.add(b.id)
        self.recv[b.id] = t
        self.set_tip(b.id, t, 0)
        self.broadcast(b, t)
        if self is sim.observer:
            sim.observe(b, t)

class SelfishNode(Node):
    is_attacker = True

    def __init__(self, nid, share, sim):
        super().__init__(nid, share, sim)
        self.active = False
        self.priv = []             
        self.racing = False     
        self.speed = 0.2          

    def mine(self, t):
        if not self.active:
            return super().mine(t)
        if not self.priv and self.racing:     
            self.racing = False
            return super().mine(t)
        parent = self.priv[-1].id if self.priv else self.tip
        b = self.sim.new_block(parent, self.id, t)
        self.known.add(b.id)
        self.recv[b.id] = t
        self.priv.append(b)                    

    def publish(self, blocks, t):
        for b in blocks:
            self.broadcast(b, t)
        self.tip = blocks[-1].id

    def consider(self, b, t):
        if not self.active:
            return super().consider(b, t)
        pub = self.sim.blocks[self.tip]
        if b.height <= pub.height:
            return
        if not self.priv:                     
            self.tip, self.racing = b.id, False
            return
        delta = self.priv[-1].height - b.height   
        if delta < 0:                          
            self.priv, self.tip, self.racing = [], b.id, False
        elif delta == 0:                      
            self.publish(self.priv, t)
            self.priv, self.racing = [], True
        elif delta == 1:                        
            self.publish(self.priv, t)
            self.priv, self.racing = [], False
        else:                                  
            k = [x for x in self.priv if x.height <= b.height]
            self.publish(k, t)
            self.priv = self.priv[len(k):]

class DoubleSpender(Node):
    is_attacker = True

    def __init__(self, nid, share, sim):
        super().__init__(nid, share, sim)
        self.mode = "idle"         
        self.priv, self.fork = [], None
        self.speed = 0.2

    def consider(self, b, t):
        if self.mode == "armed" and b.tx == "PAY":
            self.mode, self.fork, self.priv = "attack", b.parent, []
        super().consider(b, t)
        self.check(t)

    def mine(self, t):
        if self.mode != "attack":
            return super().mine(t)
        parent = self.priv[-1].id if self.priv else self.fork
        b = self.sim.new_block(parent, self.id, t, "DS" if not self.priv else "")
        self.known.add(b.id)
        self.recv[b.id] = t
        self.priv.append(b)
        self.check(t)

    def check(self, t):
        if self.mode != "attack":
            return
        sim = self.sim
        pub_h = sim.blocks[self.tip].height
        priv_h = self.priv[-1].height if self.priv else sim.blocks[self.fork].height
        if sim.shipped_at is not None and self.priv and priv_h > pub_h:
            for b in self.priv:                 
                self.broadcast(b, t)
            self.tip = self.priv[-1].id
            self.mode = "done"
            sim.attack_resolved()
        elif pub_h - priv_h > sim.give_up:      
            self.mode = "done"
            sim.attack_resolved()

class Sim:
    def __init__(self, n_nodes=20, attack=None, attacker_share=0.0, interval=600.0,
                 delay_mean=10.0, seed=0, tie_break="first", max_reorg=None,
                 z_confirm=6, adaptive_z=False, give_up=10, pay_at_warmup=False,
                 detector_cls=None, detector_kwargs=None, tx_value=20.0, reward=1.0):
        self.rng = random.Random(seed)
        self.blocks = [Block(0, -1, 0, -1, 0.0)]
        self.events, self.seq, self.t, self.mined = [], 0, 0.0, 0
        self.interval, self.delay_mean = interval, delay_mean
        self.tie_break, self.max_reorg = tie_break, max_reorg
        self.z_confirm, self.adaptive_z, self.give_up = z_confirm, adaptive_z, give_up
        self.attack, self.attack_start = attack, None
        self.tx_value, self.reward = tx_value, reward
        self.rejected, self.reorgs = 0, []
        self.pay_at_warmup = pay_at_warmup      
        self.pay_pending, self.pay_block, self.shipped_at = False, None, None
        self.z_req = None                    
        self.stop_mining_at = 10 ** 9
        n_h = n_nodes - 1 if attack else n_nodes
        a = attacker_share if attack else 0.0
        self.nodes = [Node(i, (1 - a) / n_h, self) for i in range(n_h)]
        if attack == "selfish":
            self.nodes.append(SelfishNode(n_h, a, self))
        elif attack == "51":
            self.nodes.append(DoubleSpender(n_h, a, self))
        self.observer = self.nodes[0]            
        self.weights = [nd.share for nd in self.nodes]
        self.detector = detector_cls(self, **(detector_kwargs or {})) if detector_cls else None

    def delay(self):
        return 1.0 + self.rng.expovariate(1.0 / self.delay_mean)

    def push(self, t, data):
        self.seq += 1
        heapq.heappush(self.events, (t, self.seq, data))

    def new_block(self, parent, miner, t, tx=""):
        b = Block(len(self.blocks), parent, self.blocks[parent].height + 1, miner, t, tx)
        self.blocks.append(b)
        return b

    def reorg_depth(self, a, b):
        A, B, d = self.blocks[a], self.blocks[b], 0
        while A.id != B.id:
            if A.height >= B.height:
                A = self.blocks[A.parent]
                d += 1
            else:
                B = self.blocks[B.parent]
        return d

    def is_ancestor(self, a, b):
        x = self.blocks[b]
        while x.height > self.blocks[a].height:
            x = self.blocks[x.parent]
        return x.id == a

    def main_chain(self):
        out, b = [], self.blocks[self.observer.tip]
        while b.id != 0:
            out.append(b)
            b = self.blocks[b.parent]
        return out[::-1]

    def observe(self, b, t):
        if self.detector:
            self.detector.observe(b, t)

    def observer_max_share(self, window=200):
        counts, b, n = {}, self.blocks[self.observer.tip], 0
        while b.id != 0 and n < window:
            counts[b.miner] = counts.get(b.miner, 0) + 1
            b, n = self.blocks[b.parent], n + 1
        return max(counts.values()) / n if n else 0.0

    def required_conf(self):
        if not self.adaptive_z:
            return self.z_confirm
        return adaptive_confirmations(self.observer_max_share() + 0.05, self.z_confirm)

    def check_shipping(self, t):
        if self.pay_block is None or self.shipped_at is not None:
            return
        tip, pb = self.blocks[self.observer.tip], self.blocks[self.pay_block]
        if not self.is_ancestor(pb.id, tip.id):
            return
        if self.z_req is None:     
            self.z_req = self.required_conf()  
        if tip.height - pb.height + 1 >= self.z_req:
            self.shipped_at = t                  

    def start_attack(self, t):
        self.attack_start = t
        a = self.nodes[-1]
        if self.attack == "selfish":
            a.active = True
        elif self.attack == "51":
            a.mode, self.pay_pending = "armed", True

    def attack_resolved(self):
        self.stop_mining_at = min(self.stop_mining_at, self.mined + 100)

    def run(self, n_blocks=800, warmup=300):
        self.warmup = warmup
        self.stop_mining_at = n_blocks
        self.push(self.rng.expovariate(1.0 / self.interval), None)
        while self.events:
            t, _, data = heapq.heappop(self.events)
            self.t = t
            if data is None:
                self.mined += 1
                self.rng.choices(self.nodes, self.weights)[0].mine(t)
                if self.mined < self.stop_mining_at:
                    self.push(t + self.rng.expovariate(1.0 / self.interval), None)
            else:
                nid, bid = data
                self.nodes[nid].on_receive(self.blocks[bid], t)
            if self.pay_at_warmup and self.blocks[self.observer.tip].height >= warmup:
                self.pay_pending, self.pay_at_warmup = True, False
            if self.attack and self.attack_start is None and \
                    self.blocks[self.observer.tip].height >= warmup:
                self.start_attack(t)
        return self.summary()

    def summary(self):
        chain = self.main_chain()
        post = [b for b in chain if b.height > self.warmup]
        known = len(self.observer.known) - 1
        att_main = [b for b in post if self.nodes[b.miner].is_attacker]
        att_total = sum(1 for b in self.blocks[1:] if self.nodes[b.miner].is_attacker)
        att_wasted = att_total - sum(1 for b in chain if self.nodes[b.miner].is_attacker)
        ds = None
        if self.attack == "51":
            ds = bool(self.shipped_at is not None and self.pay_block is not None and
                      not self.is_ancestor(self.pay_block, self.observer.tip))
        det = self.detector.first_alert if self.detector else None
        r = dict(
            orphan_rate=(known - len(chain)) / max(known, 1),
            attacker_share=len(att_main) / max(len(post), 1),
            max_reorg=max([d for _, d in self.reorgs], default=0),
            rejected_reorgs=self.rejected,
            ds_success=ds,
            victim_loss=self.tx_value if ds else 0.0,
            attacker_net=(self.tx_value if ds else 0.0) - self.reward * att_wasted
            if self.attack == "51" else None,
            confirm_delay=(self.shipped_at - self.blocks[self.pay_block].time)
            if (self.shipped_at is not None and self.pay_block) else None,
            refused=bool(self.z_req is not None and self.z_req >= 10 ** 9),
            alert=det is not None,
            detect_time=(det[0] - self.attack_start)
            if (det and self.attack_start is not None) else None,
            false_alarm=bool(det and (self.attack_start is None or det[0] < self.attack_start)),
            detector_cpu=self.detector.cpu if self.detector else 0.0,
        )
        return r