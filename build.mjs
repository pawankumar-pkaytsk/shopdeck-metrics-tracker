// Build a clean, Babel-free static site into public/ for Vercel.
// Source of truth stays index.html (the self-unpacking bundle used by GitHub Pages).
// This pre-compiles every JSX chunk at build time and drops Babel standalone (~3.1MB).
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import babel from '@babel/core';

const SRC = 'index.html';
const OUT = 'public';
const ASSET_DIR = path.join(OUT, 'assets');

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(ASSET_DIR, { recursive: true });

const html = fs.readFileSync(SRC, 'utf8');
const manifest = JSON.parse(html.match(/<script type="__bundler\/manifest">([\s\S]*?)<\/script>/)[1]);
let template = JSON.parse(html.match(/<script type="__bundler\/template">("[\s\S]*?")<\/script>/)[1]);

// --- Vercel-only transforms (source stays Google-based for GitHub Pages) ---
// 1) Replace the Google SheetsAPI with a JSON-backed shim (reads build-time sheet JSON).
{
  const s = template.indexOf('window.SheetsAPI = (function');
  if (s >= 0) {
    let i = template.indexOf('{', s), depth = 0, close = -1;
    for (let j = i; j < template.length; j++) {
      if (template[j] === '{') depth++;
      else if (template[j] === '}') { if (--depth === 0) { close = j; break; } }
    }
    const end = close + 5; // include })();
    const shim = `window.SheetsAPI = (function () {
  var cfg = window.SHEETS_CONFIG || {};
  /* Access allowlist for the Google sign-in gate. Any @ domain below, or any explicit email, may enter. */
  var ALLOW_DOMAINS = ['blitzscale.co', 'shopdeck.com'];
  var ALLOW_EMAILS = [];
  var KEY = 'hits_auth_email';
  var stored = null; try { stored = sessionStorage.getItem(KEY); } catch (e) {}
  var jc = {};
  function load(f) { if (jc[f]) return jc[f]; jc[f] = fetch(f, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error(f + ' not generated'); return r.json(); }).then(function (j) { return j.values || []; }); return jc[f]; }
  var FILE = { '1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM': 'daily_plan.json', '1ZLOcj648aYvVaEGHX_QHB1Qx3OMUT3K_eeW-SBUbCso': 'handover.json', '1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk': 'escalation.json' };
  function s2iso(s) { return new Date(Math.round((s - 25569) * 86400000)).toISOString().slice(0, 10); }
  function toISO(v) { if (v === null || v === undefined || v === '') return null; if (typeof v === 'number') return s2iso(v); var d = new Date(v); return isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10); }
  function pr(r) { return { date: toISO(r[0]), gc: r[1] != null ? String(r[1]).trim() : '', gm: r[2] != null ? String(r[2]).trim() : '', assigned: Number(r[3]) || 0, live: Number(r[5]) || 0, spending: Number(r[7]) || 0 }; }
  function allowed(email) { email = (email || '').toLowerCase(); if (!email) return false; if (ALLOW_EMAILS.indexOf(email) >= 0) return true; var dom = email.split('@')[1] || ''; return ALLOW_DOMAINS.indexOf(dom) >= 0; }
  function waitForGIS() { return new Promise(function (res, rej) { var t = 0; (function c() { if (window.google && google.accounts && google.accounts.oauth2) return res(); if (t++ > 60) return rej(new Error('Google sign-in failed to load')); setTimeout(c, 100); })(); }); }
  return {
    signIn: function () { return waitForGIS().then(function () { return new Promise(function (resolve, reject) {
      var tc = google.accounts.oauth2.initTokenClient({ client_id: cfg.clientId, scope: 'openid email profile', callback: function (resp) {
        if (resp.error) { reject(new Error(resp.error)); return; }
        fetch('https://www.googleapis.com/oauth2/v3/userinfo', { headers: { Authorization: 'Bearer ' + resp.access_token } }).then(function (r) { return r.ok ? r.json() : null; }).then(function (u) {
          var email = (u && u.email) || '';
          if (allowed(email)) { try { sessionStorage.setItem(KEY, email); } catch (e) {} stored = email; resolve(email); }
          else { reject(new Error('Access denied for ' + (email || 'this account') + '. Ask an admin to allowlist you.')); }
        }).catch(function () { reject(new Error('Could not verify your Google account')); });
      }, error_callback: function (err) { reject(new Error((err && err.type) || 'sign_in_cancelled')); } });
      tc.requestAccessToken({ prompt: '' });
    }); }); },
    isSignedIn: function () { return !!stored; },
    getUser: function () { return stored ? { email: stored, name: stored } : null; },
    signOut: function () { try { sessionStorage.removeItem(KEY); } catch (e) {} stored = null; },
    getRows: function (start, end) { return load('spendinputs.json').then(function (vals) { return vals.map(pr).filter(function (row) { if (!row.date) return false; if (start && row.date < start) return false; if (end && row.date > end) return false; return true; }); }); },
    refresh: function (start, end) { jc = {}; return this.getRows(start, end); },
    getValues: function (id) { var f = FILE[id]; return f ? load(f) : Promise.resolve([]); }
  };
})();`;
    template = template.slice(0, s) + shim + template.slice(end);
  }
}
// 2) Google sign-in gate: start unauthed (gate shows), but stay signed in for the session.
template = template.replace('const [authed, setAuthed] = useState(false)', 'const [authed, setAuthed] = useState(window.SheetsAPI && window.SheetsAPI.isSignedIn ? window.SheetsAPI.isSignedIn() : false)');

// decode + gunzip every manifest resource
const res = {};
for (const [uuid, e] of Object.entries(manifest)) {
  let buf = Buffer.from(e.data, 'base64');
  if (e.compressed) buf = zlib.gunzipSync(buf);
  res[uuid] = { mime: e.mime, buf };
}

const EXT = { 'text/javascript': 'js', 'application/javascript': 'js', 'image/svg+xml': 'svg', 'font/ttf': 'ttf', 'font/woff2': 'woff2', 'font/woff': 'woff' };
const isBabel = (u) => res[u].buf.length > 2_000_000 && res[u].buf.includes('transformScriptTags');
const compile = (code) => babel.transformSync(code, { presets: [['@babel/preset-react', { runtime: 'classic' }]], compact: false, comments: false, sourceType: 'script' }).code;

let nBabelChunks = 0, nPlain = 0, droppedBabel = false;

// 1) inline JSX block -> main.js (content-hash version query so browsers always fetch fresh JS)
template = template.replace(/<script type="text\/babel">([\s\S]*?)<\/script>/g, (_m, code) => {
  const compiled = compile(code);
  fs.writeFileSync(path.join(OUT, 'main.js'), compiled);
  const v = crypto.createHash('md5').update(compiled).digest('hex').slice(0, 10);
  return '<script src="main.js?v=' + v + '"></script>';
});

// 2) external JSX chunks <script type="text/babel" src="UUID"></script> -> compiled .js
template = template.replace(/<script\b[^>]*type="text\/babel"[^>]*src="([0-9a-f-]{36})"[^>]*><\/script>/g, (_m, uuid) => {
  const js = compile(res[uuid].buf.toString('utf8'));
  fs.writeFileSync(path.join(OUT, uuid + '.js'), js);
  nBabelChunks++;
  return `<script src="${uuid}.js"></script>`;
});

// 3) plain JS resources (React, ReactDOM, Babel, component chunk) -> keep, except drop Babel
template = template.replace(/<script\b([^>]*)\ssrc="([0-9a-f-]{36})"([^>]*)><\/script>/g, (m, _pre, uuid, _post) => {
  if (!res[uuid]) return m;
  if (isBabel(uuid)) { droppedBabel = true; return ''; }
  fs.writeFileSync(path.join(OUT, uuid + '.js'), res[uuid].buf);
  nPlain++;
  return `<script src="${uuid}.js"></script>`;
});

// 4) remaining resource UUIDs (fonts / images only) -> files under assets/
//    (JS resources were already emitted above; skip them so we don't re-match their "UUID.js" refs)
for (const [uuid, e] of Object.entries(res)) {
  if (e.mime.includes('javascript')) continue;
  if (template.includes(uuid)) {
    const f = 'assets/' + uuid + '.' + (EXT[e.mime] || 'bin');
    fs.writeFileSync(path.join(OUT, f), e.buf);
    template = template.split(uuid).join(f);
  }
}

// SRI/crossorigin make no sense for same-origin static files
template = template.replace(/\s+integrity="[^"]*"/gi, '').replace(/\s+crossorigin="[^"]*"/gi, '');

fs.writeFileSync(path.join(OUT, 'index.html'), template);

// copy data JSON next to index.html (the app fetches them by relative path)
for (const f of fs.readdirSync('.')) if (f.endsWith('.json') && f !== 'package.json' && f !== 'package-lock.json') fs.copyFileSync(f, path.join(OUT, f));

console.log(`[build] public/ ready · ${nPlain} JS resources, ${nBabelChunks} compiled JSX chunks, main.js · Babel dropped: ${droppedBabel}`);

// Fetch the Google Sheets at build time via the service account (GOOGLE_SA_KEY env) so they
// ship as static JSON — the frontend needs no browser Google login.
if (process.env.GOOGLE_SA_KEY) {
  try {
    const sa = JSON.parse(process.env.GOOGLE_SA_KEY);
    const b64 = (b) => Buffer.from(b).toString('base64url');
    const now = Math.floor(Date.now() / 1000);
    const head = b64(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const claim = b64(JSON.stringify({ iss: sa.client_email, scope: 'https://www.googleapis.com/auth/spreadsheets.readonly', aud: 'https://oauth2.googleapis.com/token', iat: now, exp: now + 3600 }));
    const sig = crypto.createSign('RSA-SHA256').update(head + '.' + claim).sign(sa.private_key).toString('base64url');
    const tr = await fetch('https://oauth2.googleapis.com/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: 'grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer&assertion=' + head + '.' + claim + '.' + sig });
    const tok = (await tr.json()).access_token;
    const SH = [
      ['spendinputs.json', '1wwfbMVkMKq80Znq1mkpO-NCLI-fc7d2hPIepCp04bQ0', 'A2:H', 'SERIAL_NUMBER'],
      ['daily_plan.json', '1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM', "'Daily Plan'!A:AK", 'FORMATTED_STRING'],
      ['handover.json', '1ZLOcj648aYvVaEGHX_QHB1Qx3OMUT3K_eeW-SBUbCso', "'handover'!A:J", 'FORMATTED_STRING'],
      ['escalation.json', '1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk', "'Raw_Suggested'!A:P", 'FORMATTED_STRING'],
    ];
    for (const [out, sid, range, dr] of SH) {
      const u = `https://sheets.googleapis.com/v4/spreadsheets/${sid}/values/${encodeURIComponent(range)}?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption=${dr}`;
      const r = await fetch(u, { headers: { Authorization: 'Bearer ' + tok } });
      const vals = (await r.json()).values || [];
      const trimmed = vals.length ? [vals[0], ...vals.slice(1).filter((row) => row.some((c) => String(c).trim()))] : [];
      fs.writeFileSync(path.join(OUT, out), JSON.stringify({ generatedAt: new Date().toISOString(), range, values: trimmed }));
      console.log(`[build] sheet ${out}: ${trimmed.length} rows`);
    }
  } catch (e) {
    console.error('[build] sheet fetch failed:', e.message);
  }
} else {
  console.log('[build] GOOGLE_SA_KEY not set — sheet-backed views will be empty');
}
