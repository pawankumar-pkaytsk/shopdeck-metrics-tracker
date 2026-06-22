// Build a clean, Babel-free static site into public/ for Vercel.
// Source of truth stays index.html (the self-unpacking bundle used by GitHub Pages).
// This pre-compiles every JSX chunk at build time and drops Babel standalone (~3.1MB).
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import babel from '@babel/core';

const SRC = 'index.html';
const OUT = 'public';
const ASSET_DIR = path.join(OUT, 'assets');

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(ASSET_DIR, { recursive: true });

const html = fs.readFileSync(SRC, 'utf8');
const manifest = JSON.parse(html.match(/<script type="__bundler\/manifest">([\s\S]*?)<\/script>/)[1]);
let template = JSON.parse(html.match(/<script type="__bundler\/template">("[\s\S]*?")<\/script>/)[1]);

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

// 1) inline JSX block -> main.js
template = template.replace(/<script type="text\/babel">([\s\S]*?)<\/script>/g, (_m, code) => {
  fs.writeFileSync(path.join(OUT, 'main.js'), compile(code));
  return '<script src="main.js"></script>';
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
