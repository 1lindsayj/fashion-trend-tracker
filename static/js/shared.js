/* ════════════════════════════════════════════════════════════════════════════
   static/js/shared.js   (new)   
   Code that used to be copy-pasted into index.html AND today.html:
   loading overlay, clock, learn-more + settings modals, theme, language,
   reduce-motion, text size, saved preferences, Escape key, HTML escaping.
   Every function is null-safe, so pages that lack a modal (e.g. compare.html) still work.

   Page hooks (optional, define them in the page's own <script>):
     onThemeApplied()  — called after the theme changes (re-draw charts here)
     onEscape()        — called when Escape is pressed (close page-specific modals here)
   ════════════════════════════════════════════════════════════════════════════ */

let _reduceMotion = false;          // pages read this to turn chart animation off
const BASE_FONT_PCT = 112.5;        // [new - accessibility] root size 18px: .55–.7rem text was ~9–11px

// ── HTML ESCAPE ──────────────────────────────────────────────────────────────
function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── LOADING OVERLAY ──────────────────────────────────────────────────────────
// [new] was a fixed 5-second wait on every page load. Now: first visit in a session only, 1.2s,
//      and skipped entirely for people whose OS asks for reduced motion.
(function initOverlay() {
  const ov = document.getElementById('loading-overlay');
  if (!ov) return;
  const prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced || sessionStorage.getItem('ts_seen_intro')) { ov.style.display = 'none'; return; }
  sessionStorage.setItem('ts_seen_intro', '1');
  window.addEventListener('load', () => setTimeout(() => {
    ov.classList.add('hidden');
    setTimeout(() => { ov.style.display = 'none'; }, 650);
  }, 1200));
})();

// ── LIVE CLOCK (nav on home, header date on today) ───────────────────────────
function updateClock() {
  const now = new Date();
  const nav = document.getElementById('live-datetime');
  if (nav) nav.textContent = now.toLocaleString('en-US', { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).toLowerCase();
  const hdr = document.getElementById('header-date');
  if (hdr) hdr.textContent = now.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' }).toLowerCase();
}
updateClock(); setInterval(updateClock, 60000);

// ── MODALS ───────────────────────────────────────────────────────────────────
function _openModal(id)  { const el = document.getElementById(id); if (el) { el.classList.add('open'); document.body.style.overflow = 'hidden'; } }
function _closeModal(id, e) {
  const el = document.getElementById(id);
  if (!el || (e && e.target !== el)) return;           // clicks inside the dialog shouldn't close it
  el.classList.remove('open'); document.body.style.overflow = '';
}
function openLearnMore()      { _openModal('learn-modal-overlay'); }
function closeLearnMore(e)    { _closeModal('learn-modal-overlay', e); }
function openSettings()       { _openModal('settings-modal-overlay'); }
function closeSettings(e)     { _closeModal('settings-modal-overlay', e); }

// ── THEME ────────────────────────────────────────────────────────────────────
function setTheme(t) {
  localStorage.setItem('ts_theme', t);
  const l = document.getElementById('theme-light'), d = document.getElementById('theme-dark');
  if (l) l.classList.toggle('active', t === 'light');
  if (d) d.classList.toggle('active', t === 'dark');
  applyTheme(t);
}
function applyTheme(t) {
  const root = document.documentElement.style, dark = t === 'dark';
  root.setProperty('--white',  dark ? '#0f0f0f' : '#fff');
  root.setProperty('--black',  dark ? '#f0f0f0' : '#000');
  root.setProperty('--bg',     dark ? '#1a1a1a' : '#f7f7f7');
  root.setProperty('--border', dark ? '#333'    : '#ddd');
  root.setProperty('--muted',  dark ? '#999'    : '#666');
  // [new] teal TEXT needs a darker shade on white (4.5:1+), but the bright teal reads fine on dark
  root.setProperty('--teal-text', dark ? '#00BFA6' : '#00695C');
  document.body.style.background = dark ? '#0f0f0f' : '#fff';
  document.body.style.color      = dark ? '#f0f0f0' : '#000';
  if (typeof onThemeApplied === 'function') onThemeApplied();
}

// ── LANGUAGE (Google Translate widget) ───────────────────────────────────────
function setLanguage(lang) {
  localStorage.setItem('ts_lang', lang);
  if (lang === 'en') { const el = document.getElementById('google-translate-el'); if (el) el.remove(); return; }
  if (!document.getElementById('google-translate-el')) {
    const div = document.createElement('div'); div.id = 'google-translate-el'; div.style.display = 'none'; document.body.appendChild(div);
  }
  if (!document.getElementById('gt-script')) {
    window.gtInit = function () {
      new google.translate.TranslateElement({ pageLanguage: 'en', includedLanguages: lang, autoDisplay: true }, 'google-translate-el');
    };
    const s = document.createElement('script'); s.id = 'gt-script';
    s.src = 'https://translate.google.com/translate_a/element.js?cb=gtInit';   // https:// (was protocol-relative)
    document.head.appendChild(s);
  }
}

// ── REDUCE MOTION ────────────────────────────────────────────────────────────
function toggleReduceMotion() {
  _reduceMotion = !_reduceMotion;
  localStorage.setItem('ts_reduce_motion', _reduceMotion);
  const b = document.getElementById('reduce-motion-btn'); if (b) b.classList.toggle('active', _reduceMotion);
  applyReduceMotion(_reduceMotion);
}
function applyReduceMotion(on) {
  let tag = document.getElementById('ts-reduce-motion-style');
  if (!tag) { tag = document.createElement('style'); tag.id = 'ts-reduce-motion-style'; document.head.appendChild(tag); }
  tag.textContent = on ? '*, *::before, *::after { animation-duration: 0.001s !important; transition-duration: 0.001s !important; }' : '';
}

// ── TEXT SIZE (slider is a multiplier of the new 112.5% base) ────────────────
function setFontSize(val) {
  document.documentElement.style.fontSize = (BASE_FONT_PCT * Number(val) / 100) + '%';
  const el = document.getElementById('font-size-val'); if (el) el.textContent = val + '%';
  localStorage.setItem('ts_font_size', val);
}

// ── SAVED PREFERENCES ────────────────────────────────────────────────────────
function loadSavedPrefs() {
  const theme = localStorage.getItem('ts_theme') || 'light';
  const l = document.getElementById('theme-light'), d = document.getElementById('theme-dark');
  if (l) l.classList.toggle('active', theme === 'light');
  if (d) d.classList.toggle('active', theme === 'dark');
  applyTheme(theme);

  const fs = localStorage.getItem('ts_font_size') || '100';
  const rng = document.getElementById('font-size-range'); if (rng) rng.value = fs;
  setFontSize(fs);

  // [new] respect the OS-level "reduce motion" setting unless the person chose otherwise
  const saved = localStorage.getItem('ts_reduce_motion');
  const osPref = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  _reduceMotion = saved === null ? osPref : saved === 'true';
  const rmBtn = document.getElementById('reduce-motion-btn'); if (rmBtn) rmBtn.classList.toggle('active', _reduceMotion);
  if (_reduceMotion) applyReduceMotion(true);

  const lang = localStorage.getItem('ts_lang');
  if (lang) {
    const sel = document.getElementById('lang-select'); if (sel) sel.value = lang;
    if (lang !== 'en') setLanguage(lang);
  }
}
window.addEventListener('load', loadSavedPrefs);

// ── ESCAPE KEY ───────────────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  ['learn-modal-overlay', 'settings-modal-overlay'].forEach(id => { const el = document.getElementById(id); if (el) el.classList.remove('open'); });
  if (typeof onEscape === 'function') onEscape();
  document.body.style.overflow = '';
});