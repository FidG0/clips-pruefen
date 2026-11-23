"""Freigaben von der Handyseite -> Buffer (TikTok Clipwreck), Slots 16:00 / 19:00 / 20:30.

Läuft alle 15 Min in GitHub Actions. Ohne BUFFER_LIVE=ja nur Probelauf:
Es wird berechnet und in info.posting.probelauf gezeigt, was geplant würde, aber nichts an Buffer gesendet.

Freigabe = Auftrag mit aktion 'behalten' und Kommentar, der mit '[posten]' beginnt
(so bleibt die bestehende PC-Verarbeitung von 'behalten' unverändert gültig).
"""
import datetime as dt
import urllib.parse
from zoneinfo import ZoneInfo

from lib import CONFIG, Buffer, Supabase, caption_bauen, live, naechster_slot, titel_typ

STANDARD = {"erledigte_auftraege": [], "geplant": [], "probelauf": [], "letzter_lauf": None}


def offene_freigaben(sb, erledigt):
    q = urllib.parse.urlencode({
        "select": "id,karte_id,kommentar,titel,erstellt,karten(*)",
        "aktion": "eq.behalten",
        "kommentar": f"like.{CONFIG['marker']}*",
        "order": "id.asc",
    })
    return [a for a in sb.holen("auftraege", q) if a["id"] not in erledigt]


def eintrag(auftrag, slot_utc, text):
    k = auftrag.get("karten") or {}
    titel = auftrag.get("titel") or k.get("titel")
    tz = slot_utc.astimezone(ZoneInfo(CONFIG["zeitzone"]))
    return {
        "auftrag_id": auftrag["id"], "karte_id": auftrag["karte_id"], "titel": titel,
        "art": k.get("art"), "kanal": k.get("kanal"), "dauer": k.get("dauer") or k.get("laenge"),
        "titel_typ": titel_typ(titel), "slot_utc": slot_utc.isoformat(), "slot": tz.strftime("%H:%M"),
        "slot_lokal": tz.strftime("%a %d.%m. %H:%M"), "text": text, "status": "geplant", "buffer_id": None,
    }


def main():
    sb = Supabase()
    zustand = {**STANDARD, **(sb.info_lesen("posting", {}) or {})}
    erledigt = set(zustand["erledigte_auftraege"])
    offen = offene_freigaben(sb, erledigt)
    ist_live = live()
    buf = kanal = None
    if offen and ist_live:
        buf = Buffer()
        _, kanal = buf.kanal(CONFIG["buffer_kanal_name"])

    belegt = {g["slot_utc"] for g in zustand["geplant"] if g["status"] in ("geplant", "gesendet")}
    probe = []
    for a in offen:
        k = a.get("karten") or {}
        pfad = k.get("datei_fertig") or k.get("vorschau")
        text = caption_bauen({**k, "titel": a.get("titel") or k.get("titel")})
        slot = naechster_slot(belegt)
        e = eintrag(a, slot, text)
        if not pfad:
            e.update(status="fehler", fehler="Keine Videodatei in Supabase (vorschau/datei_fertig fehlt).")
        elif ist_live:
            try:
                gueltig = (slot - dt.datetime.now(dt.timezone.utc)).total_seconds() + CONFIG["link_gueltig_tage"] * 86400
                link = sb.signierter_link(CONFIG["storage_bucket"], pfad, gueltig)
                post = buf.post_planen(kanal["id"], text, link, slot)
                e["buffer_id"] = post["id"]
            except Exception as fehler:  # einzelner Fehler soll die anderen Freigaben nicht blockieren
                e.update(status="fehler", fehler=str(fehler)[:300])
        if ist_live:
            zustand["geplant"].append(e)
            zustand["erledigte_auftraege"].append(a["id"])
        else:
            e["status"] = "probelauf"
            probe.append(e)
        if e["status"] in ("geplant", "probelauf"):
            belegt.add(e["slot_utc"])
        print(f"{e['status']:9} {e['slot_lokal']}  {e['titel']}")

    zustand["probelauf"] = probe
    zustand["modus"] = "live" if ist_live else "probelauf"
    zustand["letzter_lauf"] = dt.datetime.now(dt.timezone.utc).isoformat()
    zustand["geplant"] = zustand["geplant"][-300:]
    zustand["erledigte_auftraege"] = zustand["erledigte_auftraege"][-1000:]
    sb.info_schreiben("posting", zustand)
    print(f"{len(offen)} neue Freigaben, Modus {zustand['modus']}.")


if __name__ == "__main__":
    main()
