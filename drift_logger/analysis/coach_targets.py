"""Per-corner braking targets for the in-game coach.

For every car on every road, look at the passes that ended in a successful drift and keep,
corner by corner, the speed window at turn-in, where the braking started and how much brake
was still on at turn-in. The game app reads these files and coaches each corner live.

Output: ../coach/<car>__<track>.txt, a plain text file the game's Python 3.3 reads without
any library. Corners with fewer than MIN_OK successful drifts get no target: better silence
than a made-up number.
"""

import os
import time

import numpy as np

MIN_OK = 2          # successful drifts needed before a corner gets a target
SPEED_PAD = 3.0     # km/h added on each side of the observed window
MIN_WIDTH = 8.0     # km/h: never narrower than this
MAX_BRAKE_M = 100   # m: a braking point further out usually belongs to the previous corner
DECEL_SHARE = 0.7   # share of the car's peak braking g the coach counts on (drift entries aren't threshold stops)


def _track_length(tr):
    """Metres per unit of normalised track position, from the reference trace."""
    x, z, pos = (np.asarray(tr["ref_trace"][k], float) for k in ("x", "z", "pos"))
    if len(x) < 10:
        return None
    step = np.hypot(np.diff(x), np.diff(z))
    span = float(pos.max() - pos.min())
    return float(step[step < 50].sum() / span) if span > 0.05 else None


def _window(v):
    lo, hi = np.percentile(v, 25) - SPEED_PAD, np.percentile(v, 75) + SPEED_PAD
    if hi - lo < MIN_WIDTH:
        mid = (hi + lo) / 2
        lo, hi = mid - MIN_WIDTH / 2, mid + MIN_WIDTH / 2
    return int(round(lo)), int(round(hi))


def corner_targets(tr, sids):
    """Targets for one track from the passes of the given sessions (one car)."""
    out = []
    for c in tr["corners"]:
        passes = [a for a in tr["attempts"] if a["corner"] == c["id"] and a["sid"] in sids]
        ok = [a for a in passes if a["kind"] == "drift" and a["success"] and a.get("v_in") is not None]
        if len(ok) < MIN_OK:
            continue
        lo, hi = _window([a["v_in"] for a in ok])
        bd = [a["brake_dist"] for a in ok if a.get("brake_dist") is not None]
        out.append({"id": c["id"], "pos0": c["pos0"], "kind": c["kind"], "dir": c["dir"], "v_lo": lo, "v_hi": hi,
                    "brake_m": int(round(float(np.median(bd)))) if len(bd) >= MIN_OK and np.median(bd) <= MAX_BRAKE_M else -1,
                    "brake_in": round(float(np.median([a.get("brk_in") or 0 for a in ok])), 2),
                    "n_ok": len(ok), "n_all": len(passes)})
    return out


def write_all(sessions, tracks, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    by = {}
    for s in sessions:
        m = s["summary"]
        by.setdefault((m["car"], m["track"]), set()).add(m["id"])
    n = 0
    for (car, track), sids in sorted(by.items()):
        tr = tracks.get(track)
        if not tr:
            continue
        length = _track_length(tr)
        rows = corner_targets(tr, sids)
        peaks = [s["summary"].get("gforce", {}).get("max_brake") for s in sessions if s["summary"]["id"] in sids]
        peaks = [p for p in peaks if p]
        decel = round(DECEL_SHARE * float(np.median(peaks)), 2) if peaks else 0.5
        if not length or not rows:
            continue
        lines = ["# Drift Logger coach targets: %d session(s), generated %s" % (len(sids), time.strftime("%Y-%m-%d %H:%M")),
                 "car=%s" % car, "track=%s" % track, "length_m=%.1f" % length, "decel_g=%.2f" % decel,
                 "# id,pos0,kind,dir,v_lo,v_hi,brake_m,brake_in,n_ok,n_all"]
        lines += ["%d,%.5f,%s,%s,%d,%d,%d,%.2f,%d,%d" % (r["id"], r["pos0"], r["kind"], r["dir"], r["v_lo"], r["v_hi"],
                                                       r["brake_m"], r["brake_in"], r["n_ok"], r["n_all"]) for r in rows]
        with open(os.path.join(out_dir, "%s__%s.txt" % (car, track)), "w") as f:
            f.write("\n".join(lines) + "\n")
        n += 1
    return n
