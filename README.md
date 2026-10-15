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

Corners are detected from the path's curvature (radius under 90 m) and numbered by their position along the track spline, so a corner keeps its number from one session to the next. Each corner gets cumulative stats (passes, drifts, average angle, impacts, spins) and a trouble score.

## Layout

```
drift_logger/                  copy this folder to assettocorsa/apps/python/
├── drift_logger.py            in-game app (Python 3.3, Assetto Corsa's interpreter)
├── logs/                      CSV files written by the app (created on first REC)
└── analysis/
    ├── analyse.py             logs -> sessions/, tracks/, history.json
    ├── build_dashboard.py     -> dashboard.html
    ├── dashboard_template.html
    └── requirements.txt
```

## Usage

1. Copy `drift_logger/` into `steamapps/common/assettocorsa/apps/python/`.
2. In Content Manager: *Settings → Assetto Corsa → Apps*, tick **drift_logger**.
3. On track, open **Drift Logger** from the right-hand app bar, press **REC**, drive, press **STOP**.
4. Run the analysis and build the dashboard:

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
