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

    def post_bearbeiten(self, post_id, text, metadata=None):
        """25.11.: nur Text und Metadaten (Titel) eines geplanten Posts ändern (editPost). Video und Uhrzeit bleiben,
        weil assets und dueAt weggelassen werden (laut Buffer-Doku: weglassen = unverändert)."""
        q = """mutation($i: EditPostInput!) { editPost(input: $i) { __typename
                 ... on PostActionSuccess { post { id dueAt } } ... on MutationError { message } } }"""
        eingabe = {"id": post_id, "text": text}
        if metadata:
            eingabe["metadata"] = metadata
        r = self.gql(q, {"i": eingabe})["editPost"]
        if "post" not in r:
            raise RuntimeError(f"Buffer ändert nicht: {r.get('message') or r.get('__typename')}")
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
    """Nächster freier Slot (Slots aus config.json ohne Bonus-Slots, Berlin), der mind. X Min in der Zukunft liegt.

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
            if s in cfg.get("bonus_slots", []):   # Bonus-Slot nur, wenn Marvin ihn ausdrücklich wählt
                continue
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
        if wert == "sofort":   # 25.11.: Bonus-Clip jetzt posten (siehe sofort_slot)
            aus[p] = "sofort"
            continue
        try:
            t = dt.datetime.fromisoformat(wert.replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is not None:
            aus[p] = t.astimezone(dt.timezone.utc)
    return aus


def wunsch_kommentar(slots, cfg=CONFIG):
    """Gegenstück zu wunsch_slots: {plattform: slot_utc} -> Kommentar mit Marker."""
    teile = [cfg["marker"]] + [f"slot:{p}={t if t == 'sofort' else t.astimezone(dt.timezone.utc).isoformat()}" for p, t in sorted(slots.items())]
    return " ".join(teile)


def slot_aus_lokal(text, cfg=CONFIG):
    """'2026-11-26T16:00' (Berliner Zeit, wie im Kalender) -> UTC-datetime."""
    t = dt.datetime.fromisoformat(str(text)[:16])
    return t.replace(tzinfo=ZoneInfo(cfg["zeitzone"])).astimezone(dt.timezone.utc)


def ist_bonus(slot_utc, cfg=CONFIG):
    return slot_utc.astimezone(ZoneInfo(cfg["zeitzone"])).strftime("%H:%M") in cfg.get("bonus_slots", [])


def sofort_slot(geplant, plattform, jetzt_utc=None, cfg=CONFIG):
    """Bonus-Clip jetzt: (slot_utc, None). 25.11. abends (Marvin): Bonus geht IMMER sofort raus. Abstand und „ein Bonus
    pro Tag“ sind keine Sperre mehr, nur noch Warnungen (sofort_warnungen). Rückgabe bleibt (t, grund) für alte Aufrufer."""
    jetzt = jetzt_utc or dt.datetime.now(dt.timezone.utc)
    return (jetzt + dt.timedelta(minutes=cfg.get("sofort_vorlauf_minuten", 5))).replace(second=0, microsecond=0), None


def sofort_warnungen(geplant, plattform, jetzt_utc=None, cfg=CONFIG):
    """Hinweise für den Bestätigungsdialog, nie eine Sperre. Grund: viele Posts kurz hintereinander teilen sich die
    Reichweite und können bei TikTok und Instagram wie Spam wirken (Plattform drosselt). Marvin entscheidet."""
    tz = ZoneInfo(cfg["zeitzone"])
    t, _ = sofort_slot(geplant, plattform, jetzt_utc, cfg)
    tag = t.astimezone(tz).date()
    heute = []
    for g in geplant:
        if g.get("plattform", "tiktok") != plattform or g.get("status") not in ("geplant", "gesendet"):
            continue
        try:
            s = dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if s.astimezone(tz).date() == tag:
            heute.append((s, g))
    aus = []
    abstand = cfg.get("sofort_warnung_abstand_minuten", 60)
    nah = sorted(s for s, _ in heute if abs((s - t).total_seconds()) < abstand * 60)
    if nah:
        aus.append(f"Nur {int(abs((nah[0] - t).total_seconds()) // 60)} Min Abstand zum Post um {nah[0].astimezone(tz).strftime('%H:%M')}. "
                   "Kurz hintereinander teilen sich die Posts die Reichweite.")
    if any(g.get("bonus") or ist_bonus(s, cfg) for s, g in heute):
        aus.append("Heute gibt es schon einen Bonus-Clip.")
    if len(heute) >= cfg["max_posts_pro_tag"]:
        aus.append(f"Heute sind schon {len(heute)} Posts geplant oder gesendet (sonst max. {cfg['max_posts_pro_tag']}).")
    return aus


def buffer_voll(geplant, plattform, jetzt_utc=None, cfg=CONFIG):
    """Grund oder None. Buffer Free erlaubt nur N geplante (noch nicht gesendete) Posts je Kanal gleichzeitig."""
    grenze = cfg.get("buffer_max_geplant")
    if not grenze:
        return None
    jetzt = jetzt_utc or dt.datetime.now(dt.timezone.utc)
    offen = 0
    for g in geplant:
        if g.get("plattform", "tiktok") != plattform or g.get("status") != "geplant":
            continue
        try:
            if dt.datetime.fromisoformat(g["slot_utc"].replace("Z", "+00:00")) > jetzt:
                offen += 1
        except (KeyError, ValueError):
            pass
    if offen >= grenze:
        return f"Buffer Free: schon {offen} von {grenze} Posts auf {plattform} geplant. Wird nach dem nächsten gesendeten Post neu versucht."
    return None


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


def caption_bauen(karte, cfg=CONFIG, plattform="tiktok", trends=None):
    """Posttext für Buffer (Feld text). 25.11.: aus texte_bauen, nie mehr der rohe Block „Titel: … Beschreibung: …“."""
    return texte_text(texte_bauen(karte, plattform, cfg, trends), plattform, cfg)


def metadata_bauen(karte, plattform, cfg=CONFIG, teile=None):
    """Buffer PostInputMetaData je Plattform (Feldnamen laut developers.buffer.com, Stand 25.11.2026).
    YouTube: echter Titel aus teile['titel'] (ohne Hashtags). TikTok hat für Videos kein Titelfeld (nur für Fotos)."""
    p = cfg.get("plattformen", {}).get(plattform, {})
    if plattform == "youtube":
        teile = teile or texte_bauen(karte, "youtube", cfg)
        titel = re.sub(r"\s+", " ", (teile.get("titel") or "Clip")).strip()[:100]
        kat = p.get("kategorie_nach_format", {}).get(format_von(karte) or "", p.get("kategorie", "24"))
        return {"youtube": {"title": titel, "categoryId": kat, "privacy": "public", "madeForKids": False,
                            "notifySubscribers": True, "type": "short"}}
    if plattform == "instagram":
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    return None


# ---------------------------------------------------------------- Titel, Beschreibung, Hashtags (25.11.)
# Die Karten-Caption ist ein Block für Menschen: „Titel: X\n\nBeschreibung:\nY\n\n🎥 Quelle\n#tags“, manchmal mit
# „YouTube Shorts: kürzerer Titel #tags“ am Ende. Bis 25.11. ging der ganze Block als Posttext raus (Marvin: „falsch“).
# Jetzt: zerlegen und je Plattform sauber zusammensetzen. Marvins Änderungen aus der Vorschau kommen als texte: im Kommentar.
_HASHTAG = re.compile(r"#[^\s#.,!?;:()\[\]{}\"'„“”»«]+")
_NUR_TAGS = re.compile(r"(\s*#\S+)+\s*")
_QUELLE = re.compile(r"(🎥|📺|fanpage|eigener schnitt|quelle\s*:)", re.I)


def _tags_aus(text):
    return [t for t in _HASHTAG.findall(text or "") if len(t) > 1]


def _ohne_tags(text):
    return re.sub(r"[ \t]{2,}", " ", _HASHTAG.sub("", text or "")).strip()


def texte_zerlegen(karte):
    """{'titel', 'yt_titel', 'beschreibung', 'quelle', 'hashtags'} aus karte['caption'] und karte['titel']."""
    roh = str(karte.get("caption") or "").replace("\r", "")
    yt = None
    m = re.search(r"(?im)^[ \t]*youtube[ -]?shorts?[ \t]*:[ \t]*(.+)$", roh)
    if m:
        yt = _ohne_tags(m.group(1)) or None
        roh = roh[:m.start()] + roh[m.end():]
    tags = _tags_aus(roh)
    titel_block = None
    m = re.match(r"(?is)\s*titel\s*:\s*(.*?)(?:\n\s*beschreibung\s*:[ \t]*|\Z)", roh)
    if m:
        titel_block = _ohne_tags(m.group(1).split("\n")[0]) or None
        roh = roh[m.end():]
    roh = re.sub(r"(?im)^\s*beschreibung\s*:\s*", "", roh)
    zeilen, quelle = [], None
    for z in roh.split("\n"):
        z = z.strip()
        if not z:
            if zeilen and zeilen[-1]:
                zeilen.append("")
            continue
        if _QUELLE.search(z) and len(z) < 160:
            quelle = quelle or _ohne_tags(z)
            continue
        if _NUR_TAGS.fullmatch(z):   # reine Hashtag-Zeile: Tags wandern nach hinten
            continue
        worte = z.split()
        while worte and worte[-1].startswith("#"):   # Hashtags am Zeilenende auch, mitten im Satz bleiben sie stehen
            worte.pop()
        if worte:
            zeilen.append(" ".join(worte))
    while zeilen and not zeilen[-1]:
        zeilen.pop()
    beschreibung = "\n".join(zeilen).strip()
    titel = _ohne_tags(str(karte.get("titel") or "").strip() or titel_block or "")
    if not titel and beschreibung:
        titel = re.split(r"(?<=[.!?])\s", beschreibung, maxsplit=1)[0][:100]
    if beschreibung == titel:
        beschreibung = ""
    im_text = {t.lower() for t in _tags_aus(beschreibung + " " + titel)}
    eindeutig = []
    for t in tags:
        if t.lower() not in im_text and t.lower() not in {x.lower() for x in eindeutig}:
            eindeutig.append(t)
    # Karte hat einen anderen Titel als der Block (später geändert, z. B. von Marvin): der gilt dann überall, auch auf YouTube
    geaendert = bool(titel_block and karte.get("titel") and _ohne_tags(str(karte["titel"])) != titel_block)
    return {"titel": titel, "yt_titel": yt, "beschreibung": beschreibung, "quelle": quelle, "hashtags": eindeutig,
            "titel_geaendert": geaendert}


_KEIN_NAME = {"fanpage", "sichtung", "vod", "live", "clips", "clip", "unbekannt", ""}


def _quelle_bauen(karte, titel):
    """Quellenzeile, wenn der Block keine hat: Streamer so geschrieben wie im Titel."""
    kanal = str(karte.get("kanal") or "").strip()
    name = None
    if kanal.lower() not in _KEIN_NAME:
        m = re.search(re.escape(kanal), titel, re.I)
        name = m.group(0) if m else kanal
    else:
        m = re.match(r"\s*([^\s']{2,30})(?:'s)?", titel)
        name = m.group(1) if m else None
    return f"🎥 {name} (Fanpage, eigener Schnitt)" if name else None


def _gluecksspiel(text, cfg=CONFIG):
    t = str(text or "").lower().replace("#", " ")
    return any(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t) or w.replace(" ", "") in t.split()
               for w in cfg.get("sperren", {}).get("gluecksspiel_woerter", []))


def trend_tags(karte, trends, cfg=CONFIG, ohne=()):
    """Trend-Hashtags, die zum Clip passen: Streamer im Clip, Spiel des Clips oder erlaubte allgemeine Tags.
    Nie fremde Kanal-Marken (Konkurrenz aus trends.top_clips), nie Glücksspiel. trends = lernen/trends.json bzw. info.trends."""
    if not trends:
        return []
    t_cfg = cfg.get("trend_hashtags", {})
    text = " ".join(str(karte.get(k) or "") for k in ("titel", "caption", "kanal", "art", "format")).lower()
    streamer = {str(s.get("name") or "").lower() for s in trends.get("streamer") or []}
    streamer |= {c.lower() for c in cfg.get("hashtags_nach_creator", {})}
    im_clip = {s for s in streamer if s and re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", text)}
    spiele = {t.lower().lstrip("#") for spiel, tags in t_cfg.get("spiele", {}).items() if spiel.lower() in text for t in tags}
    fremd = {re.sub(r"[^a-z0-9]", "", str(c.get("kanal") or "").lower()) for c in trends.get("top_clips") or []}
    fremd |= {x.lower().lstrip("#") for x in t_cfg.get("gesperrt", [])}
    erlaubt = {x.lower().lstrip("#") for x in t_cfg.get("allgemein", [])}
    aus = []
    for h in sorted(trends.get("hashtags") or [], key=lambda x: -float(x.get("gewicht") or 0)):
        tag = str(h.get("tag") or "").lower().lstrip("#")
        if not tag or tag in fremd or "#" + tag in ohne or _gluecksspiel(tag, cfg):
            continue
        if tag in im_clip or tag in spiele or tag in erlaubt:
            aus.append("#" + tag)
    return aus[: t_cfg.get("max", 2)]


def texte_bauen(karte, plattform, cfg=CONFIG, trends=None, titel_gesetzt=False):
    """Vorschlag je Plattform: {'titel', 'beschreibung', 'hashtags': [...]}. titel = YouTube-Titel bzw. erste Zeile (Hook).
    titel_gesetzt: Marvin hat beim Posten einen eigenen Titel gewählt, der gilt dann auch für YouTube."""
    p = cfg.get("plattformen", {}).get(plattform, {})
    z = texte_zerlegen(karte)
    titel = z["titel"]
    if plattform == "youtube" and z["yt_titel"] and not (titel_gesetzt or z["titel_geaendert"]):
        titel = z["yt_titel"]
    if plattform == "youtube" and len(titel) > 100:
        titel = titel[:99].rsplit(" ", 1)[0] + "…"
    beschreibung = z["beschreibung"]
    quelle = z["quelle"]
    if not quelle:
        quelle = _quelle_bauen(karte, z["titel"])
    if quelle:
        beschreibung = (beschreibung + "\n\n" + quelle).strip()
    # Reihenfolge = Wichtigkeit: Plattform-Pflicht (#shorts), die 3 Tags des Clips, passende Trends, Rest, Creator/Format/Marke
    eigene = z["hashtags"]
    trend = trend_tags(karte, trends, cfg, ohne={t.lower() for t in eigene})
    tags = list(p.get("hashtags_extra", [])) + eigene[:3] + trend + eigene[3:] + _hashtags(karte, cfg, set())
    im_text = {t.lower() for t in _tags_aus(titel + " " + beschreibung)}
    max_tags = max(0, p.get("max_hashtags", cfg["max_hashtags"]) - len(im_text))
    nie = {x.lower() for x in p.get("hashtags_nie", cfg.get("hashtags_nie", []))}
    neu = []
    for t in tags:
        t = "#" + str(t).strip().lstrip("#")
        if len(t) < 2 or t.lower() in im_text or t.lower() in nie or t.lower() in {x.lower() for x in neu} or _gluecksspiel(t, cfg):
            continue
        neu.append(t)
    return {"titel": titel, "beschreibung": beschreibung, "hashtags": neu[:max_tags]}


def texte_text(teile, plattform, cfg=CONFIG):
    """Feld text für Buffer aus {'titel','beschreibung','hashtags'}.
    TikTok: eine Zeile (API kann keine Umbrüche), Hook vorne. YouTube: Titel steht im Titelfeld, hier nur Beschreibung.
    Instagram: Hook, Absatz, Beschreibung, Absatz, Hashtags."""
    p = cfg.get("plattformen", {}).get(plattform, {})
    titel = str(teile.get("titel") or "").strip()
    besch = str(teile.get("beschreibung") or "").strip()
    tags = " ".join("#" + str(t).strip().lstrip("#") for t in (teile.get("hashtags") or []) if str(t).strip().lstrip("#"))
    if plattform == "tiktok":
        return " ".join(x for x in (titel, re.sub(r"\s*\n+\s*", " ", besch), tags) if x).strip()[:2200]
    stuecke = [besch, tags] if plattform == "youtube" else [titel, besch, tags]
    return "\n\n".join(x for x in stuecke if x).strip()[:p.get("max_zeichen", 2200)]


def texte_pruefen(teile, plattform, cfg=CONFIG):
    """None = ok, sonst Grund. Gilt für Vorschlag und für Marvins Änderung gleich (Sperre bleibt Sperre)."""
    gesamt = " ".join([str(teile.get("titel") or ""), str(teile.get("beschreibung") or "")] + [str(t) for t in teile.get("hashtags") or []])
    if _gluecksspiel(gesamt, cfg):
        return "Glücksspiel-Wort im Text, wird nie gepostet."
    if re.search(r"(?im)^\s*(titel|beschreibung)\s*:", str(teile.get("titel") or "") + "\n" + str(teile.get("beschreibung") or "")):
        return "Text enthält noch „Titel:“ oder „Beschreibung:“."
    p = cfg.get("plattformen", {}).get(plattform, {})
    alles = texte_text(teile, plattform, cfg) + (" " + str(teile.get("titel") or "") if plattform == "youtube" else "")
    anzahl = len({t.lower() for t in _tags_aus(alles)})
    grenze = p.get("max_hashtags_hart")
    if grenze and anzahl > grenze:
        return f"{anzahl} Hashtags, {plattform} erlaubt höchstens {grenze}."
    if plattform == "youtube" and not str(teile.get("titel") or "").strip():
        return "YouTube braucht einen Titel."
    if plattform == "youtube" and len(str(teile.get("titel") or "")) > 100:
        return "YouTube-Titel ist länger als 100 Zeichen."
    return None


def texte_aus_kommentar(kommentar):
    """Marvins bearbeitete Texte aus dem Freigabe-Kommentar: 'texte:<base64url JSON>' -> {plattform: teile}."""
    import base64
    m = re.search(r"(?<!\S)texte:([A-Za-z0-9_\-]+)", str(kommentar or ""))
    if not m:
        return {}
    try:
        roh = json.loads(base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    aus = {}
    for p, t in (roh.items() if isinstance(roh, dict) else []):
        if isinstance(t, dict):
            tags = t.get("hashtags") or []
            if isinstance(tags, str):
                tags = tags.replace(",", " ").split()
            aus[str(p)] = {"titel": str(t.get("titel") or "")[:300], "beschreibung": str(t.get("beschreibung") or "")[:2200],
                           "hashtags": ["#" + str(x).strip().lstrip("#") for x in tags if str(x).strip().lstrip("#")][:30]}
    return aus


def texte_kommentar(texte):
    """Gegenstück zu texte_aus_kommentar (die Handyseite baut dasselbe in JavaScript)."""
    import base64
    roh = json.dumps(texte, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "texte:" + base64.urlsafe_b64encode(roh).decode().rstrip("=")


def ohne_texte(kommentar):
    """Kommentar ohne den texte:-Teil (für Lernen und Anzeige)."""
    return re.sub(r"\s*(?<!\S)texte:[A-Za-z0-9_\-]+", "", str(kommentar or "")).strip()


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
