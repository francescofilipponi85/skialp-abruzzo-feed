import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import export_feed

class TestExport(unittest.TestCase):
    def test_parsing_and_complete_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "data").mkdir()
            (base / "collector").mkdir()
            (base / "collector/skialp_raster.py").write_text(
                'print("gran_sasso_ovest        rigelo  81/100 | min -2.1° | sottozero 90% | campioni 10/11")\n'
                'print("gran_sasso_est          RIGELO N/D | notte incompleta (3/11 campioni)")\n',
                encoding="utf8"
            )
            (base / "data/report_latest.txt").write_text(
                "gran_sasso_ovest        finale 68/100 | meteo  74 | rigelo 81/100 | neve  30% | nuova  4.5cm | VALUTABILE\n"
                "gran_sasso_est          finale N/D | meteo  61 | rigelo N/D | neve N/D | nuova N/D | NEVE N/D\n",
                encoding="utf8"
            )
            (base / "data/meteomont_latest.json").write_text(
                json.dumps({"gran_sasso_ovest": {"danger": 3}, "gran_sasso_est": {"danger": None}}),
                encoding="utf8"
            )
            db = sqlite3.connect(base / "data/raster_temps.sqlite")
            db.execute("""CREATE TABLE raster_hourly(
                  observed_hour_utc TEXT, zone TEXT, temp_c REAL,
                  sample_elev_m REAL, gradient_c_per_100m REAL,
                  sample_note TEXT
            )""")
            at = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(
                minute=0, second=0, microsecond=0).isoformat()
            for zone in export_feed.NAMES:
                db.execute("INSERT INTO raster_hourly VALUES (?,?,?,?,?,?)",
                    (at, zone, -1.2, 1920.0, -0.55, "OK"))
            db.commit()
            db.close()
            out = base / "public/latest.json"
            export_feed.export(base, out)
            data = json.loads(out.read_text(encoding="utf8"))
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(len(data["zones"]), 6)
            west, east = data["zones"][:2]
            self.assertEqual(west["freeze_score"], 81)
            self.assertEqual(west["night_min_c"], -2.1)
            self.assertEqual(west["subzero_pct"], 90)
            self.assertEqual(west["weather_score"], 74)
            self.assertEqual(west["snow_pct"], 30)
            self.assertEqual(west["fresh_cm"], 4.5)
            self.assertEqual(west["avalanche_danger"], 3)
            self.assertEqual(west["temp_c"], -1.2)
            self.assertEqual(east["weather_score"], 61)
            self.assertIsNone(east["freeze_score"])
            self.assertIsNone(east["snow_pct"])
            self.assertIsNone(east["fresh_cm"])
            self.assertIsNone(east["avalanche_danger"])
            self.assertNotIn("TOKEN", out.read_text(encoding="utf8"))
            self.assertNotIn("192.168.", out.read_text(encoding="utf8"))

    def test_no_night_script_is_nd(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(export_feed.load_night_rigelo(Path(tmp)), {})

if __name__ == "__main__":
    unittest.main()
