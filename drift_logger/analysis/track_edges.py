"""
Real road edges, read from the track's AI line (ai/fast_lane.ai).

Every point of the AI spline stores how far the road extends to its left and
to its right. Offsetting the spline by those distances gives both edges of the
road, which is far closer to the walls than anything rebuilt from the car's
own path.

The Assetto Corsa folder is found from the AC_ROOT environment variable or the
usual Steam locations. Without it (or for a layout with no AI line) the
dashboard falls back to a band drawn around the driven line.
"""
import hashlib
import os
import struct

import numpy as np

AC_ROOTS = [
    os.environ.get("AC_ROOT"),
    r"C:\Program Files (x86)\Steam\steamapps\common\assettocorsa",
    r"C:\Program Files\Steam\steamapps\common\assettocorsa",
    r"D:\SteamLibrary\steamapps\common\assettocorsa",
    r"E:\SteamLibrary\steamapps\common\assettocorsa",
]
STEP_M = 3.0  # keep one edge point every ~3 m


def find_ai(track):
    """track is '<folder>' or '<folder>-<layout>'; the folder name may itself contain '-'."""
    splits = [(track, "")] + [(track[:i], track[i + 1:]) for i, ch in enumerate(track) if ch == "-"]
    for root in filter(None, AC_ROOTS):
        base = os.path.join(root, "content", "tracks")
        for name, layout in splits:
            p = os.path.join(base, name, layout, "ai", "fast_lane.ai")
            if os.path.isfile(p):
                return p
    return None


def read_ai(path):
    """Spline x, z and side distances. Supports the common version 7 layout:
    header (version, count, lap time, sample count), count x (x y z length id),
    extra count, count x 18 floats with sideLeft / sideRight at index 5 / 6."""
    with open(path, "rb") as f:
        b = f.read()
    version, n = struct.unpack_from("<2i", b, 0)
    if version != 7 or n < 10:
        return None
    pts = np.frombuffer(b, dtype="<f4", count=n * 5, offset=16).reshape(n, 5)
    off = 16 + n * 20
    m = struct.unpack_from("<i", b, off)[0]
    if m != n:
        return None
    ex = np.frombuffer(b, dtype="<f4", count=n * 18, offset=off + 4).reshape(n, 18)
    return pts[:, 0].astype(float), pts[:, 2].astype(float), ex[:, 5].astype(float), ex[:, 6].astype(float)


def edges(track):
    path = find_ai(track)
    if not path:
        return None
    try:
        data = read_ai(path)
    except (OSError, struct.error, ValueError):
        return None
    if data is None:
        return None
    x, z, left, right = data
    tx, tz = np.gradient(x), np.gradient(z)
    norm = np.hypot(tx, tz)
    norm[norm == 0] = 1
    tx, tz = tx / norm, tz / norm
    nx, nz = tz, -tx                      # unit vector toward the spline's left side
    step = np.hypot(np.diff(x), np.diff(z))
    keep = max(1, int(round(STEP_M / max(float(np.median(step)), 0.1))))
    idx = np.r_[np.arange(0, len(x), keep), len(x) - 1]
    r1 = lambda v: np.round(v[idx], 1).tolist()
    return {"l": {"x": r1(x + left * nx), "z": r1(z + left * nz)},
            "r": {"x": r1(x - right * nx), "z": r1(z - right * nz)}}


def road_key(track):
    """Same road, different track folder (e.g. a wet version of the same map): identical AI line.
    Returns a short fingerprint of the AI line, or the track name when it can't be read."""
    path = find_ai(track)
    if not path:
        return track
    try:
        data = read_ai(path)
    except (OSError, struct.error, ValueError):
        return track
    if data is None:
        return track
    x, z = data[0], data[1]
    pts = np.round(np.c_[x, z], 1).tobytes()
    return "road_" + hashlib.md5(pts).hexdigest()[:10]
