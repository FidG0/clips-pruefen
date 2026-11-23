# Clipwreck: Posting und Analyse automatisch

Ablauf:
1. Handyseite: Knopf **Posten (nächster Slot)** auf einer Karte. Gespeichert als Auftrag `behalten` mit Kommentar `[posten] …`, der PC verarbeitet ihn also wie bisher als Behalten.
2. GitHub Actions `posten.yml` (alle 15 Min, läuft ohne PC): plant den Clip über die Buffer-API in den nächsten freien Slot 16:00 / 19:00 / 20:30 Uhr (Berlin), max. 3 pro Tag, mit Caption + Hashtags (`config.json`). Video geht als signierter Supabase-Link (14 Tage gültig) an Buffer.
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
