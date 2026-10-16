"""
Why a drift lives or dies: engine speed against the car's power curve, rear
wheelspin, speed lost per second and g-forces; plus slides that started but
never became a drift (aborted initiations).
"""
import numpy as np

import technique

BAND_FRAC = 0.85     # share of peak power that counts as "in the power band"
ABORT_MIN = 6.0      # deg: the rear moved...
ABORT_MAX = 12.0     # ...but never reached a drift
ABORT_DUR = 0.2      # s


def power_frac(spec, rpm):
    if not spec:
        return None
    r = np.array([p[0] for p in spec["power"]], float)
    h = np.array([p[1] for p in spec["power"]], float)
    return np.interp(rpm, r, h, right=h[-1]) / spec["peak_hp"]


def rear_spin(w):
    s = w[["slip_ratio_rl", "slip_ratio_rr"]].mean(axis=1).clip(-1, 2)
    return float(s.mean()) if len(s) else None


def drift_physics(df, drifts, spec):
    # steering lock of this car, as seen in the session (only if the wheel clearly hit a stop)
    full = float(df.steer_deg.abs().max())
    at_stop = (df.steer_deg.abs() >= full - 5).sum() / max(len(df) / max(df.time_s.iloc[-1], 1), 1)
    lock = full if full >= 200 and at_stop >= 0.3 else None
    for d in drifts:
        w = technique.window(df, d["t0"], d["t1"])
        if len(w) < 3:
            continue
        dur = max(d["t1"] - d["t0"], 0.1)
        d["rpm"] = int(w.rpm.mean())
        d["rpm_min"] = int(w.rpm.min())
        pf = power_frac(spec, w.rpm.values)
        d["pw"] = round(float(np.mean(pf)) * 100, 0) if pf is not None else None
        d["spin"] = round(rear_spin(w) * 100, 0)
        d["decel"] = round((w.speed_kmh.iloc[0] - w.speed_kmh.iloc[-1]) / dur, 1)
        d["g"] = round(float(w.g.mean()), 2)
        if lock:  # seconds spent with the wheel against the stop: no counter-steer left
            ww = technique.window(df, d["t0"], d["t1"] + 0.6)
            hz = len(ww) / max(ww.time_s.iloc[-1] - ww.time_s.iloc[0], 0.1)
            d["at_lock"] = round(float((ww.steer_deg.abs() >= lock - 10).sum() / hz), 2)
        if spec and spec.get("band"):
            lo, hi = spec["band"]
            d["rev"] = "limiter" if d["rpm"] > hi + 150 else ("low" if d["rpm"] < lo - 300 else "band")
        else:
            d["rev"] = None
    return drifts


def aborted(df, drifts, spec):
    """Slides above ABORT_MIN that never reached the drift threshold, with how they were started."""
    a = df.beta.abs().values
    t = df.time_s.values
    on = (a > ABORT_MIN) & (df.speed_kmh.values > 25)
    out, i, n = [], 0, len(a)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        if t[j - 1] - t[i] >= ABORT_DUR and a[i:j].max() < ABORT_MAX and \
                not any(d["t0"] - 1.0 <= t[i] <= d["t1"] + 0.5 for d in drifts):
            w = df.iloc[i:j]
            main, flags = technique.initiation(df, {"t0": float(t[i]), "dir": int(np.sign(w.beta.mean())) or 1}, None)
            pf = power_frac(spec, w.rpm.values)
            out.append({"t": round(float(t[i]), 2), "pos": round(float(df.track_pos.iloc[i]), 5),
                        "x": round(float(df.world_x.iloc[i]), 1), "z": round(float(df.world_z.iloc[i]), 1),
                        "technique": main, "flags": {k: v for k, v in flags.items() if v},
                        "gear": int(df.gear.iloc[i]), "speed": round(float(df.speed_kmh.iloc[i]), 0),
                        "rpm": int(w.rpm.mean()), "pw": round(float(np.mean(pf)) * 100, 0) if pf is not None else None,
                        "max_angle": round(float(a[i:j].max()), 1), "thr": round(float(w.throttle.mean()), 2)})
        i = j
    return out


def gear_speeds(df):
    """km/h per 1000 rpm for each gear, measured while gripping with the clutch engaged."""
    rear = df[["slip_ratio_rl", "slip_ratio_rr"]].mean(axis=1).abs()
    g = df[(~df.drifting) & (df.speed_kmh > 20) & (df.clutch > 0.95) & (rear < 0.03) & (df.rpm > 1500)]
    out = {}
    for gear, x in g.groupby(g.gear):
        if gear >= 1 and len(x) > 60:
            out[str(int(gear))] = round(float((x.speed_kmh / x.rpm * 1000).median()), 2)
    return out


def g_stats(df):
    dd = df[df.drifting]
    return {"max_lat": round(float(df.acc_g_x.abs().quantile(0.99)), 2),
            "max_brake": round(float(-df.acc_g_z.quantile(0.01)), 2),
            "max_acc": round(float(df.acc_g_z.quantile(0.99)), 2),
            "drift_g": round(float(dd.g.mean()), 2) if len(dd) else None}


DS_LOCK = -0.25       # rear slip ratio after re-engagement below this = the downshift locked the rear


def downshifts(df, gears, spec):
    """Every downshift: was there a throttle blip with the brake on (heel-toe), did the brake
    stay pressed during the blip, how close were the revs to the new gear's revs when the
    clutch came back, and how hard the rear wheels were jerked."""
    g = df.gear.values.astype(int)
    t = df.time_s.values
    top = spec["power"][-1][0] if spec else None
    out, last = [], None
    for i in range(1, len(df)):
        prev = last
        if g[i] >= 1:
            last = g[i]
        if not (g[i] >= 1 and prev and g[i] < prev and g[i - 1] != g[i] and df.speed_kmh.iat[i] > 20
                and df.track_pos.iat[i] < 0.999):  # past the finish: just stopping the car
            continue
        w = df[(df.time_s >= t[i] - 1.0) & (df.time_s <= t[i] + 1.0)]
        cl = w[w.clutch < 0.5]
        if not len(cl):
            continue
        c0, c1 = float(cl.time_s.iloc[0]), float(cl.time_s.iloc[-1])
        sh = df[(df.time_s >= c0) & (df.time_s <= c1)]
        hz = len(sh) / max(c1 - c0, 0.01) if len(sh) > 1 else 90.0
        before = df[(df.time_s >= c0 - 0.4) & (df.time_s < c0)].brake
        braking = float(max(before.max() if len(before) else 0, sh.brake.max()))
        blip = sh.throttle > 0.3
        j = df.index[df.time_s >= c1][0]
        k = gears.get(str(g[i]))
        target = df.speed_kmh.at[j] / k * 1000 if k else None
        after = df[(df.time_s > c1) & (df.time_s <= c1 + 0.4)]
        rear = after[["slip_ratio_rl", "slip_ratio_rr"]].mean(axis=1).clip(-1, 1)
        ev = {"t": round(t[i], 2), "pos": round(float(df.track_pos.iat[i]), 5),
              "x": round(float(df.world_x.iat[i]), 1), "z": round(float(df.world_z.iat[i]), 1),
              "from": int(prev), "to": int(g[i]), "speed": round(float(df.speed_kmh.iat[i]), 0),
              "braking": braking > 0.2, "blip": bool(blip.any()),
              "blip_s": round(float(blip.sum() / hz), 2),
              "clutch_s": round(c1 - c0, 2),
              "rpm": int(df.rpm.at[j]), "target": int(target) if target else None,
              "match": round(float(df.rpm.at[j] / target * 100), 0) if target else None,
              "over_rev": bool(target and top and target > top + 200),
              "rear": round(float(rear.min()) * 100, 0) if len(rear) else None}
        ev["heel_toe"] = bool(ev["braking"] and ((sh.brake > 0.1) & (sh.throttle > 0.15)).any())
        # brake held: no dip to zero while the clutch is in, if braking carries on after the shift
        # (the foot often rolls off the brake while blipping; ending the braking zone is fine)
        if ev["heel_toe"]:
            later = df[(df.time_s > c1) & (df.time_s <= c1 + 0.5)].brake
            ev["brake_held"] = not (sh.brake.min() < 0.05 and len(later) and later.max() > 0.15)
        else:
            ev["brake_held"] = None
        ev["lock"] = bool(ev["rear"] is not None and ev["rear"] <= DS_LOCK * 100)
        out.append(ev)
    return out


def downshift_lock_mask(df, events):
    """Samples just after a downshift that locked the rear: an initiation technique of its own."""
    m = np.zeros(len(df), dtype=bool)
    t = df.time_s.values
    for e in events:
        if e["lock"]:
            m |= (t >= e["t"] - 0.2) & (t <= e["t"] + 0.8)
    return m
