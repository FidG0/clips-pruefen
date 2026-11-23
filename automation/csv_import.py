"""CSV-Fallback (PC): TikTok-Studio-Export einlesen, wo die API nichts liefert (Haltezeit, ganz angesehen, neue Follower).

Ablauf: Export aus TikTok Studio (Web: tiktok.com/tiktokstudio > Analysen > Daten herunterladen) als CSV
in den Ordner  Clips-Pipeline\\analyse\\csv_eingang  legen. Dieses Skript liest jede neue Datei,
ordnet Zeilen den Posts zu (Titel-Anfang oder Veröffentlichungszeit +-90 Min) und schreibt sie nach info.metriken[...].csv.
Kontozahlen pro Tag (ohne Videobezug) landen in info.konto_taeglich.
Kein Browser-Bot: den Export lädt ein Mensch herunter, das Skript liest nur die Datei.

Aufruf:  python csv_import.py [ordner]
"""
import csv
import datetime as dt
import os
import re
import sys
from pathlib import Path

import lib

SPALTEN = {
    "titel": ["video title", "titel", "title", "beschreibung", "description", "caption"],
    "zeit": ["post time", "posted", "veröffentlicht", "datum", "date", "time"],
    "views": ["video views", "views", "aufrufe", "wiedergaben", "total views"],
    "avg_watch_s": ["average watch time", "durchschnittliche wiedergabezeit", "avg. watch time", "average time watched"],
    "full_watch_pct": ["watched full video", "vollständig angesehen", "ganz angesehen", "completion"],
    "new_followers": ["new followers", "neue follower", "followers gained"],
    "likes": ["likes", "total likes", "gefällt"],
    "comments": ["comments", "kommentare"],
    "shares": ["shares", "geteilt"],
}


def spalte_finden(kopf, art):
    k = [h.lower().strip() for h in kopf]
    for name in SPALTEN[art]:
        for i, h in enumerate(k):
            if name in h:
                return i
    return None


def zahl(text):
    if text is None:
        return None
    t = str(text).strip().replace("\u00a0", "")
    if not t:
        return None
    m = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", t)  # 0:12 oder 0:00:12
    if m:
        teile = [int(x) for x in m.groups() if x is not None]
        return float(sum(v * 60 ** (len(teile) - i - 1) for i, v in enumerate(teile)))
    t = t.replace("%", "").replace("s", "")
    mult = 1
    if t[-1:].lower() == "k":
        mult, t = 1000, t[:-1]
    elif t[-1:].lower() == "m":
        mult, t = 1_000_000, t[:-1]
    t = t.replace(".", "").replace(",", ".") if re.search(r",\d{1,2}$", t) else t.replace(",", "")
    try:
        return float(t) * mult
    except ValueError:
        return None


def zeit_lesen(text):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M", "%Y-%m-%d", "%d.%m.%Y", "%B %d"):
        try:
            return dt.datetime.strptime(str(text).strip(), f)
        except ValueError:
            pass
    return None


def zuordnen(zeile, metriken):
    titel = (zeile.get("titel") or "").lower()[:30]
    zeit = zeile.get("zeit")
    for pid, e in metriken.items():
        m = e.get("meta", {})
        if titel and m.get("titel") and m["titel"].lower()[:30] in titel or titel and titel in (m.get("titel") or "").lower():
            return pid
        if zeit and m.get("gesendet") and zeit.hour:
            gesendet = dt.datetime.fromisoformat(m["gesendet"].replace("Z", "+00:00")).astimezone(lib.ZoneInfo(lib.CONFIG["zeitzone"])).replace(tzinfo=None)
            if abs((gesendet - zeit).total_seconds()) <= 90 * 60:
                return pid
    return None


def datei_lesen(pfad):
    with open(pfad, encoding="utf-8-sig", newline="") as f:
        probe = f.read(4096)
        f.seek(0)
        trenner = ";" if probe.count(";") > probe.count(",") else ","
        zeilen = list(csv.reader(f, delimiter=trenner))
    if not zeilen:
        return []
    kopf, daten = zeilen[0], zeilen[1:]
    idx = {art: spalte_finden(kopf, art) for art in SPALTEN}
    aus = []
    for r in daten:
        z = {}
        for art, i in idx.items():
            if i is None or i >= len(r):
                continue
            z[art] = r[i] if art == "titel" else zeit_lesen(r[i]) if art == "zeit" else zahl(r[i])
        if any(v is not None for k, v in z.items() if k not in ("titel", "zeit")):
            aus.append(z)
    return aus


def main():
    ordner = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CSV_EINGANG", r"C:\Users\MalcherMarvin\Documents\Clips-Pipeline\analyse\csv_eingang"))
    ordner.mkdir(parents=True, exist_ok=True)
    fertig = ordner / "eingelesen"
    fertig.mkdir(exist_ok=True)
    sb = lib.Supabase()
    metriken = sb.info_lesen("metriken", {}) or {}
    konto = sb.info_lesen("konto_taeglich", {}) or {}
    zugeordnet = offen = 0
    for pfad in sorted(ordner.glob("*.csv")):
        for z in datei_lesen(pfad):
            pid = zuordnen(z, metriken) if z.get("titel") or (z.get("zeit") and z["zeit"].hour) else None
            werte = {k: v for k, v in z.items() if k not in ("titel", "zeit") and v is not None}
            if pid:
                metriken[pid].setdefault("csv", {}).update(werte, quelle=pfad.name)
                zugeordnet += 1
            elif z.get("zeit") and not z.get("titel"):
                konto[z["zeit"].date().isoformat()] = werte
            else:
                offen += 1
        pfad.rename(fertig / pfad.name)
    sb.info_schreiben("metriken", metriken)
    sb.info_schreiben("konto_taeglich", konto)
    print(f"CSV: {zugeordnet} Videozeilen zugeordnet, {offen} nicht zuordenbar, {len(konto)} Kontotage.")


if __name__ == "__main__":
    main()
