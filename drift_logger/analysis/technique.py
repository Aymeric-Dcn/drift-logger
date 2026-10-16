"""
Per-drift breakdown: how the drift was started, how it was held, how it ended,
a 0-100 score and, for failed drifts, the most likely reasons.

Everything works on the full-rate session dataframe prepared by analyse.py.
"""
import numpy as np

# initiation techniques, in priority order when several are detected
TECHNIQUES = ["transition", "handbrake", "downshift", "feint", "clutch", "brake", "lift", "power", "other"]

LIGHT_TOUCH_LOSS = 10.0   # km/h lost at the contact: below this, a touch on exit is fine
OVER_ANGLE = 65.0         # deg: too much angle, the drift bogs down
LATE_CS = 0.5             # s


def window(df, t0, t1):
    return df[(df.time_s >= t0) & (df.time_s <= t1)]


def handbrake_mask(df):
    """Rear wheels much slower than the car (slip ratio below -0.4) with the brake
    pedal released and the front wheels rolling: only the handbrake does that.
    Engine braking cannot pull the rear wheels below engine idle speed."""
    if "handbrake" in df and df.handbrake.notna().any():
        return df.handbrake > 0.3
    rear = df[["slip_ratio_rl", "slip_ratio_rr"]].mean(axis=1)
    front = df[["slip_ratio_fl", "slip_ratio_fr"]].mean(axis=1)
    m = (rear < -0.4) & (front > -0.15) & (df.brake < 0.15) & (df.speed_kmh > 15)
    if "ds_lock" in df:  # a locking downshift does the same to the rear wheels
        m &= ~df.ds_lock
    return m


def handbrake(df, t0):
    w = window(df, t0 - 1.8, t0 + 0.2)
    return bool(len(w) and w.handbrake_on.sum() >= 3)


def initiation(df, d, prev):
    """Flags for every technique seen just before the drift starts, plus the main one."""
    t0, direction = d["t0"], d["dir"]
    pre = window(df, t0 - 1.5, t0 + 0.15)
    flags = {}
    # transition: previous slide in the other direction ended less than 1 s ago
    flags["transition"] = bool(prev and prev["dir"] == -direction and t0 - prev["t1"] < 1.0)
    # feint: a flick away from the corner while the car is still straight, then turn-in
    sd = pre.steer_deg.values * direction
    calm = pre.beta.abs().values < 5
    away = [i for i in range(len(sd)) if calm[i] and sd[i] < -40]
    flags["feint"] = bool(away and (sd[away[0]:] > 30).any() and not flags["transition"])
    flags["handbrake"] = handbrake(df, t0)
    flags["downshift"] = bool("ds_lock" in df and window(df, t0 - 1.5, t0 + 0.2).ds_lock.any())
    flags["clutch"] = bool((window(df, t0 - 1.0, t0 + 0.3).clutch < 0.5).any())
    brk = window(df, t0 - 1.2, t0 + 0.2)
    flags["brake"] = bool(brk.brake.max() > 0.25) if len(brk) else False
    flags["trail_brake"] = bool(flags["brake"] and ((brk.brake > 0.1) & (brk.steer_deg.abs() > 30)).any())
    recent = window(df, t0 - 0.35, t0 + 0.05).throttle
    earlier = window(df, t0 - 1.0, t0 - 0.35).throttle
    flags["lift"] = bool(len(recent) and len(earlier) and earlier.max() > 0.6 and recent.min() < 0.4
                         and not flags["brake"])
    flags["power"] = bool(not flags["lift"] and len(window(df, t0 - 0.5, t0)) and
                          window(df, t0 - 0.5, t0).throttle.mean() > 0.7)
    main = next((k for k in TECHNIQUES[:-1] if flags.get(k)), "other")
    return main, flags


def ending(df, d, impacts, spins):
    """clean / exit_touch / impact / spin / bogged (too much angle, speed collapses)."""
    dur = max(d["t1"] - d["t0"], 0.1)
    for e in impacts:
        if d["t0"] - 0.2 <= e["t"] <= d["t1"] + 1.2:
            late = (e["t"] - d["t0"]) / dur >= 0.85
            if late and e.get("loss", 99) <= LIGHT_TOUCH_LOSS:
                return "exit_touch", e
            return "impact", e
    for e in spins:
        if d["t0"] - 0.2 <= e["t"] <= d["t1"] + 1.2:
            return "spin", e
    if d["max_angle"] > OVER_ANGLE and d["exit"] < 0.6 * d["entry"]:
        return "bogged", None
    return "clean", None


def reasons(df, d, outcome, event, locks, cs_delay):
    """Most likely causes of a failed drift, strongest first."""
    out = []
    w = window(df, d["t0"], (event["t"] if event else d["t1"]))
    for e in locks:
        if d["t0"] - 0.3 <= e["t"] <= d["t1"]:
            out.append("handbrake_late" if e.get("cause") == "handbrake" else "rear_lock")
            break
    if len(w) and (w.throttle < 0.05).mean() > 0.2:
        out.append("lift")
    if cs_delay is not None and cs_delay > LATE_CS:
        out.append("late_cs")
    if d["max_angle"] > OVER_ANGLE:
        out.append("over_angle")
    if outcome == "impact" and event is not None:
        rel = (event["t"] - d["t0"]) / max(d["t1"] - d["t0"], 0.1)
        if rel < 0.85 and d["avg_angle"] < 30:
            out.append("line")          # hit the wall mid-corner without much angle: placement
        elif rel >= 0.85:
            out.append("exit_wide")     # ran out of road on exit
    if not out:
        out.append("line")
    return out


def score(d):
    s = 100.0
    s -= {"clean": 0, "exit_touch": 6, "impact": 40, "spin": 50, "bogged": 30}[d["outcome"]]
    s -= min(25.0, d["lift_pct"] * 0.6)
    if d.get("cs_delay") is not None and d["cs_delay"] > LATE_CS:
        s -= 10
    if d["angle_std"] > 15:
        s -= 8
    if d["exit"] < 0.75 * d["entry"]:
        s -= 8
    if d["avg_angle"] < 18:
        s -= 8
    s -= min(10.0, d["stabs"] * 2)
    return int(max(0, min(100, round(s))))


def analyse_drifts(df, drifts, events, countersteer):
    impacts = [e for e in events if e["type"] == "impact"]
    spins = [e for e in events if e["type"] == "spin"]
    locks = [e for e in events if e["type"] == "lock"]
    prev = None
    for d in drifts:
        w = window(df, d["t0"], d["t1"])
        d["dir"] = int(np.sign(w.beta.mean())) or 1
        d["angle_std"] = round(float(w.beta.abs().std()), 1) if len(w) > 2 else 0.0
        d["thr_mid"] = round(float(((w.throttle >= 0.4) & (w.throttle < 0.9)).mean() * 100), 0)
        d["brake_max"] = round(float(window(df, d["t0"] - 1.5, d["t0"] + 0.2).brake.max()), 2)
        d["technique"], d["flags"] = initiation(df, d, prev)
        cs = [c for c in countersteer if abs(c["t"] - d["t0"]) < 0.6]
        d["cs_delay"] = cs[0]["delay"] if cs else None
        d["outcome"], ev = ending(df, d, impacts, spins)
        d["success"] = d["outcome"] in ("clean", "exit_touch")
        d["reasons"] = [] if d["success"] else reasons(df, d, d["outcome"], ev, locks, d["cs_delay"])
        d["score"] = score(d)
        prev = d
    return drifts
