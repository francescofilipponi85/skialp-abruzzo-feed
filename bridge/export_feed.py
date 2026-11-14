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
    """Legge il report mattutino già calcolato, anche quando la neve è N/D."""
    if not path.exists() or path.stat().st_size > 100000:
        return {}
    if now.timestamp() - path.stat().st_mtime > 22 * 3600:
        return {}
    results = {}
    for line in path.read_text(errors="replace").splitlines():
        head = re.match(r"^\s*([a-z_]+)\s+finale\b", line)
        if not head or head.group(1) not in NAMES:
            continue
        fields = {}
        for field in line.split("|")[1:]:
            field = field.strip()
            for key, pattern in (
                ("weather_score", r"^meteo\s+(\d{1,3})\b"),
                ("snow_pct", r"^neve\s+(\d{1,3})%"),
                ("fresh_cm", r"^nuova\s+([\d.,]+)\s*cm\b"),
            ):
                match = re.match(pattern, field, re.I)
                if match:
                    value = float(match.group(1).replace(",", ".")) if key == "fresh_cm" else int(match.group(1))
                    if key == "weather_score" and not 0 <= value <= 100:
                        continue
                    if key == "snow_pct" and not 0 <= value <= 100:
                        continue
                    if key == "fresh_cm" and not 0 <= value <= 300:
                        continue
                    fields[key] = value
        results[head.group(1)] = fields
    return results


def recent_file(path, now, max_age_hours=26):
    """Non usare report meteorologici vecchi: N/D in caso di file assente/scaduto."""
    try:
        age = now.timestamp() - path.stat().st_mtime
        return path.is_file() and 0 <= age <= max_age_hours * 3600 and path.stat().st_size < 150000
    except OSError:
        return False

def field_number(line, pattern, low=0, high=100, decimal=False):
    m = re.search(pattern, line, re.I)
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ".")) if decimal else int(m.group(1))
        return n if low <= n <= high else None
    except (TypeError, ValueError):
        return None

def load_morning_cache(base, now):
    """Meteo e neve già elaborati dagli script delle 07, senza query di rete aggiuntive."""
    cache = Path("/tmp/skialp_morning_cache")
    data = {k: {} for k in NAMES}
    weather_path = cache / "weather.txt"
    if recent_file(weather_path, now):
        for line in weather_path.read_text(errors="replace").splitlines():
            m = re.match(r"^\s*(\d{1,3})\s*/100\s+([a-z_]+)\b", line)
            if not m or m.group(2) not in data:
                continue
            zone, score = m.group(2), int(m.group(1))
            if 0 <= score <= 100:
                data[zone]["weather_score"] = score
            for key, pat, upper in (
                ("sky_score", r"\bcielo\s+(\d{1,3})\b", 100),
                ("wind_score", r"\bvento\s+(\d{1,3})\b", 100),
                ("max_gust_kmh", r"\braffica\s+([\d.,]+)", 250),
                ("sky_min_score", r"\bcielo_min\s+([\d.,]+)", 100),
            ):
                value = field_number(line, pat, 0, upper,
                                     decimal=key in ("max_gust_kmh", "sky_min_score"))
                if value is not None:
                    data[zone][key] = value
            data[zone]["weather_updated_utc"] = datetime.fromtimestamp(
                weather_path.stat().st_mtime, UTC).isoformat()
    snow_path = cache / "snow.txt"
    if recent_file(snow_path, now):
        for line in snow_path.read_text(errors="replace").splitlines():
            m = re.match(r"^\s*([a-z_]+)\s+neve\b", line)
            if not m or m.group(1) not in data:
                continue
            zone = m.group(1)
            confidence = field_number(line, r"\baffid\s+(\d{1,3})\s*%", 0, 100)
            data[zone]["snow_confidence_pct"] = confidence
            # Affidabilità zero: mai scambiare assenza del dato per assenza di neve.
            if confidence is None or confidence == 0:
                continue
            snow = field_number(line, r"\bneve\s+(\d{1,3})\s*%", 0, 100)
            cloud = field_number(line, r"\bnubi\s+(\d{1,3})\s*%", 0, 100)
            fresh = field_number(line, r"\bnuova(?:24h)?(?:\s+max)?\s+([\d.,]+)\s*cm", 0, 300, True)
            if snow is not None: data[zone]["snow_pct"] = snow
            if cloud is not None: data[zone]["snow_cloud_pct"] = cloud
            if fresh is not None: data[zone]["fresh_cm"] = fresh
            data[zone]["snow_updated_utc"] = datetime.fromtimestamp(
                snow_path.stat().st_mtime, UTC).isoformat()
    return data


def load_thermal_morning(now):
    """Gradiente e profili notturni dal workflow principale, se presenti e recenti."""
    cache = Path("/tmp/skialp_morning_cache")
    result = {z: {} for z in NAMES}
    lapse = cache / "lapse.txt"
    if recent_file(lapse, now):
        for line in lapse.read_text(errors="replace").splitlines():
            m = re.match(r"^\s*([a-z_]+)\s+([+-]?[\d.,]+)\s*°\s*C\s*/\s*100\s*m", line, re.I)
            if not m or m.group(1) not in result or "NON AFFIDABILE" in line.upper():
                continue
            zone = m.group(1)
            try:
                grad = float(m.group(2).replace(",", "."))
            except ValueError:
                continue
            if not -3 <= grad <= 3:
                continue
            result[zone]["night_gradient_c_per_100m"] = round(grad, 3)
            inversion = field_number(line, r"\binversione\s*(\d{1,3})\s*%", 0, 100)
            result[zone]["inversion_pct"] = inversion
            result[zone]["thermal_profile_updated_utc"] = datetime.fromtimestamp(
                lapse.stat().st_mtime, UTC).isoformat()
    profile = cache / "temp.txt"
    if recent_file(profile, now):
        for line in profile.read_text(errors="replace").splitlines():
            m = re.match(r"^\s*([a-z_]+)\s*:", line)
            if not m or m.group(1) not in result or "N/D" in line:
                continue
            prof = {}
            for alt, value in re.findall(
                r"\b(1600|1800|2000|2200)\s*m\s*([+-]?[\d.,]+)\s*°",
                line, re.I
            ):
                try:
                    v = float(value.replace(",", "."))
                except ValueError:
                    continue
                if -45 <= v <= 40:
                    prof[alt] = round(v, 1)
            if len(prof) >= 2:
                result[m.group(1)]["temperature_profile_c"] = prof
                result[m.group(1)]["thermal_profile_updated_utc"] = datetime.fromtimestamp(
                    profile.stat().st_mtime, UTC).isoformat()
    return result

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
            r"\bmin\s*([+-]?\d+(?:[.,]\d+)?)\s*°?\s*C?(?=\s*\||\s|$)",
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
    morning = load_morning_cache(base, now)
    thermal = load_thermal_morning(now)
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
            if isinstance(danger, bool) or not isinstance(danger, (int, float)) or not 1 <= danger <= 5:
                danger = None
            else:
                danger = int(danger)
            weather = {**report.get(zone_id, {}), **morning.get(zone_id, {})}
            if weather.get("snow_confidence_pct") == 0:
                weather["snow_pct"] = None
                weather["fresh_cm"] = None
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
                "snow_confidence_pct": weather.get("snow_confidence_pct"),
                "snow_cloud_pct": weather.get("snow_cloud_pct"),
                "snow_updated_utc": weather.get("snow_updated_utc"),
                "fresh_cm": weather.get("fresh_cm"),
                "avalanche_danger": danger,
                "avalanche_label": (str(mm.get("label"))[:90] if mm.get("label") else None) if isinstance(mm, dict) else None,
                "avalanche_situation": (str(mm.get("situation"))[:450] if mm.get("situation") else None) if isinstance(mm, dict) else None,
                "avalanche_updated_utc": datetime.fromtimestamp(metpath.stat().st_mtime, UTC).isoformat() if danger and recent_file(metpath, now, 22) else None,
                "weather_score": weather.get("weather_score"),
                "sky_score": weather.get("sky_score"),
                "wind_score": weather.get("wind_score"),
                "max_gust_kmh": weather.get("max_gust_kmh"),
                "sky_min_score": weather.get("sky_min_score"),
                "weather_updated_utc": weather.get("weather_updated_utc"),
                "night_gradient_c_per_100m": thermal[zone_id].get("night_gradient_c_per_100m"),
                "inversion_pct": thermal[zone_id].get("inversion_pct"),
                "temperature_profile_c": thermal[zone_id].get("temperature_profile_c"),
                "thermal_profile_updated_utc": thermal[zone_id].get("thermal_profile_updated_utc"),
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
