"""
Power curve and weight of a car, read from its ui/ui_car.json in the Assetto
Corsa folder (the same AC_ROOT lookup as track_edges). Those files often hold
raw line breaks inside strings, so only the fields needed are pulled out.
"""
import json
import os
import re

from track_edges import AC_ROOTS


def _num(s):
    m = re.search(r"[\d.]+", s or "")
    return float(m.group()) if m else None


def _curve(text, key):
    m = re.search(r'"%s"\s*:\s*\[(.*?)\]\s*\]' % key, text, re.S)
    if not m:
        return []
    pts = re.findall(r'\[\s*"?([\d.]+)"?\s*,\s*"?([\d.]+)"?\s*\]', m.group(1) + "]")
    return sorted((float(a), float(b)) for a, b in pts)


def load(car):
    for root in filter(None, AC_ROOTS):
        p = os.path.join(root, "content", "cars", car, "ui", "ui_car.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        name = re.search(r'"name"\s*:\s*"([^"]*)"', text)
        bhp = re.search(r'"bhp"\s*:\s*"([^"]*)"', text)
        weight = re.search(r'"weight"\s*:\s*"([^"]*)"', text)
        power = _curve(text, "powerCurve")
        torque = _curve(text, "torqueCurve")
        if not power:
            continue
        peak = max(power, key=lambda p: p[1])
        spec = {"name": name.group(1) if name else car,
                "bhp": _num(bhp.group(1)) if bhp else peak[1],
                "weight": _num(weight.group(1)) if weight else None,
                "power": [[int(r), round(h, 1)] for r, h in power],
                "torque": [[int(r), round(t, 1)] for r, t in torque],
                "peak_rpm": int(peak[0]), "peak_hp": peak[1]}
        # rev band where the engine gives at least 85% of its peak power
        band = [r for r, h in power if h >= 0.85 * peak[1]]
        spec["band"] = [int(min(band)), int(max(band))] if band else None
        if spec["weight"] and spec["bhp"]:
            spec["kg_per_hp"] = round(spec["weight"] / spec["bhp"], 2)
        return spec
    return None
