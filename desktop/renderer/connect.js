'use strict';

const form = document.getElementById('form');
const input = document.getElementById('url');
const msg = document.getElementById('msg');
const btn = document.getElementById('connect');
const recentWrap = document.getElementById('recentWrap');
const recentList = document.getElementById('recentList');
const langs = document.getElementById('langs');

// ── i18n ─────────────────────────────────────────────────────────────────────
const I18N = {
  en: {
    title: 'Connect to your VMS server',
    sub: 'Enter the address of your Kanagatly VMS. This is the HTTPS URL served by the on-site appliance.',
    label: 'Server URL',
    hint: 'Don’t know the address? Ask your administrator — it usually looks like https://your-vms-server:8443',
    connect: 'Connect',
    recent: 'Recent',
    foot: 'Self-signed certificates from the server you enter are trusted automatically.',
    invalidUrl: 'Enter a valid URL, e.g. https://your-vms-server:8443',
    connecting: 'Connecting…',
    connectFail: 'Could not connect.',
  },
  ru: {
    title: 'Подключение к VMS-серверу',
    sub: 'Введите адрес вашей Kanagatly VMS — это HTTPS-адрес сервера, установленного на объекте.',
    label: 'Адрес сервера',
    hint: 'Не знаете адрес? Спросите у администратора — обычно он выглядит так: https://your-vms-server:8443',
    connect: 'Подключиться',
    recent: 'Недавние',
    foot: 'Самоподписанные сертификаты указанного сервера доверяются автоматически.',
    invalidUrl: 'Введите корректный адрес, например https://your-vms-server:8443',
    connecting: 'Подключение…',
    connectFail: 'Не удалось подключиться.',
  },
  tk: {
    title: 'VMS serweriňize birikmek',
    sub: 'Kanagatly VMS-iň salgysyny giriziň — bu obýektdäki enjamyň HTTPS salgysy.',
    label: 'Serweriň salgysy',
    hint: 'Salgyny bilmeýärsiňizmi? Administratordan soraň — adatça şeýle bolýar: https://your-vms-server:8443',
    connect: 'Birikmek',
    recent: 'Soňkular',
    foot: 'Giren serweriňiziň öz-özüne gol çekilen şahadatnamalaryna awtomatiki ynanylýar.',
    invalidUrl: 'Dogry salgy giriziň, meselem https://your-vms-server:8443',
    connecting: 'Birikdirilýär…',
    connectFail: 'Birikip bolmady.',
  },
};

let lang = 'en';
try {
  const saved = localStorage.getItem('kanagatly-lang');
  if (saved && I18N[saved]) lang = saved;
} catch (_e) { /* localStorage may be unavailable — default to en */ }

const t = (k) => (I18N[lang] || I18N.en)[k];

function applyLang(next) {
  lang = I18N[next] ? next : 'en';
  try { localStorage.setItem('kanagatly-lang', lang); } catch (_e) { /* ignore */ }
  document.documentElement.lang = lang;
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  langs.querySelectorAll('button[data-lang]').forEach((b) => {
    b.classList.toggle('active', b.dataset.lang === lang);
  });
}

langs.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-lang]');
  if (b) applyLang(b.dataset.lang);
});

// ── connect flow ─────────────────────────────────────────────────────────────
function setMsg(text, ok) {
  msg.textContent = text || '';
  msg.classList.toggle('ok', !!ok);
}

async function refreshValidity() {
  const raw = input.value;
  if (!raw.trim()) {
    input.classList.remove('invalid');
    setMsg('');
    return false;
  }
  const { ok, url } = await window.vms.validate(raw);
  input.classList.toggle('invalid', !ok);
  setMsg(ok ? url : t('invalidUrl'), ok);
  return ok;
}

input.addEventListener('input', () => {
  refreshValidity();
});

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  btn.disabled = true;
  const ok = await refreshValidity();
  if (!ok) {
    btn.disabled = false;
    input.focus();
    return;
  }
  setMsg(t('connecting'), true);
  const res = await window.vms.connect(input.value);
  if (!res.ok) {
    setMsg(res.error || t('connectFail'), false);
    input.classList.add('invalid');
    btn.disabled = false;
  }
  // On success the main process swaps to the VMS window and closes this one.
});

async function init() {
  applyLang(lang);
  const state = await window.vms.getState();
  if (state.serverUrl) input.value = state.serverUrl;

  const recent = (state.recentServers || []).filter((u) => u !== state.serverUrl);
  if (recent.length) {
    recentWrap.hidden = false;
    for (const url of recent) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = url;
      b.addEventListener('click', () => {
        input.value = url;
        refreshValidity();
        input.focus();
      });
      recentList.appendChild(b);
    }
  }
  input.focus();
  input.select();
}

init();
