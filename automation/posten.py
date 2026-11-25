"""Freigaben von der Handyseite -> Buffer: TikTok, YouTube Shorts, Instagram Reels (Clipwreck).

Läuft alle 15 Min in GitHub Actions. Slots aus config.json (13:00/16:00/19:00/20:30 plus Bonus 22:00, Berlin),
je Plattform max. 5 pro Tag. Den Bonus-Slot gibt es nur auf ausdrücklichen Wunsch, „nächster Slot“ nimmt ihn nie.
Buffer Free: max. 10 geplante Posts je Kanal, darüber bleibt die Freigabe offen.
Ohne BUFFER_LIVE=ja nur Probelauf: info.posting.probelauf zeigt, was geplant würde, nichts geht an Buffer.

Freigabe = Auftrag mit aktion 'behalten' und Kommentar, der mit '[posten]' beginnt
(so bleibt die bestehende PC-Verarbeitung von 'behalten' unverändert gültig).
"""
import datetime as dt
import re
import urllib.parse
from zoneinfo import ZoneInfo

from lib import (CONFIG, Buffer, Supabase, buffer_voll, ist_bonus, sofort_slot, sofort_warnungen, format_von, live, metadata_bauen,
                 naechster_slot, slot_pruefen, sperrgrund, texte_aus_kommentar, texte_bauen, texte_pruefen, texte_text, titel_typ,
                 wunsch_slots)

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
    """Marvins Text aus der Vorschau (texte: im Kommentar) oder der Vorschlag. Dazu der Sperrgrund oder None."""
    teile = texte_aus_kommentar(auftrag.get("kommentar")).get(plattform) or \
        texte_bauen(karte, plattform, trends=trends, titel_gesetzt=bool(auftrag.get("titel")))
    return teile, texte_pruefen(teile, plattform)


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


def texte_korrigieren(sb, zustand, buf, org_id, kanaele, ist_live, trends, jetzt):
    """25.11. (Marvins Ja nötig): geplante Posts mit dem alten Block-Text in Buffer per editPost auf saubere Texte setzen.
    Läuft nur, wenn info.texte_korrektur.freigabe true ist. Vorher wird Buffer gefragt, welche Posts schon gesendet sind:
    die bleiben unberührt und werden nur gemeldet. Ohne Freigabe oder im Probelauf: nur Vorschau."""
    auftrag = sb.info_lesen("texte_korrektur", {}) or {}
    kandidaten = []
    for g in zustand["geplant"]:
        if g.get("status") != "geplant" or not g.get("buffer_id") or not alter_text(g):
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
        zeile = {"buffer_id": g["buffer_id"], "plattform": pf, "slot_lokal": g.get("slot_lokal"), "titel": teile.get("titel"),
                 "alt": g.get("text"), "neu": neu, "meta": meta}
        if g["buffer_id"] in gesendet:
            zeile["status"] = "schon veröffentlicht, nicht angefasst"
            g["status"] = "gesendet"
        elif slot <= jetzt + dt.timedelta(minutes=3):
            zeile["status"] = "zu knapp vor dem Senden, nicht angefasst"
        elif sperre:
            zeile["status"] = "gesperrt: " + sperre
        elif darf:
            try:
                buf.post_bearbeiten(g["buffer_id"], neu, meta)
                g.update(text=neu, metadata=meta, teile=teile, titel=teile.get("titel"), hinweis="Text am 25.11. korrigiert.")
                zeile["status"] = "korrigiert"
            except Exception as fehler:   # bleibt alt, nächster Lauf versucht es nochmal
                zeile["status"] = "Fehler: " + str(fehler)[:200]
        else:
            zeile["status"] = "Vorschau (wartet auf Freigabe)"
        (ergebnis if darf else vorschau).append(zeile)
        print(f"korrektur {pf:9} {zeile['status']}  {g.get('slot_lokal')}  {teile.get('titel')}")
    if darf:
        fertig = all(z["status"] != "Vorschau (wartet auf Freigabe)" and not z["status"].startswith("Fehler") for z in ergebnis)
        sb.info_schreiben("texte_korrektur", {**auftrag, "freigabe": not fertig, "ergebnis": ergebnis,
                                              "gelaufen": jetzt.isoformat()})
    else:
        sb.info_schreiben("texte_korrektur", {**auftrag, "vorschau": vorschau, "geprueft": jetzt.isoformat()})


def videodatei(karte):
    """(bucket, pfad): Original aus 'fertig' bevorzugt, sonst Vorschau."""
    if karte.get("datei"):
        return CONFIG.get("storage_bucket_fertig", "fertig"), karte["datei"]
    if karte.get("vorschau"):
        return CONFIG["storage_bucket"], karte["vorschau"]
    return None, None


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
    zustand = {**STANDARD, **(sb.info_lesen("posting", {}) or {})}
    erledigt = set(zustand["erledigte_auftraege"])
    offen = offene_freigaben(sb, erledigt)
    ist_live = live()
    plattformen = aktive_plattformen()
    kanaele, org_id, buf = {}, None, None
    jetzt = dt.datetime.now(dt.timezone.utc)
    trends = sb.info_lesen("trends", None)   # vom PC (cloud_sync aus lernen/trends.json), fehlt er: nur config-Hashtags
    bstand = zustand.get("buffer") or {}
    try:
        alter = (jetzt - dt.datetime.fromisoformat(bstand["geprueft"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        alter = 1e9
    # Verbindungs-Status für die Kanäle-Seite der Zentrale: bei jeder Freigabe, sonst höchstens stündlich
    korrektur = (sb.info_lesen("texte_korrektur", {}) or {}).get("freigabe")
    if ist_live and (offen or alter > 3600 or korrektur):
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

    belegt = {p: belegte_slots(zustand["geplant"], p) for p in plattformen}
    probe, gesperrt = [], []
    for a in offen:
        k = {**(a.get("karten") or {})}
        if a.get("titel"):
            k["titel"] = a["titel"]
        grund = sperrgrund(k)
        if grund:  # bleibt offen und wird jeden Lauf neu geprüft (z. B. sobald die Ton-Ampel grün ist)
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"), "grund": grund})
            print(f"gesperrt  {k.get('titel')}: {grund}")
            continue
        texte = {p: texte_fuer(a, k, p, trends) for p in plattformen}
        sperre_text = next((f"{p}: {g}" for p, (_, g) in texte.items() if g), None)
        if sperre_text:   # z. B. Glücksspiel-Wort in Marvins geändertem Text: bleibt offen, wird nie so gepostet
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"), "grund": "Text " + sperre_text})
            print(f"gesperrt  {k.get('titel')}: Text {sperre_text}")
            continue
        bucket, pfad = videodatei(k)
        link = None
        wunsch = wunsch_slots(a.get("kommentar"))   # Kalender: nur gewählte Plattformen, je im gewählten Slot
        ziel = [p for p in plattformen if p in wunsch] if wunsch else plattformen
        voll = [g for g in (buffer_voll(zustand["geplant"], p) for p in ziel) if g]
        if voll:   # Buffer Free: max. 10 geplante je Kanal. Freigabe bleibt offen, nächster Lauf versucht es wieder.
            gesperrt.append({"auftrag_id": a["id"], "karte_id": a["karte_id"], "titel": k.get("titel"), "grund": voll[0]})
            print(f"wartet   {k.get('titel')}: {voll[0]}")
            continue
        slots, hinweise = {}, {}
        for p in ziel:
            if wunsch.get(p) == "sofort":   # Bonus jetzt: geht immer in wenigen Minuten raus (25.11. abends, Marvin)
                warn = sofort_warnungen(zustand["geplant"] + probe, p)
                slots[p] = sofort_slot(zustand["geplant"] + probe, p)[0]
                if warn:
                    hinweise[p] = "Bonus trotz Hinweis gepostet: " + " ".join(warn)
                continue
            grund_slot = slot_pruefen(wunsch[p], belegt[p]) if p in wunsch else None
            slots[p] = wunsch[p] if p in wunsch and grund_slot is None else naechster_slot(belegt[p])
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
            if not pfad:
                e.update(status="fehler", fehler="Keine Videodatei in Supabase (datei/vorschau fehlt).")
            elif ist_live:
                try:
                    if p not in kanaele:
                        raise RuntimeError(f"Kein {p}-Kanal in Buffer verbunden.")
                    if link is None:
                        letzter = max(slots.values())
                        gueltig = (letzter - dt.datetime.now(dt.timezone.utc)).total_seconds() + CONFIG["link_gueltig_tage"] * 86400
                        link = sb.signierter_link(bucket, pfad, gueltig)
                    e["buffer_id"] = buf.post_planen(kanaele[p]["id"], text, link, slot, meta)["id"]
                except Exception as fehler:  # ein Fehler blockiert nicht die anderen Plattformen/Freigaben
                    e.update(status="fehler", fehler=str(fehler)[:300])
            if ist_live:
                zustand["geplant"].append(e)
            else:
                e["status"] = "probelauf"
                probe.append(e)
            if e["status"] in ("geplant", "probelauf"):
                belegt[p].add(e["slot_utc"])
            print(f"{p:9} {e['status']:9} {e['slot_lokal']}  {e['titel']}")
        if ist_live:
            zustand["erledigte_auftraege"].append(a["id"])

    try:
        texte_korrigieren(sb, zustand, buf, org_id, kanaele, ist_live, trends, jetzt)
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
