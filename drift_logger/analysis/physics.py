"""
Why a drift lives or dies: engine speed against the car's power curve, rear
wheelspin, speed lost per second and g-forces; plus slides that started but
never became a drift (aborted initiations).
"""
import numpy as np
import pandas as pd

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


CORNERS = {"front_left": (2.25, -0.9), "front_right": (2.25, 0.9), "rear_left": (-2.25, -0.9), "rear_right": (-2.25, 0.9)}


def _seg_dist(p, ax, az, bx, bz):
    """Distance from point p to every segment (a_i, b_i)."""
    dx, dz = bx - ax, bz - az
    L2 = np.maximum(dx * dx + dz * dz, 1e-9)
    u = np.clip(((p[0] - ax) * dx + (p[1] - az) * dz) / L2, 0, 1)
    return np.hypot(ax + u * dx - p[0], az + u * dz - p[1])


def contact_points(df, events, edges):
    """Where the car actually touched: the body corner closest to a road edge at the impact.
    The impact point used to be the car's centre, which in a drift can be 2-3 m from the wall.
    A contact far from any edge means something the AI line counts as road (gutter, kerb)."""
    if not edges:
        return
    segs = []
    for side in ("l", "r"):
        x, z = np.array(edges[side]["x"]), np.array(edges[side]["z"])
        segs.append((x[:-1], z[:-1], x[1:], z[1:]))
    t = df.time_s.values
    for e in events:
        if e["type"] != "impact":
            continue
        i = max(0, int(np.searchsorted(t, e["t"] - 0.08)))  # just before the hit
        cx, cz, h = df.world_x.iat[i], df.world_z.iat[i], np.radians(df.heading.iat[i])
        fwd, right = np.array([np.sin(h), np.cos(h)]), np.array([np.cos(h), -np.sin(h)])
        best = None
        for name, (a, b) in CORNERS.items():
            p = np.array([cx, cz]) + a * fwd + b * right
            d = min(float(_seg_dist(p, *s).min()) for s in segs)
            if best is None or d < best[0]:
                best = (d, name, p)
        e["cx"], e["cz"] = round(float(best[2][0]), 1), round(float(best[2][1]), 1)
        e["contact"] = best[1]
        e["wall_gap"] = round(best[0], 1)
        e["no_wall"] = best[0] > 2.0


US_FRONT = 7.0      # deg: front tyres sliding past their grip peak
US_GAP = 4.0        # deg: ...clearly more than the rears
US_MIN_DUR = 0.25   # s


def understeer(df):
    """Moments where the front tyres slide much more than the rears while the wheel is turned
    and the car is not sliding: the car goes wider than the steering asks (understeer).
    Each episode says what the pedals were doing, and whether the driver had braked and then
    released the brake before turning in (front tyres unloaded at the worst moment)."""
    fs = df[["slip_angle_fl", "slip_angle_fr"]].abs().mean(axis=1)
    rs = df[["slip_angle_rl", "slip_angle_rr"]].abs().mean(axis=1)
    fsr = df[["slip_ratio_fl", "slip_ratio_fr"]].mean(axis=1)
    on = ((fs > US_FRONT) & (fs - rs > US_GAP) & (df.beta.abs() < 6) &
          (df.steer_deg.abs() > 40) & (df.speed_kmh > 25)).values
    t = df.time_s.values
    out, i, n = [], 0, len(on)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        while j < n and on[j]:
            j += 1
        if t[j - 1] - t[i] >= US_MIN_DUR:
            w = df.iloc[i:j]
            lock = float((fsr.iloc[i:j] < -0.15).mean())
            kind = ("brake_lock" if lock > 0.3 else "braking" if w.brake.mean() > 0.2 else
                    "throttle" if w.throttle.mean() > 0.4 else "coast")
            pre = df[(df.time_s > t[i] - 2.5) & (df.time_s < t[i])]
            braked = pre[pre.brake > 0.1]
            out.append({"t": round(float(t[i]), 2), "t1": round(float(t[j - 1]), 2),
                        "pos": round(float(df.track_pos.iat[i]), 5),
                        "x": round(float(df.world_x.iat[i]), 1), "z": round(float(df.world_z.iat[i]), 1),
                        "speed": int(w.speed_kmh.iloc[0]), "speed_end": int(w.speed_kmh.iloc[-1]),
                        "kind": kind, "thr": round(float(w.throttle.mean()), 2), "brk": round(float(w.brake.mean()), 2),
                        "steer": int(w.steer_deg.abs().max()), "front": round(float(fs.iloc[i:j].mean()), 1),
                        "rear": round(float(rs.iloc[i:j].mean()), 1), "gear": int(w.gear.iloc[0]),
                        # braked hard, then let go before turning: the front gets light just as you turn
                        "released": bool(len(pre) and pre.brake.max() > 0.3 and w.brake.iloc[0] < 0.1),
                        "release_s": round(float(t[i] - braked.time_s.iloc[-1]), 2) if len(braked) else None})
        i = j
    return out


TB_KINDS = ("hairpin", "tight")


def trail_brake(df, corners, us_episodes, gforce):
    """Trail braking, entry by entry, for hairpins and tight corners (from the session's own
    corner detection; numbered against the track later).

    Score /100: brake still on when turning in (30), brake held while turning (20),
    progressive release (20), the front bites, i.e. no understeer (20), no front lock (10).
    When the front bit with grip to spare, estimate how much faster the entry could be."""
    t = df.time_s.values
    pos = df.track_pos.values
    steer = df.steer_deg.abs().values
    brk = df.brake.values
    fsr = df[["slip_ratio_fl", "slip_ratio_fr"]].mean(axis=1).values
    fsa = df[["slip_angle_fl", "slip_angle_fr"]].abs().mean(axis=1).values
    seg = df.segment.values
    max_lat = max(gforce.get("max_lat") or 1.0, 0.5)
    out = []
    for c in corners:
        if c["kind"] not in TB_KINDS:
            continue
        zone = np.where((pos >= c["pos0"] - 0.003) & (pos <= c["pos1"]))[0]
        if len(zone) < 20 or seg[zone[0]] != seg[zone[-1]]:
            continue
        ti = next((k for k in zone if steer[k] > 30), None)
        if ti is None or df.speed_kmh.iat[ti] < 20:
            continue
        end = zone[-1]
        tt = t[ti]
        # brake around the moment the wheel goes in (from 0.1 s before to 0.3 s after)
        b_ti = float(brk[max(0, ti - 9):ti + 27].mean())
        b_before = float(brk[(t >= tt - 2.0) & (t < tt)].max(initial=0))
        after = (t >= tt) & (t <= t[end])
        # brake held while turning: brake > 10% with the wheel turned
        held = float(((brk > 0.1) & (steer > 30) & after).sum() / 90.0)
        # release: from turn-in until the brake is off; steepest drop in pedal per second
        rel_idx = next((k for k in range(ti, end) if brk[k] < 0.05), end)
        release_s = float(t[rel_idx] - tt)
        # steepest release, measured over 0.15 s steps (pedal fraction per second)
        seg_b = brk[ti:min(rel_idx + 14, len(brk))]
        drop = float(np.max(seg_b[:-13] - seg_b[13:]) / 0.15) if len(seg_b) > 14 else (b_ti / 0.15 if b_ti > 0.08 else 0.0)
        lock = bool((fsr[ti:end] < -0.15).mean() > 0.05)
        # does the front bite on entry: understeer from just before turn-in to 2 s after
        t_end = min(t[end], tt + 2.0)
        us = sum(min(u["t1"], t_end) - max(u["t"], tt - 0.3) for u in us_episodes
                 if u["t1"] >= tt - 0.3 and u["t"] <= t_end)
        us = max(0.0, us)
        # lateral g used (impacts clipped out, smoothed over 0.2 s)
        lat = float(pd.Series(np.clip(np.abs(df.acc_g_x.values[ti:end]), 0, 1.6)).rolling(18, min_periods=1).median().max())
        beta = float(np.abs(df.beta.values[ti:end]).max())
        v = float(df.speed_kmh.iat[ti])
        # score
        s_ti = 30 if 0.15 <= b_ti <= 0.6 else (22 if b_ti > 0.6 else (15 if b_ti >= 0.08 else 0))
        s_held = 20 * min(1.0, held / 0.5)
        smooth = b_ti >= 0.08 and release_s >= 0.3 and drop < 3.0
        s_rel = 0 if b_ti < 0.08 else (20 if smooth else 10 if release_s >= 0.15 else 4)
        s_bite = 20 * max(0.0, 1 - us / 1.5)
        s_lock = 0 if lock else 10
        score = int(round(s_ti + s_held + s_rel + s_bite + s_lock))
        # margin: the front bit and lateral grip was not used up
        margin = 0
        if us < 0.2 and not lock and fsa[ti:end].max(initial=0) < 7 and lat < 0.85 * max_lat and lat > 0.2:
            margin = int(min(10, round(v * ((max_lat / lat) ** 0.5 - 1) / 5) * 5))
        if b_ti < 0.08:
            verdict = "no_brake"
        elif lock:
            verdict = "lock"
        elif us >= 0.3:
            verdict = "understeer"
        elif not smooth:
            verdict = "abrupt"
        else:
            verdict = "good"
        out.append({"t": round(float(tt), 2), "pos": round(float(pos[ti]), 5), "kind": c["kind"],
                    "speed": int(v), "brake_before": round(b_before, 2), "brake_turnin": round(b_ti, 2),
                    "held_s": round(held, 2), "release_s": round(release_s, 2), "drop": round(drop, 1),
                    "lock": lock, "understeer_s": round(us, 2), "lat_g": round(lat, 2), "max_angle": round(beta, 0),
                    "score": score, "margin": margin, "verdict": verdict})
    return out
