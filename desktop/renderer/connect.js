'use strict';

const form = document.getElementById('form');
const input = document.getElementById('url');
const msg = document.getElementById('msg');
const btn = document.getElementById('connect');
const recentWrap = document.getElementById('recentWrap');
const recentList = document.getElementById('recentList');

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
  setMsg(ok ? url : 'Enter a valid URL, e.g. https://10.10.1.152:8443', ok);
  return ok;
}

input.addEventListener('input', () => {
  // Debounce-light: validate on each input; cheap round-trip.
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
  setMsg('Connecting…', true);
  const res = await window.vms.connect(input.value);
  if (!res.ok) {
    setMsg(res.error || 'Could not connect.', false);
    input.classList.add('invalid');
    btn.disabled = false;
  }
  // On success the main process swaps to the VMS window and closes this one.
});

async function init() {
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
