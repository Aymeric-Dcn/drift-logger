# ==========================================================================
# Drift Logger - telemetry recorder for Assetto Corsa
# REC / STOP button: writes one CSV per session to apps/python/drift_logger/logs
# Python 3.3 compatible (Assetto Corsa's embedded interpreter)
# ==========================================================================

import ac
import acsys
import os
import time
import math

APP_NAME = "Drift Logger"
LOG_DIR = "apps/python/drift_logger/logs"
MAX_HZ = 100.0          # max recording rate
FLUSH_EVERY = 2.0       # write to disk every 2 s

app_window = 0
btn_rec = 0
lbl_status = 0

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
    ]
    ok = []
    for name, fn in candidates:
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
        track = safe(ac.getTrackName(0))
        try:
            cfg = safe(ac.getTrackConfiguration(0))
            if cfg:
                track = track + "-" + cfg
        except Exception:
            pass
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


def on_click(*args):
    if recording:
        stop_recording()
    else:
        start_recording()


def acMain(ac_version):
    global app_window, btn_rec, lbl_status
    app_window = ac.newApp(APP_NAME)
    ac.setSize(app_window, 260, 90)
    ac.setTitle(app_window, APP_NAME)

    btn_rec = ac.addButton(app_window, "REC")
    ac.setPosition(btn_rec, 10, 35)
    ac.setSize(btn_rec, 70, 40)
    ac.addOnClickedListener(btn_rec, on_click)

    lbl_status = ac.addLabel(app_window, "Ready")
    ac.setPosition(lbl_status, 90, 45)
    ac.setFontSize(lbl_status, 14)
    return APP_NAME


def acUpdate(deltaT):
    global t_rec, t_since_sample, t_since_flush, n_samples
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
