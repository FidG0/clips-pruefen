"""Freigaben von der Handyseite -> Buffer: TikTok, YouTube Shorts, Instagram Reels (Clipwreck).

Läuft alle 15 Min in GitHub Actions. Slots 16:00 / 19:00 / 20:30 (Berlin), je Plattform max. 3 pro Tag.
Ohne BUFFER_LIVE=ja nur Probelauf: info.posting.probelauf zeigt, was geplant würde, nichts geht an Buffer.

Freigabe = Auftrag mit aktion 'behalten' und Kommentar, der mit '[posten]' beginnt
(so bleibt die bestehende PC-Verarbeitung von 'behalten' unverändert gültig).
"""
import datetime as dt
import urllib.parse
from zoneinfo import ZoneInfo

from lib import (CONFIG, Buffer, Supabase, caption_bauen, format_von, live, metadata_bauen,
                 naechster_slot, sperrgrund, titel_typ)

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
    kanaele = {}
    if offen and ist_live:
        buf = Buffer()
        _, kanaele = buf.kanaele(CONFIG["buffer_kanal_name"])

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
        bucket, pfad = videodatei(k)
        link = None
        for p in plattformen:
            slot = naechster_slot(belegt[p])
            text, meta = caption_bauen(k, plattform=p), metadata_bauen(k, p)
            e = eintrag(a, p, slot, text, meta)
            if not pfad:
                e.update(status="fehler", fehler="Keine Videodatei in Supabase (datei/vorschau fehlt).")
            elif ist_live:
                try:
                    if p not in kanaele:
                        raise RuntimeError(f"Kein {p}-Kanal in Buffer verbunden.")
                    if link is None:
                        letzter = max(naechster_slot(belegt[x]) for x in plattformen)
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
