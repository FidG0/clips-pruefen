# Vault-Notizen: Handyseite Ausbau (2026-11-24)

Für Business-Vault, Ordner 50 Tools, beim nächsten PC-Start eintragen.

## Entscheidung 2026-11-24
Handyseite ohne PC ausgebaut (PR auf Basis von PR #1). Grund: Marvin will vom Handy aus prüfen und entscheiden, ohne dass der PC an sein muss. Kosten 0 €.

## Neu
- Reiter **Clips**: eine Liste aller Karten, Formatfilter (GTA, Best of, IRL, Top-5, erkannt über `format`, sonst `art`/`titel`), Statusfilter, Suche, Seitenweise laden (30), Video erst beim Tippen.
- Reiter **Prüfen** ist jetzt eine Queue: eine Karte nach der anderen. Wischen rechts = behalten, links = verwerfen, hoch = überarbeiten. Tasten J / K / R / P, Leertaste, N für Notiz. Später-Knopf, Rückgängig 6 s.
- Gründe-Chips (Ton asynchron, zu lang, langweilig …) plus Notiz. Gespeichert im Feld `kommentar` als `[gründe: ton, lang] Text`, ohne neue Spalte. Für das Selbstlernen: PC parst das Präfix, zählt Gründe pro Format/Creator und passt Regelwerk an.
- **Ampel** Ton / Länge / Text. Länge rechnet die Seite selbst (10 bis 35 s, Ampel gelb 8 bis 40 s). Ton und Text kommen aus `karten.checks`.
- 480p: Seite nimmt `vorschau_480`, sonst `vorschau`. Nur die aktuelle Karte lädt ihr Video, nächste Signatur wird vorgeholt.
- Download-Knopf: signierter Link aus Bucket `fertig` (Feld `datei`), sonst Fallback auf Vorschau.

## Für die PC-Pipeline (offen)
1. `docs/supabase-migration.sql` im Supabase-SQL-Editor ausführen (Felder, Bucket `fertig`, Policies).
2. Pipeline füllt pro Karte: `dauer`, `format`, `vorschau_480` (ffmpeg 480p, ca. 1 MB pro Clip), `checks` (Ton-Sync-Versatz in ms aus Audio-Peak gegen Bildereignis, Untertitel-Check), optional `datei` (Original hochladen, Free-Plan hat nur 1 GB).
3. `vault_sync.py` um das Parsen von `[gründe: …]` erweitern.
4. Alte 720p-Vorschauen nach dem Umstellen löschen, spart Speicher.

## Ehrlich
- Die Seite ist nur mit Stub-Daten im Browser getestet, nicht gegen die echte Supabase (kein Zugriff ohne Secret).
- Rückgängig braucht die Delete-Policy aus der Migration, sonst zeigt die Seite einen Hinweis.
- Echte Ton-Sync-Messung geht nur am PC. Im Browser wäre das unzuverlässig.
