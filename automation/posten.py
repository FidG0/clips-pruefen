"""Freigaben von der Handyseite -> Buffer: TikTok, YouTube Shorts, Instagram Reels (Clipwreck).

Läuft alle 15 Min in GitHub Actions. Slots aus config.json (13:00/16:00/19:00/20:30 plus Bonus 22:00, Berlin),
je Plattform max. 5 pro Tag. Den Bonus-Slot gibt es nur auf ausdrücklichen Wunsch, „nächster Slot“ nimmt ihn nie.
Buffer Free: max. 10 geplante Posts je Kanal. Seit 27.11. bekommt jede Freigabe sofort ihren Slot (Kalender voll),
liegt aber im eigenen Vorrat (vorrat=True) und wird erst nachgeschoben, wenn bei Buffer Platz ist (Rolling Window).
Ohne BUFFER_LIVE=ja nur Probelauf: info.posting.probelauf zeigt, was geplant würde, nichts geht an Buffer.

Freigabe = Auftrag mit aktion 'behalten' und Kommentar, der mit '[posten]' beginnt
(so bleibt die bestehende PC-Verarbeitung von 'behalten' unverändert gültig).
"""
import json
import datetime as dt
import re
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from lib import (CONFIG, Buffer, Supabase, YouTube, buffer_frei, vorrat_nachschieben, fremde_posts, fremd_doppelt, vorrat_ausweichen, clip_wert, regeln, strike_sperre, ist_bonus, sofort_slot, sofort_warnungen, format_von, live, metadata_bauen,
                 naechster_slot, slot_pruefen, sperrgrund, texte_aus_kommentar, texte_bauen, texte_pruefen, texte_text, titel_typ,
                 wunsch_slots, RAUSNEHMEN, RUECKZUG, rueckzug_art, rueckzug_plattformen)

STANDARD = {"erledigte_auftraege": [], "geplant": [], "probelauf": [], "gesperrt": [], "letzter_lauf": None}


def aktive_plattformen(cfg=CONFIG):
    return [p for p, x in cfg.get("plattformen", {"tiktok": {"aktiv": True}}).items() if x.get("aktiv")]


def offene_freigaben(sb, erledigt):
    q = urllib.parse.urlencode({
        "select": "id,karte_id,kommentar,titel,erstellt,karten(*)",
        "aktion": "eq.behalten",
        "kommentar": f"like.{CONFIG['marker']}*",
        "order": "id.asc",
    })
    return [a for a in sb.holen("auftraege", q) if a["id"] not in erledigt]


def texte_fuer(auftrag, karte, plattform, trends):
    """Marvins Text aus der Vorschau (texte: im Kommentar) oder der Vorschlag. Dazu der Sperrgrund oder None.
    Marvins Text wird voll geprüft. Beim Vorschlag sperrt nur Glücksspiel (neue Sperre auf den fertigen Text),
    kleinere Mängel (z. B. leerer YouTube-Titel) laufen wie bisher mit Ersatz („Clip“) durch."""
    eigen = texte_aus_kommentar(auftrag.get("kommentar")).get(plattform)
    teile = eigen or texte_bauen(karte, plattform, trends=trends, titel_gesetzt=bool(auftrag.get("titel")))
    grund = texte_pruefen(teile, plattform)
    if grund and not eigen and "Glücksspiel" not in grund:
        grund = None
    return teile, grund


def vorschlaege_schreiben(sb, trends, plattformen):
    """info.texte_vorschlag: Titel, Beschreibung, Hashtags je postbarer Karte und Plattform für die Vorschau
    auf Handy und PC (dieselbe Logik wie beim Posten, Trends inklusive)."""
    q = urllib.parse.urlencode({"select": "id,titel,caption,kanal,art,status,datei", "datei": "not.is.null", "order": "aktualisiert.desc",
                                "limit": "80"})
    karten = sb.holen("karten", q)
    aus = {}
    for k in karten:
        if k.get("status") == "abgelehnt":
            continue
        aus[k["id"]] = {}
        for pf in plattformen:
            t = texte_bauen(k, pf, trends=trends)
            aus[k["id"]][pf] = {**t, "text": texte_text(t, pf), "sperre": texte_pruefen(t, pf)}
    sb.info_schreiben("texte_vorschlag", {"stand": dt.datetime.now(dt.timezone.utc).isoformat(), "karten": aus,
                                          "trends_stand": (trends or {}).get("stand")})


def alter_text(g):
    t = str(g.get("text") or "")
    return bool(re.search(r"(?i)(^|\s)(titel|beschreibung)\s*:", t) or re.search(r"(?i)youtube shorts\s*:", t))


def texte_korrigieren(sb, zustand, buf, org_id, kanaele, ist_live, trends, jetzt, yt=None):
    """25.11. (Marvins Ja nötig): geplante Posts mit dem alten Block-Text in Buffer per editPost auf saubere Texte setzen.
    Läuft nur, wenn info.texte_korrektur.freigabe true ist. Vorher wird Buffer gefragt, welche Posts schon gesendet sind:
    die bleiben unberührt und werden nur gemeldet. Ohne Freigabe oder im Probelauf: nur Vorschau."""
    auftrag = sb.info_lesen("texte_korrektur", {}) or {}
    kandidaten = []
    for g in zustand["geplant"]:
        if g.get("status") != "geplant" or not (g.get("buffer_id") or g.get("youtube_id")):
            continue
        try:
            slot = dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        kandidaten.append((slot, g))
    if not kandidaten and not auftrag.get("freigabe"):
        return
    ids = sorted({g["auftrag_id"] for _, g in kandidaten})
    freig = {}
    if ids:
        q = urllib.parse.urlencode({"select": "id,karte_id,kommentar,titel,karten(*)", "id": f"in.({','.join(map(str, ids))})"})
        freig = {a["id"]: a for a in sb.holen("auftraege", q)}
    gesendet = set()
    darf = bool(auftrag.get("freigabe")) and ist_live and buf is not None
    if darf:
        gesendet = {x["id"] for x in buf.gesendete_posts(org_id, [k["id"] for k in kanaele.values()], max_seiten=4)}
    vorschau, ergebnis = [], []
    for slot, g in sorted(kandidaten, key=lambda x: x[0]):
        a = freig.get(g["auftrag_id"])
        if not a:
            continue
        k = {**(a.get("karten") or {})}
        if a.get("titel"):
            k["titel"] = a["titel"]
        pf = g.get("plattform", "tiktok")
        teile, sperre = texte_fuer(a, k, pf, trends)
        neu, meta = texte_text(teile, pf), metadata_bauen(k, pf, teile=teile)
        if neu == g.get("text") and not alter_text(g):
            continue   # schon im aktuellen Format
        zeile = {"buffer_id": g.get("buffer_id") or g.get("youtube_id"), "plattform": pf, "slot_lokal": g.get("slot_lokal"), "titel": teile.get("titel"),
                 "alt": g.get("text"), "neu": neu, "meta": meta}
        if g.get("buffer_id") and g["buffer_id"] in gesendet:
            zeile["status"] = "schon veröffentlicht, nicht angefasst"
            g["status"] = "gesendet"
        elif slot <= jetzt + dt.timedelta(minutes=3):
            zeile["status"] = "zu knapp vor dem Senden, nicht angefasst"
        elif sperre:
            zeile["status"] = "gesperrt: " + sperre
        elif darf:
            try:
                if g.get("weg") == "direkt":   # 26.11.: direkte YouTube-Uploads per videos.update
                    if yt is None:
                        raise RuntimeError("kein YouTube-Zugang")
                    yt.bearbeiten(g["youtube_id"], teile, (meta or {}).get("youtube", {}).get("categoryId", "20"))
                    antwort = {"dueAt": g["slot_utc"]}
                else:
                    antwort = buf.post_bearbeiten(g["buffer_id"], neu, meta) or {}
                g.update(text=neu, metadata=meta, teile=teile, titel=teile.get("titel"), hinweis="Text an Marvins Vorlage angepasst.")
                zeile["status"] = "korrigiert"
                try:   # Buffer-Doku: dueAt weglassen = unverändert. Hier nachgeprüft statt geglaubt.
                    neu_due = dt.datetime.fromisoformat(str(antwort.get("dueAt")).replace("Z", "+00:00"))
                    if abs((neu_due - slot).total_seconds()) > 60:
                        zeile["status"] = f"korrigiert, ABER Uhrzeit geändert auf {neu_due.isoformat()}"
                        g["hinweis"] = "Text korrigiert, Buffer meldet neue Uhrzeit: bitte in Buffer prüfen."
                except (TypeError, ValueError):
                    pass
            except Exception as fehler:   # bleibt alt, nächster Lauf versucht es nochmal
                zeile["status"] = "Fehler: " + str(fehler)[:200]
        else:
            zeile["status"] = "Vorschau (wartet auf Freigabe)"
        (ergebnis if darf else vorschau).append(zeile)
        print(f"korrektur {pf:9} {zeile['status']}  {g.get('slot_lokal')}  {teile.get('titel')}")
    if darf:
        fertig = all(not z["status"].startswith("Fehler") for z in ergebnis)
        sb.info_schreiben("texte_korrektur", {**auftrag, "freigabe": not fertig, "ergebnis": ergebnis,
                                              "gelaufen": jetzt.isoformat()})
    else:
        sb.info_schreiben("texte_korrektur", {**auftrag, "vorschau": vorschau, "geprueft": jetzt.isoformat()})


def buffer_abgleich(zustand, buf, org_id, kanaele):
    """25.11. (Marvin hat Posts in Buffer gelöscht): Einträge, die Buffer nicht mehr kennt, werden „geloescht“.
    Sonst blockieren sie Slots und die Buffer-Grenze, und neu einplanen ginge nicht. Gesendete bleiben gesendet."""
    ids = [k["id"] for k in kanaele.values()]
    da = buf.post_ids(org_id, ids, ["scheduled", "sending", "error", "needs_approval", "draft"])
    gesendet = buf.post_ids(org_id, ids, ["sent"], max_seiten=6)
    for g in zustand["geplant"]:
        if g.get("status") != "geplant" or not g.get("buffer_id") or g.get("weg") == "direkt":
            continue
        if g.get("buffer_id") and g["buffer_id"] in gesendet:
            g["status"] = "gesendet"
        elif g["buffer_id"] not in da:
            g.update(status="geloescht", hinweis="In Buffer gelöscht (25.11.), Slot ist wieder frei.")
            print(f"geloescht {g.get('plattform')} {g.get('slot_lokal')}  {g.get('titel')}")


def stopp_anwenden(sb, zustand, buf, ist_live, offen, jetzt, yt=None):
    """25.11. 21:35 (Marvin: „lösche die sachen so lange aus dem kalender und buffer, bis ich sie bestätigt habe“):
    info.posten_stopp = {"bis_auftrag": N}. Freigaben mit ID <= N gelten nicht mehr (nur ein neuer Klick zählt),
    ihre noch geplanten Buffer-Posts werden gelöscht. Veröffentlichte Posts fasst das nie an. Gibt die gültigen offenen zurück."""
    stopp = sb.info_lesen("posten_stopp", {}) or {}
    bis = int(stopp.get("bis_auftrag") or 0)
    if not bis:
        return offen
    alt = [a for a in offen if a["id"] <= bis]
    zustand["youtube_warte"] = [w for w in zustand.get("youtube_warte", []) if (w.get("auftrag_id") or 0) > bis]
    for a in alt:
        print(f"ungültig  Freigabe {a['id']} (vor dem Stopp, braucht neue Bestätigung)")
        if ist_live:
            zustand["erledigte_auftraege"].append(a["id"])
    ergebnis = []
    for g in zustand["geplant"]:
        # loeschen_offen: am 25.11. schon in der Anzeige storniert (Seite sollte sofort leer sein), Buffer-Löschung steht noch aus
        if (g.get("auftrag_id") or 0) > bis or not (g.get("status") == "geplant" or g.get("loeschen_offen")):
            continue
        zeile = {"buffer_id": g.get("buffer_id"), "plattform": g.get("plattform"), "slot_lokal": g.get("slot_lokal"), "titel": g.get("titel")}
        if not ist_live or buf is None:
            zeile["status"] = "würde gelöscht (Probelauf)"
        elif g.get("weg") == "direkt":
            try:
                if yt is None:
                    raise RuntimeError("kein YouTube-Zugang")
                yt.loeschen(g["youtube_id"])
                g.update(status="storniert", hinweis="Stopp: auf YouTube gelöscht, wartet auf neue Bestätigung.")
                g.pop("loeschen_offen", None)
                zeile["status"] = "auf YouTube gelöscht"
            except Exception as fehler:
                zeile["status"] = "Fehler: " + str(fehler)[:160]
        elif not g.get("buffer_id"):
            g.update(status="storniert", hinweis="Stopp 25.11.: wartet auf neue Bestätigung.")
            zeile["status"] = "storniert"
        else:
            try:
                buf.post_loeschen(g["buffer_id"])
                g.update(status="storniert", hinweis="Stopp 25.11.: in Buffer gelöscht, wartet auf neue Bestätigung.")
                g.pop("loeschen_offen", None)
                zeile["status"] = "in Buffer gelöscht"
            except Exception as fehler:   # meist schon von Marvin gelöscht (nicht gefunden): dann ist er weg
                if "not" in str(fehler).lower() and "found" in str(fehler).lower():
                    g.update(status="storniert", hinweis="Stopp 25.11.: war in Buffer schon gelöscht.")
                    g.pop("loeschen_offen", None)
                    zeile["status"] = "war schon gelöscht"
                elif "not allowed" in str(fehler).lower():
                    # 27.11.: Buffer lässt veröffentlichte Posts nicht mehr löschen („Account is not allowed to perform
                    # this action on post“). Marvin hat am 26.11. für den Gaming-Begriff-Reel „So lassen“ gewählt.
                    # Vorher versuchte jeder Lauf (alle 15 min) erneut zu löschen. Jetzt gilt er als gesendet.
                    g.update(status="gesendet", hinweis="Stopp 25.11.: Buffer erlaubt kein Löschen mehr (schon veröffentlicht), bleibt online.")
                    g.pop("loeschen_offen", None)
                    zeile["status"] = "schon veröffentlicht, Buffer erlaubt kein Löschen, bleibt"
                else:
                    zeile["status"] = "Fehler: " + str(fehler)[:160]
        ergebnis.append(zeile)
        print(f"stopp     {g.get('plattform')} {g.get('slot_lokal')} {zeile['status']}  {g.get('titel')}")
    if ergebnis:
        sb.info_schreiben("posten_stopp", {**stopp, "ergebnis": ergebnis, "gelaufen": jetzt.isoformat()})
    return [a for a in offen if a["id"] > bis]


def offene_rueckzuege(sb, erledigt):
    """26.11.: „Überarbeiten“ aus Posten ([zurueckziehen]) und „×“ ([rausnehmen], optional nur:plattform), älteste zuerst."""
    q = urllib.parse.urlencode({
        "select": "id,karte_id,kommentar,erstellt",
        "or": f"(kommentar.like.{RUECKZUG}*,kommentar.like.{RAUSNEHMEN}*)",
        "order": "id.asc",
    })
    return [a for a in sb.holen("auftraege", q) if a["id"] not in erledigt and rueckzug_art(a.get("kommentar"))]


def rueckzuege_anwenden(rueck, zustand, buf, ist_live, yt=None):
    """Löscht die betroffenen noch geplanten Posts (Buffer oder YouTube direkt). Veröffentlichtes bleibt, bekommt nur einen
    Hinweis. Gibt (fertige Rückzug-IDs, Sperren) zurück. Sperren: {karte_id: [(rueckzug_id, plattformen oder None)]},
    sie gelten für Freigaben mit kleinerer ID. Geht ein Löschen schief, bleibt der Rückzug offen (nächster Lauf)."""
    fertig, sperren = [], {}
    for r in sorted(rueck, key=lambda a: a["id"]):
        kid, art = r["karte_id"], rueckzug_art(r.get("kommentar"))
        nur = None if art == "schlecht" else rueckzug_plattformen(r.get("kommentar"))
        sperren.setdefault(kid, []).append((r["id"], nur))
        text = "Zum Überarbeiten zurückgezogen" if art == "schlecht" else "Aus dem Plan genommen"
        alles_gut = True
        for g in zustand["geplant"]:
            if g.get("karte_id") != kid or (g.get("auftrag_id") or 0) > r["id"] or (nur and g.get("plattform", "tiktok") not in nur):
                continue
            if g.get("status") == "gesendet":
                g["hinweis"] = f"{text}, aber schon veröffentlicht: nur in der App der Plattform löschbar."
                continue
            if g.get("status") != "geplant" or not ist_live:
                continue
            try:
                if g.get("weg") == "direkt":
                    if yt is None:
                        raise RuntimeError("kein YouTube-Zugang")
                    yt.loeschen(g["youtube_id"])
                elif g.get("buffer_id"):
                    if buf is None:
                        raise RuntimeError("Buffer nicht erreichbar")
                    buf.post_loeschen(g["buffer_id"])
            except Exception as fehler:
                if not ("not" in str(fehler).lower() and "found" in str(fehler).lower()):   # „nicht gefunden“ = schon weg
                    alles_gut = False
                    g["hinweis"] = f"{text}: Löschen ging nicht ({str(fehler)[:160]}). Nächster Lauf versucht es wieder, sonst von Hand löschen."
                    continue
            g.update(status="storniert", hinweis=f"{text}: gelöscht.")
            print(f"{art:9} {g.get('plattform')} storniert  {g.get('titel')}")
        if alles_gut:
            fertig.append(r["id"])
    return fertig, sperren


def pause_zurueckholen(sb, zustand, buf, ist_live, jetzt):
    """27.11. (0-Aufrufe-Alarm): während der TikTok-Pause dürfen auch die schon bei Buffer geplanten Posts nicht raus.
    Unsere eigenen werden in Buffer gelöscht und warten im Vorrat (nach der Pause plant vorrat_nachschieben sie neu).
    Von Hand geplante Posts stehen nicht in zustand["geplant"] und werden nie angefasst. Ohne Videodatei oder wenn das
    Löschen scheitert, bleibt der Post bei Buffer und bekommt einen Hinweis. Gibt (zurückgeholt, noch bei Buffer) zurück."""
    geholt = bleibt = 0
    for g in zustand["geplant"]:
        if (g.get("plattform", "tiktok") != "tiktok" or g.get("status") != "geplant" or not g.get("buffer_id")
                or g.get("weg") == "direkt"):
            continue
        slot = dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00"))
        if slot <= jetzt:
            continue
        try:
            if not ist_live or buf is None:
                raise RuntimeError("Buffer nicht erreichbar")
            if slot <= jetzt + dt.timedelta(minutes=3):
                raise RuntimeError("zu knapp vor dem Senden")
            if not g.get("pfad"):
                q = urllib.parse.urlencode({"select": "*", "id": "eq." + str(g.get("karte_id"))})
                g["bucket"], g["pfad"] = videodatei((sb.holen("karten", q) or [{}])[0])
            if not g.get("pfad"):
                raise RuntimeError("keine Videodatei zum späteren Neuplanen")
            buf.post_loeschen(g["buffer_id"])
        except Exception as fehler:
            bleibt += 1
            g["hinweis"] = f"TikTok-Pause: bleibt bei Buffer geplant ({str(fehler)[:160]}). Zur Not in Buffer von Hand löschen."
            print(f"pause     bleibt     {g.get('slot_lokal')}  {g.get('titel')}: {fehler}")
            continue
        g.setdefault("buffer_ids_alt", []).append(g["buffer_id"])
        g.update(buffer_id=None, vorrat=True, versuche=0, hinweis="TikTok-Pause (0 Aufrufe): aus Buffer zurückgeholt, wartet.")
        geholt += 1
        print(f"pause     zurück     {g.get('slot_lokal')}  {g.get('titel')}")
    return geholt, bleibt


def gesperrte_plattformen(auftrag, sperren):
    """None = Freigabe gilt normal. Menge = diese Plattformen entfallen. 'alle' = Freigabe verfällt ganz."""
    weg = set()
    for rid, nur in sperren.get(auftrag["karte_id"], []):
        if rid > auftrag["id"]:
            if nur is None:
                return "alle"
            weg |= nur
    return weg or None


def strikes_raeumen(zustand, strikes, buf, yt, ist_live):
    """Strike-Regel: noch geplante Posts gesperrter Streamer (bzw. aller Fanpage-Clips ab dem 2. Strike) löschen
    und aus der YouTube-Warteschlange nehmen. Veröffentlichte Posts löscht Marvin in der App."""
    if not (strikes or {}).get("liste"):
        return
    zustand["youtube_warte"] = [w for w in zustand.get("youtube_warte", []) if not strike_sperre(w.get("karte") or {}, strikes)]
    for g in zustand["geplant"]:
        if g.get("status") != "geplant":
            continue
        grund = strike_sperre({"titel": g.get("titel"), "kanal": g.get("kanal"), "art": g.get("art")}, strikes)
        if not grund or not ist_live:
            continue
        try:
            if g.get("weg") == "direkt":
                if yt is None:
                    raise RuntimeError("kein YouTube-Zugang")
                yt.loeschen(g["youtube_id"])
            elif g.get("buffer_id") and buf is not None:
                buf.post_loeschen(g["buffer_id"])
            g.update(status="storniert", hinweis="Strike-Regel: " + grund)
            print(f"strike    {g.get('plattform')} {g.get('slot_lokal')} gelöscht  {g.get('titel')}")
        except Exception as fehler:
            g["hinweis"] = f"Strike-Regel: Löschen ging nicht ({str(fehler)[:120]}), bitte von Hand."


def nur_plattformen(karte):
    """27.11.: Liste der Plattformen, auf die diese Karte darf, oder None (= alle). Steht in karten.checks.nur_plattformen
    ({"plattformen": [...]} oder direkt eine Liste) oder im Feld nur_plattformen. Gesetzt vom PC, wenn ein Clip neu
    gebaut wird, der auf anderen Plattformen schon läuft."""
    wert = karte.get("nur_plattformen")
    if wert is None:
        wert = (karte.get("checks") or {}).get("nur_plattformen")
    if isinstance(wert, dict):
        wert = wert.get("plattformen")
    if wert is None:
        return None
    if isinstance(wert, str):
        wert = [w.strip() for w in wert.split(",")]
    return {str(w).strip().lower() for w in wert if str(w).strip()}


def youtube_schon(karte_id, eintraege, fremd=(), karte=None, titel=None):
    """27.11. (YouTube doppelt: Asteroiden, Papaplatte Quiz je direkt UND über Buffer): Grund, warum diese Karte auf
    YouTube nicht noch einmal raus darf, sonst None. Zählt beide Wege (direkt und Buffer), geplant UND gesendet,
    und von Hand in Buffer gelegte YouTube-Posts mit demselben Titel. Ein YouTube-Video je Clip, für immer."""
    for g in eintraege:
        if g.get("karte_id") == karte_id and g.get("plattform") == "youtube" and g.get("status") in ("geplant", "gesendet", "probelauf"):
            weg = "direkt" if g.get("weg") == "direkt" or g.get("youtube_id") else "Buffer"
            return f"schon auf YouTube ({weg}, {g.get('slot_lokal') or g.get('slot_utc')})"
    if karte is not None or titel:
        e = fremd_doppelt(karte or {}, list(fremd), "youtube", titel)
        if e:
            return f"von Hand in Buffer schon auf YouTube ({e.get('slot_lokal')})"
    return None


def youtube_warte_planen(zustand, r, jetzt, senden, ist_live, probe, cfg=CONFIG):
    """25.11. (Marvin: „auf youtube dann nur die 2 besten clips“): Freigaben ohne festen YouTube-Slot warten in
    zustand.youtube_warte. Für jeden Tag wird erst kurz vorher entschieden (youtube_entscheid_stunden vor dem ersten
    Slot), dann bekommen die besten nach clip_wert die freien Plätze (youtube_pro_tag), frühestens versetzt nach TikTok.
    Wer nach youtube_warte_tagen nicht dran war, fällt raus (steht in youtube_verfallen)."""
    tz = ZoneInfo(cfg["zeitzone"])
    warte = zustand.setdefault("youtube_warte", [])
    frisch = []
    for w in warte:
        if (jetzt - dt.datetime.fromisoformat(w["erstellt"])).total_seconds() > r["youtube_warte_tage"] * 86400:
            zustand.setdefault("youtube_verfallen", []).append({k: w.get(k) for k in ("auftrag_id", "karte_id", "titel", "wert")})
            zustand["youtube_verfallen"] = zustand["youtube_verfallen"][-100:]
            print(f"youtube   verfallen  {w.get('titel')}")
        else:
            frisch.append(w)
    warte[:] = frisch
    schon = [(w, youtube_schon(w.get("karte_id"), zustand["geplant"] + probe, zustand.get("buffer_fremd") or [], w.get("karte"), w.get("titel")))
             for w in warte]
    for w, grund in schon:
        if grund:
            print(f"youtube   doppelt    {w.get('titel')}: {grund}, fällt aus der Warteschlange")
    warte[:] = [w for w, grund in schon if not grund]
    if not warte or r["youtube_pro_tag"] <= 0:
        return
    alle = zustand["geplant"] + probe + (zustand.get("buffer_fremd") or [])   # 27.11.: Hand-Posts belegen Slots mit
    normale = [s for s in cfg["slots"] if s not in cfg.get("bonus_slots", [])]
    for tag in range(0, 8):
        datum = (jetzt.astimezone(tz) + dt.timedelta(days=tag)).date()
        h, m = map(int, normale[0].split(":"))
        erster = dt.datetime(datum.year, datum.month, datum.day, h, m, tzinfo=tz)
        if jetzt < erster - dt.timedelta(hours=r["youtube_entscheid_stunden"]):
            break   # für diesen Tag wird noch nicht entschieden, vielleicht kommen bessere Clips
        tag_belegt = {g["slot_utc"] for g in alle if g.get("plattform") == "youtube" and g.get("status") in ("geplant", "gesendet", "probelauf")
                      and dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00")).astimezone(tz).date() == datum}
        frei = r["youtube_pro_tag"] - len(tag_belegt)
        tagesende = dt.datetime(datum.year, datum.month, datum.day, 23, 59, tzinfo=tz)
        kandidaten = sorted([w for w in warte if dt.datetime.fromisoformat(w["ab_utc"]) <= tagesende], key=lambda w: -w["wert"])
        for w in kandidaten[:max(0, frei)]:
            if youtube_schon(w.get("karte_id"), alle):   # zwei Warte-Einträge derselben Karte: nur einer geht raus
                warte.remove(w)
                continue
            belegt = {g["slot_utc"] for g in alle if g.get("plattform") == "youtube" and g.get("status") in ("geplant", "gesendet", "probelauf")}
            tagesanfang = dt.datetime(datum.year, datum.month, datum.day, 0, 0, tzinfo=tz)
            try:
                slot = naechster_slot(belegt, jetzt, cfg, plattform="youtube", ab_utc=max(dt.datetime.fromisoformat(w["ab_utc"]), tagesanfang))
            except RuntimeError:
                continue
            if slot.astimezone(tz).date() != datum:
                continue
            e = senden(w, slot)
            warte.remove(w)
            if ist_live:
                zustand["geplant"].append(e)
            else:
                e["status"] = "probelauf"
                probe.append(e)
            alle = zustand["geplant"] + probe + (zustand.get("buffer_fremd") or [])
            print(f"youtube   {e['status']:9} {e['slot_lokal']}  {e['titel']}  (Wert {w['wert']:.0f}, beste {r['youtube_pro_tag']} des Tages)")


def aktuelle_zuerst(offen):
    def schluessel(a):
        k = a.get("karten") or {}
        return str(k.get("zeit") or a.get("erstellt") or "")
    return sorted(offen, key=schluessel, reverse=True)


def videodatei(karte):
    """(bucket, pfad): Original aus 'fertig' bevorzugt, sonst Vorschau."""
    if karte.get("datei"):
        return CONFIG.get("storage_bucket_fertig", "fertig"), karte["datei"]
    if karte.get("vorschau"):
        return CONFIG["storage_bucket"], karte["vorschau"]
    return None, None


def einfrieren(sb, auftrag, bucket, pfad, ist_live):
    """27.11. (Marvin: „in posten immer die beste, zuletzt bearbeitete und bestätigte Fassung, und nichts mehr
    verändert“): Bei der Freigabe wird die Videodatei nach eingefroren/<auftrag>_<name> kopiert, gepostet wird nur
    noch diese Kopie. Rendert der PC die Karte später neu (Rollout, Layout, Überarbeiten), ändert das den geplanten
    Post nicht. Eine neue Fassung braucht eine neue Freigabe. Gibt (pfad, hinweis) zurück."""
    if not pfad or not ist_live:
        return pfad, None
    if pfad.startswith("eingefroren/"):
        return pfad, None
    ziel = f"eingefroren/{auftrag['id']}_{pfad.rsplit('/', 1)[-1]}"
    try:
        sb.kopieren(bucket, pfad, ziel)
        return ziel, None
    except Exception as fehler:   # z. B. schon kopiert (gleicher Auftrag, zweiter Lauf): dann gilt die Kopie
        text = str(fehler)
        if "exists" in text.lower() or "409" in text or "Duplicate" in text:
            return ziel, None
        print(f"nicht eingefroren {pfad}: {text[:200]}")
        return pfad, "Fassung nicht eingefroren (Kopie fehlgeschlagen), gepostet wird die aktuelle Datei."


def eintrag(auftrag, plattform, slot_utc, text, meta):
    k = auftrag.get("karten") or {}
    titel = auftrag.get("titel") or k.get("titel")
    lokal = slot_utc.astimezone(ZoneInfo(CONFIG["zeitzone"]))
    return {
        "auftrag_id": auftrag["id"], "karte_id": auftrag["karte_id"], "plattform": plattform, "titel": titel,
        "art": format_von(k), "kanal": k.get("kanal"), "dauer": k.get("dauer") or k.get("laenge"),
        "titel_typ": titel_typ(titel), "slot_utc": slot_utc.isoformat(), "slot": lokal.strftime("%H:%M"),
        "slot_lokal": lokal.strftime("%a %d.%m. %H:%M"), "text": text, "metadata": meta,
        "status": "geplant", "buffer_id": None,
    }


def belegte_slots(geplant, plattform):
    return {g["slot_utc"] for g in geplant
            if g.get("plattform", "tiktok") == plattform and g["status"] in ("geplant", "gesendet")}


def main():
    sb = Supabase()
    zustand = {**json.loads(json.dumps(STANDARD)), **(sb.info_lesen("posting", {}) or {})}   # Kopie: Listen in STANDARD nie mitverändern
    erledigt = set(zustand["erledigte_auftraege"])
    offen = offene_freigaben(sb, erledigt)
    rueck = offene_rueckzuege(sb, set(zustand.get("erledigte_rueckzuege") or []))
    ist_live = live()
    plattformen = aktive_plattformen()
    kanaele, org_id, buf = {}, None, None
    jetzt = dt.datetime.now(dt.timezone.utc)
    trends = sb.info_lesen("trends", None)   # vom PC (cloud_sync aus lernen/trends.json), fehlt er: nur config-Hashtags
    r = regeln(sb)
    try:   # 26.11.: automatisch gefundene Handles (tools/handles.py) ergänzen die geprüften aus config.json
        from lib import handles_mischen
        CONFIG["handles"] = handles_mischen(sb.info_lesen("handles", {}) or {})
    except Exception as fehler:
        print(f"Handles nicht ergänzt: {fehler}")
    CONFIG["plattformen"].setdefault("youtube", {})["max_posts_pro_tag"] = max(1, r["youtube_pro_tag"])
    strikes = sb.info_lesen("strikes", {}) or {}
    yt, yt_fehler = None, None
    if r["youtube_weg"] == "direkt" and ist_live:
        try:
            yt = YouTube.aus_supabase(sb)
            yt.token()
        except Exception as fehler:   # ohne Zugang warten YouTube-Clips, nichts geht verloren
            yt, yt_fehler = None, str(fehler)[:200]
            print(f"YouTube direkt nicht bereit: {yt_fehler}")
    zustand["youtube"] = {"weg": r["youtube_weg"], "pro_tag": r["youtube_pro_tag"], "bereit": yt is not None or r["youtube_weg"] != "direkt",
                          "fehler": yt_fehler, "geprueft": jetzt.isoformat()}
    bstand = zustand.get("buffer") or {}
    try:
        alter = (jetzt - dt.datetime.fromisoformat(bstand["geprueft"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        alter = 1e9
    # Verbindungs-Status für die Kanäle-Seite der Zentrale: bei jeder Freigabe, sonst höchstens stündlich
    korrektur = (sb.info_lesen("texte_korrektur", {}) or {}).get("freigabe")
    stopp_offen = bool((sb.info_lesen("strikes", {}) or {}).get("liste")) or any((g.get("status") == "geplant" or g.get("loeschen_offen")) and (g.get("auftrag_id") or 0) <= int((sb.info_lesen("posten_stopp", {}) or {}).get("bis_auftrag") or 0)
                      for g in zustand["geplant"])
    # 27.11.: Vorrat mit freiem Platz bei Buffer braucht auch einen Buffer-Zugang (Nachschieben)
    vorrat_wartet = any(g.get("vorrat") and g.get("status") == "geplant" for g in zustand["geplant"])
    # 27.11.: 0-Aufrufe-Alarm (cloud/tiktok_wache.py am PC setzt die Pause, Fortsetzen in der Zentrale)
    pause = {p for p, x in (sb.info_lesen("posten_pause", {}) or {}).items() if p == "tiktok" and (x or {}).get("aktiv")}
    pause_offen = any(g.get("buffer_id") and g.get("status") == "geplant" and g.get("plattform", "tiktok") in pause
                      for g in zustand["geplant"])
    if ist_live and (offen or rueck or alter > 3600 or korrektur or stopp_offen or vorrat_wartet or pause_offen):
        try:
            buf = Buffer()
            org_id, kanaele = buf.kanaele(CONFIG["buffer_kanal_name"])
            zustand["buffer"] = {"geprueft": jetzt.isoformat(), "fehler": None,
                                 "kanaele": {p: (k.get("displayName") or k.get("name")) for p, k in kanaele.items()}}
        except Exception as fehler:
            zustand["buffer"] = {"geprueft": jetzt.isoformat(), "fehler": str(fehler)[:200], "kanaele": {}}
            if offen:   # wie bisher: Freigaben bleiben offen und werden im nächsten Lauf neu versucht
                sb.info_schreiben("posting", zustand)
                raise

    if ist_live and buf is not None and kanaele and org_id:
        try:
            buffer_abgleich(zustand, buf, org_id, kanaele)
        except Exception as fehler:   # Abgleich ist Hilfe, kein Muss
            print(f"Buffer-Abgleich nicht gelaufen: {fehler}")
        try:   # 27.11.: von Hand geplante Posts mitzählen, nie anfassen
            zustand["buffer_fremd"], verschoben = fremde_posts(
                buf.offene_posts(org_id, [k["id"] for k in kanaele.values()]), zustand["geplant"],
                {k["id"]: p for p, k in kanaele.items()})
            zustand["buffer_fremd_geprueft"] = jetzt.isoformat()
            for g in verschoben:
                print(f"verschoben {g.get('plattform')} {g.get('slot_lokal')}  {g.get('titel')} (von Hand in Buffer)")
            if zustand["buffer_fremd"]:
                print(f"von Hand  {len(zustand['buffer_fremd'])} Posts in Buffer, zählen mit, werden nicht angefasst")
        except Exception as fehler:   # dann gilt der letzte bekannte Stand
            print(f"Hand-Posts nicht gelesen: {fehler}")
    # ohne frischen Buffer-Stand: letzter bekannter, nur künftige (vergangene sind gesendet und zählen nicht mehr)
    fremd = [e for e in (zustand.get("buffer_fremd") or [])
             if e.get("status") == "entwurf" or (e.get("slot_utc") and dt.datetime.fromisoformat(e["slot_utc"]) > jetzt)]
    zustand["buffer_fremd"] = fremd

    offen = stopp_anwenden(sb, zustand, buf, ist_live, offen, jetzt, yt)
    # 26.11. (Marvin: Clips werden unaktuell, andere posten schneller): die neuesten Momente bekommen die frühesten
    # Slots. Sortiert nach Zeitpunkt des Moments (karten.zeit), ohne Zeit nach Freigabe. Ältere warten auf spätere Slots.
    offen = aktuelle_zuerst(offen)
    # 26.11.: erst zurücknehmen („Überarbeiten“, „×“), dann neue Freigaben. So wird nie ein zurückgenommener Clip geplant.
    fertig_rueck, rueck_sperren = rueckzuege_anwenden(rueck, zustand, buf, ist_live, yt)
    if ist_live:
        zustand["erledigte_rueckzuege"] = (list(zustand.get("erledigte_rueckzuege") or []) + fertig_rueck)[-1000:]
    zustand["youtube_warte"] = [w for w in zustand.get("youtube_warte", [])
                                if gesperrte_plattformen({"id": w.get("auftrag_id") or 0, "karte_id": w.get("karte_id")}, rueck_sperren) is None]
    strikes_raeumen(zustand, strikes, buf, yt, ist_live)
    zustand["pause"] = {}
    if pause:
        geholt, bleibt = pause_zurueckholen(sb, zustand, buf, ist_live, jetzt)
        zustand["pause"] = {"tiktok": {"zurueckgeholt": geholt, "in_buffer": bleibt}}
    for g in zustand["geplant"]:   # direkte YouTube-Uploads: nach der Veröffentlichungszeit gelten sie als gesendet
        if g.get("weg") == "direkt" and g.get("status") == "geplant" and dt.datetime.fromisoformat(g["slot_utc"]) <= jetzt:
            g["status"] = "gesendet"

    def senden(e, p, slot, text, meta, teile, bucket, pfad, link_cache):
        """Ein Post an Buffer oder (YouTube direkt) an die YouTube-API. Fehler landen im Eintrag, nichts bricht ab."""
        if not pfad:
            e.update(status="fehler", fehler="Keine Videodatei in Supabase (datei/vorschau fehlt).")
            return e
        if not ist_live:
            return e
        try:
            if "link" not in link_cache:
                gueltig = (slot - dt.datetime.now(dt.timezone.utc)).total_seconds() + CONFIG["link_gueltig_tage"] * 86400
                link_cache["link"] = sb.signierter_link(bucket, pfad, max(gueltig, 3600))
            if p == "youtube" and r["youtube_weg"] == "direkt":
                if yt is None:
                    raise RuntimeError("YouTube direkt nicht eingerichtet: " + (yt_fehler or "kein Zugang"))
                with urllib.request.urlopen(link_cache["link"], timeout=300) as datei:
                    video = datei.read()
                kat = (meta or {}).get("youtube", {}).get("categoryId", "20")
                e["youtube_id"] = yt.hochladen(video, teile, kat, slot)
                e["weg"] = "direkt"
            else:
                if p not in kanaele:
                    raise RuntimeError(f"Kein {p}-Kanal in Buffer verbunden.")
                e["buffer_id"] = buf.post_planen(kanaele[p]["id"], text, link_cache["link"], slot, meta)["id"]
        except Exception as fehler:  # ein Fehler blockiert nicht die anderen Plattformen/Freigaben
            e.update(status="fehler", fehler=str(fehler)[:300])
        return e

    def ueber_buffer(p):
        return not (p == "youtube" and r["youtube_weg"] == "direkt")

    belegt = {p: belegte_slots(zustand["geplant"] + fremd, p) for p in plattformen}
    probe, gesperrt = [], []
    for a in offen:
        weg = gesperrte_plattformen(a, rueck_sperren)
        if weg == "alle":   # 26.11.: zurückgenommen, Freigabe verfällt ohne Post
            print(f"verfallen {a['karte_id']}: zurückgenommen")
            if ist_live:
                zustand["erledigte_auftraege"].append(a["id"])
            continue
        k = {**(a.get("karten") or {})}
        if a.get("titel"):
            k["titel"] = a["titel"]
        grund = sperrgrund(k, strikes=strikes)
        if grund:  # bleibt offen und wird jeden Lauf neu geprüft (z. B. sobald die Ton-Ampel grün ist)
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"), "grund": grund})
            print(f"gesperrt  {k.get('titel')}: {grund}")
            continue
        bucket, pfad = videodatei(k)
        pfad, frost_hinweis = einfrieren(sb, a, bucket, pfad, ist_live)
        link_cache = {}
        wunsch = wunsch_slots(a.get("kommentar"))   # Kalender: nur gewählte Plattformen, je im gewählten Slot
        ziel = [p for p in plattformen if p in wunsch] if wunsch else plattformen
        if weg:   # 26.11.: „×“ auf einzelnen Plattformen, die fallen weg
            ziel = [p for p in ziel if p not in weg]
        nur = nur_plattformen(k)
        if nur is not None:   # 27.11.: neu gebauter Clip, der woanders schon läuft (z. B. Rollator: nur TikTok neu)
            ziel = [p for p in ziel if p in nur]
        # 25.11.: kein Doppelpost. Ist die Karte auf einer Plattform noch geplant, entfällt diese Plattform.
        doppelt = [p for p in ziel if any(g.get("karte_id") == a["karte_id"] and g.get("plattform", "tiktok") == p
                                          and g.get("status") == "geplant" for g in zustand["geplant"] + probe)]
        # 27.11.: YouTube nie zweimal, egal ob direkt oder über Buffer, auch nicht nach dem Senden
        if "youtube" in ziel and "youtube" not in doppelt and youtube_schon(a["karte_id"], zustand["geplant"] + probe):
            doppelt.append("youtube")
        # 27.11.: auch nicht, wenn Marvin denselben Clip schon von Hand in Buffer gelegt hat
        hand = [p for p in ziel if p not in doppelt and fremd_doppelt(k, fremd, p, a.get("titel"))]
        doppelt += hand
        ziel = [p for p in ziel if p not in doppelt]
        if doppelt:
            print(f"doppelt   {k.get('titel')}: schon geplant auf {', '.join(doppelt)}" + (f" (von Hand: {', '.join(hand)})" if hand else ""))
        if not ziel:
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"),
                             "grund": "Schon auf allen Plattformen geplant" + (" (zum Teil von Hand in Buffer)" if hand else "") + ", kein Doppelpost."})
            if ist_live:
                zustand["erledigte_auftraege"].append(a["id"])
            continue
        texte = {p: texte_fuer(a, k, p, trends) for p in ziel}
        sperre_text = next((f"{p}: {g}" for p, (_, g) in texte.items() if g), None)
        if sperre_text:   # z. B. Glücksspiel-Wort in Marvins geändertem Text: bleibt offen, wird nie so gepostet
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"), "grund": "Text " + sperre_text})
            print(f"gesperrt  {k.get('titel')}: Text {sperre_text}")
            continue
        # 27.11.: keine Sperre mehr bei „Buffer voll“. Der Post bekommt seinen Slot und wartet im Vorrat (siehe unten).
        slots, hinweise = {}, {}
        # 25.11. (Marvin, Idee 5): TikTok zuerst. Reels versetzt, YouTube wartet auf die Tages-Auswahl der besten.
        reihe = sorted(ziel, key=lambda p: {"tiktok": 0}.get(p, 1))
        warte_youtube = "youtube" in reihe and "youtube" not in wunsch
        if warte_youtube and any(w.get("karte_id") == a["karte_id"] for w in zustand.get("youtube_warte", [])):
            reihe.remove("youtube")   # wartet schon, kein zweites Mal
            ziel = [p for p in ziel if p != "youtube"]
            warte_youtube = False
        if warte_youtube:
            reihe.remove("youtube")
            ziel = [p for p in ziel if p != "youtube"]
        for p in reihe:
            if wunsch.get(p) == "sofort":   # Bonus jetzt: geht immer in wenigen Minuten raus (25.11. abends, Marvin)
                warn = sofort_warnungen(zustand["geplant"] + probe + fremd, p)
                slots[p] = sofort_slot(zustand["geplant"] + probe, p)[0]
                if warn:
                    hinweise[p] = "Bonus trotz Hinweis gepostet: " + " ".join(warn)
                continue
            grund_slot = slot_pruefen(wunsch[p], belegt[p], plattform=p) if p in wunsch else None
            ab = None
            if p not in wunsch and p != "tiktok" and slots.get("tiktok"):
                ab = slots["tiktok"] + dt.timedelta(hours=r["versatz_stunden"].get(p, 0))
            slots[p] = wunsch[p] if p in wunsch and grund_slot is None else naechster_slot(belegt[p], plattform=p, ab_utc=ab)
            if grund_slot:
                hinweise[p] = "Wunsch-Slot ging nicht (" + grund_slot + "), nächster freier genommen."
        for p in ziel:
            slot = slots[p]
            teile = texte[p][0]
            text, meta = texte_text(teile, p), metadata_bauen(k, p, teile=teile)
            e = eintrag(a, p, slot, text, meta)
            e["teile"] = teile
            if p in wunsch:
                e["wunsch"] = True
            if wunsch.get(p) == "sofort" or ist_bonus(slot):
                e["bonus"] = True
            if hinweise.get(p):
                e["hinweis"] = hinweise[p]
            if pfad and pfad.startswith("eingefroren/"):
                e["eingefroren"] = pfad
            elif frost_hinweis:
                e["hinweis"] = ((e.get("hinweis") or "") + " " + frost_hinweis).strip()
            if p in pause:   # 27.11.: Pause, auch „sofort“ wartet
                e.update(vorrat=True, bucket=bucket, pfad=pfad, hinweis="TikTok-Pause (0 Aufrufe): wartet, bis du fortsetzt.")
            elif wunsch.get(p) == "sofort" or not ueber_buffer(p):
                senden(e, p, slot, text, meta, teile, bucket, pfad, link_cache)
            elif not pfad:
                e.update(status="fehler", fehler="Keine Videodatei in Supabase (datei/vorschau fehlt).")
            else:   # 27.11. Rolling Window: erst in den Vorrat, nachgeschoben wird nach Slot-Reihenfolge
                e.update(vorrat=True, bucket=bucket, pfad=pfad)
            if ist_live:
                zustand["geplant"].append(e)
            else:
                e["status"] = "probelauf"
                probe.append(e)
            if e["status"] in ("geplant", "probelauf"):
                belegt[p].add(e["slot_utc"])
            print(f"{p:9} {e['status']:9} {e['slot_lokal']}  {e['titel']}")
        if warte_youtube:
            teile_yt = texte["youtube"][0]
            ab_yt = (slots.get("tiktok") or jetzt) + dt.timedelta(hours=r["versatz_stunden"].get("youtube", 0))
            w = {"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": a.get("titel") or k.get("titel"), "teile": teile_yt,
                 "eigen": bool(texte_aus_kommentar(a.get("kommentar")).get("youtube")), "titel_gesetzt": bool(a.get("titel")),
                 "wert": clip_wert(k, trends), "ab_utc": ab_yt.isoformat(), "erstellt": jetzt.isoformat(), "bucket": bucket, "pfad": pfad,
                 "karte": {x: k.get(x) for x in ("titel", "art", "format", "kanal", "dauer", "laenge")},
                 "meta": metadata_bauen(k, "youtube", teile=teile_yt)}
            if ist_live:
                zustand.setdefault("youtube_warte", []).append(w)
            else:
                zustand.setdefault("_probe_warte", []).append(w)
            print(f"youtube   wartet    ab {ab_yt.astimezone(ZoneInfo(CONFIG['zeitzone'])).strftime('%a %d.%m. %H:%M')}  {w['titel']}  (Wert {w['wert']:.0f})")
        if ist_live:
            zustand["erledigte_auftraege"].append(a["id"])

    # 27.11. Rolling Window: Vorrat in Slot-Reihenfolge zu Buffer schieben, bis je Kanal wieder 10 geplant sind
    vorrat_log = []
    if ist_live and buf is not None and kanaele:
        for p in plattformen:
            if not ueber_buffer(p) or p in pause:
                continue
            def senden_vorrat(g, p=p):
                slot = dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00"))
                return senden(g, p, slot, g.get("text"), g.get("metadata"), g.get("teile") or {}, g.get("bucket"), g.get("pfad"), {})
            try:
                vorrat_log += vorrat_ausweichen(zustand["geplant"], fremd, p, jetzt)
                # die Liste ist nur zum Zählen zusammengesetzt: fremde Einträge haben nie vorrat=True, werden also nie gesendet
                vorrat_log += vorrat_nachschieben(zustand["geplant"] + fremd, p, senden_vorrat, jetzt)
            except Exception as fehler:   # Nachschieben darf den Rest nie blockieren
                vorrat_log.append(f"Nachschieben {p} nicht gelaufen: {str(fehler)[:200]}")
    for zeile in vorrat_log:
        print(zeile)
    zustand["vorrat"] = {p: {"im_vorrat": sum(1 for g in zustand["geplant"] if g.get("vorrat") and g.get("status") == "geplant"
                                              and g.get("plattform", "tiktok") == p),
                             "buffer_frei": buffer_frei(zustand["geplant"] + fremd, p, jetzt),
                             "von_hand": sum(1 for e in fremd if e.get("plattform") == p and e.get("status") == "geplant")}
                         for p in plattformen if ueber_buffer(p)}
    zustand["vorrat_log"] = (list(zustand.get("vorrat_log") or []) + [jetzt.isoformat()[:16] + " " + z for z in vorrat_log])[-60:]

    def senden_warte(w, slot):
        auftrag = {"id": w["auftrag_id"], "karte_id": w["karte_id"], "titel": w.get("titel"), "karten": w.get("karte") or {}}
        if not w.get("eigen"):   # nicht von Marvin bearbeitet: Text jetzt nach dem aktuellen Baukasten bauen
            try:
                q = urllib.parse.urlencode({"select": "*", "id": "eq." + w["karte_id"]})
                frisch = (sb.holen("karten", q) or [None])[0]
                if frisch:
                    kk = {**frisch, **({"titel": w["titel"]} if w.get("titel_gesetzt") else {})}
                    w["teile"] = texte_bauen(kk, "youtube", trends=trends, titel_gesetzt=bool(w.get("titel_gesetzt")))
                    w["meta"] = metadata_bauen(kk, "youtube", teile=w["teile"])
            except Exception as fehler:   # dann eben der gespeicherte Text
                print(f"YouTube-Text nicht neu gebaut: {fehler}")
        text = texte_text(w["teile"], "youtube")
        e = eintrag(auftrag, "youtube", slot, text, w.get("meta"))
        e.update(teile=w["teile"], auswahl=f"beste {r['youtube_pro_tag']} des Tages, Wert {w['wert']:.0f}")
        return senden(e, "youtube", slot, text, w.get("meta"), w["teile"], w.get("bucket"), w.get("pfad"), {})

    probe_warte = zustand.pop("_probe_warte", [])
    if probe_warte:   # Probelauf: Warteschlange nur ansehen, nicht speichern
        echt = zustand.get("youtube_warte", [])
        zustand["youtube_warte"] = echt + probe_warte
        youtube_warte_planen(zustand, r, jetzt, senden_warte, ist_live, probe)
        zustand["youtube_warte_probe"] = [w for w in zustand["youtube_warte"] if w in probe_warte]
        zustand["youtube_warte"] = [w for w in zustand["youtube_warte"] if w not in probe_warte]
    else:
        youtube_warte_planen(zustand, r, jetzt, senden_warte, ist_live, probe)

    try:
        texte_korrigieren(sb, zustand, buf, org_id, kanaele, ist_live, trends, jetzt, yt)
    except Exception as fehler:   # Korrektur darf das normale Posten nie blockieren
        print(f"Korrektur nicht gelaufen: {fehler}")
    try:
        vorschlaege_schreiben(sb, trends, plattformen)
    except Exception as fehler:
        print(f"Vorschläge nicht geschrieben: {fehler}")

    zustand["probelauf"] = probe
    zustand["gesperrt"] = gesperrt
    zustand["plattformen"] = plattformen
    zustand["modus"] = "live" if ist_live else "probelauf"
    zustand["letzter_lauf"] = dt.datetime.now(dt.timezone.utc).isoformat()
    zustand["geplant"] = zustand["geplant"][-600:]
    zustand["erledigte_auftraege"] = zustand["erledigte_auftraege"][-1000:]
    sb.info_schreiben("posting", zustand)
    print(f"{len(offen)} neue Freigaben ({len(gesperrt)} gesperrt), Plattformen {', '.join(plattformen)}, Modus {zustand['modus']}.")


if __name__ == "__main__":
    main()
