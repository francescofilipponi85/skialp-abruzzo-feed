#!/usr/bin/env python3
"""Esporta SOLO meteo pubblico dal NAS: nessuna password, IP, ID WhatsApp o SQLite."""
import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROME = ZoneInfo("Europe/Rome")
NAMES = {
    "gran_sasso_ovest": "Gran Sasso Ovest",
    "gran_sasso_est": "Gran Sasso Est",
    "velino_sirente": "Velino–Sirente",
    "majella_ovest": "Majella Ovest",
    "alto_sangro_pnalm": "Alto Sangro / PNALM",
    "laga": "Monti della Laga",
}

def number(x, digits=2):
    if x is None:
        return None
    return round(float(x), digits)

def load_report(path, now):
    if not path.exists() or path.stat().st_size > 100000:
        return {}
    if now.timestamp() - path.stat().st_mtime > 22 * 3600:
        return {}
    results = {}
    pattern = r'^\s*(\S+)\s+finale\s+.*?\| meteo\s+(\d+).*?\|.*?neve\s+(\d+)%\s+\| nuova\s+([\d.]+)cm'
    for line in path.read_text(errors="replace").splitlines():
        m = re.search(pattern, line)
        if m and m.group(1) in NAMES:
            results[m.group(1)] = {
                "weather_score": int(m.group(2)),
                "snow_pct": int(m.group(3)),
                "fresh_cm": float(m.group(4)),
            }
    return results

def export(base, dest):
    now = datetime.now(UTC)
    dbpath = base / "data/raster_temps.sqlite"
    if not dbpath.is_file():
        raise FileNotFoundError("Database raster non trovato")
    metpath = base / "data/meteomont_latest.json"
    meteo = {}
    if metpath.is_file() and now.timestamp() - metpath.stat().st_mtime < 22 * 3600:
        meteo = json.loads(metpath.read_text())
    report = load_report(base / "data/report_latest.txt", now)
    end = now.astimezone(ROME).replace(hour=7, minute=0, second=0, microsecond=0)
    if now.astimezone(ROME) < end:
        end -= timedelta(days=1)
    start = end - timedelta(hours=11)
    cutoff = (now - timedelta(hours=24)).isoformat()
    zones = []
    with sqlite3.connect("file:" + str(dbpath) + "?mode=ro", uri=True, timeout=15) as db:
        for zone_id, name in NAMES.items():
            rows = db.execute(
                """SELECT observed_hour_utc, temp_c, sample_elev_m,
                          gradient_c_per_100m, sample_note
                   FROM raster_hourly
                   WHERE zone=? AND observed_hour_utc>=?
                   ORDER BY observed_hour_utc""",
                (zone_id, cutoff),
            ).fetchall()
            valid = [r for r in rows if r[4] == "OK"]
            last = valid[-1] if valid else None
            night = []
            for at, temp, altitude, gradient, note in valid:
                t = datetime.fromisoformat(at).astimezone(ROME)
                if start <= t < end:
                    night.append((t, float(temp)))
            unique = len({t.replace(minute=0, second=0, microsecond=0) for t, _ in night})
            contiguous = all(
                timedelta(0) < b[0] - a[0] <= timedelta(hours=2)
                for a, b in zip(night, night[1:])
            )
            sufficient = unique >= 8 and contiguous
            values = [temp for _, temp in night]
            below = sum(t <= 0 for t in values) / len(values) if sufficient else None
            min_temp = min(values) if sufficient else None
            freeze = min(100, round(below * 70 + max(0, min(30, -min_temp * 6)))) if sufficient else None
            mm = meteo.get(zone_id, {}) if isinstance(meteo, dict) else {}
            danger = mm.get("danger") if isinstance(mm, dict) else None
            if not isinstance(danger, (int, float)) or not 0 <= danger <= 5:
                danger = None
            else:
                danger = int(danger)
            weather = report.get(zone_id, {})
            zones.append({
                "id": zone_id, "name": name,
                "temp_c": number(last[1]) if last else None,
                "elevation_m": round(last[2]) if last else None,
                "gradient_c_per_100m": number(last[3], 3) if last and last[3] is not None else None,
                "observed_at_utc": last[0] if last else None,
                "freeze_score": freeze,
                "night_min_c": number(min_temp),
                "subzero_pct": round(below * 100) if below is not None else None,
                "snow_pct": weather.get("snow_pct"),
                "fresh_cm": weather.get("fresh_cm"),
                "avalanche_danger": danger,
                "weather_score": weather.get("weather_score"),
                "history": [{"at": r[0], "temp_c": number(r[1])} for r in valid[-24:]],
            })
    payload = {
        "schema_version": 1,
        "demo": False,
        "generated_at_utc": now.isoformat(),
        "observation_lag_minutes": 60,
        "source": "AbruzzoMap TEMP.tif + DEM SRTM; Meteomont e previsione se disponibili",
        "zones": zones,
    }
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(data) > 512000:
        raise ValueError("Feed eccessivo")
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(".part")
    temporary.write_bytes(data)
    os.replace(temporary, dest)
    print("OK:", dest, "| zone:", len(zones), "| bytes:", len(data))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="/config/skialp_abruzzo")
    parser.add_argument("--output", default="/config/skialp_abruzzo/public/latest.json")
    args = parser.parse_args()
    export(Path(args.base), Path(args.output))
