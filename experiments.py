import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import os
import time
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core import Sim, attacker_success
from detector import AdaptiveDetector, StaticDetector
from pool_sybil import bwh_sim, sybil_experiment

os.makedirs("results", exist_ok=True)
os.makedirs("figures", exist_ok=True)
REPS = int(os.environ.get("REPS", 100))
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)

def e1():
    rows = []
    configs = {
        "attacked (z=6)": dict(),
        "reference: checkpoint (max_reorg=4)": dict(max_reorg=4),
        "proposed: adaptive confirmations": dict(adaptive_z=True),
    }
    for q in [0.1, 0.2, 0.3, 0.4, 0.45, 0.51, 0.6]:
        for name, cfg in configs.items():
            for s in range(REPS):
                r = Sim(attack="51", attacker_share=q, seed=1000 + s, detector_cls=AdaptiveDetector,
                        **cfg).run(n_blocks=700, warmup=300)
                r.update(q=q, config=name, seed=s)
                rows.append(r)
        log(f"E1 q={q}")
    for name, cfg in configs.items():
        for s in range(REPS):
            sim = Sim(seed=5000 + s, detector_cls=AdaptiveDetector, pay_at_warmup=True, **cfg)
            r = sim.run(n_blocks=350, warmup=300)   
            r.update(q=0.0, config=name, seed=s)
            rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv("results/e1_51attack.csv", index=False)
    return df

def e2():
    rows = []
    configs = {
        "attacked (first-seen)": dict(),
        "reference: random tie-break": dict(tie_break="random"),
        "proposed: detect -> freshness rule": dict(detector_cls=AdaptiveDetector),
    }
    for q in [0.1, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45]:
        for name, cfg in configs.items():
            for s in range(REPS // 2):
                r = Sim(attack="selfish", attacker_share=q, seed=2000 + s, **cfg).run(n_blocks=1000, warmup=300)
                r.update(q=q, config=name, seed=s)
                rows.append(r)
        log(f"E2 q={q}")
    df = pd.DataFrame(rows)
    df.to_csv("results/e2_selfish.csv", index=False)
    rows = []
    for n in [10, 20, 50]:
        for d in [5, 10, 30]:
            for name, cfg in configs.items():
                for s in range(10):
                    r = Sim(n_nodes=n, delay_mean=d, attack="selfish", attacker_share=0.3,
                            seed=3000 + s, **cfg).run(n_blocks=1000, warmup=300)
                    r.update(n_nodes=n, delay=d, config=name)
                    rows.append(r)
    pd.DataFrame(rows).to_csv("results/e2b_selfish_robustness.csv", index=False)
    return df

def e3():
    rows = []
    for delay in [5, 10, 30, 60]:
        for det_name, det in [("adaptive (EWMA)", AdaptiveDetector), ("static thresholds", StaticDetector)]:
            for s in range(REPS // 2):
                r = Sim(delay_mean=delay, seed=7000 + s, detector_cls=det,
                        detector_kwargs=dict(react=False)).run(n_blocks=800, warmup=300)
                rows.append(dict(delay=delay, detector=det_name, truth=0, alert=r["alert"],
                                 detect_time=None))
                for q in (0.2, 0.3, 0.4):
                    r = Sim(delay_mean=delay, attack="selfish", attacker_share=q, seed=8000 + s,
                            detector_cls=det, detector_kwargs=dict(react=False)).run(n_blocks=800, warmup=300)
                    tp_alert = r["alert"] and not r["false_alarm"]
                    rows.append(dict(delay=delay, detector=det_name, truth=1, alert=tp_alert,
                                     detect_time=r["detect_time"] if tp_alert else None))
        log(f"E3 delay={delay}")
    df = pd.DataFrame(rows)
    df.to_csv("results/e3_detection_raw.csv", index=False)
    out = []
    for (delay, det), g in df.groupby(["delay", "detector"]):
        tp = ((g.truth == 1) & g.alert).sum()
        fp = ((g.truth == 0) & g.alert).sum()
        fn = ((g.truth == 1) & ~g.alert).sum()
        tn = ((g.truth == 0) & ~g.alert).sum()
        out.append(dict(delay=delay, detector=det, precision=tp / max(tp + fp, 1), recall=tp / max(tp + fn, 1),
                        fpr=fp / max(fp + tn, 1),
                        mean_detect_blocks=g.detect_time.dropna().mean() / 600 if g.detect_time.notna().any() else np.nan))
    res = pd.DataFrame(out)
    res.to_csv("results/e3_detection.csv", index=False)
    return res

def e4():
    rows = []
    for a in [0.01, 0.02, 0.05, 0.10]:
        for s in range(10):
            for name, kw in [("no attack", dict(attacks=False)),
                             ("attack", dict(attacks=True)),
                             ("attack + proposed detection", dict(attacks=True, defend=True))]:
                r = bwh_sim(attacker_power=a, seed=s, **kw)
                r.update(attacker_power=a, scenario=name)
                rows.append(r)
        log(f"E4 a={a}")
    fp = []
    for s in range(30):
        r = bwh_sim(attacker_power=0.05, attacks=False, defend=True, seed=100 + s)
        fp.append(r["banned_at"] is not None)
    pd.DataFrame(rows).to_csv("results/e4_bwh.csv", index=False)
    return pd.DataFrame(rows), float(np.mean(fp))

def e5():
    rows = []
    for ns in [0, 100, 200, 400, 800, 1600]:
        for d in ["none", "diversity", "diversity+eviction"]:
            for s in range(5):
                r = sybil_experiment(ns, d, seed=s)
                r.update(n_sybil=ns, sybil_ratio=ns / (200 + ns), defense=d)
                rows.append(r)
        log(f"E5 sybil={ns}")
    df = pd.DataFrame(rows)
    df.to_csv("results/e5_sybil.csv", index=False)
    return df

if __name__ == "__main__":
    d1, d2, d3 = e1(), e2(), e3()
    d4, bwh_fpr = e4()
    d5 = e5()

    g = d1[d1.q > 0].groupby(["config", "q"]).ds_success.mean().reset_index()
    plt.figure(figsize=(7, 4.5))
    for name, gg in g.groupby("config"):
        plt.plot(gg.q, gg.ds_success, "o-", label=name)
    qs = np.linspace(0.05, 0.6, 50)
    plt.plot(qs, [attacker_success(q, 6) for q in qs], "k--", label="analytic (Rosenfeld, z=6)")
    plt.xlabel("attacker hash share q"); plt.ylabel("double-spend success rate")
    plt.title("51% attack: success rate"); plt.legend(fontsize=8); plt.grid(alpha=.3)
    plt.tight_layout(); plt.savefig("figures/fig1_51attack_success.png", dpi=150); plt.close()

    g = d2.groupby(["config", "q"]).attacker_share.mean().reset_index()
    plt.figure(figsize=(7, 4.5))
    for name, gg in g.groupby("config"):
        plt.plot(gg.q, gg.attacker_share, "o-", label=name)
    plt.plot([0.1, 0.45], [0.1, 0.45], "k--", label="fair share (honest mining)")
    plt.xlabel("attacker hash share q"); plt.ylabel("attacker share of main-chain blocks")
    plt.title("Selfish mining: attacker revenue"); plt.legend(fontsize=8); plt.grid(alpha=.3)
    plt.tight_layout(); plt.savefig("figures/fig2_selfish_revenue.png", dpi=150); plt.close()

    g = d2.groupby(["config", "q"]).orphan_rate.mean().reset_index()
    plt.figure(figsize=(7, 4.5))
    for name, gg in g.groupby("config"):
        plt.plot(gg.q, gg.orphan_rate, "o-", label=name)
    plt.xlabel("attacker hash share q"); plt.ylabel("orphan rate")
    plt.title("Selfish mining: orphan rate (the signal the detector watches)")
    plt.legend(fontsize=8); plt.grid(alpha=.3)
    plt.tight_layout(); plt.savefig("figures/fig3_selfish_orphans.png", dpi=150); plt.close()

    fig, ax = plt.subplots(1, 3, figsize=(12, 3.8))
    for det, gg in d3.groupby("detector"):
        ax[0].plot(gg.delay, gg.recall, "o-", label=det)
        ax[1].plot(gg.delay, gg.fpr, "o-", label=det)
        ax[2].plot(gg.delay, gg.precision, "o-", label=det)
    for a_, t_ in zip(ax, ["Recall", "False-positive rate", "Precision"]):
        a_.set_title(t_); a_.set_xlabel("mean network delay (s)"); a_.grid(alpha=.3)
    ax[0].legend(fontsize=8)
    plt.tight_layout(); plt.savefig("figures/fig4_detection_quality.png", dpi=150); plt.close()

    g = d4.groupby(["scenario", "attacker_power"])[["honest_rev", "attacker_rev"]].mean().reset_index()
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    for name, gg in g.groupby("scenario"):
        ax[0].plot(gg.attacker_power, gg.honest_rev, "o-", label=name)
        ax[1].plot(gg.attacker_power, gg.attacker_rev, "o-", label=name)
    ax[0].set_title("Honest members' revenue per unit power (ideal = 1)")
    ax[1].set_title("Attacker's revenue per unit power")
    for a_ in ax:
        a_.set_xlabel("attacker power (fraction of network)"); a_.grid(alpha=.3)
    ax[0].legend(fontsize=8)
    plt.tight_layout(); plt.savefig("figures/fig5_block_withholding.png", dpi=150); plt.close()

    g = d5.groupby(["defense", "sybil_ratio"])[["coverage", "t_median"]].mean().reset_index()
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    for name, gg in g.groupby("defense"):
        ax[0].plot(gg.sybil_ratio, gg.coverage, "o-", label=name)
        ax[1].plot(gg.sybil_ratio, gg.t_median, "o-", label=name)
    ax[0].set_title("Share of honest nodes that receive a block"); ax[1].set_title("Median propagation time (s)")
    for a_ in ax:
        a_.set_xlabel("Sybil share of address table"); a_.grid(alpha=.3)
    ax[0].legend(fontsize=8)
    plt.tight_layout(); plt.savefig("figures/fig6_sybil.png", dpi=150); plt.close()

    pd.set_option("display.width", 200); pd.set_option("display.max_columns", 30)
    print("\n=== E1: 51% attack (mean over runs) ===")
    t1 = d1.groupby(["config", "q"]).agg(success=("ds_success", "mean"), victim_loss=("victim_loss", "mean"),
        attacker_net=("attacker_net", "mean"), confirm_delay_s=("confirm_delay", "mean"),
        refused=("refused", "mean"), detect_rate=("alert", "mean")).round(3)
    print(t1.to_string()); t1.to_csv("results/summary_e1.csv")
    print("\n=== E2: selfish mining ===")
    t2 = d2.groupby(["config", "q"]).agg(attacker_share=("attacker_share", "mean"), orphan_rate=("orphan_rate", "mean"),
        detect_rate=("alert", "mean"), detect_time_s=("detect_time", "mean")).round(3)
    print(t2.to_string()); t2.to_csv("results/summary_e2.csv")
    print("\n=== E3: detection quality ===")
    print(d3.round(3).to_string())
    print("\n=== E4: block withholding ===")
    t4 = d4.groupby(["scenario", "attacker_power"]).agg(honest_rev=("honest_rev", "mean"), attacker_rev=("attacker_rev", "mean"),
        banned_at=("banned_at", "mean"), attacker_banned=("attacker_banned", "mean"), honest_banned=("honest_banned", "sum")).round(3)
    print(t4.to_string()); t4.to_csv("results/summary_e4.csv")
    print(f"False-positive rate of BWH detector on honest pool: {bwh_fpr:.3f}")
    print("\n=== E5: Sybil ===")
    t5 = d5.groupby(["defense", "n_sybil"])[["coverage", "t_median", "peer_precision", "peer_recall"]].mean().round(3)
    print(t5.to_string()); t5.to_csv("results/summary_e5.csv")
    log("done")