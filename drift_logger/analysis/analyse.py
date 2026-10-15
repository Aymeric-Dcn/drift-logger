"""
Drift Logger - session analysis
-------------------------------
Reads the CSV files recorded by the Drift Logger in-game app, computes driving
metrics, and updates:

  sessions/<id>.json    one session in detail (trace, events, drifts)
  history.json          one row of metrics per session

Usage:   python analyse.py                 analyse new log files
         python analyse.py --all           recompute everything
         python analyse.py --logs <dir>    read logs from another folder
Requires: numpy, pandas
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(HERE, "sessions")
HISTORY = os.path.join(HERE, "history.json")

# --- thresholds ---------------------------------------------------------------
DRIFT_ANGLE = 12.0        # deg of body slip angle to count as a drift
DRIFT_MIN_SPEED = 20.0    # km/h
DRIFT_MIN_DUR = 0.4       # s
SPIN_ANGLE = 75.0         # deg: near spin
IMPACT_G = 3.5            # g: wall contact
LIFT_THR = 0.05           # throttle below this = foot off
LOCK_RPM = 1500           # rpm collapse in gear = rear wheels locked
CS_ONSET = 15.0           # deg: rear stepping out, start of counter-steer timing


def load_csv(path):
    meta = {}
    with open(path, "r") as f:
        first = f.readline().strip()
    if first.startswith("#"):
        for part in first[1:].split():
            if "=" in part:
                k, v = part.split("=", 1)
                meta[k] = v
    df = pd.read_csv(path, comment="#")
    df = df.dropna(subset=["time_s", "speed_kmh"]).reset_index(drop=True)
    return df, meta


def prepare(df):
    # body slip angle: angle between the car's axis and its velocity
    df["beta"] = np.degrees(np.arctan2(df["vel_x"], df["vel_z"].clip(lower=0.1)))
    df.loc[df["speed_kmh"] < 8, "beta"] = 0.0
    dx = df["world_x"].diff().fillna(0)
    dz = df["world_z"].diff().fillna(0)
    step = np.sqrt(dx ** 2 + dz ** 2)
    jump = step > 30  # teleport / session restart
    step[jump] = 0
    df["dist"] = step.cumsum()
    df["segment"] = jump.cumsum()
    df["g"] = np.sqrt(df["acc_g_x"] ** 2 + df["acc_g_z"] ** 2)
    df["drifting"] = (df["beta"].abs() > DRIFT_ANGLE) & (df["speed_kmh"] > DRIFT_MIN_SPEED)
    return df


def group_times(indices, gap):
    groups = []
    for i in indices:
        if groups and i - groups[-1][-1] <= gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def point(df, i):
    r = df.loc[i]
    return {"t": round(float(r.time_s), 2), "pos": round(float(r.track_pos), 5),
            "x": round(float(r.world_x), 1), "z": round(float(r.world_z), 1),
            "d": round(float(r.dist), 1)}


def detect_events(df):
    ev = []
    t = df["time_s"].values

    # wall contacts
    for grp in group_times(list(df.index[df["g"] > IMPACT_G]), 90):
        i = grp[int(np.argmax(df.loc[grp, "g"].values))]
        w = df[(df.time_s > t[i] - 1.5) & (df.time_s <= t[i])]
        e = point(df, i)
        e.update({"type": "impact", "kind": "drift" if w["beta"].abs().max() > 15 else "grip",
                  "speed": round(float(w.speed_kmh.iloc[0]), 0), "g": round(float(df.g[i]), 1),
                  "throttle": round(float(w.throttle.mean()), 2)})
        ev.append(e)

    # near spins
    for grp in group_times(list(df.index[(df["beta"].abs() > SPIN_ANGLE) & (df.speed_kmh > 10)]), 180):
        i = grp[int(np.argmax(df.loc[grp, "beta"].abs().values))]
        e = point(df, i)
        e.update({"type": "spin", "angle": round(float(abs(df.beta[i])), 0),
                  "speed": round(float(df.speed_kmh[i]), 0)})
        ev.append(e)

    # rear wheels locked by engine braking (rpm collapses while in gear)
    lock = (df.rpm < LOCK_RPM) & (df.gear >= 1) & (df.clutch > 0.9) & (df.speed_kmh > 30)
    for grp in group_times(list(df.index[lock]), 90):
        e = point(df, grp[0])
        e.update({"type": "lock", "rpm": int(df.rpm[grp].min()), "speed": round(float(df.speed_kmh[grp[0]]), 0)})
        ev.append(e)

    # foot fully off the throttle while drifting (> 0.15 s)
    for grp in group_times(list(df.index[df.drifting & (df.throttle < LIFT_THR)]), 3):
        if t[grp[-1]] - t[grp[0]] < 0.15:
            continue
        i = grp[0]
        after = df[(df.time_s >= t[i]) & (df.time_s <= t[i] + 1.0)]
        e = point(df, i)
        e.update({"type": "lift", "duration": round(float(t[grp[-1]] - t[grp[0]]), 2),
                  "angle_before": round(float(abs(df.beta[i])), 0),
                  "angle_after": round(float(after.beta.abs().max()), 0)})
        ev.append(e)
    return ev


def countersteer_delays(df):
    """Time between the rear stepping out (|slip| > 15 deg) and opposite lock."""
    b, s, t, v = df.beta.values, df.steer_deg.values, df.time_s.values, df.speed_kmh.values
    out, i, n = [], 1, len(df)
    while i < n:
        if abs(b[i]) >= CS_ONSET and abs(b[i - 1]) < CS_ONSET and v[i] > DRIFT_MIN_SPEED:
            if np.max(np.abs(b[max(0, i - 40):i])) > CS_ONSET:  # not a fresh slide
                i += 1
                continue
            sign, k, found = np.sign(b[i]), i, None
            while k < n and t[k] - t[i] < 2.5 and abs(b[k]) > 5:
                if np.sign(s[k]) == -sign and abs(s[k]) > 20:
                    found = k
                    break
                k += 1
            if found is not None:
                out.append({"t": round(float(t[i]), 2), "pos": round(float(df.track_pos.values[i]), 5),
                            "delay": round(float(t[found] - t[i]), 2)})
            i = k + 1
            continue
        i += 1
    return out


def find_drifts(df, events):
    m, t = df.drifting.values, df.time_s.values
    res, start = [], None
    for i, v in enumerate(list(m) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            end = min(i, len(df) - 1)
            if t[end] - t[start] >= DRIFT_MIN_DUR:
                w = df.iloc[start:end + 1]
                outcome = "clean"
                for e in events:
                    if t[start] - 0.2 <= e["t"] <= t[end] + 1.2:
                        if e["type"] == "impact":
                            outcome = "impact"
                            break
                        if e["type"] == "spin":
                            outcome = "spin"
                res.append({"t0": round(float(t[start]), 2), "t1": round(float(t[end]), 2),
                            "pos0": round(float(df.track_pos.values[start]), 5),
                            "pos1": round(float(df.track_pos.values[end]), 5),
                            "entry": round(float(df.speed_kmh.values[start]), 0),
                            "exit": round(float(df.speed_kmh.values[end]), 0),
                            "gear": int(df.gear.values[start]),
                            "max_angle": round(float(w.beta.abs().max()), 0),
                            "avg_angle": round(float(w.beta.abs().mean()), 0),
                            "throttle": round(float(w.throttle.mean()), 2),
                            "lift_pct": round(float((w.throttle < LIFT_THR).mean() * 100), 0),
                            "outcome": outcome})
            start = None
    return res


def downsample(df, hz=10):
    step = max(1, int(round(len(df) / max(df.time_s.iloc[-1], 1) / hz)))
    s = df.iloc[::step]
    return {"t": s.time_s.round(2).tolist(), "x": s.world_x.round(1).tolist(),
            "z": s.world_z.round(1).tolist(), "pos": s.track_pos.round(5).tolist(),
            "speed": s.speed_kmh.round(0).tolist(), "angle": s.beta.abs().round(0).tolist(),
            "thr": s.throttle.round(2).tolist(), "brk": s.brake.round(2).tolist(),
            "steer": s.steer_deg.round(0).tolist(), "gear": s.gear.astype(int).tolist(),
            "rpm": s.rpm.round(-1).tolist(), "seg": s.segment.astype(int).tolist()}


def summarize(sid, meta, df, ev, dr, cs):
    km = df.dist.iloc[-1] / 1000.0
    dd = df[df.drifting]

    def count(typ, kind=None):
        return sum(1 for e in ev if e["type"] == typ and (kind is None or e.get("kind") == kind))

    delays = [c["delay"] for c in cs]
    clean = sum(1 for d in dr if d["outcome"] == "clean")
    return {
        "id": sid, "car": meta.get("car", "?"), "track": meta.get("track", "?"),
        "date": "%s-%s-%s %s:%s" % (sid[:4], sid[4:6], sid[6:8], sid[9:11], sid[11:13]),
        "duration_s": round(float(df.time_s.iloc[-1]), 0), "distance_km": round(km, 2),
        "track_cover": round(float(df.track_pos.max() - df.track_pos.min()), 3),
        "drifts": len(dr), "clean_pct": round(100.0 * clean / len(dr), 0) if dr else None,
        "drift_time_pct": round(float(df.drifting.mean() * 100), 1),
        "avg_angle": round(float(dd.beta.abs().mean()), 1) if len(dd) else None,
        "avg_entry": round(float(np.mean([d["entry"] for d in dr])), 0) if dr else None,
        "lift_pct": round(float((dd.throttle < LIFT_THR).mean() * 100), 1) if len(dd) else None,
        "cs_delay": round(float(np.median(delays)), 2) if delays else None,
        "cs_late": sum(1 for x in delays if x > 0.5),
        "impacts": count("impact"), "impacts_grip": count("impact", "grip"), "impacts_drift": count("impact", "drift"),
        "spins": count("spin"), "locks": count("lock"), "lifts": count("lift"),
        "impacts_per_km": round(count("impact") / km, 2) if km > 0.05 else None,
        "max_speed": round(float(df.speed_kmh.max()), 0),
    }


def safe(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def analyse_file(path):
    df, meta = load_csv(path)
    df = prepare(df)
    sid = os.path.basename(path)[:15]
    ev = detect_events(df)
    dr = find_drifts(df, ev)
    cs = countersteer_delays(df)
    return {"summary": summarize(sid, meta, df, ev, dr, cs), "events": ev, "drifts": dr,
            "countersteer": cs,
            "trace": downsample(df), "file": os.path.basename(path)}


def main():
    args = sys.argv[1:]
    redo = "--all" in args
    log_dir = args[args.index("--logs") + 1] if "--logs" in args else os.path.join(HERE, "..", "logs")
    os.makedirs(SESS_DIR, exist_ok=True)
    done = {os.path.basename(p)[:-5] for p in glob.glob(os.path.join(SESS_DIR, "*.json"))}
    for p in sorted(glob.glob(os.path.join(log_dir, "*.csv"))):
        sid = os.path.basename(p)[:15]
        if sid in done and not redo:
            continue
        try:
            s = analyse_file(p)
        except Exception as e:  # empty or interrupted file
            print("skipped %s: %s" % (os.path.basename(p), e))
            continue
        if s["summary"]["duration_s"] < 20:
            print("skipped %s: too short" % os.path.basename(p))
            continue
        with open(os.path.join(SESS_DIR, sid + ".json"), "w") as f:
            json.dump(s, f)
        print("analysed %s: %d drifts, %d impacts" % (sid, s["summary"]["drifts"], s["summary"]["impacts"]))

    sessions = []
    for p in sorted(glob.glob(os.path.join(SESS_DIR, "*.json"))):
        with open(p) as f:
            sessions.append(json.load(f))
    with open(HISTORY, "w") as f:
        json.dump([s["summary"] for s in sessions], f, indent=1)
    print("%d session(s) in history" % len(sessions))


if __name__ == "__main__":
    main()
