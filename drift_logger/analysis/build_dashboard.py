"""Build the dashboard: injects history, sessions, corners and coaching notes
into dashboard_template.html -> dashboard.html (opens in any browser)."""
import glob
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main():
    sessions = {}
    for p in sorted(glob.glob(os.path.join(HERE, "sessions", "*.json"))):
        s = load(p, None)
        if s:
            s.pop("corners", None)
            sessions[s["summary"]["id"]] = s
    tracks = {}
    for p in glob.glob(os.path.join(HERE, "tracks", "*.json")):
        t = load(p, None)
        if t:
            for c in t["corners"]:
                c.pop("by_session", None)
            tracks[t["track"]] = t
    data = {
        "history": load(os.path.join(HERE, "history.json"), []),
        "sessions": sessions,
        "tracks": tracks,
        "coaching": load(os.path.join(HERE, "coaching.json"), []),
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8") as f:
        html = f.read()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    with open(os.path.join(HERE, "dashboard.html"), "w", encoding="utf-8") as f:
        f.write(html.replace("__DATA__", blob))
    print("dashboard.html: %d session(s), %.0f KB" % (len(sessions), len(blob) / 1024))


if __name__ == "__main__":
    main()
