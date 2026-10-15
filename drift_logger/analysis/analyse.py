"""
Drift Logger - session analysis
-------------------------------
Reads the CSV files recorded by the Drift Logger in-game app, computes driving
metrics, detects the corners of each road from the car's trajectory and updates:

  sessions/<id>.json    one session in detail (trace, events, drifts)
  tracks/<track>.json   detected corners + cumulative stats per corner
  history.json          one row of metrics per session

Usage:   python analyse.py                 analyse new log files
         python analyse.py --all           recompute everything
         python analyse.py --logs <dir>    read logs from another folder
Requires: numpy, pandas
"""
import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd

import technique

HERE = os.path.dirname(os.path.abspath(__file__))
SESS_DIR = os.path.join(HERE, "sessions")
TRACK_DIR = os.path.join(HERE, "tracks")
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
PUMP_WINDOW = 0.35        # s: full-on <-> full-off within this = a throttle stab
CORNER_RADIUS = 90.0      # m: max path radius to count as a corner
CORNER_MIN_LEN = 12.0     # m
THROTTLE_BANDS = [("off", 0.0, 0.05), ("low", 0.05, 0.40), ("mid", 0.40, 0.90), ("full", 0.90, 1.01)]


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
        before = df[(df.time_s > t[i] - 0.4) & (df.time_s < t[i] - 0.1)].speed_kmh.mean()
        after = df[(df.time_s > t[i] + 0.3) & (df.time_s < t[i] + 0.7)].speed_kmh.mean()
        loss = before - after if not (np.isnan(before) or np.isnan(after)) else 99.0
        e = point(df, i)
        e.update({"type": "impact", "kind": "drift" if w["beta"].abs().max() > 15 else "grip",
                  "loss": round(float(loss), 0),
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


def throttle_stabs(df):
    """Full-on <-> full-off throttle swings completed within PUMP_WINDOW seconds."""
    thr, t, dr = df.throttle.values, df.time_s.values, df.drifting.values
    out, state = [], None
    for i in range(len(df)):
        s = "on" if thr[i] > 0.85 else ("off" if thr[i] < 0.1 else None)
        if s:
            if state and s != state[0] and t[i] - state[1] < PUMP_WINDOW:
                out.append({"t": round(float(t[i]), 2), "drift": bool(dr[i]), "to": s})
            state = (s, t[i])
    return out


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


def find_drifts(df, stabs):
    m, t = df.drifting.values, df.time_s.values
    res, start = [], None
    for i, v in enumerate(list(m) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            end = min(i, len(df) - 1)
            if t[end] - t[start] >= DRIFT_MIN_DUR:
                w = df.iloc[start:end + 1]
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
                            "stabs": sum(1 for p in stabs if t[start] <= p["t"] <= t[end])})
            start = None
    return res


def detect_corners(df):
    """Corners = stretches where the path radius drops below CORNER_RADIUS."""
    seg = df[df.segment == df.segment.mode()[0]]
    pts = seg[["dist", "world_x", "world_z", "track_pos"]].values
    d = pts[:, 0]
    if d[-1] - d[0] < 100:
        return []
    grid = np.arange(d[0], d[-1], 2.0)  # resample every 2 m
    x = np.interp(grid, d, pts[:, 1])
    z = np.interp(grid, d, pts[:, 2])
    p = np.interp(grid, d, pts[:, 3])
    xs = np.convolve(x, np.ones(6) / 6, mode="same")
    zs = np.convolve(z, np.ones(6) / 6, mode="same")
    head = np.unwrap(np.arctan2(np.gradient(zs), np.gradient(xs)))
    curv = np.convolve(np.gradient(head) / 2.0, np.ones(8) / 8, mode="same")
    is_corner = np.abs(curv) > 1.0 / CORNER_RADIUS
    corners, start = [], None
    for i, v in enumerate(list(is_corner) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            end = i - 1
            if (end - start) * 2.0 >= CORNER_MIN_LEN:
                turn = math.degrees(head[end] - head[start])
                apex = start + int(np.argmax(np.abs(curv[start:end + 1])))
                corners.append({"pos0": round(float(p[start]), 5), "pos1": round(float(p[end]), 5),
                                "x": round(float(x[apex]), 1), "z": round(float(z[apex]), 1),
                                "turn": round(turn, 0),
                                "radius": round(float(1 / max(abs(curv[apex]), 1e-4)), 0),
                                "dir": "left" if turn > 0 else "right"})
            start = None
    merged = []  # join very close corners turning the same way
    for c in corners:
        if merged and c["dir"] == merged[-1]["dir"] and c["pos0"] - merged[-1]["pos1"] < 0.004:
            merged[-1]["pos1"] = c["pos1"]
            merged[-1]["turn"] += c["turn"]
        else:
            merged.append(c)
    for i, c in enumerate(merged):
        c["id"] = i + 1
        c["kind"] = "hairpin" if abs(c["turn"]) > 120 else ("tight" if c["radius"] < 35 else "fast")
    return merged


def downsample(df, hz=10):
    step = max(1, int(round(len(df) / max(df.time_s.iloc[-1], 1) / hz)))
    s = df.iloc[::step]
    return {"t": s.time_s.round(2).tolist(), "x": s.world_x.round(1).tolist(),
            "z": s.world_z.round(1).tolist(), "pos": s.track_pos.round(5).tolist(),
            "speed": s.speed_kmh.round(0).tolist(), "angle": s.beta.abs().round(0).tolist(),
            "thr": s.throttle.round(2).tolist(), "brk": s.brake.round(2).tolist(),
            "steer": s.steer_deg.round(0).tolist(), "gear": s.gear.astype(int).tolist(),
            "rpm": s.rpm.round(-1).tolist(), "seg": s.segment.astype(int).tolist()}


def summarize(sid, meta, df, ev, dr, cs, stabs):
    km = df.dist.iloc[-1] / 1000.0
    dd = df[df.drifting]
    drift_min = len(dd) / max(len(df), 1) * df.time_s.iloc[-1] / 60.0
    grip_min = df.time_s.iloc[-1] / 60.0 - drift_min

    def count(typ, kind=None):
        return sum(1 for e in ev if e["type"] == typ and (kind is None or e.get("kind") == kind))

    delays = [c["delay"] for c in cs]
    clean = sum(1 for d in dr if d["success"])
    techs = {}
    for d in dr:
        techs[d["technique"]] = techs.get(d["technique"], 0) + 1
    bands = {name: round(float(((dd.throttle >= lo) & (dd.throttle < hi)).mean() * 100), 1) if len(dd) else None
             for name, lo, hi in THROTTLE_BANDS}
    n_stab_drift = sum(1 for p in stabs if p["drift"])
    return {
        "id": sid, "car": meta.get("car", "?"), "track": meta.get("track", "?"),
        "date": "%s-%s-%s %s:%s" % (sid[:4], sid[4:6], sid[6:8], sid[9:11], sid[11:13]),
        "duration_s": round(float(df.time_s.iloc[-1]), 0), "distance_km": round(km, 2),
        "track_cover": round(float(df.track_pos.max() - df.track_pos.min()), 3),
        "drifts": len(dr), "clean_pct": round(100.0 * clean / len(dr), 0) if dr else None,
        "avg_score": round(float(np.mean([d["score"] for d in dr])), 0) if dr else None,
        "exit_touches": sum(1 for d in dr if d["outcome"] == "exit_touch"),
        "techniques": techs,
        "drift_time_pct": round(float(df.drifting.mean() * 100), 1),
        "avg_angle": round(float(dd.beta.abs().mean()), 1) if len(dd) else None,
        "avg_entry": round(float(np.mean([d["entry"] for d in dr])), 0) if dr else None,
        "lift_pct": bands["off"],
        "thr_bands": bands,
        "stabs_per_min": round(n_stab_drift / drift_min, 1) if drift_min > 0.05 else None,
        "stabs_per_min_grip": round((len(stabs) - n_stab_drift) / grip_min, 1) if grip_min > 0.05 else None,
        "cs_delay": round(float(np.median(delays)), 2) if delays else None,
        "cs_late": sum(1 for x in delays if x > 0.5),
        "impacts": count("impact"), "impacts_grip": count("impact", "grip"), "impacts_drift": count("impact", "drift"),
        "impacts_heavy": sum(1 for e in ev if e["type"] == "impact" and e.get("loss", 99) > 10),
        "spins": count("spin"), "locks": count("lock"), "lifts": count("lift"),
        "impacts_per_km": round(count("impact") / km, 2) if km > 0.05 else None,
        "max_speed": round(float(df.speed_kmh.max()), 0),
    }


def corner_of(corners, pos):
    for c in corners:
        if c["pos0"] - 0.003 <= pos <= c["pos1"] + 0.003:
            return c["id"]
    return None


def update_track(track, sessions):
    """Corners come from the session covering the most road; stats from all sessions."""
    os.makedirs(TRACK_DIR, exist_ok=True)
    ref = max(sessions, key=lambda s: s["summary"]["track_cover"])
    corners = [dict(c) for c in ref["corners"]]
    for c in corners:
        c.update({"passes": 0, "drifts": 0, "impacts": 0, "spins": 0, "lifts": 0, "locks": 0,
                  "late_cs": 0, "angle_sum": 0.0, "entry_sum": 0.0})
    keys = {"impact": "impacts", "spin": "spins", "lift": "lifts", "lock": "locks"}
    for s in sessions:
        pos = np.array(s["trace"]["pos"])
        for c in corners:
            if ((pos >= c["pos0"]) & (pos <= c["pos1"])).any():
                c["passes"] += 1
        for d in s["drifts"]:
            cid = corner_of(corners, (d["pos0"] + d["pos1"]) / 2)
            if cid:
                corners[cid - 1]["drifts"] += 1
                corners[cid - 1]["angle_sum"] += d["avg_angle"]
                corners[cid - 1]["entry_sum"] += d["entry"]
        for e in s["events"]:
            cid = corner_of(corners, e["pos"])
            if cid:
                corners[cid - 1][keys[e["type"]]] += 1
        for c_s in s["countersteer"]:
            cid = corner_of(corners, c_s["pos"])
            if cid and c_s["delay"] > 0.5:
                corners[cid - 1]["late_cs"] += 1
    for c in corners:
        c["avg_angle"] = round(c["angle_sum"] / c["drifts"], 0) if c["drifts"] else None
        c["avg_entry"] = round(c["entry_sum"] / c["drifts"], 0) if c["drifts"] else None
        c["trouble"] = c["impacts"] * 3 + c["spins"] * 2 + c["late_cs"] + c["locks"]
        del c["angle_sum"], c["entry_sum"]
    data = {"track": track, "ref_session": ref["summary"]["id"], "corners": corners,
            "ref_trace": {k: ref["trace"][k] for k in ("x", "z", "pos")}}
    with open(os.path.join(TRACK_DIR, safe(track) + ".json"), "w") as f:
        json.dump(data, f)
    return data


def safe(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)


def analyse_file(path):
    df, meta = load_csv(path)
    df = prepare(df)
    sid = os.path.basename(path)[:15]
    ev = detect_events(df)
    stabs = throttle_stabs(df)
    cs = countersteer_delays(df)
    dr = technique.analyse_drifts(df, find_drifts(df, stabs), ev, cs)
    return {"summary": summarize(sid, meta, df, ev, dr, cs, stabs), "events": ev, "drifts": dr,
            "countersteer": cs, "stabs": stabs, "corners": detect_corners(df),
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
    by_track = {}
    for s in sessions:
        by_track.setdefault(s["summary"]["track"], []).append(s)
    tracks = {t: update_track(t, ss) for t, ss in by_track.items()}
    for s in sessions:  # tag every event and drift with its corner
        cs = tracks[s["summary"]["track"]]["corners"]
        for e in s["events"]:
            e["corner"] = corner_of(cs, e["pos"])
        for d in s["drifts"]:
            d["corner"] = corner_of(cs, (d["pos0"] + d["pos1"]) / 2)
        with open(os.path.join(SESS_DIR, s["summary"]["id"] + ".json"), "w") as f:
            json.dump(s, f)
    with open(HISTORY, "w") as f:
        json.dump([s["summary"] for s in sessions], f, indent=1)
    print("%d session(s) in history" % len(sessions))


if __name__ == "__main__":
    main()
