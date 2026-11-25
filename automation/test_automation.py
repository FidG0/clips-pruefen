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
        self.assertEqual((s.day, s.hour, s.minute), (24, 13, 0))

    def test_belegt_und_vorlauf(self):
        jetzt = dt.datetime(2026, 11, 24, 18, 45, tzinfo=BER).astimezone(UTC)  # 19:00 nur 15 Min entfernt
        s = lib.naechster_slot(set(), jetzt).astimezone(BER)
        self.assertEqual((s.hour, s.minute), (20, 30))
        belegt = {s.astimezone(UTC).isoformat()}
        s2 = lib.naechster_slot(belegt, jetzt).astimezone(BER)
        self.assertEqual((s2.day, s2.hour), (25, 13))  # 22:00 ist Bonus, nie automatisch

    def test_sommerzeit(self):
        jetzt = dt.datetime(2026, 7, 1, 8, 0, tzinfo=BER).astimezone(UTC)
        s = lib.naechster_slot(set(), jetzt)
        self.assertEqual(s.hour, 11)  # 13:00 MESZ = 11:00 UTC


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

    def test_nur_ein_regelwerk_schritt_pro_tag(self):
        jetzt, posts, geplant = self.daten()
        m = zahlen.metriken_aktualisieren({}, posts, geplant, jetzt)
        a1 = zahlen.auswerten(m, {}, jetzt)
        a2 = zahlen.auswerten(m, a1, jetzt)
        self.assertEqual(a2["neu_heute"], [])
        self.assertEqual(a2["regelwerk_auto"]["slot_gewichte"], a1["regelwerk_auto"]["slot_gewichte"])

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
                         "karten": {"titel": "Zarbex fährt rein", "caption": "lol", "format": "GTA", "kanal": "Zarbex", "vorschau": "a.mp4",
                                     "datei": "a.mp4", "checks": {"ton_sync": {"status": "gruen", "ms": 12}}}},
                        {"id": 8, "karte_id": "k2", "kommentar": "[posten]", "titel": None,
                         "karten": {"titel": "Monte Casino Run", "caption": "x", "kanal": "Monte", "datei": "b.mp4",
                                    "checks": {"ton_sync": {"status": "gruen"}}}}]

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
        self.assertEqual([g["auftrag_id"] for g in z["gesperrt"]], [8])


class WunschSlots(unittest.TestCase):
    UTC = dt.timezone.utc
    jetzt = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)   # 13:00 Berlin

    def test_kommentar_hin_und_zurueck(self):
        s = {"tiktok": lib.slot_aus_lokal("2026-11-26T16:00"), "youtube": lib.slot_aus_lokal("2026-11-26T19:00")}
        k = lib.wunsch_kommentar(s)
        self.assertTrue(k.startswith("[posten] "))
        self.assertEqual(lib.wunsch_slots(k), s)
        self.assertEqual(s["tiktok"], dt.datetime(2026, 11, 26, 15, 0, tzinfo=self.UTC))
        self.assertEqual(lib.wunsch_slots("[posten]"), {})
        self.assertEqual(lib.wunsch_slots("[posten] slot:tiktok=kaputt"), {})

    def test_slot_regeln(self):
        p = lambda t, b=(): lib.slot_pruefen(lib.slot_aus_lokal(t), set(b), self.jetzt)  # noqa: E731
        self.assertIsNone(p("2026-11-25T16:00"))
        self.assertIn("kein Posting-Slot", p("2026-11-25T17:00"))
        knapp = dt.datetime(2026, 11, 25, 14, 45, tzinfo=self.UTC)   # 15:45 Berlin, 16:00 nur 15 Min weg
        self.assertIn("vorbei", lib.slot_pruefen(lib.slot_aus_lokal("2026-11-25T16:00"), set(), knapp))
        self.assertIn("belegt", p("2026-11-25T19:00", ["2026-11-25T18:00:00+00:00"]))
        voll = ["2026-11-26T15:00:00+00:00", "2026-11-26T18:00:00+00:00", "2026-11-26T19:30:00+00:00"]
        self.assertIn("belegt", p("2026-11-26T20:30", voll))

    def _lauf(self, kommentar, geplant=()):
        import os
        import posten

        class FakeSb:
            gespeichert = {}

            def info_lesen(self, k, d=None):
                return {"geplant": [dict(g) for g in geplant]}

            def info_schreiben(self, k, w):
                FakeSb.gespeichert[k] = w

            def holen(self, t, q):
                return [{"id": 9, "karte_id": "k9", "kommentar": kommentar, "titel": None,
                         "karten": {"titel": "NPC fährt gegen Laterne", "kanal": "Zarbex", "datei": "a.mp4",
                                    "checks": {"ton_sync": {"status": "gruen"}}}}]

        alt = posten.Supabase
        posten.Supabase = FakeSb
        os.environ.pop("BUFFER_LIVE", None)
        try:
            posten.main()
        finally:
            posten.Supabase = alt
        return FakeSb.gespeichert["posting"]["probelauf"]

    def test_kalender_slot_nur_gewaehlte_plattform(self):
        morgen = (dt.datetime.now(ZoneInfo("Europe/Berlin")) + dt.timedelta(days=2)).strftime("%Y-%m-%d")
        ziel = lib.slot_aus_lokal(morgen + "T19:00")
        z = self._lauf(lib.wunsch_kommentar({"tiktok": ziel}))
        self.assertEqual([e["plattform"] for e in z], ["tiktok"])
        self.assertEqual(z[0]["slot_utc"], ziel.isoformat())
        self.assertTrue(z[0]["wunsch"])

    def test_belegter_wunsch_nimmt_naechsten(self):
        morgen = (dt.datetime.now(ZoneInfo("Europe/Berlin")) + dt.timedelta(days=2)).strftime("%Y-%m-%d")
        ziel = lib.slot_aus_lokal(morgen + "T19:00")
        z = self._lauf(lib.wunsch_kommentar({"youtube": ziel}),
                       [{"plattform": "youtube", "slot_utc": ziel.isoformat(), "status": "geplant"}])
        self.assertEqual(len(z), 1)
        self.assertNotEqual(z[0]["slot_utc"], ziel.isoformat())
        self.assertIn("Wunsch-Slot ging nicht", z[0]["hinweis"])

    def test_bonus_nur_auf_wunsch(self):
        jetzt = dt.datetime(2026, 11, 24, 21, 0, tzinfo=BER).astimezone(UTC)   # nur 22:00 (Bonus) übrig
        self.assertEqual(lib.naechster_slot(set(), jetzt).astimezone(BER).day, 25)
        self.assertIsNone(lib.slot_pruefen(lib.slot_aus_lokal("2026-11-24T22:00"), set(), jetzt))

    def test_fuenf_pro_tag(self):
        tag = ["2026-11-27T" + s for s in ("13:00", "16:00", "19:00", "20:30")]
        belegt = {lib.slot_aus_lokal(z).isoformat() for z in tag}
        self.assertIsNone(lib.slot_pruefen(lib.slot_aus_lokal("2026-11-27T22:00"), belegt, self.jetzt))

    def test_buffer_free_grenze(self):
        zukunft = [{"plattform": "tiktok", "status": "geplant", "slot_utc": (self.jetzt + dt.timedelta(hours=i + 1)).isoformat()} for i in range(10)]
        self.assertIn("Buffer Free", lib.buffer_voll(zukunft, "tiktok", self.jetzt))
        self.assertIsNone(lib.buffer_voll(zukunft[:9], "tiktok", self.jetzt))
        self.assertIsNone(lib.buffer_voll(zukunft, "youtube", self.jetzt))
        alt = [dict(g, slot_utc=(self.jetzt - dt.timedelta(hours=1)).isoformat()) for g in zukunft]
        self.assertIsNone(lib.buffer_voll(alt, "tiktok", self.jetzt))

    def test_ohne_wunsch_alle_plattformen(self):
        self.assertEqual(sorted(e["plattform"] for e in self._lauf("[posten]")), ["instagram", "tiktok", "youtube"])


class Sperren(unittest.TestCase):
    ok = {"titel": "NPC fährt gegen Laterne", "datei": "x.mp4", "checks": {"ton_sync": {"status": "gruen"}}}

    def test_gruen_mit_original_frei(self):
        self.assertIsNone(lib.sperrgrund(self.ok))

    def test_ton_gelb_rot_ungeprueft(self):
        for c in ({"ton_sync": {"status": "gelb"}}, {"ton_sync": {"status": "rot"}}, {}, None):
            self.assertIn("Ton-Sync", lib.sperrgrund({**self.ok, "checks": c}))

    def test_alte_charge(self):
        self.assertIn("Alte Charge", lib.sperrgrund({**self.ok, "datei": None}))

    def test_gluecksspiel(self):
        for t in ("Papaplatte im Casino", "Slots um 3 Uhr", "Poker-Abend", "Monte Sportwetten"):
            self.assertIn("Glücksspiel", lib.sperrgrund({**self.ok, "titel": t}))
        self.assertIn("Glücksspiel", lib.sperrgrund({**self.ok, "checks": {"ton_sync": {"status": "gruen"}, "gluecksspiel": True}}))
        self.assertIsNone(lib.sperrgrund({**self.ok, "titel": "Steakhouse Chaos"}))


if __name__ == "__main__":
    unittest.main()
