# Fitbit Air Export

Copy your own Fitbit Air data (sleep, resting heart rate, HRV, SpO2, steps, active minutes) from the Google Health API to plain files on your computer: one JSON per day, plus `summary.csv` and `summary.md`. Python 3.8+, standard library only, no server.

```
python fitbit_export.py auth            # once
python fitbit_export.py                 # last 3 days into ./fitbit-data
python fitbit_export.py --days 30 --out ~/notes/fitbit
```

Setup steps: https://9-7-8.github.io/fitbit-air-export/ · Privacy: https://9-7-8.github.io/fitbit-air-export/privacy.html

Not affiliated with Google or Fitbit. GPL-3.0-or-later, see `LICENSE`.
