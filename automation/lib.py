"""Gemeinsame Helfer: Supabase (REST + Storage), Buffer (GraphQL), Slots, Captions.

Nur Standardbibliothek, damit es ohne Installation in GitHub Actions und auf dem PC läuft.
Geheimnisse kommen NUR aus Umgebungsvariablen:
  SUPABASE_URL, SUPABASE_SECRET   (Secret-Key, umgeht Zeilenschutz)
  BUFFER_API_KEY                  (persönlicher Key, Buffer > Settings > API)
  BUFFER_LIVE=ja                  (sonst Probelauf: es wird nichts an Buffer geschickt)
"""
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

HIER = Path(__file__).resolve().parent
CONFIG = json.loads((HIER / "config.json").read_text(encoding="utf-8"))
BUFFER_URL = "https://api.buffer.com"


def env(name, pflicht=True):
    wert = os.environ.get(name, "").strip()
    if pflicht and not wert:
        raise SystemExit(f"{name} fehlt (als Secret/Umgebungsvariable setzen).")
    return wert


def live():
    return os.environ.get("BUFFER_LIVE", "").strip().lower() == "ja"


# ---------------------------------------------------------------- HTTP
def _anfrage(url, methode="GET", daten=None, kopf=None, timeout=60):
    body = None if daten is None else json.dumps(daten).encode()
    req = urllib.request.Request(url, data=body, method=methode, headers={"Content-Type": "application/json", **(kopf or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode() or "null"
            return json.loads(text)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{methode} {url.split('?')[0]} -> HTTP {e.code}: {e.read().decode()[:400]}") from None


# ---------------------------------------------------------------- Supabase
SUPABASE_URL_STANDARD = "https://ycmecdixsdfhcspqeinw.supabase.co"  # öffentlich, steht auch in index.html
SECRET_DATEI_PC = Path(r"C:\Users\MalcherMarvin\Documents\Clips-Pipeline\cloud\supabase_secret.txt")


class Supabase:
    def __init__(self):
        self.url = (env("SUPABASE_URL", pflicht=False) or SUPABASE_URL_STANDARD).rstrip("/")
        self.key = env("SUPABASE_SECRET", pflicht=False)
        if not self.key and SECRET_DATEI_PC.exists():  # auf dem PC: Key liegt nur lokal
            self.key = SECRET_DATEI_PC.read_text(encoding="utf-8").strip()
        if not self.key:
            raise SystemExit("SUPABASE_SECRET fehlt.")
        self.kopf = {"apikey": self.key}

    def holen(self, tabelle, query):
        return _anfrage(f"{self.url}/rest/v1/{tabelle}?{query}", kopf=self.kopf)

    def info_lesen(self, schluessel, standard=None):
        q = urllib.parse.urlencode({"select": "wert", "schluessel": f"eq.{schluessel}"})
        zeilen = self.holen("info", q)
        return zeilen[0]["wert"] if zeilen else standard

    def info_schreiben(self, schluessel, wert):
        _anfrage(f"{self.url}/rest/v1/info?on_conflict=schluessel", "POST", {"schluessel": schluessel, "wert": wert},
                 {**self.kopf, "Prefer": "resolution=merge-duplicates,return=minimal"})

    def signierter_link(self, bucket, pfad, sekunden):
        pfad_q = urllib.parse.quote(pfad)
        r = _anfrage(f"{self.url}/storage/v1/object/sign/{bucket}/{pfad_q}", "POST", {"expiresIn": int(sekunden)}, self.kopf)
        signiert = r.get("signedURL") or r.get("signedUrl")
        if not signiert:
            raise RuntimeError(f"Kein signierter Link für {pfad}: {r}")
        return signiert if signiert.startswith("http") else f"{self.url}/storage/v1{signiert}"


# ---------------------------------------------------------------- Buffer
class Buffer:
    def __init__(self):
        self.key = env("BUFFER_API_KEY")

    def gql(self, query, variablen=None):
        r = _anfrage(BUFFER_URL, "POST", {"query": query, "variables": variablen or {}},
                     {"Authorization": f"Bearer {self.key}"})
        if r.get("errors"):
            raise RuntimeError(f"Buffer-Fehler: {r['errors']}")
        return r["data"]

    def kanaele(self, name):
        """Liefert (organizationId, {plattform: channel}) für alle Kanäle, deren Name `name` enthält."""
        orgs = self.gql("query { account { organizations { id name } } }")["account"]["organizations"]
        for org in orgs:
            # IDs werden direkt eingesetzt, damit wir keine Scalar-Typnamen raten müssen.
            alle = self.gql(
                "query { channels(input: {organizationId: %s}) { id name displayName service } }" % json.dumps(org["id"])
            )["channels"]
            treffer = {k["service"].lower(): k for k in alle
                       if name.lower() in (k["name"] + " " + (k.get("displayName") or "")).lower()}
            if treffer:
                return org["id"], treffer
        raise RuntimeError(f"Keine Buffer-Kanäle mit '{name}' gefunden.")

    def kanal(self, name, service="tiktok"):
        org, k = self.kanaele(name)
        if service not in k:
            raise RuntimeError(f"Kein {service}-Kanal '{name}' in Buffer.")
        return org, k[service]

    def post_planen(self, kanal_id, text, video_url, faellig_utc, metadata=None):
        q = """mutation($i: CreatePostInput!) { createPost(input: $i) {
                 ... on PostActionSuccess { post { id dueAt } }
                 ... on MutationError { message } } }"""
        eingabe = {"channelId": kanal_id, "text": text, "schedulingType": "automatic", "mode": "customScheduled",
                   "dueAt": faellig_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "needsApproval": False,
                   "assets": [{"video": {"url": video_url}}]}
        if metadata:
            eingabe["metadata"] = metadata
        r = self.gql(q, {"i": eingabe})["createPost"]
        if "post" not in r:
            raise RuntimeError(f"Buffer lehnt ab: {r.get('message')}")
        return r["post"]

    def gesendete_posts(self, org_id, kanal_ids, max_seiten=10):
        q = """query($a: String) {
                 posts(first: 50, after: $a, input: {organizationId: %s, filter: {status: [sent], channelIds: %s}}) {
                   edges { node { id text dueAt channelId metrics { type name value unit } metricsUpdatedAt } }
                   pageInfo { endCursor hasNextPage } } }""" % (json.dumps(org_id), json.dumps(list(kanal_ids) if isinstance(kanal_ids, (list, tuple, set)) else [kanal_ids]))
        posts, nach = [], None
        for _ in range(max_seiten):
            d = self.gql(q, {"a": nach})["posts"]
            posts += [e["node"] for e in d["edges"]]
            if not d["pageInfo"]["hasNextPage"]:
                break
            nach = d["pageInfo"]["endCursor"]
        return posts


# ---------------------------------------------------------------- Slots
def naechster_slot(belegt_utc, jetzt_utc=None, cfg=CONFIG):
    """Nächster freier Slot (16:00/19:00/20:30 Berlin), der mind. X Min in der Zukunft liegt.

    belegt_utc: Menge von ISO-Strings (UTC) bereits geplanter Posts.
    """
    tz = ZoneInfo(cfg["zeitzone"])
    jetzt = (jetzt_utc or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    frueh = jetzt + dt.timedelta(minutes=cfg["mindest_vorlauf_minuten"])
    belegt = {dt.datetime.fromisoformat(b.replace("Z", "+00:00")).astimezone(tz) for b in belegt_utc}
    for tag in range(0, 60):
        datum = (jetzt + dt.timedelta(days=tag)).date()
        pro_tag = sum(1 for b in belegt if b.date() == datum)
        for s in cfg["slots"]:
            if pro_tag >= cfg["max_posts_pro_tag"]:
                break
            h, m = map(int, s.split(":"))
            kandidat = dt.datetime(datum.year, datum.month, datum.day, h, m, tzinfo=tz)
            if kandidat >= frueh and kandidat not in belegt:
                return kandidat.astimezone(dt.timezone.utc)
    raise RuntimeError("Kein freier Slot in den nächsten 60 Tagen.")


# Wunsch-Slots (25.11., Kalender in der Zentrale): steht im Kommentar der Freigabe hinter dem Marker, z. B.
# "[posten] slot:tiktok=2026-11-26T15:00:00+00:00 slot:youtube=2026-11-26T15:00:00+00:00". Ohne slot:-Teil gilt
# wie bisher "nächster freier Slot auf allen aktiven Plattformen" (Handyseite unverändert).
_WUNSCH = re.compile(r"slot:([a-z]+)=(\S+)")


def wunsch_slots(kommentar):
    """{plattform: slot_utc (datetime)} aus dem Kommentar. Kaputte Einträge werden ignoriert."""
    aus = {}
    for p, wert in _WUNSCH.findall(str(kommentar or "")):
        try:
            t = dt.datetime.fromisoformat(wert.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is not None:
            aus[p] = t.astimezone(dt.timezone.utc)
    return aus


def wunsch_kommentar(slots, cfg=CONFIG):
    """Gegenstück zu wunsch_slots: {plattform: slot_utc} -> Kommentar mit Marker."""
    teile = [cfg["marker"]] + [f"slot:{p}={t.astimezone(dt.timezone.utc).isoformat()}" for p, t in sorted(slots.items())]
    return " ".join(teile)


def slot_aus_lokal(text, cfg=CONFIG):
    """'2026-11-26T16:00' (Berliner Zeit, wie im Kalender) -> UTC-datetime."""
    t = dt.datetime.fromisoformat(str(text)[:16])
    return t.replace(tzinfo=ZoneInfo(cfg["zeitzone"])).astimezone(dt.timezone.utc)


def slot_pruefen(slot_utc, belegt_utc, jetzt_utc=None, cfg=CONFIG):
    """None = Slot ist erlaubt. Sonst der Grund. Gleiche Regeln wie naechster_slot:
    nur die festen Uhrzeiten, mindestens X Min Vorlauf, nicht schon belegt, höchstens N Posts pro Tag."""
    tz = ZoneInfo(cfg["zeitzone"])
    lokal = slot_utc.astimezone(tz)
    if lokal.strftime("%H:%M") not in cfg["slots"]:
        return f"{lokal.strftime('%H:%M')} ist kein Posting-Slot ({', '.join(cfg['slots'])})."
    jetzt = (jetzt_utc or dt.datetime.now(dt.timezone.utc)).astimezone(tz)
    if lokal < jetzt + dt.timedelta(minutes=cfg["mindest_vorlauf_minuten"]):
        return f"Slot {lokal.strftime('%d.%m. %H:%M')} ist zu knapp oder vorbei (mind. {cfg['mindest_vorlauf_minuten']} Min Vorlauf)."
    belegt = {dt.datetime.fromisoformat(b.replace("Z", "+00:00")).astimezone(tz) for b in belegt_utc}
    if lokal in belegt:
        return f"Slot {lokal.strftime('%d.%m. %H:%M')} ist schon belegt."
    if sum(1 for b in belegt if b.date() == lokal.date()) >= cfg["max_posts_pro_tag"]:
        return f"Am {lokal.strftime('%d.%m.')} sind schon {cfg['max_posts_pro_tag']} Posts geplant."
    return None


# ---------------------------------------------------------------- Caption
def _hashtags(karte, cfg, vorhanden):
    """Reihenfolge = Wichtigkeit: Creator, Marke, Format, Rest."""
    tags = []
    for creator, t in cfg["hashtags_nach_creator"].items():
        if creator.lower() in (str(karte.get("kanal")) + " " + str(karte.get("titel"))).lower():
            tags += t
    tags += cfg["hashtags_basis"][:1]
    tags += cfg["hashtags_nach_format"].get(format_von(karte) or "", [])
    tags += cfg["hashtags_basis"][1:]
    neu = []
    for t in tags:
        if t.lower() not in vorhanden and t.lower() not in {x.lower() for x in neu}:
            neu.append(t)
    return neu


def format_von(karte):
    return karte.get("format") or karte.get("art")


def caption_bauen(karte, cfg=CONFIG, plattform="tiktok"):
    """Text pro Plattform. TikTok: ohne Zeilenumbrüche (API kann keine). Instagram: mit Umbruch vor Hashtags.
    YouTube: Beschreibung mit #shorts."""
    p = cfg.get("plattformen", {}).get(plattform, {})
    text = (karte.get("caption") or karte.get("titel") or "").strip()
    vorhanden = {t.lower() for t in re.findall(r"#\w+", text)}
    neu = [t for t in p.get("hashtags_extra", []) if t.lower() not in vorhanden] + _hashtags(karte, cfg, vorhanden)
    max_tags = p.get("max_hashtags", cfg["max_hashtags"])
    neu = neu[:max(0, max_tags - len(vorhanden))]
    if plattform == "tiktok":
        text = re.sub(r"\s*\n+\s*", " ", text)
        return (text + " " + " ".join(neu)).strip()[:2200]
    return (text + ("\n\n" if neu else "") + " ".join(neu)).strip()[:p.get("max_zeichen", 2200)]


def metadata_bauen(karte, plattform, cfg=CONFIG):
    """Buffer PostInputMetaData je Plattform (Feldnamen laut developers.buffer.com, Stand 24.11.2026)."""
    p = cfg.get("plattformen", {}).get(plattform, {})
    if plattform == "youtube":
        titel = (karte.get("titel") or karte.get("caption") or "Clip").strip()
        titel = re.sub(r"\s+", " ", titel)
        if "#shorts" not in titel.lower() and len(titel) <= 90:
            titel += " #shorts"
        kat = p.get("kategorie_nach_format", {}).get(format_von(karte) or "", p.get("kategorie", "24"))
        return {"youtube": {"title": titel[:100], "categoryId": kat, "privacy": "public", "madeForKids": False,
                            "notifySubscribers": True, "type": "short"}}
    if plattform == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    return None


def titel_typ(titel):
    t = (titel or "").strip()
    if re.search(r'[„"“»«]', t):
        return "zitat"
    if t.endswith("?"):
        return "frage"
    if re.match(r"^(top|platz)\s*\d", t, re.I):
        return "liste"
    return "aussage"


# ---------------------------------------------------------------- Sperren vor dem Posten
def sperrgrund(karte, cfg=CONFIG):
    """None = darf gepostet werden. Sonst der Grund, warum nicht (wird jeden Lauf neu geprüft).
    1) Glücksspiel nie. 2) Nur neu gerenderte Originale (Feld datei im Fach 'fertig'), nicht die alte Charge.
    3) Ton-Sync-Ampel muss grün sein (rot, gelb oder ungeprüft = gesperrt)."""
    s = cfg.get("sperren", {})
    c = karte.get("checks") or {}
    text = " ".join(str(karte.get(k) or "") for k in ("titel", "caption", "art", "format", "kanal", "quelle", "spiel"))
    text += " " + " ".join(str(h) for h in (karte.get("hinweise") or []))
    text = text.lower()
    if c.get("gluecksspiel") or any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text) for w in s.get("gluecksspiel_woerter", [])):
        return "Glücksspiel-Szene, wird nie gepostet."
    if s.get("original_pflicht", True) and not karte.get("datei"):
        return "Alte Charge: kein neu gerendertes Original im Fach 'fertig'."
    pflicht = s.get("ton_sync_pflicht", "gruen")
    status = (c.get("ton_sync") or {}).get("status")
    if pflicht and status != pflicht:
        return f"Ton-Sync-Ampel {status or 'ungeprüft'}, gepostet wird nur bei grün."
    return None
