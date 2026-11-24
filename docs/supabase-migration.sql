-- Optionale Felder für die neue Handyseite. Die Seite läuft auch ohne sie (Felder fehlen = Ampel grau, Vorschau normal).
-- Im Supabase SQL-Editor einmal ausführen. Kostet nichts.
alter table karten add column if not exists vorschau_480 text;   -- Pfad im Bucket "vorschau", 480p-Fassung
alter table karten add column if not exists datei text;          -- Pfad im Bucket "fertig", Original zum Download
alter table karten add column if not exists dauer numeric;       -- Länge in Sekunden
alter table karten add column if not exists format text;         -- GTA | Best of | IRL | Top-5 (sonst wird aus art/titel erraten)
alter table karten add column if not exists checks jsonb;        -- {"ton_sync":{"status":"gruen","ms":12},"laenge":{"status":"gruen","s":24},"untertitel":{"status":"gelb","hinweis":"..."}}

-- Privater Bucket für fertige Originale (Free-Plan hat 1 GB Speicher gesamt, nur gezielt hochladen).
insert into storage.buckets (id, name, public) values ('fertig', 'fertig', false) on conflict do nothing;
-- Lesen nur für angemeldete Nutzer, wie beim Bucket "vorschau" (Policy dort kopieren):
create policy "fertig lesen" on storage.objects for select to authenticated using (bucket_id = 'fertig');

-- Rückgängig-Knopf: eigenes Löschen unerledigter Aufträge erlauben.
create policy "auftraege eigene offene loeschen" on auftraege for delete to authenticated using (erledigt is null);
