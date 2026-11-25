"""Täglicher Analyse-Loop: Zahlen holen, 10 Tage nachverfolgen, auswerten, Regelwerk anpassen.

Quellen:
  1) Buffer-API (Post.metrics, täglich aktualisiert): Views, Likes, Kommentare, Shares, Follows, totalTimeWatched
  2) CSV-Fallback (csv_import.py auf dem PC, TikTok-Studio-Export) für Haltezeit/Wiedergabe-Quote

Ergebnis in Supabase info:
  metriken     Rohdaten je Post, Tag 1..10
  auswertung   Gruppen (Format, Länge, Titeltyp, Uhrzeit), Vorschläge, regelwerk_auto (begrenzt angepasst)
vault_sync.py schreibt das auf dem PC in den Obsidian-Vault.
"""
import datetime as dt
import statistics

from lib import CONFIG, Buffer, Supabase, YouTube, regeln

A = CONFIG["analyse"]


def tag_seit(iso, jetzt):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return max(1, (jetzt - t).days + 1)


def metriken_aktualisieren(metriken, posts, geplant, jetzt, plattform_von_kanal=None):
    nach_id = {g["buffer_id"]: g for g in geplant if g.get("buffer_id")}
    plattform_von_kanal = plattform_von_kanal or {}
    for p in posts:
        g = nach_id.get(p["id"])
        if g and g["status"] != "gesendet":
            g["status"] = "gesendet"
        eintrag = metriken.setdefault(p["id"], {"meta": {}, "verlauf": {}})
        eintrag["meta"] = {**eintrag["meta"], **({k: g.get(k) for k in ("titel", "art", "kanal", "dauer", "titel_typ", "slot", "karte_id", "plattform")} if g else {"titel": (p.get("text") or "")[:80], "manuell": True}),
                           "plattform": (g or {}).get("plattform") or plattform_von_kanal.get(p.get("channelId")) or eintrag["meta"].get("plattform") or "tiktok",
                           "gesendet": p.get("dueAt")}
        if not p.get("dueAt") or not p.get("metrics"):
            continue
        tag = tag_seit(p["dueAt"], jetzt)
        if tag > CONFIG["tracking_tage"]:
            continue
        werte = {m["type"]: m["value"] for m in p["metrics"]}
        eintrag["verlauf"][str(tag)] = {**werte, "abgerufen": jetzt.isoformat()}
    return metriken


def youtube_aktualisieren(metriken, yt, geplant, jetzt):
    """25.11.: direkt hochgeladene Shorts (weg „direkt“) über die YouTube Analytics API: Aufrufe, engagedViews
    (nicht sofort weggewischt), durchschnittlich gesehener Anteil, Wiedergabedauer, Likes, Shares, neue Abos.
    YouTube liefert die Zahlen mit 1 bis 2 Tagen Verzug."""
    direkt = [g for g in geplant if g.get("weg") == "direkt" and g.get("youtube_id") and g.get("status") == "gesendet"]
    frisch = [g for g in direkt if tag_seit(g["slot_utc"], jetzt) <= CONFIG["tracking_tage"] + 2]
    if not frisch:
        return 0
    start = min(dt.datetime.fromisoformat(g["slot_utc"]) for g in frisch).date().isoformat()
    werte = yt.analytics([g["youtube_id"] for g in frisch], start, jetzt.date().isoformat())
    for g in frisch:
        w = werte.get(g["youtube_id"])
        e = metriken.setdefault("yt:" + g["youtube_id"], {"meta": {}, "verlauf": {}})
        e["meta"] = {**{k: g.get(k) for k in ("titel", "art", "kanal", "dauer", "titel_typ", "slot", "karte_id")},
                     "plattform": "youtube", "gesendet": g["slot_utc"], "weg": "direkt"}
        if not w:
            continue
        views = w.get("views") or 0
        tag = tag_seit(g["slot_utc"], jetzt)
        if tag > CONFIG["tracking_tage"]:
            continue
        e["verlauf"][str(tag)] = {"views": views, "likes": w.get("likes"), "shares": w.get("shares"), "follows": w.get("subscribersGained"),
                                  "totalTimeWatched": (w.get("averageViewDuration") or 0) * views,
                                  "engagedViews": w.get("engagedViews"), "avg_prozent": w.get("averageViewPercentage"),
                                  "wegwisch_quote": (1 - (w.get("engagedViews") or 0) / views) if views else None,
                                  "abgerufen": jetzt.isoformat()}
    return len(frisch)


def kennzahlen(eintrag):
    """Views an Tag 3 (fairer Vergleich), sonst letzter Stand. Dazu Haltezeit und Engagement."""
    v = eintrag.get("verlauf", {})
    if not v:
        return None
    tage = sorted(int(t) for t in v)
    basis = v[str(max([t for t in tage if t <= 3] or [tage[0]]))]
    letzt = v[str(tage[-1])]
    views = basis.get("views") or basis.get("impressions") or 0
    csv = eintrag.get("csv", {})
    haltezeit = csv.get("avg_watch_s")
    if haltezeit is None and letzt.get("totalTimeWatched") and letzt.get("views"):
        haltezeit = letzt["totalTimeWatched"] / letzt["views"]
    inter = sum(letzt.get(k) or 0 for k in ("likes", "comments", "shares", "saves"))
    if csv.get("full_watch_pct") is None and letzt.get("avg_prozent") is not None:   # YouTube direkt
        csv = {**csv, "full_watch_pct": letzt["avg_prozent"]}
    return {"views_d3": views, "views_letzt": letzt.get("views") or 0, "haltezeit": haltezeit, "wegwisch_quote": letzt.get("wegwisch_quote"),
            "voll_angesehen": csv.get("full_watch_pct"), "follows": letzt.get("follows") or csv.get("new_followers"),
            "engagement": inter / letzt["views"] if letzt.get("views") else None, "alter_tage": tage[-1]}


def laengen_bucket(dauer):
    try:
        d = float(dauer)
    except (TypeError, ValueError):
        return None
    for lo, hi in A["laengen_buckets"]:
        if lo <= d < hi:
            return f"{lo}-{hi}s"
    return "ausserhalb"


def gruppieren(metriken):
    """Views werden je Plattform normiert (score = Views / Median der Plattform), damit YouTube, TikTok und
    Instagram fair vergleichbar sind. Gewichte im Regelwerk beruhen auf dem Score."""
    zeilen = []
    for pid, e in metriken.items():
        k = kennzahlen(e)
        if k:
            m = e.get("meta", {})
            zeilen.append({"id": pid, **m, **k, "plattform": m.get("plattform") or "tiktok", "laenge": laengen_bucket(m.get("dauer"))})
    medianen = {}
    for z in zeilen:
        medianen.setdefault(z["plattform"], []).append(z["views_d3"])
    medianen = {p: statistics.median(v) or 1 for p, v in medianen.items()}
    for z in zeilen:
        z["score"] = z["views_d3"] / medianen[z["plattform"]]
    gruppen = {}
    for dim in ("plattform", "art", "slot", "titel_typ", "laenge", "kanal"):
        werte = {}
        for z in zeilen:
            if z.get(dim):
                werte.setdefault(z[dim], []).append(z)
        gruppen[dim] = {w: {"n": len(l), "median_views": statistics.median(x["views_d3"] for x in l),
                            "median_score": statistics.median(x["score"] for x in l),
                            "median_haltezeit": _median([x["haltezeit"] for x in l]),
                            "median_engagement": _median([x["engagement"] for x in l])}
                        for w, l in werte.items()}
    return zeilen, gruppen


def _median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def regelwerk_anpassen(gruppen, alt, gesamt_median, heute):
    """Gewichte je Slot/Format/Titeltyp. Nur bei genug Daten, höchstens +-20 % pro Lauf, Basis 1.0."""
    neu = {k: dict(alt.get(k, {})) for k in ("slot_gewichte", "format_gewichte", "titel_gewichte")}
    neu["laenge_ziel"] = alt.get("laenge_ziel")
    aenderungen = []
    for dim, schluessel in (("slot", "slot_gewichte"), ("art", "format_gewichte"), ("titel_typ", "titel_gewichte")):
        for wert, g in gruppen.get(dim, {}).items():
            if g["n"] < A["mindest_anzahl_pro_gruppe"] or not gesamt_median:
                continue
            ziel = g["median_score"]  # 1.0 = Plattform-Durchschnitt
            vorher = neu[schluessel].get(wert, 1.0)
            schritt = max(-A["max_aenderung_pro_lauf"], min(A["max_aenderung_pro_lauf"], ziel - vorher))
            jetzt = round(min(3.0, max(0.3, vorher + schritt)), 2)
            if abs(jetzt - vorher) >= 0.05:
                neu[schluessel][wert] = jetzt
                aenderungen.append({"datum": heute, "was": f"{schluessel}.{wert}", "von": vorher, "auf": jetzt,
                                    "grund": f"Score {g['median_score']:.2f} (1,0 = Plattform-Median), {g['median_views']:.0f} Views, n={g['n']}"})
    laengen = {w: g for w, g in gruppen.get("laenge", {}).items() if g["n"] >= A["mindest_anzahl_pro_gruppe"] and w != "ausserhalb"}
    if len(laengen) >= 2:
        # Haltezeit zählt mit, wenn vorhanden: Views * (1 + Haltezeit-Anteil) wäre spekulativ, daher nur Views.
        beste = max(laengen, key=lambda w: laengen[w]["median_score"])
        if beste != neu["laenge_ziel"]:
            aenderungen.append({"datum": heute, "was": "laenge_ziel", "von": neu["laenge_ziel"], "auf": beste,
                                "grund": f"Bester Längenbereich nach Score ({laengen[beste]['median_score']:.2f}, n={laengen[beste]['n']})"})
            neu["laenge_ziel"] = beste
    neu["grenzen"] = A["regelwerk_grenzen"]
    return neu, aenderungen


def vorschlaege(gruppen, gesamt_median):
    tipps = []
    for dim, name in (("plattform", "Plattform"), ("art", "Format"), ("slot", "Uhrzeit"), ("titel_typ", "Titeltyp"), ("laenge", "Länge"), ("kanal", "Creator")):
        g = {w: x for w, x in gruppen.get(dim, {}).items() if x["n"] >= 2}
        if len(g) < 2 or not gesamt_median:
            continue
        sk = "median_views" if dim == "plattform" else "median_score"
        top = max(g, key=lambda w: g[w][sk])
        flop = min(g, key=lambda w: g[w][sk])
        sicher = "sicher" if min(g[top]["n"], g[flop]["n"]) >= A["mindest_anzahl_pro_gruppe"] else "noch wenig Daten"
        tipps.append(f"{name}: '{top}' läuft am besten ({g[top]['median_views']:.0f} Views, n={g[top]['n']}), "
                     f"'{flop}' am schwächsten ({g[flop]['median_views']:.0f}, n={g[flop]['n']}). {sicher}.")
    return tipps


def auswerten(metriken, alt_auswertung, jetzt):
    zeilen, gruppen = gruppieren(metriken)
    gesamt = statistics.median([z["views_d3"] for z in zeilen]) if zeilen else 0
    heute = jetzt.date().isoformat()
    alt_regel = (alt_auswertung or {}).get("regelwerk_auto", {})
    if any(x.get("datum") == heute for x in (alt_auswertung or {}).get("aenderungen", [])):
        regel, aend = alt_regel, []  # höchstens ein Regelwerk-Schritt pro Tag, auch wenn der Job zweimal läuft
    else:
        regel, aend = regelwerk_anpassen(gruppen, alt_regel, gesamt, heute)
    historie = ((alt_auswertung or {}).get("aenderungen", []) + aend)[-200:]
    top = sorted(zeilen, key=lambda z: z["score"], reverse=True)
    return {"stand": jetzt.isoformat(), "anzahl_posts": len(zeilen), "median_views_d3": gesamt,
            "gruppen": gruppen, "vorschlaege": vorschlaege(gruppen, gesamt), "regelwerk_auto": regel,
            "aenderungen": historie, "neu_heute": aend,
            "top": [{k: z.get(k) for k in ("titel", "plattform", "art", "slot", "views_d3", "haltezeit", "alter_tage")} for z in top[:5]],
            "flop": [{k: z.get(k) for k in ("titel", "plattform", "art", "slot", "views_d3", "haltezeit", "alter_tage")} for z in top[-3:]]}


def main():
    sb = Supabase()
    jetzt = dt.datetime.now(dt.timezone.utc)
    posting = sb.info_lesen("posting", {}) or {}
    metriken = sb.info_lesen("metriken", {}) or {}
    try:
        buf = Buffer()
        org, kanaele = buf.kanaele(CONFIG["buffer_kanal_name"])
        von_kanal = {k["id"]: p for p, k in kanaele.items()}
        posts = buf.gesendete_posts(org, list(von_kanal))
        metriken = metriken_aktualisieren(metriken, posts, posting.get("geplant", []), jetzt, von_kanal)
        typen = sorted({m["type"] for p in posts for m in (p.get("metrics") or [])})
        print(f"Kanäle: {', '.join(kanaele)}. Metrik-Typen: {', '.join(typen) or 'keine'}")
        print(f"{len(posts)} gesendete Posts von Buffer gelesen.")
    except SystemExit as e:
        print(f"Buffer übersprungen: {e}")
    if regeln(sb)["youtube_weg"] == "direkt" or any(g.get("weg") == "direkt" for g in posting.get("geplant", [])):
        try:
            n = youtube_aktualisieren(metriken, YouTube.aus_supabase(sb), posting.get("geplant", []), jetzt)
            print(f"YouTube Analytics: {n} direkte Shorts abgefragt.")
        except Exception as e:  # Zahlen sind Hilfe, kein Muss
            print(f"YouTube Analytics übersprungen: {str(e)[:200]}")
    sb.info_schreiben("metriken", metriken)
    if posting:
        sb.info_schreiben("posting", posting)
    a = auswerten(metriken, sb.info_lesen("auswertung", {}), jetzt)
    sb.info_schreiben("auswertung", a)
    print(f"Auswertung: {a['anzahl_posts']} Posts, {len(a['neu_heute'])} Regelwerk-Änderungen.")
    for t in a["vorschlaege"]:
        print(" -", t)


if __name__ == "__main__":
    main()
