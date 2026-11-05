# ==========================================================================
# Drift Logger - telemetry recorder for Assetto Corsa
# REC / STOP button: writes one CSV per session to apps/python/drift_logger/logs
# Coach: before each corner, shows the turn-in speed window and braking point
# that worked for you before (targets written by analysis/analyse.py)
# Python 3.3 compatible (Assetto Corsa's embedded interpreter)
# ==========================================================================

import ac
import acsys
import os
import time
import math

APP_NAME = "Drift Logger"
LOG_DIR = "apps/python/drift_logger/logs"
COACH_DIR = "apps/python/drift_logger/coach"
LANG = "fr"             # "fr" or "en"
SHOW_FROM_M = 250.0     # start coaching a corner this far before it
BRAKE_ON = 0.3          # pedal above this = braking (same threshold as the analysis)
VERDICT_S = 3.5         # how long the verdict stays up
BRAKE_TOL_M = 15.0      # braking point tolerance before calling it early / late
BRAKE_WINDOW_M = 150.0  # only braking that starts this close counts for the corner (as in the analysis)
REACTION_S = 0.3        # added to the braking distance: time to get on the pedal
MAX_HZ = 100.0          # max recording rate
FLUSH_EVERY = 2.0       # write to disk every 2 s

app_window = 0
btn_rec = 0
lbl_status = 0
btn_coach = 0
lbl_c1 = lbl_c2 = lbl_c3 = 0

TXT = {
    "fr": {"kind": {"hairpin": "épingle", "tight": "serré", "fast": "rapide"}, "dir": {"left": "G", "right": "D"},
           "target": "V%d %s %s · cible %d-%d km/h", "brake_in": "Freine dans %d m", "brake_now": "FREINE",
           "speed": "%d km/h", "keep": "Garde ~%d %% de frein en tournant", "ok": "Bon : %d km/h",
           "fast": "Trop vite : +%d km/h", "slow": "Trop lent : -%d km/h", "early": "freinage trop tôt de %d m",
           "late": "freinage trop tard de %d m", "nobrake": "pas de freinage", "released": "frein relâché avant de tourner",
           "none": "Coach : pas encore de cibles pour cette voiture ici", "ready": "Coach : %d virages",
           "on": "COACH", "off": "coach"},
    "en": {"kind": {"hairpin": "hairpin", "tight": "tight", "fast": "fast"}, "dir": {"left": "L", "right": "R"},
           "target": "C%d %s %s · target %d-%d km/h", "brake_in": "Brake in %d m", "brake_now": "BRAKE",
           "speed": "%d km/h", "keep": "Keep ~%d%% brake as you turn in", "ok": "Good: %d km/h",
           "fast": "Too fast: +%d km/h", "slow": "Too slow: -%d km/h", "early": "braked %d m early",
           "late": "braked %d m late", "nobrake": "no braking", "released": "brake released before turn-in",
           "none": "Coach: no targets for this car here yet", "ready": "Coach: %d corners",
           "on": "COACH", "off": "coach"},
}
T = TXT.get(LANG, TXT["en"])

GREEN, ORANGE, BLUE, WHITE, GREY = (0.4, 1, 0.4, 1), (1, 0.6, 0.2, 1), (0.4, 0.7, 1, 1), (1, 1, 1, 1), (0.7, 0.7, 0.7, 1)

coach_on = True
coach = None            # {"length": metres per unit of track_pos, "corners": [...] sorted by pos0}
coach_file = ""
coach_mtime = 0.0
t_check = 0.0
nxt = None              # corner being approached
brake_d = None          # distance to the corner when the last braking started
brake_need = 0.0        # where the braking should have started, given the speed at that moment
was_braking = False
last_pos = -1.0
verdict_t = 0.0

recording = False
log_file = None
file_name = ""
t_rec = 0.0
t_since_sample = 0.0
t_since_flush = 0.0
n_samples = 0
channels = []           # list of (column_name, getter)


def cs(key, idx=None):
    if idx is None:
        return lambda: ac.getCarState(0, key)
    return lambda: ac.getCarState(0, key)[idx]


def opt(name, idx=None):
    """Channel that may not exist in every game / CSP version."""
    key = getattr(acsys.CS, name, None)
    return None if key is None else cs(key, idx)


def build_channels():
    """Probe each channel once and keep only the ones that work."""
    candidates = [
        ("speed_kmh", cs(acsys.CS.SpeedKMH)),
        ("throttle", cs(acsys.CS.Gas)),
        ("brake", cs(acsys.CS.Brake)),
        ("clutch", cs(acsys.CS.Clutch)),
        ("gear", lambda: ac.getCarState(0, acsys.CS.Gear) - 1),
        ("rpm", cs(acsys.CS.RPM)),
        ("steer_deg", cs(acsys.CS.Steer)),
        ("acc_g_x", cs(acsys.CS.AccG, 0)),
        ("acc_g_y", cs(acsys.CS.AccG, 1)),
        ("acc_g_z", cs(acsys.CS.AccG, 2)),
        ("vel_x", cs(acsys.CS.LocalVelocity, 0)),
        ("vel_y", cs(acsys.CS.LocalVelocity, 1)),
        ("vel_z", cs(acsys.CS.LocalVelocity, 2)),
        ("yaw_rate", cs(acsys.CS.LocalAngularVelocity, 1)),
        ("pitch_rate", cs(acsys.CS.LocalAngularVelocity, 0)),
        ("roll_rate", cs(acsys.CS.LocalAngularVelocity, 2)),
        ("slip_angle_fl", cs(acsys.CS.SlipAngle, 0)),
        ("slip_angle_fr", cs(acsys.CS.SlipAngle, 1)),
        ("slip_angle_rl", cs(acsys.CS.SlipAngle, 2)),
        ("slip_angle_rr", cs(acsys.CS.SlipAngle, 3)),
        ("slip_ratio_fl", cs(acsys.CS.SlipRatio, 0)),
        ("slip_ratio_fr", cs(acsys.CS.SlipRatio, 1)),
        ("slip_ratio_rl", cs(acsys.CS.SlipRatio, 2)),
        ("slip_ratio_rr", cs(acsys.CS.SlipRatio, 3)),
        ("load_fl", cs(acsys.CS.Load, 0)),
        ("load_fr", cs(acsys.CS.Load, 1)),
        ("load_rl", cs(acsys.CS.Load, 2)),
        ("load_rr", cs(acsys.CS.Load, 3)),
        ("track_pos", cs(acsys.CS.NormalizedSplinePosition)),
        ("world_x", cs(acsys.CS.WorldPosition, 0)),
        ("world_y", cs(acsys.CS.WorldPosition, 1)),
        ("world_z", cs(acsys.CS.WorldPosition, 2)),
        ("wheel_speed_fl", opt("WheelAngularSpeed", 0)),
        ("wheel_speed_fr", opt("WheelAngularSpeed", 1)),
        ("wheel_speed_rl", opt("WheelAngularSpeed", 2)),
        ("wheel_speed_rr", opt("WheelAngularSpeed", 3)),
        ("handbrake", opt("Handbrake")),
    ]
    ok = []
    for name, fn in candidates:
        if fn is None:
            ac.console("Drift Logger: channel unavailable %s" % name)
            continue
        try:
            float(fn())
            ok.append((name, fn))
        except Exception:
            ac.console("Drift Logger: channel unavailable %s" % name)
    return ok


def safe(s):
    out = ""
    for c in str(s):
        out += c if (c.isalnum() or c in "-_") else "_"
    return out


def start_recording():
    global recording, log_file, file_name, t_rec, t_since_sample, t_since_flush, n_samples, channels
    try:
        if not os.path.isdir(LOG_DIR):
            os.makedirs(LOG_DIR)
        channels = build_channels()
        car = safe(ac.getCarName(0))
        track = track_id()
        stamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = "%s_%s_%s.csv" % (stamp, car, track)
        log_file = open(os.path.join(LOG_DIR, file_name), "w")
        log_file.write("# car=%s track=%s\n" % (car, track))
        log_file.write("time_s," + ",".join([c[0] for c in channels]) + "\n")
        t_rec = 0.0
        t_since_sample = 1.0
        t_since_flush = 0.0
        n_samples = 0
        recording = True
        ac.setText(btn_rec, "STOP")
        ac.console("Drift Logger: recording -> %s" % file_name)
    except Exception as e:
        ac.setText(lbl_status, "Error: %s" % e)
        ac.console("Drift Logger ERROR start: %s" % e)


def stop_recording():
    global recording, log_file
    recording = False
    try:
        if log_file:
            log_file.flush()
            log_file.close()
    except Exception as e:
        ac.console("Drift Logger ERROR stop: %s" % e)
    log_file = None
    ac.setText(btn_rec, "REC")
    ac.setText(lbl_status, "Saved: %d samples (%.0f s)" % (n_samples, t_rec))


def track_id():
    track = safe(ac.getTrackName(0))
    try:
        cfg = safe(ac.getTrackConfiguration(0))
        if cfg:
            track = track + "-" + cfg
    except Exception:
        pass
    return track


def load_coach():
    """Read the targets written by the analysis for this car on this road."""
    global coach, coach_file, coach_mtime, nxt, brake_d
    coach, nxt, brake_d = None, None, None
    try:
        coach_file = os.path.join(COACH_DIR, "%s__%s.txt" % (safe(ac.getCarName(0)), track_id()))
        if not os.path.isfile(coach_file):
            show(T["none"], "", "", GREY)
            return
        coach_mtime = os.path.getmtime(coach_file)
        length, decel, corners = None, 0.5, []
        for line in open(coach_file):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("length_m="):
                length = float(line.split("=", 1)[1])
            elif line.startswith("decel_g="):
                decel = float(line.split("=", 1)[1])
            elif "=" not in line:
                f = line.split(",")
                corners.append({"id": int(f[0]), "pos0": float(f[1]), "kind": f[2], "dir": f[3],
                                "lo": int(f[4]), "hi": int(f[5]), "brake_m": int(f[6]), "brake_in": float(f[7])})
        if length and corners:
            corners.sort(key=lambda c: c["pos0"])
            coach = {"length": length, "decel": max(0.2, decel), "corners": corners}
            show(T["ready"] % len(corners), "", "", GREY)
        else:
            show(T["none"], "", "", GREY)
    except Exception as e:
        coach = None
        ac.console("Drift Logger coach: %s" % e)


def show(a, b, c, color=WHITE, color2=None):
    try:
        ac.setText(lbl_c1, a)
        ac.setText(lbl_c2, b)
        ac.setText(lbl_c3, c)
        ac.setFontColor(lbl_c1, *WHITE)
        ac.setFontColor(lbl_c2, *(color2 or color))
        ac.setFontColor(lbl_c3, *color)
    except Exception:
        pass


def next_corner(pos):
    for c in coach["corners"]:
        if c["pos0"] > pos:
            return c
    return coach["corners"][0]


def brake_point(c, v):
    """Distance before the corner where braking should start at this speed: the larger of the braking
    point that worked before and the distance this car needs to get from v down into the window."""
    vt = (c["lo"] + c["hi"]) / 2.0
    need = 0.0
    if v > vt:
        ms, mt = v / 3.6, vt / 3.6
        need = (ms * ms - mt * mt) / (2 * 9.81 * coach["decel"]) + ms * REACTION_S
    return max(float(c["brake_m"]), need) if c["brake_m"] >= 0 else need


def judge(c, v, b):
    """Verdict at the corner start: speed window first, then braking point and trail brake."""
    if v > c["hi"]:
        head, col = T["fast"] % (v - c["hi"]), ORANGE
    elif v < c["lo"]:
        head, col = T["slow"] % (c["lo"] - v), BLUE
    else:
        head, col = T["ok"] % v, GREEN
    notes = []
    if brake_d is None:
        if v > c["hi"]:
            notes.append(T["nobrake"])
    elif brake_need - brake_d > BRAKE_TOL_M and v > c["hi"]:  # late only matters if it left you too fast
        notes.append(T["late"] % (brake_need - brake_d))
    elif brake_d - brake_need > BRAKE_TOL_M and v < c["lo"]:  # early only matters if it cost speed
        notes.append(T["early"] % (brake_d - brake_need))
    if c["brake_in"] >= 0.15 and b < 0.08 and brake_d is not None:
        notes.append(T["released"])
    if notes and col == GREEN:
        col = ORANGE
    return head, " · ".join(notes), col


def coach_update(dt):
    global nxt, brake_d, brake_need, was_braking, last_pos, verdict_t, t_check
    t_check += dt
    if t_check > 5.0:  # targets rewritten by a new analysis
        t_check = 0.0
        try:
            if coach_file and os.path.isfile(coach_file) and os.path.getmtime(coach_file) != coach_mtime:
                load_coach()
        except Exception:
            pass
    if not coach_on or coach is None:
        return
    pos = ac.getCarState(0, acsys.CS.NormalizedSplinePosition)
    v = ac.getCarState(0, acsys.CS.SpeedKMH)
    b = ac.getCarState(0, acsys.CS.Brake)
    L = coach["length"]
    if nxt is None or pos < last_pos - 0.002 or (pos - last_pos) * L > 50:  # start, reset, teleport
        nxt, brake_d = next_corner(pos), None
    last_pos = pos
    d = (nxt["pos0"] - pos) * L
    if d < -L * 0.5:  # lap wrap
        d += L
    verdict_t = max(0.0, verdict_t - dt)
    if d <= 0:  # corner start reached: judge it
        head, notes, col = judge(nxt, int(round(v)), b)
        k = T["kind"].get(nxt["kind"], nxt["kind"])
        show("V%d %s" % (nxt["id"], k) if LANG == "fr" else "C%d %s" % (nxt["id"], k), head, notes, col, col)
        verdict_t = VERDICT_S
        nxt, brake_d = next_corner(pos + 1e-6), None
        return
    braking = b > BRAKE_ON
    bp = brake_point(nxt, v)
    if braking and d <= BRAKE_WINDOW_M and (not was_braking or brake_d is None):
        brake_d = d if not was_braking else BRAKE_WINDOW_M  # already braking when entering the window
        brake_need = bp
    was_braking = braking
    if d > SHOW_FROM_M or verdict_t > 0:
        if verdict_t <= 0 and d > SHOW_FROM_M:
            show("", "", "")
        return
    c = nxt
    top = T["target"] % (c["id"], T["kind"].get(c["kind"], c["kind"]), T["dir"].get(c["dir"], ""), c["lo"], c["hi"])
    vi = int(round(v))
    scol = GREEN if c["lo"] <= vi <= c["hi"] else (ORANGE if vi > c["hi"] else BLUE)
    if braking or bp <= 0:
        mid = T["speed"] % vi
    elif d - bp > 0:
        mid, scol = T["brake_in"] % (d - bp), WHITE
    else:
        mid, scol = T["brake_now"], ORANGE
    low = T["keep"] % int(round(c["brake_in"] * 100)) if c["brake_in"] >= 0.15 else ""
    show(top, mid, low, GREY, scol)


def on_coach(*args):
    global coach_on
    coach_on = not coach_on
    ac.setText(btn_coach, T["on"] if coach_on else T["off"])
    if not coach_on:
        show("", "", "")
    else:
        load_coach()


def on_click(*args):
    if recording:
        stop_recording()
    else:
        start_recording()


def acMain(ac_version):
    global app_window, btn_rec, lbl_status, btn_coach, lbl_c1, lbl_c2, lbl_c3
    app_window = ac.newApp(APP_NAME)
    ac.setSize(app_window, 340, 175)
    ac.setTitle(app_window, APP_NAME)

    btn_rec = ac.addButton(app_window, "REC")
    ac.setPosition(btn_rec, 10, 35)
    ac.setSize(btn_rec, 70, 40)
    ac.addOnClickedListener(btn_rec, on_click)

    lbl_status = ac.addLabel(app_window, "Ready")
    ac.setPosition(lbl_status, 90, 45)
    ac.setFontSize(lbl_status, 14)

    btn_coach = ac.addButton(app_window, T["on"])
    ac.setPosition(btn_coach, 260, 35)
    ac.setSize(btn_coach, 70, 40)
    ac.addOnClickedListener(btn_coach, on_coach)

    lbl_c1 = ac.addLabel(app_window, "")
    ac.setPosition(lbl_c1, 10, 85)
    ac.setFontSize(lbl_c1, 14)
    lbl_c2 = ac.addLabel(app_window, "")
    ac.setPosition(lbl_c2, 10, 106)
    ac.setFontSize(lbl_c2, 24)
    lbl_c3 = ac.addLabel(app_window, "")
    ac.setPosition(lbl_c3, 10, 142)
    ac.setFontSize(lbl_c3, 14)
    load_coach()
    return APP_NAME


def acUpdate(deltaT):
    global t_rec, t_since_sample, t_since_flush, n_samples
    try:
        coach_update(deltaT)
    except Exception as e:
        ac.console("Drift Logger coach ERROR: %s" % e)
    if not recording or log_file is None:
        return
    t_rec += deltaT
    t_since_sample += deltaT
    t_since_flush += deltaT
    if t_since_sample < 1.0 / MAX_HZ:
        return
    t_since_sample = 0.0
    try:
        vals = []
        for name, fn in channels:
            try:
                vals.append("%.4f" % float(fn()))
            except Exception:
                vals.append("")
        log_file.write("%.3f," % t_rec + ",".join(vals) + "\n")
        n_samples += 1
        if t_since_flush >= FLUSH_EVERY:
            log_file.flush()
            t_since_flush = 0.0
            ac.setText(lbl_status, "REC %.0f s - %d samples" % (t_rec, n_samples))
    except Exception as e:
        ac.console("Drift Logger ERROR update: %s" % e)


def acShutdown(*args):
    if recording:
        stop_recording()
