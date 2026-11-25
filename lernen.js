/* Lernen auf dem Handy (25.11.2026, Marvin: „Fragen zu Clips, aus meiner Bewertung lernen, Pointe-Knopf, deine Schätzung markieren“).
   Liest info.lernen (Lernset vom PC, tools/lern_fragen.py) und Vorschauen aus dem Bucket vorschau/lernen/.
   Schreibt nur in die Tabelle lern_antworten (SQL: Clips-Pipeline/cloud/supabase_lernen_2026-11-25.sql).
   Eine Lernen-Antwort entscheidet, rendert und postet nie etwas. Schätz-Marker bleiben verdeckt, bis du einmal ganz
   geschaut oder selbst aufgedeckt hast, damit sie dein Tippen nicht lenken (faire Messung). */
(() => {
  "use strict";
  const nav = $("#nav");
  if (!nav || nav.querySelector('[data-ansicht="lernen"]')) return;
  const L = { d: null, id: null, tipps: [], keine: false, auf: false, aufVorTipp: false, antw: {}, text: "", gesehen: 0, raf: 0, urls: {}, zeit: 0 };
  const PNAME = { text: "Gesagtes", laut: "Lautstärke", chat: "Chat", szene: "Bildwechsel" };
  const sek = t => (t == null ? "–" : Number(t).toLocaleString("de-DE", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " s");
  const pz = x => (x == null ? "–" : Math.round(x * 100) + " %");
  const ERLEDIGT = "lz_erledigt", ENTWURF = id => "lz_entwurf_" + id;
  const erledigt = () => { try { return new Set(JSON.parse(localStorage.getItem(ERLEDIGT) || "[]")); } catch (e) { return new Set(); } };
  const offen = () => (L.d?.clips || []).filter(c => !c.beantwortet && !erledigt().has(c.id));
  const clip = () => (L.d?.clips || []).find(c => c.id === L.id);

  document.head.insertAdjacentHTML("beforeend", `<style id="lernen-css">
  .lzk { position:relative; background:var(--karte); border:1px solid var(--linie); border-radius:3px; padding:12px; margin-bottom:12px; }
  .lzk::after { content:""; position:absolute; left:14px; right:14px; top:-1px; height:1px; background:linear-gradient(90deg,transparent,var(--cyan),transparent); opacity:.5; }
  .lzkpis { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-bottom:12px; }
  .lzkpi { background:var(--karte); border:1px solid var(--linie); border-radius:3px; padding:9px 10px; }
  .lzkpi b { display:block; font-family:var(--display); font-size:22px; line-height:1.1; font-variant-numeric:tabular-nums; }
  .lzkpi span { font-size:11px; color:var(--leise); }
  .lzkpi.gold b { color:var(--gelb); text-shadow:0 0 12px rgba(255,210,63,.35); } .lzkpi.rot { border-color:rgba(255,42,75,.55); }
  .lzwahl { display:grid; grid-template-columns:repeat(auto-fill,minmax(40px,1fr)); gap:6px; margin-bottom:12px; }
  .lzwahl button { padding:8px 0; font-family:var(--mono); font-size:13px; }
  .lzwahl button.an { border-color:var(--cyan); color:var(--cyan); box-shadow:0 0 10px rgba(37,226,255,.3); }
  .lzwahl button.fertig { color:var(--gruen); border-color:rgba(61,255,154,.5); opacity:.6; }
  .lzkopf { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; margin-bottom:8px; }
  .lzkopf h3 { margin:0; font-size:15px; text-transform:none; letter-spacing:0; font-family:inherit; line-height:1.3; }
  .lzkopf h3::before { display:none; } .lzkopf small { display:block; color:var(--leise); font-family:var(--mono); font-size:11px; margin-top:2px; }
  .lzgr { font-family:var(--mono); font-size:10px; padding:1px 6px; border:1px solid var(--magenta); color:var(--magenta); border-radius:2px; white-space:nowrap; }
  .lzbuehne { position:relative; background:#000; border:1px solid var(--linie2); }
  .lzbuehne video { display:block; width:100%; max-height:58vh; background:#000; }
  .lzblitz { position:absolute; inset:0; pointer-events:none; background:radial-gradient(circle at 50% 60%,rgba(255,42,75,.5),transparent 60%); opacity:0; }
  .lzblitz.an { animation:lzblitz .45s ease-out; } @keyframes lzblitz { 0% { opacity:1; } 100% { opacity:0; } }
  .lzzeit svg { display:block; width:100%; height:70px; margin-top:8px; }
  .lzzeit .spur { fill:var(--cyan); opacity:.32; } .lzzeit .ges { fill:rgba(37,226,255,.08); }
  .lzzeit .kopf { stroke:#fff; stroke-width:1.5; } .lzzeit .tipp { stroke:var(--rot); stroke-width:2.5; } .lzzeit .tippk { fill:var(--rot); }
  .lzzeit .mark { fill:var(--gelb); } .lzzeit .markl { stroke:var(--gelb); stroke-dasharray:3 3; } .lzzeit .tol { fill:rgba(255,210,63,.1); }
  .lzzeit .treffer { stroke:var(--gruen); stroke-width:2; } .lzzeit text { font-family:var(--mono); font-size:10px; fill:var(--leise); } .lzzeit .tl { fill:var(--gelb); font-weight:700; }
  .lzpointe { width:100%; margin-top:10px; padding:20px 12px; font-family:var(--display); font-size:26px; font-weight:700; letter-spacing:4px; color:#fff; position:relative;
    background:linear-gradient(180deg,rgba(255,42,75,.45),rgba(255,42,75,.15)); border:1px solid var(--rot); box-shadow:0 0 22px rgba(255,42,75,.45),inset 0 0 26px rgba(255,42,75,.25); touch-action:manipulation; }
  .lzpointe small { display:block; font-family:var(--mono); font-size:11px; letter-spacing:.5px; font-weight:400; color:#ffd5dc; margin-top:4px; }
  .lzpointe:active { transform:scale(.98); } .lzpointe .zahl { position:absolute; right:10px; top:8px; font-family:var(--mono); font-size:12px; letter-spacing:0; background:var(--rot); padding:0 7px; border-radius:3px; }
  .lzreihe { display:grid; grid-template-columns:1fr 1fr 1fr; gap:6px; margin-top:8px; } .lzreihe button { padding:11px 4px; font-size:13px; }
  .lzreihe button.an { background:rgba(255,210,63,.16); border-color:var(--gelb); color:#fff1c2; }
  .lzinfo { font-size:13px; color:var(--leise); margin-top:8px; } .lzinfo b { color:var(--text); font-family:var(--mono); }
  .lzverdeckt { font-family:var(--mono); font-size:11px; color:var(--gelb); border:1px dashed rgba(255,210,63,.5); padding:2px 8px; border-radius:2px; }
  .lzq { padding:10px 0; border-bottom:1px solid var(--linie); } .lzq:last-of-type { border-bottom:0; }
  .lzq p { margin:0 0 7px; font-weight:600; font-size:14px; } .lzq p small { display:block; font-weight:400; font-family:var(--mono); font-size:10.5px; color:var(--leise); margin-top:2px; }
  .lzq.clip p::before { content:"◆ "; color:var(--magenta); }
  .lzq .opt { display:flex; flex-wrap:wrap; gap:6px; } .lzq .opt button { padding:8px 11px; font-size:14px; border-radius:3px; }
  .lzq .opt button.an { background:rgba(37,226,255,.14); color:var(--cyan); border-color:var(--cyan); box-shadow:0 0 12px rgba(37,226,255,.25); }
  .lzq .skala { display:grid; grid-template-columns:repeat(5,1fr); gap:6px; } .lzq .skala button { padding:10px 0; font-family:var(--mono); font-size:16px; }
  .lzq textarea { width:100%; box-sizing:border-box; margin-top:8px; font:inherit; font-size:16px; background:rgba(5,7,13,.9); color:var(--text); border:1px solid var(--linie2); border-radius:3px; padding:8px; }
  .lzein { display:grid; grid-template-columns:1fr; gap:2px; padding:7px 0; border-bottom:1px solid var(--linie); font-size:13px; } .lzein b { font-family:var(--display); color:#cfe0f5; }
  .lzerg { font-size:13px; margin-top:8px; padding:8px 10px; border:1px solid var(--linie2); background:rgba(8,12,22,.6); } .lzerg .t { color:var(--gelb); font-family:var(--mono); }
  @media (prefers-reduced-motion:reduce) { .lzblitz.an { animation:none; } }
  </style>`);

  // Menüpunkt Lernen (vor Abmelden), Name im Menü-Knopf kommt aus NAMEN im HUD-2-Block
  nav.querySelector("#abmelden").insertAdjacentHTML("beforebegin", `<button data-ansicht="lernen">Lernen<small>Fragen und Pointe-Knopf</small></button>`);
  nav.querySelector('[data-ansicht="lernen"]').onclick = function () { ansicht = "lernen"; nav.querySelectorAll("[data-ansicht]").forEach(x => x.classList.toggle("an", x === this)); zeige(); };
  const _zeige = zeige;
  zeige = async function () {
    if (ansicht !== "lernen") { cancelAnimationFrame(L.raf); return _zeige(); }
    const k = $("#h2menue"); if (k && k.children[1]) k.children[1].textContent = "Lernen";
    await seite($("#inhalt"));
  };

  function laden(id) {
    L.id = id; L.tipps = []; L.keine = false; L.auf = false; L.aufVorTipp = false; L.antw = {}; L.text = ""; L.gesehen = 0;
    try { const e = JSON.parse(localStorage.getItem(ENTWURF(id)) || "null"); if (e) Object.assign(L, e); } catch (x) {}
  }
  function merken() { try { localStorage.setItem(ENTWURF(L.id), JSON.stringify({ tipps: L.tipps, keine: L.keine, auf: L.auf, aufVorTipp: L.aufVorTipp, antw: L.antw, text: L.text, gesehen: L.gesehen })); } catch (x) {} }

  async function holen() {
    if (!L.d || Date.now() - L.zeit > 30000) {
      const { data, error } = await sb.from("info").select("wert,aktualisiert").eq("schluessel", "lernen").maybeSingle();
      if (error) throw error;
      L.d = data?.wert || { clips: [] }; L.zeit = Date.now();
    }
    if (Date.now() - (L.urlZeit || 0) > 50 * 60000) { L.urls = {}; L.urlZeit = Date.now(); }   // signierte Links gelten 1 h
    const pfade = (L.d.clips || []).map(c => c.video).filter(p => p && !L.urls[p]);
    if (pfade.length) { const { data: u } = await sb.storage.from("vorschau").createSignedUrls(pfade, 3600); (u || []).forEach(x => { if (x.signedUrl) L.urls[x.path] = x.signedUrl; }); }
  }

  function zeitleiste(c) {
    const W = L.W = Math.max(280, Math.round($("#lzZeit")?.clientWidth || 360)), H = 70, Y = 26, d = Math.max(0.1, c.dauer || 1), x = t => (Math.max(0, Math.min(d, t)) / d) * W;
    const sp = c.spur || [], bw = W / Math.max(1, sp.length), tol = L.d?.toleranz || 1.5, re = L.d?.schaetzer?.reaktion || 0.4;
    let s = `<svg viewBox="0 0 ${W} ${H}" aria-label="Zeitleiste"><rect class="ges" id="lzGes" x="0" y="${Y - 2}" width="0" height="${H - Y - 12}"/>`;
    sp.forEach((v, i) => { const h = Math.max(1, (v / 99) * (H - Y - 14)); s += `<rect class="spur" x="${(i * bw).toFixed(1)}" y="${(H - 14 - h).toFixed(1)}" width="${Math.max(1, bw - .5).toFixed(1)}" height="${h.toFixed(1)}"/>`; });
    for (let t = 0; t <= d; t += d > 40 ? 10 : 5) s += `<text x="${x(t)}" y="${H - 2}" text-anchor="${t ? "middle" : "start"}">${Math.round(t)}s</text>`;
    if (L.auf) (c.marker || []).forEach((m, i) => { s += `<rect class="tol" x="${x(m.t - tol)}" y="${Y - 6}" width="${x(m.t + tol) - x(m.t - tol)}" height="${H - Y - 8}"/><line class="markl" x1="${x(m.t)}" x2="${x(m.t)}" y1="${Y - 6}" y2="${H - 14}"/><path class="mark" d="M${x(m.t)} ${Y - 24} l6 7 -6 7 -6 -7z"/><text class="tl" x="${x(m.t) + 8}" y="${Y - 12}">${i + 1}</text>`; });
    L.tipps.forEach(t => { s += `<line class="tipp" x1="${x(t)}" x2="${x(t)}" y1="${Y - 4}" y2="${H - 14}"/><circle class="tippk" cx="${x(t)}" cy="${Y - 6}" r="4"/>`;
      if (L.auf) { const e = t - re, m = (c.marker || []).reduce((b, m) => (b == null || Math.abs(m.t - e) < Math.abs(b.t - e) ? m : b), null); if (m && Math.abs(m.t - e) <= tol) s += `<line class="treffer" x1="${x(e)}" x2="${x(m.t)}" y1="${Y - 10}" y2="${Y - 10}"/>`; } });
    return s + `<line class="kopf" id="lzKopf" x1="0" x2="0" y1="${Y - 26}" y2="${H - 14}"/></svg>`;
  }
  function treffer(c) {
    const tol = L.d?.toleranz || 1.5, re = L.d?.schaetzer?.reaktion || 0.4, mk = c.marker || [];
    if (!L.auf) return `<span class="lzverdeckt">${mk.length} ${mk.length === 1 ? "Schätzung" : "Schätzungen"} verdeckt</span>`;
    if (L.keine) return `Keine Pointe. Meine ${mk.length} Marker zählen als Fehlalarm.`;
    if (!L.tipps.length) return `Meine Schätzung: ${mk.map((m, i) => `<b>${i + 1}</b> ${sek(m.t)}`).join(" · ") || "keine"}.`;
    const n = L.tipps.filter(t => mk.some(m => Math.abs(m.t - (t - re)) <= tol)).length;
    return `<b>${n} von ${L.tipps.length}</b> deiner Pointen getroffen`;
  }
  function erkl(c) {
    if (!L.auf || !(c.marker || []).length) return "";
    return `<div class="lzerg">${c.marker.map((m, i) => `<div><span class="t">◆${i + 1} ${sek(m.t)}</span> ${m.quellen.map(q => PNAME[q] || q).join(" + ")}</div>`).join("")}</div>`;
  }
  function zeitNeu() {
    const c = clip(); if (!c) return;
    const z = $("#lzZeit"); if (z) z.innerHTML = zeitleiste(c);
    $("#lzTreff").innerHTML = treffer(c); $("#lzErkl").innerHTML = erkl(c);
    const n = $("#lzN"); n.textContent = L.tipps.length; n.hidden = !L.tipps.length;
    $("#lzKeine").classList.toggle("an", L.keine); $("#lzAuf").textContent = L.auf ? "Verbergen" : "Schätzung";
  }
  function frage(q) {
    const v = L.antw[q.id], an = o => (Array.isArray(v) ? v.includes(o) : v === o) ? "an" : "";
    const kn = q.optionen.map(o => `<button type="button" class="${an(o)}" data-lq="${esc(q.id)}" data-lo="${esc(o)}" data-lm="${q.typ === "mehrfach" ? 1 : ""}">${esc(o)}</button>`).join("");
    return `<div class="lzq ${q.quelle === "clip" ? "clip" : ""}"><p>${esc(q.text)}${q.warum ? `<small>${esc(q.warum)}</small>` : ""}</p><div class="${q.typ === "skala" ? "skala" : "opt"}">${kn}</div>
      ${q.text_feld ? `<textarea id="lzText" rows="2" placeholder="Optional in deinen Worten">${esc(L.text)}</textarea>` : ""}</div>`;
  }

  async function seite(ziel) {
    ziel.innerHTML = `<p class="leer" style="padding:20px 0">Lade Lernen …</p>`;
    try { await holen(); } catch (e) { ziel.innerHTML = `<p class="fehler">${esc(e.message)}</p>`; return; }
    if (ansicht !== "lernen") return;
    const d = L.d, alle = (d.clips || []), off = offen(), fertig = erledigt();
    if (!L.id || !alle.some(c => c.id === L.id) || fertig.has(L.id)) laden((off[0] || alle[0] || {}).id);
    const c = clip(), m = d.messung?.aktuell_auf_allen || {};
    let h = `<div class="lzkpis"><div class="lzkpi ${off.length ? "rot" : ""}"><b>${off.length}</b><span>offen von ${alle.length}</span></div>
      <div class="lzkpi gold"><b>${pz(m.trefferquote)}</b><span>Pointe-Treffer</span></div><div class="lzkpi"><b>${d.antworten ?? 0}</b><span>Antworten</span></div></div>`;
    if (!c) { ziel.innerHTML = h + `<div class="lzk"><p class="leer">Kein Lernset. Am PC in der Zentrale unter Lernen „Neue Clips vorbereiten“ drücken.</p></div>` + fort(d); return; }
    const url = L.urls[c.video];
    h += `<div class="lzwahl">${alle.map((x, i) => `<button type="button" data-lzid="${esc(x.id)}" class="${x.id === c.id ? "an" : ""} ${x.beantwortet || fertig.has(x.id) ? "fertig" : ""}">${x.beantwortet || fertig.has(x.id) ? "✓" : i + 1}</button>`).join("")}</div>
      <div class="lzk"><div class="lzkopf"><div><h3>${esc(c.titel || c.kanal)}</h3><small>${esc(c.kanal || "")} · ${esc(c.typ || "")} · ${sek(c.dauer)}</small></div><span class="lzgr">${esc(c.gruppe_text || "")}</span></div>
        <div class="lzbuehne">${url ? `<video id="lzVideo" src="${esc(url)}" controls playsinline preload="auto"></video>` : `<div class="platz">Vorschau fehlt noch, der PC lädt sie hoch.</div>`}<div class="lzblitz" id="lzBlitz"></div></div>
        <div class="lzzeit" id="lzZeit"></div>
        <button type="button" class="lzpointe" id="lzPointe">POINTE<small>tippen, wenn es witzig wird</small><span class="zahl" id="lzN" hidden>0</span></button>
        <div class="lzreihe"><button type="button" id="lzWeg">× Rückgängig</button><button type="button" id="lzKeine">Keine Pointe</button><button type="button" id="lzAuf">Schätzung</button></div>
        <div class="lzinfo" id="lzTreff"></div><div id="lzErkl"></div></div>
      <div class="lzk">${[...(d.fest || []), ...(c.fragen || [])].map(frage).join("")}
        <button type="button" class="ja" id="lzSpeichern" style="width:100%;margin-top:12px;padding:14px">Speichern und nächster</button>
        <p class="lzinfo" id="lzFehler"></p></div>` + fort(d);
    ziel.innerHTML = h;
    verdrahten(c);
  }
  function fort(d) {
    const ms = d.messung || {}, a = ms.aktuell_auf_allen || {}, s = ms.start_auf_allen || {};
    return `<details class="lzk"><summary style="cursor:pointer;font-family:var(--display);font-size:13px;letter-spacing:1.3px;text-transform:uppercase;color:#9fb6d6">Lernfortschritt</summary>
      <div class="lzein"><b>Pointe-Schätzer v${d.schaetzer?.version ?? 1}</b><span>Treffer ${pz(a.trefferquote)} (Start ${pz(s.trefferquote)}), ${a.fehlalarme ?? 0} Fehlalarme</span></div>
      ${ms.pruef_neu ? `<div class="lzein"><b>Faire Prüfung</b><span>${ms.pruefen} spätere Clips: vorher ${pz(ms.pruef_alt?.trefferquote)}, neu ${pz(ms.pruef_neu?.trefferquote)}. ${esc(ms.ergebnis || "")}</span></div>` : `<div class="lzein"><span>Ab 8 Clips mit Pointe stelle ich den Schätzer fair neu ein.</span></div>`}
      ${(d.einsichten || []).map(e => `<div class="lzein"><b>${esc(e.titel)}</b><span>${esc(e.text)}</span></div>`).join("")}</details>`;
  }
  function verdrahten(c) {
    const v = $("#lzVideo");
    zeitNeu();
    document.querySelectorAll("[data-lzid]").forEach(b => b.onclick = () => { v?.pause(); laden(b.dataset.lzid); seite($("#inhalt")); });
    cancelAnimationFrame(L.raf);
    const lauf = () => { const dd = Math.max(.1, c.dauer), k = $("#lzKopf"), g = $("#lzGes"), W = L.W || 360;
      if (k && v) { const x = (Math.min(dd, v.currentTime) / dd) * W; k.setAttribute("x1", x); k.setAttribute("x2", x); }
      if (v && !v.paused) { L.gesehen = Math.max(L.gesehen, v.currentTime); if (g) g.setAttribute("width", (Math.min(dd, L.gesehen) / dd) * W); }
      if (ansicht === "lernen") L.raf = requestAnimationFrame(lauf); };
    L.raf = requestAnimationFrame(lauf);
    if (v) v.addEventListener("ended", () => { L.gesehen = c.dauer; if (!L.auf) { L.auf = true; merken(); zeitNeu(); toast("Schätzung aufgedeckt"); } });
    $("#lzZeit").onclick = e => { const r = e.currentTarget.getBoundingClientRect(); if (v) v.currentTime = ((e.clientX - r.left) / r.width) * c.dauer; };
    $("#lzPointe").onclick = () => {
      if (!v) return; const t = Math.round(v.currentTime * 100) / 100;
      if (L.keine) { L.keine = false; if (L.antw.pointe === "keine Pointe") delete L.antw.pointe; frageNeu(); }
      if (!L.tipps.some(x => Math.abs(x - t) < .3)) L.tipps.push(t); L.tipps.sort((a, b) => a - b); merken(); zeitNeu();
      const b = $("#lzBlitz"); b.classList.remove("an"); void b.offsetWidth; b.classList.add("an"); try { navigator.vibrate?.(15); } catch (x) {}
    };
    $("#lzWeg").onclick = () => { L.tipps.pop(); merken(); zeitNeu(); };
    $("#lzKeine").onclick = () => { L.keine = !L.keine; if (L.keine) { L.tipps = []; L.antw.pointe = "keine Pointe"; } else if (L.antw.pointe === "keine Pointe") delete L.antw.pointe; merken(); zeitNeu(); frageNeu(); };
    $("#lzAuf").onclick = () => { if (!L.auf && !L.tipps.length && !L.keine) L.aufVorTipp = true; L.auf = !L.auf; merken(); zeitNeu(); };
    document.querySelectorAll("[data-lq]").forEach(b => b.onclick = () => {
      const q = b.dataset.lq, o = b.dataset.lo;
      if (b.dataset.lm) { const s = new Set(L.antw[q] || []); s.has(o) ? s.delete(o) : s.add(o); L.antw[q] = [...s]; } else L.antw[q] = L.antw[q] === o ? undefined : o;
      if (q === "pointe") { L.keine = L.antw.pointe === "keine Pointe"; if (L.keine) L.tipps = []; zeitNeu(); }
      merken(); frageNeu();
    });
    const t = $("#lzText"); if (t) t.oninput = () => { L.text = t.value; merken(); };
    $("#lzSpeichern").onclick = () => speichern(c, v);
  }
  function frageNeu() { document.querySelectorAll("[data-lq]").forEach(b => { const v = L.antw[b.dataset.lq]; b.classList.toggle("an", Array.isArray(v) ? v.includes(b.dataset.lo) : v === b.dataset.lo); }); }
  async function speichern(c, v) {
    const antw = Object.fromEntries(Object.entries(L.antw).filter(([, x]) => x != null && !(Array.isArray(x) && !x.length)));
    if (L.text.trim()) antw.text = L.text.trim();
    if (!Object.keys(antw).length && !L.tipps.length && !L.keine) return toast("Erst eine Frage beantworten oder Pointe tippen");
    const knopf = $("#lzSpeichern"); knopf.disabled = true;
    const { data: s } = await sb.auth.getSession();
    const { error } = await sb.from("lern_antworten").insert({ clip_id: c.id, von: s.session.user.id, daten: { antworten: antw, pointen: L.tipps, keine_pointe: L.keine,
      angesehen_s: Math.round(Math.max(L.gesehen, v?.currentTime || 0) * 10) / 10, marker_vorher: L.aufVorTipp, marker_version: c.marker_version } });
    knopf.disabled = false;
    if (error) { $("#lzFehler").textContent = /relation|schema cache|does not exist/i.test(error.message) ? "Noch nicht eingerichtet: einmal das SQL für lern_antworten in Supabase ausführen. Deine Antwort bleibt hier gespeichert." : "Fehler: " + error.message; return; }
    const f = erledigt(); f.add(c.id); try { localStorage.setItem(ERLEDIGT, JSON.stringify([...f].slice(-300))); localStorage.removeItem(ENTWURF(c.id)); } catch (x) {}
    const tol = L.d?.toleranz || 1.5, re = L.d?.schaetzer?.reaktion || 0.4, n = L.tipps.filter(t => (c.marker || []).some(m => Math.abs(m.t - (t - re)) <= tol)).length;
    toast(L.tipps.length ? `Gespeichert · ${n} von ${L.tipps.length} Pointen getroffen` : "Gespeichert, der PC lernt beim nächsten Abgleich");
    v?.pause(); const o = offen(); laden((o[0] || c).id); seite($("#inhalt"));
  }
  addEventListener("resize", () => { if (ansicht === "lernen" && $("#lzZeit")) zeitNeu(); });
})();
