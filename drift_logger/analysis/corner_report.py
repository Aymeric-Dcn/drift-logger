"""
Corner-by-corner and overall comparisons of every pass through every corner:
success rate, which techniques work where, what separates the good attempts from
the bad ones, and short written insights in French and English.
"""
import math
from collections import Counter

import numpy as np

T_FR = {"transition": "enchaînement", "handbrake": "frein à main", "feint": "appel-contre-appel", "clutch": "clutch kick", "brake": "freinage",
        "lift": "lever de pied", "power": "gaz", "other": "autre", "grip": "en grip"}
T_EN = {"transition": "transition", "handbrake": "handbrake", "feint": "feint", "clutch": "clutch kick", "brake": "braking",
        "lift": "lift-off", "power": "power over", "other": "other", "grip": "in grip"}
R_FR = {"too_fast": "entrée trop rapide", "rear_lock": "arrière bloqué", "lift": "pied levé", "late_cs": "contre-braquage tardif",
        "over_angle": "trop d'angle", "line": "placement", "exit_wide": "sortie trop large", "understeer": "sous-virage"}
R_EN = {"too_fast": "too fast on entry", "rear_lock": "rear lock", "lift": "foot off", "late_cs": "late counter-steer",
        "over_angle": "too much angle", "line": "line / placement", "exit_wide": "ran wide on exit", "understeer": "understeer"}


def _cum_dist(x, z):
    d = [0.0]
    for i in range(1, len(x)):
        step = math.hypot(x[i] - x[i - 1], z[i] - z[i - 1])
        d.append(d[-1] + (step if step < 30 else 0.0))
    return d


def corner_passes(session, corners):
    """One record per pass through each corner: a drift attempt or a grip pass."""
    tr = session["trace"]
    t, pos, spd, brk, seg = tr["t"], tr["pos"], tr["speed"], tr["brk"], tr["seg"]
    dist = _cum_dist(tr["x"], tr["z"])
    sid = session["summary"]["id"]
    impacts = [e for e in session["events"] if e["type"] == "impact"]
    out = []
    for c in corners:
        inside = [i for i, p in enumerate(pos) if c["pos0"] <= p <= c["pos1"]]
        runs, cur = [], []
        for i in inside:
            if cur and (t[i] - t[cur[-1]] > 1.0 or seg[i] != seg[cur[-1]]):
                runs.append(cur)
                cur = []
            cur.append(i)
        if cur:
            runs.append(cur)
        for run in runs:
            i0, i1 = run[0], run[-1]
            ta, tb = t[i0], t[i1]
            j, bdist = i0, None
            while j > 0 and dist[i0] - dist[j - 1] < 150 and seg[j - 1] == seg[i0]:
                j -= 1
            for k in range(j, i0 + 1):
                if brk[k] > 0.3:
                    bdist = round(dist[i0] - dist[k], 0)
                    break
            rec = {"sid": sid, "corner": c["id"], "t": round(ta, 1), "entry": spd[i0],
                   "min_speed": min(spd[i0:i1 + 1]), "brake_dist": bdist}
            ds = [d for d in session["drifts"] if d["t0"] <= tb + 0.3 and d["t1"] >= ta - 0.8]
            if ds:
                d = max(ds, key=lambda d: min(d["t1"], tb) - max(d["t0"], ta))
                rec.update({"kind": "drift", "technique": d["technique"], "flags": d["flags"],
                            "entry": d["entry"], "avg_angle": d["avg_angle"], "max_angle": d["max_angle"],
                            "lift_pct": d["lift_pct"], "thr_mid": d["thr_mid"], "stabs": d["stabs"],
                            "cs_delay": d["cs_delay"], "score": d["score"], "outcome": d["outcome"],
                            "success": d["success"], "reasons": list(d["reasons"]), "t": d["t0"]})
            else:
                hit = [e for e in impacts if ta - 0.3 <= e["t"] <= tb + 0.8]
                heavy = [e for e in hit if e.get("loss", 99) > 10]
                rec.update({"kind": "grip", "technique": "grip", "flags": {}, "avg_angle": 0, "max_angle": 0,
                            "lift_pct": None, "thr_mid": None, "stabs": 0, "cs_delay": None,
                            "score": 35 if heavy else (90 if not hit else 80),
                            "outcome": "impact" if heavy else ("exit_touch" if hit else "clean"),
                            "success": not heavy, "reasons": ["understeer"] if heavy else []})
            out.append(rec)
    return out


def _mean(v):
    v = [x for x in v if x is not None]
    return round(float(np.mean(v)), 0) if v else None


def corner_stats(c, attempts):
    a = attempts
    n = len(a)
    ok = [x for x in a if x["success"]]
    ko = [x for x in a if not x["success"]]
    ok_entry = _mean([x["entry"] for x in ok])
    # entry far above what works here -> too fast
    for x in ko:
        if ok_entry is not None and x["entry"] > ok_entry + 10 and "too_fast" not in x["reasons"]:
            x["reasons"].insert(0, "too_fast")
    tech = {}
    for x in a:
        s = tech.setdefault(x["technique"], {"n": 0, "ok": 0, "score": []})
        s["n"] += 1
        s["ok"] += x["success"]
        s["score"].append(x["score"])
    for s in tech.values():
        s["rate"] = round(100.0 * s["ok"] / s["n"], 0)
        s["score"] = round(float(np.mean(s["score"])), 0)
    reasons = Counter(r for x in ko for r in x["reasons"][:1])
    dom, dom_n = (Counter(x["technique"] for x in a).most_common(1) or [(None, 0)])[0]
    st = {
        "attempts": n, "success": len(ok), "rate": round(100.0 * len(ok) / n, 0) if n else None,
        "drift_share": round(100.0 * sum(1 for x in a if x["kind"] == "drift") / n, 0) if n else None,
        "techniques": tech, "reasons": dict(reasons),
        "ok": {"entry": ok_entry, "brake_dist": _mean([x["brake_dist"] for x in ok]),
               "angle": _mean([x["avg_angle"] for x in ok if x["kind"] == "drift"]),
               "lift": _mean([x["lift_pct"] for x in ok]), "score": _mean([x["score"] for x in ok])},
        "ko": {"entry": _mean([x["entry"] for x in ko]), "brake_dist": _mean([x["brake_dist"] for x in ko]),
               "angle": _mean([x["avg_angle"] for x in ko if x["kind"] == "drift"]),
               "lift": _mean([x["lift_pct"] for x in ko]), "score": _mean([x["score"] for x in ko])},
        "braked_ok": sum(1 for x in ok if x["brake_dist"] is not None),
        "braked_ko": sum(1 for x in ko if x["brake_dist"] is not None),
        "dominant": dom, "dominant_share": round(100.0 * dom_n / n, 0) if n else None,
        "best": max(a, key=lambda x: x["score"]) if a else None,
    }
    st["treacherous"] = bool(n >= 3 and st["rate"] is not None and st["rate"] <= 50)
    st["insights"] = {"fr": insights(c, st, "fr"), "en": insights(c, st, "en")}
    return st


def insights(c, st, lang):
    T = T_FR if lang == "fr" else T_EN
    R = R_FR if lang == "fr" else R_EN
    fr = lang == "fr"
    out = []
    n, ok, okd, kod = st["attempts"], st["success"], st["ok"], st["ko"]
    if n == 0:
        return out
    if 0 < ok < n and okd["entry"] and kod["entry"] and kod["entry"] - okd["entry"] >= 8:
        out.append(("Ça passe à %d km/h en moyenne, ça casse à %d : vise %d km/h à l'entrée." if fr else
                    "It works at %d km/h on average and fails at %d: aim for %d km/h on entry.")
                   % (okd["entry"], kod["entry"], okd["entry"]))
    tech = {k: v for k, v in st["techniques"].items() if k not in ("grip", "other")}
    if len(tech) >= 2:
        best = max(tech.items(), key=lambda kv: (kv[1]["rate"], kv[1]["n"]))
        worst = min(tech.items(), key=lambda kv: (kv[1]["rate"], -kv[1]["n"]))
        if best[1]["rate"] > worst[1]["rate"]:
            out.append(("Lancée par %s : %d/%d réussies. Par %s : %d/%d." if fr else
                        "Started with %s: %d/%d clean. With %s: %d/%d.")
                       % (T[best[0]], best[1]["ok"], best[1]["n"], T[worst[0]], worst[1]["ok"], worst[1]["n"]))
    if st["dominant_share"] and st["dominant_share"] >= 70 and n >= 3:
        out.append(("Réflexe : tu fais presque toujours %s ici (%d %% des passages)." if fr else
                    "Reflex: you almost always use %s here (%d%% of passes).")
                   % (T[st["dominant"]], st["dominant_share"]))
    if ok and n - ok and st["braked_ok"] / max(ok, 1) - st["braked_ko"] / max(n - ok, 1) >= 0.4:
        out.append(("Quand tu freines avant d'entrer, ça passe nettement mieux." if fr else
                    "When you brake before turning in, it works much better."))
    if okd["lift"] is not None and kod["lift"] is not None and kod["lift"] - okd["lift"] >= 10:
        out.append(("Les ratés ont %d %% de pied levé en glisse, contre %d %% pour les réussites." if fr else
                    "Failed attempts spend %d%% of the drift with the foot off, versus %d%% for the clean ones.")
                   % (kod["lift"], okd["lift"]))
    if st["reasons"]:
        r, k = max(st["reasons"].items(), key=lambda kv: kv[1])
        out.append(("Cause principale des ratés : %s (%d fois)." if fr else "Main cause of failures: %s (%d times).") % (R[r], k))
    if ok == n and n >= 2:
        out.append("Virage maîtrisé sur tous les passages." if fr else "Clean on every pass.")
    return out


def overall(attempts):
    drifts = [x for x in attempts if x["kind"] == "drift"]
    by = {}
    for x in drifts:
        s = by.setdefault(x["technique"], {"n": 0, "ok": 0, "score": [], "angle": [], "entry": []})
        s["n"] += 1
        s["ok"] += x["success"]
        s["score"].append(x["score"])
        s["angle"].append(x["avg_angle"])
        s["entry"].append(x["entry"])
    for s in by.values():
        s["rate"] = round(100.0 * s["ok"] / s["n"], 0)
        for k in ("score", "angle", "entry"):
            s[k] = round(float(np.mean(s[k])), 0)
    reasons = Counter(r for x in attempts if not x["success"] for r in x["reasons"][:1])
    by_kind = {}
    for x in drifts:
        k = by_kind.setdefault(x["corner_kind"], {})
        s = k.setdefault(x["technique"], {"n": 0, "ok": 0})
        s["n"] += 1
        s["ok"] += x["success"]
    grip = [x for x in attempts if x["kind"] == "grip"]
    return {"techniques": by, "reasons": dict(reasons), "by_kind": by_kind,
            "drift_rate": round(100.0 * sum(x["success"] for x in drifts) / len(drifts), 0) if drifts else None,
            "grip_rate": round(100.0 * sum(x["success"] for x in grip) / len(grip), 0) if grip else None,
            "n_drift": len(drifts), "n_grip": len(grip)}
