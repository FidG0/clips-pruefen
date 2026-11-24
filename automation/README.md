# Clipwreck: Posting und Analyse automatisch (TikTok, YouTube Shorts, Instagram Reels)

Ablauf:
1. Handyseite: Knopf **Posten (nächster Slot)** auf einer Karte. Gespeichert als Auftrag `behalten` mit Kommentar `[posten] …`, der PC verarbeitet ihn also wie bisher als Behalten.
2. GitHub Actions `posten.yml` (alle 15 Min, läuft ohne PC): plant den Clip über die Buffer-API auf allen aktiven Plattformen (`plattformen` in `config.json`) in den nächsten freien Slot 16:00 / 19:00 / 20:30 Uhr (Berlin), max. 3 pro Tag, mit Caption + Hashtags (`config.json`). Video geht als signierter Supabase-Link (14 Tage gültig) an Buffer.
3. GitHub Actions `zahlen.yml` (täglich 06:15): holt Zahlen von Buffer, verfolgt jeden Post 10 Tage, wertet nach Format, Länge, Titeltyp, Uhrzeit, Creator aus und passt `regelwerk_auto` an (Gewichte 0,3-3,0, max. ±0,2 pro Tag, erst ab 4 Posts pro Gruppe).
4. PC: `vault_sync.py` schreibt alles in `Business-Vault/TikTok-Analyse/` (Auswertung, Tageswerte, Entscheidungen mit Datum + Grund) und `Clips-Pipeline/analyse/regelwerk_auto.json`.
5. Fallback: `csv_import.py` liest TikTok-Studio-Exporte aus `Clips-Pipeline/analyse/csv_eingang/` (Haltezeit, ganz angesehen, neue Follower).

Status jederzeit im Reiter **Posting** der Handyseite.

## Sicherheit
- Ohne Secret `BUFFER_LIVE` = `ja` läuft nur ein **Probelauf**: Reiter Posting zeigt, was geplant würde, nichts geht raus.
- Keine Browser-Bots, keine Cookies, nur offizielle Buffer-API (Buffer hat die TikTok-Freigabe).
- Nur der Kanal mit Namen `buffer_kanal_name` (Clipwreck) wird benutzt. Kampagnen-Konto nie im selben Buffer-Konto verbinden.

## Einrichten (einmalig, erst nach Marvins Ja)
1. Buffer-Konto (Free, 0 €) über den Erziehungsberechtigten, TikTok **Clipwreck** als Business-Konto verbinden.
2. Buffer > Settings > API: persönlichen Key erzeugen.
3. GitHub > clips-pruefen > Settings > Secrets > Actions: `SUPABASE_SECRET`, `BUFFER_API_KEY`, später `BUFFER_LIVE` = `ja`.
4. PC: Aufgabenplanung täglich `python vault_sync.py` (und `csv_import.py`).

## Tests
`cd automation && python -m unittest -v test_automation.py`

## Plattformen (Stand 24.11.2026)
- TikTok `clipwreckofc`: normales Konto, kein Business nötig. Text ohne Zeilenumbrüche, max. 6 Hashtags.
- YouTube `Clipwreck`: Short, Titel = Clip-Titel + `#shorts` (max. 100 Zeichen), Kategorie 20 Gaming bei GTA, sonst 24 Unterhaltung, öffentlich, nicht für Kinder.
- Instagram `clipwreckofc`: Creator-Konto (ohne Facebook), Reel mit Feed-Anzeige, max. 5 Hashtags.
- Auswertung vergleicht über einen Score (Views / Median der Plattform), damit große TikTok-Zahlen YouTube nicht überdecken.

## Sperren vor dem Posten (lib.sperrgrund)
Eine "Posten"-Freigabe wird nur geplant, wenn alles drei stimmt, sonst steht sie unter info.posting.gesperrt und wird alle 15 Min neu geprüft:
1. Kein Glücksspiel (Wortliste `sperren.gluecksspiel_woerter` in config.json oder `checks.gluecksspiel = true`).
2. Neu gerendertes Original vorhanden (`karten.datei` im Fach `fertig`). Alte Charge ohne Original bleibt gesperrt.
3. Ton-Sync-Ampel grün (`karten.checks.ton_sync.status == "gruen"`). Gelb, rot oder ungeprüft bleibt gesperrt.
Die Handyseite zeigt den Grund direkt am Posten-Knopf. Slots 16:00/19:00/20:30, also höchstens 3 Posts pro Tag und Plattform.
