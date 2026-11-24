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

    def kanal(self, name):
        """Liefert (organizationId, channel) für den TikTok-Kanal mit diesem Namen."""
        orgs = self.gql("query { account { organizations { id name } } }")["account"]["organizations"]
        for org in orgs:
            # IDs werden direkt eingesetzt, damit wir keine Scalar-Typnamen raten müssen.
            kanaele = self.gql(
                "query { channels(input: {organizationId: %s}) { id name displayName service } }" % json.dumps(org["id"])
            )["channels"]
            for k in kanaele:
                if k["service"].lower() == "tiktok" and name.lower() in (k["name"] + " " + (k.get("displayName") or "")).lower():
                    return org["id"], k
        raise RuntimeError(f"Kein TikTok-Kanal '{name}' in Buffer gefunden.")

    def post_planen(self, kanal_id, text, video_url, faellig_utc):
        q = """mutation($i: CreatePostInput!) { createPost(input: $i) {
                 ... on PostActionSuccess { post { id dueAt } }
                 ... on MutationError { message } } }"""
        eingabe = {"channelId": kanal_id, "text": text, "schedulingType": "automatic", "mode": "customScheduled",
                   "dueAt": faellig_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "needsApproval": False,
                   "assets": [{"video": {"url": video_url}}]}
        r = self.gql(q, {"i": eingabe})["createPost"]
        if "post" not in r:
            raise RuntimeError(f"Buffer lehnt ab: {r.get('message')}")
        return r["post"]

    def gesendete_posts(self, org_id, kanal_id, max_seiten=10):
        q = """query($a: String) {
                 posts(first: 50, after: $a, input: {organizationId: %s, filter: {status: [sent], channelIds: [%s]}}) {
                   edges { node { id text dueAt metrics { type name value unit } metricsUpdatedAt } }
                   pageInfo { endCursor hasNextPage } } }""" % (json.dumps(org_id), json.dumps(kanal_id))
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


# ---------------------------------------------------------------- Caption
def caption_bauen(karte, cfg=CONFIG):
    """TikTok-Text: Caption der Karte (oder Titel) plus passende Hashtags, ohne Zeilenumbrüche
    (TikTok-API unterstützt keine Umbrüche)."""
    text = (karte.get("caption") or karte.get("titel") or "").strip()
    vorhanden = {t.lower() for t in re.findall(r"#\w+", text)}
    # Reihenfolge = Wichtigkeit: Creator, Marke, Format, Rest (bei max_hashtags wird hinten gekürzt)
    tags = []
    for creator, t in cfg["hashtags_nach_creator"].items():
        if creator.lower() in (str(karte.get("kanal")) + " " + str(karte.get("titel"))).lower():
            tags += t
    tags += cfg["hashtags_basis"][:1]
    tags += cfg["hashtags_nach_format"].get(karte.get("art") or "", [])
    tags += cfg["hashtags_basis"][1:]
    neu = []
    for t in tags:
        if t.lower() not in vorhanden and t.lower() not in {x.lower() for x in neu}:
            neu.append(t)
    platz = max(0, cfg["max_hashtags"] - len(vorhanden))
    text = re.sub(r"\s*\n+\s*", " ", text)
    voll = (text + " " + " ".join(neu[:platz])).strip()
    return voll[:2200]


def titel_typ(titel):
    t = (titel or "").strip()
    if re.search(r'[„"“»«]', t):
        return "zitat"
    if t.endswith("?"):
        return "frage"
    if re.match(r"^(top|platz)\s*\d", t, re.I):
        return "liste"
    return "aussage"
