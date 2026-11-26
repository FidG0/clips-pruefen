import json
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
        self.assertEqual((s.day, s.hour, s.minute), (24, 11, 0))   # 26.11.: erster Slot 11:00

    def test_belegt_und_vorlauf(self):
        jetzt = dt.datetime(2026, 11, 24, 18, 50, tzinfo=BER).astimezone(UTC)  # 19:00 nur 10 Min entfernt (Vorlauf 15)
        s = lib.naechster_slot(set(), jetzt).astimezone(BER)
        self.assertEqual((s.hour, s.minute), (20, 30))
        belegt = {s.astimezone(UTC).isoformat()}
        s2 = lib.naechster_slot(belegt, jetzt).astimezone(BER)
        self.assertEqual((s2.day, s2.hour), (25, 11))  # 22:00 ist Bonus, nie automatisch

    def test_sommerzeit(self):
        jetzt = dt.datetime(2026, 7, 1, 8, 0, tzinfo=BER).astimezone(UTC)
        s = lib.naechster_slot(set(), jetzt)
        self.assertEqual(s.hour, 9)  # 11:00 MESZ = 09:00 UTC


class Caption(unittest.TestCase):
    def test_hashtags_ohne_doppelte_und_ohne_umbruch(self):
        k = {"caption": "Er fährt einfach rein\n#gta6", "art": "GTA", "kanal": "Zarbex", "titel": "x"}
        t = lib.caption_bauen(k)
        self.assertNotIn("\n", t)
        self.assertEqual(t.lower().count("#gta6"), 1)
        self.assertTrue(t.endswith("#gta6 #gta #Gaming #viral #fyp"))   # 26.11.: Marvins Vorlage (Text bringt #gta6 mit)
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
        self.assertNotIn("#", m["title"])   # 25.11.: echter Titel, #shorts steht in der Beschreibung
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
        self.assertEqual(sorted(e["plattform"] for e in z["probelauf"]), ["instagram", "tiktok"])
        self.assertEqual([w["karte_id"] for w in z["youtube_warte_probe"]], ["k1"])   # YouTube: Tages-Auswahl der besten
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
        knapp = dt.datetime(2026, 11, 25, 14, 50, tzinfo=self.UTC)   # 15:50 Berlin, 16:00 nur 10 Min weg
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
        WunschSlots.letzter_zustand = FakeSb.gespeichert["posting"]
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

    def test_sofort_regeln(self):
        j = self.jetzt   # 13:00 Berlin
        g = lambda z, **x: {"plattform": "tiktok", "status": "geplant", "slot_utc": lib.slot_aus_lokal(z).isoformat(), **x}  # noqa: E731
        t, grund = lib.sofort_slot([], "tiktok", j)
        self.assertIsNone(grund)
        self.assertEqual(t, j + dt.timedelta(minutes=5))
        # 25.11. abends: nie mehr gesperrt, nur Warnungen
        self.assertEqual(lib.sofort_slot([g("2026-11-25T13:30")], "tiktok", j), (j + dt.timedelta(minutes=5), None))
        self.assertIn("Abstand", " ".join(lib.sofort_warnungen([g("2026-11-25T13:30")], "tiktok", j)))
        self.assertIn("schon einen Bonus", " ".join(lib.sofort_warnungen([g("2026-11-25T22:00")], "tiktok", j)))
        self.assertIn("schon einen Bonus", " ".join(lib.sofort_warnungen([g("2026-11-25T10:00", bonus=True)], "tiktok", j)))
        self.assertEqual(lib.sofort_warnungen([g("2026-11-25T22:00")], "youtube", j), [])
        self.assertEqual(lib.wunsch_slots(lib.wunsch_kommentar({"tiktok": "sofort"})), {"tiktok": "sofort"})

    def test_sofort_im_job(self):
        z = self._lauf("[posten] slot:tiktok=sofort slot:youtube=sofort")
        self.assertEqual(sorted(e["plattform"] for e in z), ["tiktok", "youtube"])
        self.assertTrue(all(e.get("bonus") for e in z))
        frueh = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=20)
        self.assertTrue(all(dt.datetime.fromisoformat(e["slot_utc"]) < frueh for e in z))

    def test_ohne_wunsch_alle_plattformen(self):
        self.assertEqual(sorted(e["plattform"] for e in self._lauf("[posten]")), ["instagram", "tiktok"])
        self.assertEqual(len(WunschSlots.letzter_zustand["youtube_warte_probe"]), 1)


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

    def test_test_clips_nie_posten(self):   # 26.11.: Gegencheck-Clips im Prüffach
        for k in ({"test": True}, {"nicht_posten": True}, {"titel": "TEST KI-Fehlgriff: Jynxzi"},
                  {"hinweise": ["TEST: von der KI fälschlich gewählt"]}):
            self.assertIn("TEST", lib.sperrgrund({**self.ok, **k}), k)
        self.assertIsNone(lib.sperrgrund({**self.ok, "titel": "Testfahrt endet im Pool"}))
        self.assertIsNone(lib.sperrgrund({**self.ok, "hinweise": ["Caption testen"]}))


class Zuruecknehmen(unittest.TestCase):
    """25.11.: „Doch schlecht“ und „×“ dürfen nur Geplantes löschen und nie etwas posten."""

    class Buf:
        def __init__(self, kaputt=()):
            self.geloescht, self.kaputt = [], set(kaputt)

        def post_loeschen(self, pid):
            if pid in self.kaputt:
                raise RuntimeError("Buffer down")
            self.geloescht.append(pid)

    class Sb:
        def __init__(self):
            self.status = {}

        def karte_status(self, kid, s):
            self.status[kid] = s

    def geplant(self):
        return {"geplant": [
            {"auftrag_id": 5, "karte_id": "k1", "plattform": "tiktok", "status": "gesendet", "buffer_id": "b0"},
            {"auftrag_id": 6, "karte_id": "k1", "plattform": "tiktok", "status": "geplant", "buffer_id": "b1"},
            {"auftrag_id": 6, "karte_id": "k1", "plattform": "youtube", "status": "geplant", "buffer_id": "b2"},
            {"auftrag_id": 6, "karte_id": "k2", "plattform": "tiktok", "status": "geplant", "buffer_id": "b3"},
            {"auftrag_id": 20, "karte_id": "k1", "plattform": "instagram", "status": "geplant", "buffer_id": "b4"}]}

    def test_doch_schlecht_loescht_nur_geplantes_dieser_karte(self):
        import posten
        z, buf, sb = self.geplant(), self.Buf(), self.Sb()
        fertig, sperren = posten.rueckzuege_anwenden([{"id": 10, "karte_id": "k1", "kommentar": "[zurueckziehen] Ende 2 s länger"}], z, buf, True)
        self.assertEqual(buf.geloescht, ["b1", "b2"])   # b0 gesendet, b3 andere Karte, b4 spätere Freigabe
        st = [g["status"] for g in z["geplant"]]
        self.assertEqual(st, ["gesendet", "storniert", "storniert", "geplant", "geplant"])
        self.assertIn("schon veröffentlicht", z["geplant"][0]["hinweis"])
        self.assertEqual(fertig, [10])
        self.assertEqual(posten.gesperrte_plattformen({"id": 9, "karte_id": "k1"}, sperren), "alle")
        self.assertIsNone(posten.gesperrte_plattformen({"id": 11, "karte_id": "k1"}, sperren))   # „Nochmal“ danach bleibt gültig

    def test_rausnehmen_einzelne_plattform(self):
        import posten
        z, buf, sb = self.geplant(), self.Buf(), self.Sb()
        fertig, sperren = posten.rueckzuege_anwenden([{"id": 10, "karte_id": "k1", "kommentar": "[rausnehmen] nur:youtube"}], z, buf, True)
        self.assertEqual(buf.geloescht, ["b2"])
        self.assertEqual(sb.status, {})   # Karte bleibt postbar
        self.assertEqual(posten.gesperrte_plattformen({"id": 9, "karte_id": "k1"}, sperren), {"youtube"})

    def test_buffer_fehler_bleibt_offen(self):
        import posten
        z, buf = self.geplant(), self.Buf(kaputt={"b1"})
        fertig, _ = posten.rueckzuege_anwenden([{"id": 10, "karte_id": "k1", "kommentar": "[rausnehmen]"}], z, buf, True)
        self.assertEqual(fertig, [])
        self.assertEqual(z["geplant"][1]["status"], "geplant")
        self.assertIn("Löschen ging nicht", z["geplant"][1]["hinweis"])

    def test_probelauf_loescht_nichts(self):
        import posten
        z, buf, sb = self.geplant(), self.Buf(), self.Sb()
        posten.rueckzuege_anwenden([{"id": 10, "karte_id": "k1", "kommentar": "[zurueckziehen]"}], z, buf, False)
        self.assertEqual(buf.geloescht, [])
        self.assertEqual(sb.status, {})

    def test_job_plant_zurueckgenommene_freigabe_nicht(self):
        import os
        import posten

        class FakeSb:
            gespeichert = {}

            def info_lesen(self, k, d=None):
                return {}

            def info_schreiben(self, k, w):
                FakeSb.gespeichert[k] = w

            def holen(self, t, q):
                karte = {"titel": "NPC fährt gegen Laterne", "kanal": "Zarbex", "datei": "a.mp4", "checks": {"ton_sync": {"status": "gruen"}}}
                if "or=" in q:
                    return [{"id": 8, "karte_id": "k1", "kommentar": "[zurueckziehen]"}, {"id": 9, "karte_id": "k2", "kommentar": "[rausnehmen] nur:tiktok,youtube"},
                            {"id": 99, "karte_id": "k3", "kommentar": "[posten]"}]
                return [{"id": 7, "karte_id": "k1", "kommentar": "[posten]", "titel": None, "karten": karte},
                        {"id": 6, "karte_id": "k2", "kommentar": "[posten]", "titel": None, "karten": karte},
                        {"id": 12, "karte_id": "k1", "kommentar": "[posten]", "titel": None, "karten": karte}]

        alt = posten.Supabase
        posten.Supabase = FakeSb
        os.environ.pop("BUFFER_LIVE", None)
        try:
            posten.main()
        finally:
            posten.Supabase = alt
        p = FakeSb.gespeichert["posting"]["probelauf"]
        paare = {(e["auftrag_id"], e["plattform"]) for e in p}
        self.assertFalse(any(a == 7 for a, _ in paare))                      # vor dem Zurückziehen freigegeben: verfällt
        self.assertEqual({pl for a, pl in paare if a == 6}, {"instagram"})   # „×“ nur:tiktok,youtube
        self.assertIn((12, "tiktok"), paare)                                 # neue Freigabe danach gilt wieder

    def test_sperre_abgelehnt(self):
        ok = {"titel": "NPC fährt gegen Laterne", "datei": "x.mp4", "checks": {"ton_sync": {"status": "gruen"}}}
        self.assertIn("Abgelehnt", lib.sperrgrund({**ok, "status": "abgelehnt"}))
        self.assertIsNone(lib.sperrgrund({**ok, "status": "fertig"}))

    def test_marker(self):
        self.assertEqual(lib.rueckzug_art("[zurueckziehen] [gründe: x]"), "schlecht")
        self.assertEqual(lib.rueckzug_art("[rausnehmen] nur:tiktok"), "raus")
        self.assertIsNone(lib.rueckzug_art("[posten]"))
        self.assertEqual(lib.rueckzug_plattformen("[rausnehmen] nur:tiktok,youtube"), {"tiktok", "youtube"})
        self.assertIsNone(lib.rueckzug_plattformen("[rausnehmen]"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- 25.11.: Titel, Beschreibung, Hashtags je Plattform
BLOCK = ("Titel: Trymacs dachte, Verstümmelung ist ein Gaming-Begriff 💀\n\nBeschreibung:\nTrymacs liest in GTA 6 die Einstellungen, "
         "erfährt was Verstümmelung bedeutet und lässt es trotzdem an 😂\n\n🎥 Trymacs auf Twitch (Fanpage, eigener Schnitt)\n"
         "#trymacs #gta6 #gta")
XQC = ("Titel: xQc's first car theft in GTA 6 goes WRONG 😭\n\nBeschreibung:\nxQc tries to steal a car in GTA 6 for the first time 😭\n"
       "Who did it better on their first try? 👇\n\n#xqc #gta6 #gta #twitchclips\n\nYouTube Shorts: xQc's first car theft goes wrong 😭 #clipwreck #xqc")
TRENDS = {"hashtags": [{"tag": "gta6", "gewicht": 500}, {"tag": "byalexz", "gewicht": 150}, {"tag": "gta6gameplay", "gewicht": 116},
                       {"tag": "zarbex", "gewicht": 109}, {"tag": "streamingdeutschland", "gewicht": 105}, {"tag": "casino", "gewicht": 99},
                       {"tag": "rohatclips", "gewicht": 56}],
          "streamer": [{"name": "trymacs"}, {"name": "zarbex"}],
          "top_clips": [{"kanal": "by.alexz"}, {"kanal": "rohatclips"}]}


class Texte(unittest.TestCase):
    def karten(self):
        return [{"caption": BLOCK, "titel": "Trymacs dachte, Verstümmelung ist ein Gaming-Begriff 💀", "kanal": "fanpage", "art": "GTA"},
                {"caption": XQC, "titel": "xQc's first car theft in GTA 6 goes WRONG 😭", "kanal": "fanpage"},
                {"caption": "Er fährt einfach rein #gta6", "titel": "Zarbex crasht", "kanal": "Zarbex", "art": "GTA"}]

    def test_kein_block_mehr_im_posttext(self):
        for k in self.karten():
            for p in ("tiktok", "youtube", "instagram"):
                t = lib.texte_bauen(k, p, trends=TRENDS)
                text = lib.texte_text(t, p)
                self.assertNotRegex(text.lower(), r"titel\s*:|beschreibung\s*:|youtube shorts\s*:")
                self.assertIsNone(lib.texte_pruefen(t, p), (p, text))
                if p == "tiktok":
                    self.assertNotIn("\n", text)
                    self.assertFalse(text.startswith(k["titel"]))   # 26.11.: kein Titel in der Beschreibung
                if p == "instagram":
                    self.assertLessEqual(len(set(lib._tags_aus(text))), 5)
                if p == "youtube":
                    m = lib.metadata_bauen(k, p, teile=t)["youtube"]
                    self.assertLessEqual(len(m["title"]), 100)
                    self.assertNotIn("#", m["title"])
                    self.assertIn("#shorts", text)
                    self.assertLessEqual(len(set(lib._tags_aus(text))), 3)

    def test_quelle_als_eigener_satz(self):
        t = lib.texte_bauen(self.karten()[0], "instagram")
        self.assertNotIn("Fanpage", t["beschreibung"])   # 26.11.: keine Quellenzeile mehr
        self.assertTrue(t["beschreibung"].startswith("@trymacs "))
        x = lib.texte_bauen(self.karten()[1], "tiktok")
        self.assertTrue(x["beschreibung"].startswith("@twitch.xqc "))

    def test_youtube_titel_aus_shorts_zeile(self):
        k = self.karten()[1]
        self.assertEqual(lib.texte_bauen(k, "youtube")["titel"], "xQc's first car theft goes wrong 😭")
        self.assertNotIn("YouTube", lib.texte_bauen(k, "instagram")["beschreibung"])
        # eigener Titel von Marvin gilt auch auf YouTube
        self.assertEqual(lib.texte_bauen({**k, "titel": "Mein Titel"}, "youtube", titel_gesetzt=True)["titel"], "Mein Titel")
        # Karte hat später einen anderen Titel bekommen als der Block: der gilt
        self.assertEqual(lib.texte_bauen({**k, "titel": "Neuer Titel"}, "youtube")["titel"], "Neuer Titel")

    def test_trends_nur_passend(self):
        k = self.karten()[0]
        tags = lib.trend_tags(k, TRENDS)
        self.assertNotIn("#byalexz", tags)        # fremde Kanal-Marke
        self.assertNotIn("#rohatclips", tags)
        self.assertNotIn("#zarbex", tags)         # anderer Streamer, nicht im Clip
        self.assertNotIn("#casino", tags)         # Glücksspiel nie
        self.assertIn("#gta6gameplay", lib.trend_tags(k, TRENDS, ohne={"#gta6"}))
        self.assertEqual(lib.trend_tags(k, None), [])

    def test_gluecksspiel_im_bearbeiteten_text_sperrt(self):
        self.assertIn("Glücksspiel", lib.texte_pruefen({"titel": "x", "beschreibung": "Ab ins Casino", "hashtags": []}, "tiktok"))
        self.assertIn("Glücksspiel", lib.texte_pruefen({"titel": "x", "beschreibung": "y", "hashtags": ["#roulette"]}, "tiktok"))
        self.assertIn("Titel:", lib.texte_pruefen({"titel": "x", "beschreibung": "Titel: y", "hashtags": []}, "tiktok"))
        self.assertIn("höchstens 5", lib.texte_pruefen({"titel": "x", "beschreibung": "y", "hashtags": [f"#t{i}" for i in range(6)]}, "instagram"))

    def test_kommentar_hin_und_zurueck(self):
        texte = {"tiktok": {"titel": "Hook 💀", "beschreibung": "Satz.\n\n🎥 Quelle", "hashtags": ["#a", "b"]}}
        k = "[posten] slot:tiktok=sofort " + lib.texte_kommentar(texte)
        back = lib.texte_aus_kommentar(k)
        self.assertEqual(back["tiktok"]["titel"], "Hook 💀")
        self.assertEqual(back["tiktok"]["hashtags"], ["#a", "#b"])
        self.assertEqual(lib.wunsch_slots(k), {"tiktok": "sofort"})
        self.assertEqual(lib.ohne_texte(k), "[posten] slot:tiktok=sofort")
        self.assertEqual(lib.texte_aus_kommentar("[posten] texte:kaputt!!"), {})


class TexteImJob(unittest.TestCase):
    def test_bearbeiteter_text_wird_gepostet(self):
        t = {p: {"titel": "Mein Hook 💀", "beschreibung": "Mein Satz.", "hashtags": ["#zarbex"]} for p in ("tiktok", "youtube", "instagram")}
        z = WunschSlots()._lauf("[posten] " + lib.texte_kommentar(t))
        tik = next(e for e in z if e["plattform"] == "tiktok")
        self.assertEqual(tik["text"], "Mein Satz. #zarbex")   # Titel nie in der Beschreibung
        yt = WunschSlots.letzter_zustand["youtube_warte_probe"][0]
        self.assertEqual(yt["meta"]["youtube"]["title"], "Mein Hook 💀")

    def test_gluecksspiel_im_bearbeiteten_text_bleibt_gesperrt(self):
        t = {"tiktok": {"titel": "Hook", "beschreibung": "Ab ins Casino", "hashtags": []}}
        self.assertEqual(WunschSlots()._lauf("[posten] " + lib.texte_kommentar(t)), [])

    def test_korrektur_nur_mit_freigabe_und_nie_gesendete(self):
        import posten
        jetzt = dt.datetime(2026, 11, 25, 20, 0, tzinfo=dt.timezone.utc)
        alt = "Titel: Trymacs dachte 💀 Beschreibung: Trymacs liest #trymacs"
        geplant = [{"auftrag_id": 5, "plattform": "tiktok", "status": "geplant", "buffer_id": "b1", "text": alt,
                    "slot_utc": "2026-11-25T21:00:00+00:00"},
                   {"auftrag_id": 5, "plattform": "instagram", "status": "geplant", "buffer_id": "b2", "text": alt,
                    "slot_utc": "2026-11-25T21:00:00+00:00"},
                   {"auftrag_id": 5, "plattform": "youtube", "status": "geplant", "buffer_id": "b3", "text": "sauber",
                    "slot_utc": "2026-11-25T21:00:00+00:00"}]

        class Sb:
            def __init__(self, info):
                self.info, self.geschrieben = info, {}

            def info_lesen(self, k, d=None):
                return self.info

            def info_schreiben(self, k, w):
                self.geschrieben[k] = w

            def holen(self, t, q):
                return [{"id": 5, "karte_id": "k5", "kommentar": "[posten]", "titel": None,
                         "karten": {"caption": BLOCK, "titel": "Trymacs dachte, Verstümmelung ist ein Gaming-Begriff 💀"}}]

        class Buf:
            bearbeitet = []

            def gesendete_posts(self, org, ids, max_seiten=10):
                return [{"id": "b2"}]

            def post_bearbeiten(self, i, text, meta):
                Buf.bearbeitet.append((i, text))

        z = {"geplant": [dict(g) for g in geplant]}
        sb = Sb({})
        posten.texte_korrigieren(sb, z, Buf(), "org", {"tiktok": {"id": "c"}}, True, None, jetzt)
        self.assertEqual(Buf.bearbeitet, [])                       # ohne Freigabe nichts
        self.assertEqual(len(sb.geschrieben["texte_korrektur"]["vorschau"]), 3)   # 26.11.: alles, was nicht Marvins Vorlage entspricht
        sb = Sb({"freigabe": True})
        posten.texte_korrigieren(sb, z, Buf(), "org", {"tiktok": {"id": "c"}}, True, None, jetzt)
        self.assertEqual([i for i, _ in Buf.bearbeitet], ["b1", "b3"])     # b2 ist schon gesendet
        self.assertNotIn("Titel:", Buf.bearbeitet[0][1])
        erg = {e["buffer_id"]: e["status"] for e in sb.geschrieben["texte_korrektur"]["ergebnis"]}
        self.assertEqual(erg, {"b1": "korrigiert", "b2": "schon veröffentlicht, nicht angefasst", "b3": "korrigiert"})
        self.assertFalse(sb.geschrieben["texte_korrektur"]["freigabe"])
        self.assertEqual(z["geplant"][1]["status"], "gesendet")
        Buf.bearbeitet = []
        sb = Sb({"freigabe": True})
        posten.texte_korrigieren(sb, z, Buf(), "org", {"tiktok": {"id": "c"}}, False, None, jetzt)
        self.assertEqual(Buf.bearbeitet, [])                       # Probelauf ändert nie etwas


class YoutubeGrenze(unittest.TestCase):
    def test_youtube_max_zwei_pro_tag(self):
        jetzt = dt.datetime(2026, 11, 26, 8, 0, tzinfo=BER).astimezone(UTC)
        zwei = {lib.slot_aus_lokal("2026-11-26T13:00").isoformat(), lib.slot_aus_lokal("2026-11-26T16:00").isoformat()}
        self.assertEqual(lib.naechster_slot(zwei, jetzt, plattform="youtube").astimezone(BER).day, 27)
        self.assertEqual(lib.naechster_slot(zwei, jetzt, plattform="tiktok").astimezone(BER).hour, 11)
        self.assertIn("2 Posts", lib.slot_pruefen(lib.slot_aus_lokal("2026-11-26T19:00"), zwei, jetzt, plattform="youtube"))
        self.assertIsNone(lib.slot_pruefen(lib.slot_aus_lokal("2026-11-26T19:00"), zwei, jetzt, plattform="tiktok"))


class Abgleich(unittest.TestCase):
    def test_geloeschte_werden_frei_und_kein_doppelpost(self):
        import posten

        class Buf:
            def post_ids(self, org, ids, status, max_seiten=4):
                return {"b2"} if "sent" in status else {"b3"}

        z = {"geplant": [{"karte_id": "k", "plattform": "tiktok", "status": "geplant", "buffer_id": "b1"},
                         {"karte_id": "k", "plattform": "youtube", "status": "geplant", "buffer_id": "b2"},
                         {"karte_id": "k", "plattform": "instagram", "status": "geplant", "buffer_id": "b3"}]}
        posten.buffer_abgleich(z, Buf(), "o", {"tiktok": {"id": "c"}})
        self.assertEqual([g["status"] for g in z["geplant"]], ["geloescht", "gesendet", "geplant"])
        # Instagram ist noch geplant: neue Freigabe der Karte postet nur TikTok und YouTube
        r = WunschSlots()._lauf("[posten]", geplant=[{"karte_id": "k9", "plattform": "instagram", "status": "geplant",
                                                      "slot_utc": "2030-01-01T12:00:00+00:00"}])
        self.assertEqual(sorted(e["plattform"] for e in r), ["tiktok"])
        self.assertEqual(len(WunschSlots.letzter_zustand["youtube_warte_probe"]), 1)


class Stopp(unittest.TestCase):
    def test_alte_freigaben_ungueltig_und_geplante_geloescht(self):
        import posten
        jetzt = dt.datetime(2026, 11, 25, 20, 40, tzinfo=dt.timezone.utc)

        class Sb:
            geschrieben = {}

            def info_lesen(self, k, d=None):
                return {"bis_auftrag": 5}

            def info_schreiben(self, k, w):
                Sb.geschrieben[k] = w

        class Buf:
            weg = []

            def post_loeschen(self, i):
                Buf.weg.append(i)

        z = {"erledigte_auftraege": [], "geplant": [
            {"auftrag_id": 5, "plattform": "tiktok", "status": "geplant", "buffer_id": "b1"},
            {"auftrag_id": 1, "plattform": "tiktok", "status": "gesendet", "buffer_id": "b0"},
            {"auftrag_id": 9, "plattform": "tiktok", "status": "geplant", "buffer_id": "b9"}]}
        offen = posten.stopp_anwenden(Sb(), z, Buf(), True, [{"id": 4}, {"id": 6}], jetzt)
        self.assertEqual([a["id"] for a in offen], [6])          # nur neue Bestätigungen zählen
        self.assertEqual(z["erledigte_auftraege"], [4])
        self.assertEqual(Buf.weg, ["b1"])                        # gesendet und neue nie
        self.assertEqual([g["status"] for g in z["geplant"]], ["storniert", "gesendet", "geplant"])
        Buf.weg = []
        posten.stopp_anwenden(Sb(), {"erledigte_auftraege": [], "geplant": [{"auftrag_id": 5, "status": "geplant", "buffer_id": "x"}]},
                              Buf(), False, [], jetzt)
        self.assertEqual(Buf.weg, [])                            # Probelauf löscht nie


class VersetztUndBeste(unittest.TestCase):
    def test_reels_einen_tag_nach_tiktok(self):
        z = WunschSlots()._lauf("[posten]")
        tik = next(e for e in z if e["plattform"] == "tiktok")
        ig = next(e for e in z if e["plattform"] == "instagram")
        self.assertGreaterEqual(dt.datetime.fromisoformat(ig["slot_utc"]) - dt.datetime.fromisoformat(tik["slot_utc"]), dt.timedelta(hours=24))

    def test_nur_die_besten_auf_youtube(self):
        import posten
        r = lib.regeln()
        jetzt = dt.datetime(2026, 11, 26, 7, 0, tzinfo=dt.timezone.utc)   # 08:00 Berlin, erster Slot 13:00
        warte = [{"auftrag_id": i, "karte_id": f"k{i}", "titel": f"C{i}", "wert": w, "ab_utc": "2026-11-26T00:00:00+00:00",
                  "erstellt": "2026-11-25T20:00:00+00:00", "teile": {"titel": f"C{i}", "beschreibung": "x", "hashtags": ["#shorts"]}}
                 for i, w in ((1, 60), (2, 90), (3, 75))]
        z = {"geplant": [], "youtube_warte": warte}
        gesendet = []

        def senden(w, slot):
            gesendet.append((w["karte_id"], slot.astimezone(BER).strftime("%d %H:%M")))
            return {"status": "geplant", "slot_utc": slot.isoformat(), "plattform": "youtube", "slot_lokal": "", "titel": w["titel"]}

        posten.youtube_warte_planen(z, r, jetzt, senden, True, [])
        self.assertEqual([k for k, _ in gesendet], ["k2", "k3"])          # die 2 besten des Tages
        self.assertEqual([w["karte_id"] for w in z["youtube_warte"]], ["k1"])
        self.assertEqual(gesendet[0][1], "26 11:00")

    def test_zu_frueh_wird_nicht_entschieden(self):
        import posten
        r = lib.regeln()
        jetzt = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc)
        z = {"geplant": [], "youtube_warte": [{"auftrag_id": 1, "karte_id": "k", "titel": "C", "wert": 50, "ab_utc": "2026-11-26T12:00:00+00:00",
                                                "erstellt": "2026-11-25T11:00:00+00:00", "teile": {}}]}
        posten.youtube_warte_planen(z, r, jetzt, lambda w, s: self.fail("zu früh"), True, [])
        self.assertEqual(len(z["youtube_warte"]), 1)

    def test_regeln_grenzen(self):
        class Sb:
            def __init__(self, w):
                self.w = w

            def info_lesen(self, k, d=None):
                return self.w

        self.assertEqual(lib.regeln(Sb({"youtube_pro_tag": 9}))["youtube_pro_tag"], 2)            # Buffer kann nur 2
        self.assertEqual(lib.regeln(Sb({"youtube_pro_tag": 9, "youtube_weg": "direkt"}))["youtube_pro_tag"], 9)
        self.assertEqual(lib.regeln(Sb({"youtube_pro_tag": 500, "youtube_weg": "direkt"}))["youtube_pro_tag"], 20)


class StrikeRegel(unittest.TestCase):
    def test_erster_pausiert_streamer_zweiter_stoppt_fanpage(self):
        k = {"titel": "Trymacs schrottet sein Auto", "kanal": "fanpage", "art": "Fanpage-Clip", "datei": "a.mp4",
             "checks": {"ton_sync": {"status": "gruen"}}}
        z = dict(k, titel="Zarbex crasht")
        eins = {"liste": [{"streamer": "trymacs"}]}
        self.assertIn("pausiert", lib.sperrgrund(k, strikes=eins))
        self.assertIsNone(lib.sperrgrund(z, strikes=eins))
        zwei = {"liste": [{"streamer": "trymacs"}, {"streamer": "papaplatte"}]}
        self.assertIn("gestoppt", lib.sperrgrund(z, strikes=zwei))
        self.assertIsNone(lib.sperrgrund(dict(z, art="Kampagne"), strikes=zwei))


class YouTubeDirekt(unittest.TestCase):
    def test_upload_geplant_privat_mit_publishat(self):
        aufrufe = []

        def roh(url, methode="GET", daten=None, kopf=None, timeout=300):
            aufrufe.append((methode, url, daten, kopf))
            if "oauth2" in url:
                return 200, {}, b'{"access_token": "t"}'
            if "uploadType=resumable" in url:
                return 200, {"Location": "https://up/1"}, b""
            return 200, {}, b'{"id": "vid1"}'

        alt = lib._roh
        lib._roh = roh
        try:
            yt = lib.YouTube({"client_id": "c", "client_secret": "s", "refresh_token": "r"})
            slot = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5)
            teile = {"titel": "Titel 💀", "beschreibung": "Satz.\n\n🎥 Quelle", "hashtags": ["#shorts", "#trymacs"]}
            self.assertEqual(yt.hochladen(b"mp4", teile, "20", slot), "vid1")
        finally:
            lib._roh = alt
        meta = json.loads(aufrufe[1][2])
        self.assertEqual(meta["snippet"]["title"], "Titel 💀")
        self.assertIn("#shorts", meta["snippet"]["description"])
        self.assertEqual(meta["snippet"]["tags"], ["shorts", "trymacs"])
        self.assertEqual(meta["status"]["privacyStatus"], "private")
        self.assertTrue(meta["status"]["publishAt"].endswith("Z"))
        self.assertFalse(meta["status"]["selfDeclaredMadeForKids"])
        self.assertEqual(aufrufe[2][0], "PUT")


class YouTubeZahlen(unittest.TestCase):
    def test_analytics_in_metriken(self):
        import zahlen

        class Yt:
            def analytics(self, ids, start, ende):
                return {"v1": {"views": 1000, "engagedViews": 700, "averageViewPercentage": 82.5, "averageViewDuration": 14,
                               "likes": 50, "shares": 5, "subscribersGained": 3}}

        jetzt = dt.datetime(2026, 11, 28, 6, 0, tzinfo=dt.timezone.utc)
        g = [{"weg": "direkt", "youtube_id": "v1", "status": "gesendet", "slot_utc": "2026-11-26T12:00:00+00:00", "titel": "T", "plattform": "youtube"}]
        m = {}
        self.assertEqual(zahlen.youtube_aktualisieren(m, Yt(), g, jetzt), 1)
        v = m["yt:v1"]["verlauf"]["2"]
        self.assertAlmostEqual(v["wegwisch_quote"], 0.3)
        k = zahlen.kennzahlen(m["yt:v1"])
        self.assertEqual(k["views_d3"], 1000)
        self.assertEqual(k["haltezeit"], 14)
        self.assertEqual(k["voll_angesehen"], 82.5)


class StrikeRaeumen(unittest.TestCase):
    def test_geplante_des_streamers_werden_geloescht(self):
        import posten

        class Buf:
            weg = []

            def post_loeschen(self, i):
                Buf.weg.append(i)

        z = {"geplant": [{"status": "geplant", "buffer_id": "b1", "titel": "Trymacs crasht", "kanal": "fanpage", "art": "Fanpage-Clip"},
                         {"status": "geplant", "buffer_id": "b2", "titel": "Zarbex crasht", "kanal": "fanpage", "art": "Fanpage-Clip"},
                         {"status": "gesendet", "buffer_id": "b3", "titel": "Trymacs alt", "kanal": "fanpage", "art": "Fanpage-Clip"}],
             "youtube_warte": [{"karte": {"titel": "Trymacs wartet", "art": "Fanpage-Clip"}}, {"karte": {"titel": "Eli wartet"}}]}
        posten.strikes_raeumen(z, {"liste": [{"streamer": "trymacs"}]}, Buf(), None, True)
        self.assertEqual(Buf.weg, ["b1"])
        self.assertEqual([g["status"] for g in z["geplant"]], ["storniert", "geplant", "gesendet"])
        self.assertEqual(len(z["youtube_warte"]), 1)


class FuenfSlots(unittest.TestCase):
    def test_fuenf_feste_slots_pro_tag(self):
        jetzt = dt.datetime(2026, 11, 26, 8, 0, tzinfo=BER).astimezone(UTC)
        belegt, tage = set(), []
        for _ in range(5):
            s = lib.naechster_slot(belegt, jetzt, plattform="tiktok")
            belegt.add(s.isoformat())
            tage.append(s.astimezone(BER).strftime("%d %H:%M"))
        self.assertEqual(tage, ["26 11:00", "26 13:00", "26 16:00", "26 19:00", "26 20:30"])
        self.assertEqual(lib.naechster_slot(belegt, jetzt, plattform="tiktok").astimezone(BER).strftime("%d %H:%M"), "27 11:00")

    def test_neueste_momente_zuerst(self):
        import posten
        offen = [{"id": 1, "karten": {"zeit": "2026-11-20T10:00:00+00:00"}}, {"id": 2, "karten": {"zeit": "2026-11-25T22:00:00+00:00"}},
                 {"id": 3, "karten": {}, "erstellt": "2026-11-23T10:00:00+00:00"}]
        self.assertEqual([a["id"] for a in posten.aktuelle_zuerst(offen)], [2, 3, 1])


class MarvinsVorlage(unittest.TestCase):
    def test_wie_marvins_beispiel(self):
        k = {"titel": "Monte findet „Schradin“ in GTA 6 und verprügelt ihn 💀", "art": "Fanpage-Clip",
             "caption": "Monte findet Schradin in GTA 6 und kennt keine Gnade 💀 Wer hätte das gedacht? Wen würdest du im Spiel zuerst verprügeln? 👇"}
        t = lib.texte_text(lib.texte_bauen(k, "tiktok"), "tiktok")
        self.assertEqual(t, "@montanablack findet @maximalschradin in GTA 6 und kennt keine Gnade 💀 Wer hätte das gedacht? "
                            "Wen würdest du im Spiel zuerst verprügeln? 👇 #gta6 #gta #Gaming #viral #fyp")
        ig = lib.texte_text(lib.texte_bauen(k, "instagram"), "instagram")
        self.assertNotIn("Monte findet „Schradin“", ig)          # Titel nie in der Beschreibung
        self.assertTrue(ig.endswith("\n\n#gta6 #gta #Gaming #viral #fyp"))
        yt = lib.texte_bauen(k, "youtube")
        self.assertEqual(yt["titel"], k["titel"])
        self.assertEqual(yt["hashtags"], ["#shorts", "#gta6", "#Gaming"])

    def test_frage_wird_ergaenzt_und_ohne_emoji(self):
        t = lib.texte_bauen({"titel": "x", "caption": "Zarbex crasht in GTA 6 😭"}, "tiktok")
        self.assertIn("?", t["beschreibung"])
        self.assertIn("😭", t["beschreibung"])   # Emojis erlaubt (26.11. nachmittags)
        self.assertTrue(t["beschreibung"].startswith("@zarbex crasht"))

    def test_ohne_spiel_streamer_tag(self):
        t = lib.texte_bauen({"titel": "x", "caption": "Papaplatte lacht sich kaputt. Warum?", "kanal": "papaplatte"}, "instagram")
        self.assertEqual(t["hashtags"], ["#papaplatte", "#twitch", "#Gaming", "#viral", "#fyp"])


class HashtagsUndHandles(unittest.TestCase):
    def test_eigene_hashtags_aus_dem_text_haben_vorrang(self):
        k = {"titel": "T", "caption": "Zarbex crasht in GTA 6 😭 Wer kennt das? #gta6 #zarbex #streamingdeutschland #fail"}
        t = lib.texte_bauen(k, "tiktok")
        self.assertEqual(t["hashtags"], ["#gta6", "#zarbex", "#streamingdeutschland", "#fail"])
        self.assertIn("😭", t["beschreibung"])                      # Emojis sind erlaubt
        self.assertEqual(lib.texte_bauen(k, "youtube")["hashtags"], ["#shorts", "#gta6", "#zarbex"])

    def test_ohne_eigene_tags_trends(self):
        trends = {"hashtags": [{"tag": "gta6gameplay", "gewicht": 100}], "streamer": [], "top_clips": []}
        t = lib.texte_bauen({"titel": "T", "caption": "Zarbex crasht in GTA 6"}, "tiktok", trends=trends)
        self.assertEqual(t["hashtags"][:3], ["#gta6", "#gta", "#gta6gameplay"])

    def test_handles_mischen_auto_fuellt_nur_luecken(self):
        tab = lib.handles_mischen({"auto": [{"namen": ["trymacs"], "youtube": "@falsch"}, {"namen": ["neuer"], "youtube": "@neuer"}]})
        self.assertEqual(next(h for h in tab if "Trymacs" in h["namen"])["youtube"], "@trymacs")
        self.assertEqual(next(h for h in tab if "neuer" in h["namen"])["youtube"], "@neuer")
        self.assertTrue(lib.handles_setzen("Neuer lacht", "youtube", {"handles": tab}).startswith("@neuer"))

    def test_handles_je_plattform(self):
        self.assertTrue(lib.handles_setzen("Schradin lacht", "instagram").startswith("@maximal_schradin"))
        self.assertTrue(lib.handles_setzen("Eli lacht", "tiktok").startswith("Eli "))   # kein geprüftes TikTok-Handle
