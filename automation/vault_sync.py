"""PC: Zahlen und Regelwerk-Änderungen aus Supabase in den Obsidian-Vault schreiben.

Schreibt:
  Business-Vault/TikTok-Analyse/Auswertung.md          aktueller Stand, Vorschläge, Top/Flop, Regelwerk auto
  Business-Vault/TikTok-Analyse/Tage/JJJJ-MM-TT.md      Tages-Schnappschuss aller Posts (Tag 1..10)
  Business-Vault/TikTok-Analyse/Entscheidungen.md       jede automatische Regelwerk-Änderung mit Datum + Grund (nur anhängen)
  Clips-Pipeline/analyse/regelwerk_auto.json            für die Pipeline (Gewichte, Längenziel)

Aufruf: python vault_sync.py   (z. B. täglich per Aufgabenplanung oder beim PC-Start)
"""
import datetime as dt
import json
import os
from pathlib import Path

import lib

VAULT = Path(os.environ.get("VAULT", r"C:\Users\MalcherMarvin\Documents\Business-Vault"))
PIPELINE = Path(os.environ.get("PIPELINE", r"C:\Users\MalcherMarvin\Documents\Clips-Pipeline"))


def zahl(x, nachkomma=0):
    if x is None:
        return "–"
    return f"{x:,.{nachkomma}f}".replace(",", ".")


def auswertung_md(a, posting):
    z = [f"# TikTok-Auswertung Clipwreck", "", f"Stand: {a.get('stand', '')[:16].replace('T', ' ')} UTC · "
         f"{a.get('anzahl_posts', 0)} Posts · Median Views (Tag 3): {zahl(a.get('median_views_d3'))}",
         f"Posting-Modus: **{posting.get('modus', 'unbekannt')}** · geplant: "
         f"{sum(1 for g in posting.get('geplant', []) if g['status'] == 'geplant')}", "", "## Vorschläge"]
    z += [f"- {t}" for t in a.get("vorschlaege", [])] or ["- Noch zu wenig Daten."]
    z += ["", "## Top 5"] + [f"- {t['titel']} ({t.get('art')}, {t.get('slot')}): {zahl(t['views_d3'])} Views" for t in a.get("top", [])]
    z += ["", "## Gruppen (Median Views Tag 3)"]
    for dim, g in a.get("gruppen", {}).items():
        z.append(f"### {dim}")
        z += ["| Wert | n | Views | Haltezeit | Engagement |", "|---|---|---|---|---|"]
        for w, x in sorted(g.items(), key=lambda i: -i[1]["median_views"]):
            eng = x.get("median_engagement")
            z.append(f"| {w} | {x['n']} | {zahl(x['median_views'])} | {zahl(x.get('median_haltezeit'), 1)} | "
                     f"{zahl(eng * 100, 1) + ' %' if eng is not None else '–'} |")
    z += ["", "## Regelwerk automatisch", "```json", json.dumps(a.get("regelwerk_auto", {}), ensure_ascii=False, indent=2), "```",
          "", "Grenzen: Gewichte 0,3 bis 3,0, max. ±0,2 pro Tag, erst ab 4 Posts pro Gruppe. Länge bleibt im Regelwerk-Rahmen 10-35 s.",
          "", "Verlauf der Änderungen: [[Entscheidungen]]"]
    return "\n".join(z) + "\n"


def tag_md(metriken, heute):
    z = [f"# TikTok-Zahlen {heute}", "", "| Titel | Format | Slot | Tag | Views | Likes | Shares | Follows | Haltezeit |", "|---|---|---|---|---|---|---|---|---|"]
    for e in metriken.values():
        v = e.get("verlauf", {})
        if not v:
            continue
        t = max(v, key=int)
        x, m, c = v[t], e.get("meta", {}), e.get("csv", {})
        z.append(f"| {m.get('titel', '')[:50]} | {m.get('art') or ''} | {m.get('slot') or ''} | {t} | {zahl(x.get('views'))} | "
                 f"{zahl(x.get('likes'))} | {zahl(x.get('shares'))} | {zahl(x.get('follows'))} | {zahl(c.get('avg_watch_s'), 1)} |")
    return "\n".join(z) + "\n"


def main():
    sb = lib.Supabase()
    a = sb.info_lesen("auswertung", {}) or {}
    posting = sb.info_lesen("posting", {}) or {}
    metriken = sb.info_lesen("metriken", {}) or {}
    ziel = VAULT / "TikTok-Analyse"
    (ziel / "Tage").mkdir(parents=True, exist_ok=True)
    heute = dt.date.today().isoformat()
    (ziel / "Auswertung.md").write_text(auswertung_md(a, posting), encoding="utf-8")
    (ziel / "Tage" / f"{heute}.md").write_text(tag_md(metriken, heute), encoding="utf-8")

    log = ziel / "Entscheidungen.md"
    bekannt = log.read_text(encoding="utf-8") if log.exists() else "# Automatische Regelwerk-Entscheidungen\n\n"
    neu = [f"- {x['datum']}: {x['was']} von {x['von']} auf {x['auf']}. Grund: {x['grund']}" for x in a.get("aenderungen", [])]
    neu = [n for n in neu if n not in bekannt]
    if neu:
        log.write_text(bekannt.rstrip("\n") + "\n" + "\n".join(neu) + "\n", encoding="utf-8")

    (PIPELINE / "analyse").mkdir(parents=True, exist_ok=True)
    (PIPELINE / "analyse" / "regelwerk_auto.json").write_text(
        json.dumps({"stand": a.get("stand"), **a.get("regelwerk_auto", {})}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Vault aktualisiert: {ziel} ({len(neu)} neue Entscheidungen)")


if __name__ == "__main__":
    main()
