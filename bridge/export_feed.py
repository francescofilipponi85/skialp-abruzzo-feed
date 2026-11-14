#!/usr/bin/env python3
"""Esporta SOLO meteo pubblico dal NAS: nessuna password, IP, ID WhatsApp o SQLite."""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
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

def load_night_rigelo(base):
    """Legge il rigelo calcolato dal motore SkiAlp, senza ricalcolarlo.
    
    Se la notte è incompleta o lo script non è disponibile, non pubblica
    un valore inventato: i campi rimangono null.
    """
    script = base / "collector/skialp_raster.py"
    if not script.is_file():
        return {}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "night"],
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            print("ATTENZIONE: rigelo non disponibile (comando night fallito)", file=sys.stderr)
            return {}
    except (OSError, subprocess.TimeoutExpired):
        print("ATTENZIONE: rigelo non disponibile (timeout/errore avvio)", file=sys.stderr)
        return {}
    result = {}
    for line in proc.stdout.splitlines():
        match = re.match(
            r"^\s*([a-z_]+)\s+rigelo\s+(\d{1,3})\s*/\s*100\b",
            line, flags=re.I
        )
        if not match or match.group(1) not in NAMES:
            continue
        score = int(match.group(2))
        if not 0 <= score <= 100:
            continue
        minimum = re.search(
            r"\bmin\s*([+-]?\d+(?:[.,]\d+)?)\s*°?\s*C\b",
            line, re.I
        )
        below = re.search(r"\bsottozero\s*(\d{1,3})\s*%", line, re.I)
        result[match.group(1)] = {
            "freeze_score": score,
            "night_min_c": number(float(minimum.group(1).replace(",", ".")))
                           if minimum else None,
            "subzero_pct": int(below.group(1))
                           if below and int(below.group(1)) <= 100 else None,
        }
    return result

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
    night_rigelo = load_night_rigelo(base)
    night_end = now.astimezone(ROME).replace(hour=7, minute=0, second=0, microsecond=0)
    if now.astimezone(ROME) < night_end:
        night_end -= timedelta(days=1)
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
            rigelo = night_rigelo.get(zone_id, {})
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
                "freeze_score": rigelo.get("freeze_score"),
                "night_min_c": rigelo.get("night_min_c"),
                "subzero_pct": rigelo.get("subzero_pct"),
                "freeze_night_end_local": night_end.isoformat() if rigelo else None,
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
