#!/usr/bin/env python3
# Fitbit Air Export <https://github.com/9-7-8/fitbit-air-export>
# Copyright (C) 2026 Ixora. SPDX-License-Identifier: GPL-3.0-or-later
# Free software with no warranty, under the GNU General Public License,
# version 3 or later: see LICENSE in the repository, or <https://www.gnu.org/licenses/>.
"""Copy your own Fitbit (Fitbit Air or any Fitbit on a Google account) data to plain files on your computer.

Uses the Google Health API, which replaced the Fitbit Web API in 2026. Python 3.8+, standard library only.
Nothing is sent anywhere except between your computer and Google.

    python fitbit_export.py auth                 once: sign in with Google, saves a refresh token
    python fitbit_export.py                      pull the last 3 days into ./fitbit-data
    python fitbit_export.py --days 30            backfill
    python fitbit_export.py --out D:/notes/fit   choose the output folder

Output: one JSON file per day (everything the API returned; a night of sleep belongs to the day you woke up),
plus summary.csv and summary.md tables, newest first. Re-running overwrites the same days, so running it daily with
--days 3 fills in anything your phone synced late.

Setup (about 10 minutes, once) is on https://9-7-8.github.io/fitbit-air-export/ :
make a Google Cloud project, enable the Google Health API, create a "Desktop app" OAuth client, and save its JSON
as ~/.google-health/client_secret.json (or set GOOGLE_HEALTH_DIR to another folder).
"""
import csv
import datetime
import http.server
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

SECRETS = Path(os.environ.get("GOOGLE_HEALTH_DIR") or Path.home() / ".google-health")
CLIENT = SECRETS / "client_secret.json"
TOKEN = SECRETS / "token.json"
API = "https://health.googleapis.com/v4/users/me/dataTypes"
SCOPES = " ".join("https://www.googleapis.com/auth/googlehealth." + s for s in (
    "sleep.readonly", "activity_and_fitness.readonly", "health_metrics_and_measurements.readonly"))

# Summed per day by the API (dailyRollUp).
ROLLUPS = ["steps", "active-zone-minutes", "active-minutes", "distance", "total-calories", "floors"]
# One value per day, listed by date: REST id -> filter field.
DAILIES = {
    "daily-resting-heart-rate": "daily_resting_heart_rate.date",
    "daily-heart-rate-variability": "daily_heart_rate_variability.date",
    "daily-oxygen-saturation": "daily_oxygen_saturation.date",
    "daily-respiratory-rate": "daily_respiratory_rate.date",
}
COLUMNS = ["Date", "Slept", "Bed-Wake", "Resting HR", "HRV (ms)", "SpO2 %", "Steps", "Zone min"]


def client():
    data = json.loads(CLIENT.read_text())
    return data.get("installed") or data.get("web")


def post_form(url, fields):
    req = urllib.request.Request(url, urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def auth():
    """Desktop-app OAuth with a loopback redirect: the code comes back to a one-shot local server, nothing to paste."""
    c, state, got = client(), secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            got.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Fitbit Air Export is connected. You can close this tab.</h2>".encode())

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = "http://127.0.0.1:%d" % server.server_port
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state})
    print("Opening Google sign-in. If no browser opens, visit:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if got.get("state", [""])[0] != state or "code" not in got:
        sys.exit("Sign-in failed: %s" % got.get("error", ["state mismatch"])[0])
    tok = post_form("https://oauth2.googleapis.com/token", {
        "code": got["code"][0], "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    if "refresh_token" not in tok:
        sys.exit("Google sent no refresh token; run auth again.")
    TOKEN.write_text(json.dumps({"refresh_token": tok["refresh_token"]}))
    print("Saved", TOKEN)


def access_token():
    c = client()
    return post_form("https://oauth2.googleapis.com/token", {
        "refresh_token": json.loads(TOKEN.read_text())["refresh_token"], "client_id": c["client_id"],
        "client_secret": c["client_secret"], "grant_type": "refresh_token"})["access_token"]


def call(token, path, params=None, body=None):
    url = API + "/" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                 {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"error": "%s %s" % (e.code, e.read().decode(errors="replace")[:500])}


def list_points(token, dtype, flt, page_size=None):
    points, page = [], None
    while True:
        params = {"filter": flt}
        if page_size:
            params["pageSize"] = page_size
        if page:
            params["pageToken"] = page
        r = call(token, dtype + "/dataPoints", params)
        if "error" in r:
            return r
        points += r.get("dataPoints", [])
        page = r.get("nextPageToken")
        if not page:
            return points


def rfc(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def civil(d):
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def pull_day(token, day):
    start = datetime.datetime.combine(day, datetime.time()).astimezone()
    end = start + datetime.timedelta(days=1)
    rec = {"date": day.isoformat(), "pulled": datetime.datetime.now().isoformat(timespec="minutes")}
    # The night (and any nap) that ended on this day.
    rec["sleep"] = list_points(token, "sleep", 'sleep.interval.end_time >= "%s" AND sleep.interval.end_time < "%s"'
                               % (rfc(start), rfc(end)), page_size=25)
    for dtype, field in DAILIES.items():
        rec[dtype] = list_points(token, dtype, '%s >= "%s" AND %s < "%s"'
                                 % (field, day.isoformat(), field, (day + datetime.timedelta(days=1)).isoformat()))
    for dtype in ROLLUPS:
        r = call(token, dtype + "/dataPoints:dailyRollUp",
                 body={"range": {"start": civil(day), "end": civil(day + datetime.timedelta(days=1))},
                       "windowSizeDays": 1})
        rec[dtype] = r.get("rollupDataPoints", r) if "error" not in r else r
    return rec


def first_number(obj, prefer=()):
    """The first number inside obj, trying the preferred keys first (the API's field names vary by data type)."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        try:
            return float(obj)
        except ValueError:
            return None
    if isinstance(obj, dict):
        for k in prefer:
            if k in obj and first_number(obj[k]) is not None:
                return first_number(obj[k])
        for k, v in obj.items():
            if k in ("civilStartTime", "civilEndTime", "date", "interval", "name", "dataSource"):
                continue
            n = first_number(v, prefer)
            if n is not None:
                return n
    if isinstance(obj, list):
        for v in obj:
            n = first_number(v, prefer)
            if n is not None:
                return n
    return None


def clock(ts):
    if not ts:
        return ""
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M")


def row(rec):
    sleeps = rec["sleep"] if isinstance(rec["sleep"], list) else []
    main = max(sleeps, key=lambda p: first_number(p.get("sleep", {}).get("summary", {}), ("minutesAsleep",)) or 0,
               default=None)
    slept = bed = ""
    if main:
        s = main.get("sleep", {})
        mins = first_number(s.get("summary", {}), ("minutesAsleep",))
        slept = "%d:%02d" % divmod(int(mins), 60) if mins else ""
        bed = "%s-%s" % (clock(s.get("interval", {}).get("startTime")), clock(s.get("interval", {}).get("endTime")))

    def num(key, prefer=(), fmt="%d"):
        v = rec.get(key)
        n = None if isinstance(v, dict) and "error" in v else first_number(v, prefer)
        return fmt % n if n is not None else ""

    return [rec["date"], slept, bed, num("daily-resting-heart-rate", ("beatsPerMinute",)),
            num("daily-heart-rate-variability", ("averageHeartRateVariabilityMilliseconds",)),
            num("daily-oxygen-saturation", ("averagePercentage",), "%.1f"), num("steps"),
            num("active-zone-minutes")]


def write_summaries(out):
    rows = [row(json.loads(p.read_text())) for p in sorted(out.glob("????-??-??.json"), reverse=True)]
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([COLUMNS] + rows)
    md = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    md += ["| " + " | ".join(r) + " |" for r in rows]
    (out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    if not CLIENT.exists():
        sys.exit("No OAuth client found at %s; see the setup steps at the top of this file." % CLIENT)
    if sys.argv[1:2] == ["auth"]:
        return auth()
    if not TOKEN.exists():
        sys.exit("Not connected yet: run  python fitbit_export.py auth")
    try:
        token = access_token()
    except urllib.error.HTTPError as e:
        sys.exit("Google refused the saved sign-in (%s); run auth again." % e.code)
    out = Path(arg("--out", "fitbit-data"))
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today()
    for i in range(int(arg("--days", 3))):
        day = today - datetime.timedelta(days=i)
        rec = pull_day(token, day)
        (out / (day.isoformat() + ".json")).write_text(json.dumps(rec, indent=1), encoding="utf-8")
        errors = [k for k, v in rec.items() if isinstance(v, dict) and "error" in v]
        print(day, "ok" if not errors else "could not read: " + ", ".join(errors))
    write_summaries(out)
    print("Wrote", out.resolve())


if __name__ == "__main__":
    main()
