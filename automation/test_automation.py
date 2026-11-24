"""Tests ohne Netz: python -m unittest test_automation.py"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

import csv_import
import lib
import zahlen

UTC = dt.timezone.utc
BER = ZoneInfo("Europe/Berlin")


class Slots(unittest.TestCase):
    def test_naechster_slot_heute(self):
        jetzt = dt.datetime(2026, 11, 24, 10, 0, tzinfo=BER).astimezone(UTC)
        s = lib.naechster_slot(set(), jetzt).astimezone(BER)
        self.assertEqual((s.day, s.hour, s.minute), (24, 16, 0))

    def test_belegt_und_vorlauf(self):
        jetzt = dt.datetime(2026, 11, 24, 18, 45, tzinfo=BER).astimezone(UTC)  # 19:00 nur 15 Min entfernt
        s = lib.naechster_slot(set(), jetzt).astimezone(BER)
        self.assertEqual((s.hour, s.minute), (20, 30))
        belegt = {s.astimezone(UTC).isoformat()}
        s2 = lib.naechster_slot(belegt, jetzt).astimezone(BER)
        self.assertEqual((s2.day, s2.hour), (25, 16))

    def test_sommerzeit(self):
        jetzt = dt.datetime(2026, 7, 1, 8, 0, tzinfo=BER).astimezone(UTC)
        s = lib.naechster_slot(set(), jetzt)
        self.assertEqual(s.hour, 14)  # 16:00 MESZ = 14:00 UTC


class Caption(unittest.TestCase):
    def test_hashtags_ohne_doppelte_und_ohne_umbruch(self):
        k = {"caption": "Er fährt einfach rein\n#gta6", "art": "GTA", "kanal": "Zarbex", "titel": "x"}
        t = lib.caption_bauen(k)
        self.assertNotIn("\n", t)
        self.assertEqual(t.lower().count("#gta6"), 1)
        self.assertIn("#zarbex", t)
        self.assertLessEqual(len([w for w in t.split() if w.startswith("#")]), 6)

    def test_titeltyp(self):
        self.assertEqual(lib.titel_typ("„Machs gut Loser“"), "zitat")
        self.assertEqual(lib.titel_typ("Wer war das?"), "frage")
        self.assertEqual(lib.titel_typ("Top 5 GTA Momente"), "liste")


class Plattformen(unittest.TestCase):
    k = {"caption": "Er fährt einfach rein\nund dann das", "format": "GTA", "kanal": "Zarbex", "titel": "„Machs gut Loser“", "dauer": 22}

    def test_youtube(self):
        t = lib.caption_bauen(self.k, plattform="youtube")
        self.assertIn("#shorts", t)
        self.assertIn("\n", t)
        m = lib.metadata_bauen(self.k, "youtube")["youtube"]
        self.assertTrue(m["title"].endswith("#shorts"))
        self.assertLessEqual(len(m["title"]), 100)
        self.assertEqual(m["categoryId"], "20")
        self.assertEqual((m["privacy"], m["madeForKids"], m["type"]), ("public", False, "short"))

    def test_instagram(self):
        t = lib.caption_bauen(self.k, plattform="instagram")
        self.assertLessEqual(len([w for w in t.split() if w.startswith("#")]), 5)
        self.assertEqual(lib.metadata_bauen(self.k, "instagram"), {"instagram": {"type": "reel", "shouldShareToFeed": True}})

    def test_tiktok_ohne_metadata(self):
        self.assertIsNone(lib.metadata_bauen(self.k, "tiktok"))
        self.assertNotIn("\n", lib.caption_bauen(self.k, plattform="tiktok"))

    def test_score_normiert_je_plattform(self):
        jetzt = dt.datetime(2026, 12, 10, 6, 0, tzinfo=UTC)
        due = (jetzt - dt.timedelta(days=4)).isoformat()
        posts = [{"id": f"{p}{i}", "dueAt": due, "channelId": p, "metrics": [{"type": "views", "value": v * (i + 1)}]}
                 for p, v in (("yt", 100), ("tt", 10000)) for i in range(3)]
        m = zahlen.metriken_aktualisieren({}, posts, [], jetzt, {"yt": "youtube", "tt": "tiktok"})
        zeilen, gruppen = zahlen.gruppieren(m)
        self.assertEqual(set(gruppen["plattform"]), {"youtube", "tiktok"})
        self.assertAlmostEqual(gruppen["plattform"]["youtube"]["median_score"], 1.0)
        self.assertAlmostEqual(gruppen["plattform"]["tiktok"]["median_score"], 1.0)


class Analyse(unittest.TestCase):
    def daten(self):
        jetzt = dt.datetime(2026, 12, 10, 6, 0, tzinfo=UTC)
        posts, geplant = [], []
        for i in range(12):
            slot = ["16:00", "19:00", "20:30"][i % 3]
            views = {"16:00": 1000, "19:00": 3000, "20:30": 2000}[slot] + i
            due = (jetzt - dt.timedelta(days=4)).isoformat()
            posts.append({"id": f"p{i}", "text": "t", "dueAt": due,
                          "channelId": "c1", "metrics": [{"type": "views", "value": views}, {"type": "likes", "value": views / 10},
                                      {"type": "totalTimeWatched", "value": views * 12}]})
            geplant.append({"buffer_id": f"p{i}", "status": "geplant", "titel": f"Clip {i}", "art": "GTA" if i % 2 else "IRL",
                            "slot": slot, "dauer": 12 if i < 6 else 22, "titel_typ": "zitat", "kanal": "Zarbex"})
        return jetzt, posts, geplant

    def test_loop(self):
        jetzt, posts, geplant = self.daten()
        m = zahlen.metriken_aktualisieren({}, posts, geplant, jetzt)
        self.assertEqual(m["p0"]["verlauf"]["5"]["views"], 1000)
        self.assertTrue(all(g["status"] == "gesendet" for g in geplant))
        a = zahlen.auswerten(m, {}, jetzt)
        self.assertEqual(a["anzahl_posts"], 12)
        sw = a["regelwerk_auto"]["slot_gewichte"]
        self.assertGreater(sw["19:00"], 1.0)
        self.assertLess(sw["16:00"], 1.0)
        self.assertLessEqual(abs(sw["19:00"] - 1.0), 0.2 + 1e-9)  # begrenzt
        self.assertTrue(a["vorschlaege"])
        self.assertTrue(all("grund" in x and "datum" in x for x in a["aenderungen"]))

    def test_tag_11_wird_nicht_mehr_verfolgt(self):
        jetzt, posts, geplant = self.daten()
        posts[0]["dueAt"] = (jetzt - dt.timedelta(days=12)).isoformat()
        m = zahlen.metriken_aktualisieren({}, posts[:1], geplant, jetzt)
        self.assertEqual(m["p0"]["verlauf"], {})


class Csv(unittest.TestCase):
    def test_import_deutsch(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text("Titel;Veröffentlicht;Aufrufe;Durchschnittliche Wiedergabezeit;Vollständig angesehen;Neue Follower\n"
                         "Machs gut Loser;24.11.2026 19:00;12,5K;0:09;31,5%;40\n", encoding="utf-8")
            z = csv_import.datei_lesen(p)[0]
            self.assertEqual(z["views"], 12500)
            self.assertEqual(z["avg_watch_s"], 9)
            self.assertAlmostEqual(z["full_watch_pct"], 31.5)
            m = {"p1": {"meta": {"titel": "Machs gut Loser", "gesendet": "2026-11-24T18:00:00+00:00"}}}
            self.assertEqual(csv_import.zuordnen(z, m), "p1")


if __name__ == "__main__":
    unittest.main()


class PostenProbelauf(unittest.TestCase):
    def test_drei_plattformen_ohne_buffer(self):
        import os
        import posten

        class FakeSb:
            gespeichert = {}

            def info_lesen(self, k, d=None):
                return {}

            def info_schreiben(self, k, w):
                FakeSb.gespeichert[k] = w

            def holen(self, t, q):
                return [{"id": 7, "karte_id": "k1", "kommentar": "[posten]", "titel": None,
                         "karten": {"titel": "Zarbex fährt rein", "caption": "lol", "format": "GTA", "kanal": "Zarbex", "vorschau": "a.mp4"}}]

        alt = posten.Supabase
        posten.Supabase = FakeSb
        os.environ.pop("BUFFER_LIVE", None)
        try:
            posten.main()
        finally:
            posten.Supabase = alt
        z = FakeSb.gespeichert["posting"]
        self.assertEqual(z["modus"], "probelauf")
        self.assertEqual(sorted(e["plattform"] for e in z["probelauf"]), ["instagram", "tiktok", "youtube"])
        self.assertEqual(z["erledigte_auftraege"], [])
        self.assertTrue(all(e["status"] == "probelauf" for e in z["probelauf"]))
