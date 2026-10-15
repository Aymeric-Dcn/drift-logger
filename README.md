# Drift Logger

Telemetry recorder and driving coach for drifting and touge in **Assetto Corsa**.

An in-game app records the car's state about 90 times per second. A Python script then finds every drift, wall contact, near spin and throttle lift, rebuilds the road's corners from the car's path, and tracks progress from one session to the next. Everything ends up in a single HTML dashboard.

*[Version française](README.fr.md)*

![Dashboard](docs/dashboard.png)

## What it measures

| Metric | How it is detected |
|---|---|
| Drifts | body slip angle above 12° at more than 20 km/h for at least 0.4 s |
| Wall contacts | acceleration spike above 3.5 g, split into *in grip* and *while drifting* |
| Near spins | slip angle above 75° |
| Foot off while drifting | throttle below 5% for more than 0.15 s during a drift |
| Rear lock | engine speed collapsing below 1,500 rpm while in gear (engine braking locks the rear) |
| Late counter-steer | more than 0.5 s between the rear stepping out (15°) and opposite lock |
| Throttle stabs | full throttle to foot off, or back, in under 0.35 s |
| Throttle use | share of drift time spent off / low / modulated (40–90%) / flat out |

Each drift is also broken down by how it was started (transition, feint, clutch kick, braking, lift-off, power over), how it was held and how it ended. A light touch on exit (under 10 km/h lost) still counts as a success. Every pass through every corner is compared: success rate per corner and per technique, entry speed and braking point of successful vs failed passes, dominant reflex, and the most likely cause of each failure (too fast on entry, foot off, rear lock, late counter-steer, too much angle, line).

## Replay

The car is redrawn as a top-down rectangle: body, light window at the front, front wheels turned by the steering input, and an arrow for the real direction of travel. The gap between the arrow and the nose is the slip angle.

- **Session tab → Replay**: the whole run with a chase camera (following the road or fixed map), a mini-map, outlines of the car a moment earlier, and live speed, angle, gear, pedals and steering. Each drift in the table has a ▶ button, and clicking the telemetry traces moves the replay there.
- **Corners tab → Replay the corner**: up to three passes of the same corner start together at the corner entry, so you can see who brakes earlier, who carries more angle and who hits the wall. *Ghosts* draws the car every 0.3 s along each pass.

AC's Python API has no world heading, so it is rebuilt: the yaw rate is integrated and its slow drift is corrected with the direction of travel minus the slip angle (about 0.4° median error).

Corners are detected from the path's curvature (radius under 90 m) and numbered by their position along the track spline, so a corner keeps its number from one session to the next. Each corner gets cumulative stats (passes, drifts, average angle, impacts, spins) and a trouble score.

## Layout

```
drift_logger/                  copy this folder to assettocorsa/apps/python/
├── drift_logger.py            in-game app (Python 3.3, Assetto Corsa's interpreter)
├── logs/                      CSV files written by the app (created on first REC)
└── analysis/
    ├── analyse.py             logs -> sessions/, tracks/, history.json, report.json
    ├── technique.py           initiation, ending, score and causes of each drift
    ├── corner_report.py       corner-by-corner and per-technique comparisons
    ├── build_dashboard.py     -> dashboard.html
    ├── update_dashboard.bat   one-click update (Windows)
    ├── dashboard_template.html
    └── requirements.txt
```

## Usage

1. Copy `drift_logger/` into `steamapps/common/assettocorsa/apps/python/`.
2. In Content Manager: *Settings → Assetto Corsa → Apps*, tick **drift_logger**.
3. On track, open **Drift Logger** from the right-hand app bar, press **REC**, drive, press **STOP**.
4. On Windows, double-click `analysis/update_dashboard.bat` (installs numpy/pandas if needed, runs the analysis and opens the dashboard), or run it by hand:

```bash
cd drift_logger/analysis
pip install -r requirements.txt
python analyse.py
python build_dashboard.py
```

5. Open `analysis/dashboard.html` in a browser. The interface switches between French and English.

Coaching notes are read from `analysis/coaching.json` (one entry per session, `fr` and `en` text). Your recorded data (`logs/`, `sessions/`, `tracks/`, `history.json`, `coaching.json`, `dashboard.html`) is ignored by git.

## Recorded channels

speed, throttle, brake, clutch, gear, rpm, steering angle, 3-axis acceleration, local velocity, yaw / pitch / roll rates, slip angle and slip ratio per wheel, load per wheel, position along the track spline, world position.

## Notes

- Tested with Assetto Corsa, Content Manager and Custom Shaders Patch.
- Slip angle is computed from the car's local velocity: `atan2(vx, vz)`.
- Thresholds are constants at the top of `analyse.py`.
