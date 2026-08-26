import {
  LiveLocationTracker,
  accuracyCircleGeoJson,
  shouldAnalyzeFix,
} from '/static/location-tracking.js';

const EMPTY_FEATURES = { type: 'FeatureCollection', features: [] };
const MAX_TRAIL_POINTS = 100;

const gate = document.getElementById('gate');
const unlockForm = document.getElementById('unlock');
const unlockButton = document.getElementById('unlock-button');
const passcodeInput = document.getElementById('passcode');
const gateError = document.getElementById('gate-error');
const appView = document.getElementById('app');
const panel = document.getElementById('panel');
const form = document.getElementById('ask');
const questionBox = document.getElementById('question');
const sendButton = document.getElementById('send');
const followButton = document.getElementById('follow-location');
const stopButton = document.getElementById('stop-location');
const describeButton = document.getElementById('describe-location');
const recenterButton = document.getElementById('recenter-location');
const locationStatus = document.getElementById('location-status');

let demoPasscode = '';
let map = null;
let marker = null;
let streamController = null;
let streamSerial = 0;
let sessionId = null;
let busy = false;
let trackingActive = false;
let followCamera = false;
let currentFix = null;
let lastAnalyzedFix = null;
let lastAnalyzedAt = 0;
let pendingAnalysis = null;
let trail = [];

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]
));
const scrollDown = () => { panel.scrollTop = panel.scrollHeight; };
const authHeaders = () => ({
  'Content-Type': 'application/json',
  'X-Demo-Passcode': demoPasscode,
});

function setBusy(value) {
  busy = value;
  const ready = sessionId !== null && !busy;
  questionBox.disabled = !ready;
  sendButton.disabled = !ready;
  questionBox.placeholder = sessionId === null
    ? 'Tap the map or follow your location to start…'
    : busy ? 'Agent is working…' : 'Ask about this place…';
}

function closeStream() {
  streamSerial += 1;
  if (streamController) streamController.abort();
  streamController = null;
}

function parseSseFrame(frame) {
  let event = 'message';
  const data = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  return { event, data: data.join('\n') };
}

async function openStream(path, body, handlers = {}) {
  closeStream();
  const serial = ++streamSerial;
  const controller = new AbortController();
  streamController = controller;
  setBusy(true);
  let started = false;
  let terminal = false;

  try {
    const response = await fetch(path, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch { /* response was not JSON */ }
      throw new Error(detail);
    }
    if (!response.body) throw new Error('This browser cannot read a streaming response.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      buffer = buffer.replaceAll('\r\n', '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (frame.trim()) {
          const parsed = parseSseFrame(frame);
          const payload = parsed.data ? JSON.parse(parsed.data) : {};
          if (parsed.event === 'step') {
            started = true;
            handlers.onStep?.(payload.label);
          } else if (parsed.event === 'delta') {
            started = true;
            handlers.onDelta?.(payload.text);
          } else if (parsed.event === 'report') {
            terminal = true;
            handlers.onReport?.(payload);
          } else if (parsed.event === 'done') {
            terminal = true;
            handlers.onDone?.(payload);
          } else if (parsed.event === 'failed') {
            throw new Error(payload.detail || 'The agent request failed.');
          }
        }
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }

    if (!terminal) throw new Error(started
      ? 'The connection ended before the agent finished.'
      : 'The agent did not start.');
    return true;
  } catch (error) {
    if (error.name === 'AbortError') return false;
    handlers.onFailure?.(error);
    showError(error.message);
    return false;
  } finally {
    if (serial === streamSerial) {
      streamController = null;
      setBusy(false);
      flushLocationAnalysis();
    }
  }
}

async function unlock(passcode) {
  demoPasscode = passcode;
  gateError.textContent = '';
  unlockButton.disabled = true;
  unlockButton.textContent = 'Unlocking…';
  try {
    const response = await fetch('/api/map-config', { headers: { 'X-Demo-Passcode': demoPasscode } });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || response.statusText);
    gate.hidden = true;
    appView.hidden = false;
    initMap(payload);
  } catch (error) {
    demoPasscode = '';
    gateError.textContent = error.message;
    passcodeInput.focus();
  } finally {
    unlockButton.disabled = false;
    unlockButton.textContent = 'Unlock map';
  }
}

unlockForm.addEventListener('submit', (event) => {
  event.preventDefault();
  unlock(passcodeInput.value);
});

function initMap(config) {
  if (map) return;
  const styleUrl = `https://maps.geo.${config.region}.amazonaws.com/v2/styles/`
    + `${config.style}/descriptor?key=${config.api_key}&color-scheme=${config.color_scheme}`;
  map = new maplibregl.Map({
    container: 'map',
    style: styleUrl,
    center: [-73.9665, 40.7812],
    zoom: 13,
    validateStyle: false,
  });
  map.addControl(new maplibregl.NavigationControl(), 'top-right');
  map.on('load', () => {
    ensureLocationLayers();
    map.resize();
  });
  map.on('click', onMapClick);
  map.on('dragstart', () => {
    if (!trackingActive) return;
    followCamera = false;
    recenterButton.hidden = false;
  });
  map.on('error', (event) => console.error('map error', event?.error));
  followButton.disabled = false;
}

function ensureLocationLayers() {
  if (!map?.isStyleLoaded()) return false;
  if (!map.getSource('device-accuracy')) {
    map.addSource('device-accuracy', { type: 'geojson', data: EMPTY_FEATURES });
    map.addLayer({
      id: 'device-accuracy-fill',
      type: 'fill',
      source: 'device-accuracy',
      paint: { 'fill-color': '#7fd1b9', 'fill-opacity': 0.16 },
    });
  }
  if (!map.getSource('device-trail')) {
    map.addSource('device-trail', { type: 'geojson', data: EMPTY_FEATURES });
    map.addLayer({
      id: 'device-trail-line',
      type: 'line',
      source: 'device-trail',
      paint: { 'line-color': '#7fd1b9', 'line-width': 3, 'line-opacity': 0.75 },
    });
  }
  return true;
}

function clearLocationLayers() {
  if (!ensureLocationLayers()) return;
  map.getSource('device-accuracy').setData(EMPTY_FEATURES);
  map.getSource('device-trail').setData(EMPTY_FEATURES);
}

function updateLocationVisual(fix) {
  if (!ensureLocationLayers()) return;
  if (!marker || !marker.getElement().classList.contains('device-marker')) {
    marker?.remove();
    const element = document.createElement('div');
    element.className = 'device-marker';
    element.setAttribute('aria-label', 'Your current location');
    marker = new maplibregl.Marker({ element, anchor: 'center' }).setLngLat([fix.longitude, fix.latitude]).addTo(map);
  } else {
    marker.setLngLat([fix.longitude, fix.latitude]);
  }

  trail.push([fix.longitude, fix.latitude]);
  trail = trail.slice(-MAX_TRAIL_POINTS);
  map.getSource('device-accuracy').setData(accuracyCircleGeoJson(fix));
  map.getSource('device-trail').setData(trail.length > 1 ? {
    type: 'Feature',
    properties: {},
    geometry: { type: 'LineString', coordinates: trail },
  } : EMPTY_FEATURES);

  if (followCamera) {
    map.easeTo({ center: [fix.longitude, fix.latitude], zoom: Math.max(map.getZoom(), 15), duration: 500 });
  }
}

function setLocationStatus(message, tone = '') {
  locationStatus.textContent = message;
  locationStatus.dataset.tone = tone;
}

function updateTrackingButtons() {
  followButton.hidden = trackingActive;
  stopButton.hidden = !trackingActive;
  describeButton.disabled = !currentFix;
  recenterButton.hidden = !trackingActive || followCamera;
}

const locationTracker = new LiveLocationTracker({
  onStatus(status) {
    if (status === 'requesting') setLocationStatus('Requesting location…');
    if (status === 'stopped') setLocationStatus('Tracking paused');
  },
  onFix(fix) {
    trackingActive = true;
    currentFix = fix;
    setLocationStatus(`Live · ±${Math.round(fix.accuracy)} m`, fix.accuracy <= 75 ? 'good' : 'poor');
    updateTrackingButtons();
    updateLocationVisual(fix);
    queueLocationAnalysis(fix);
  },
  onError(detail) {
    setLocationStatus(detail.message, 'error');
    if (detail.fatal) trackingActive = false;
    updateTrackingButtons();
  },
});

function startTracking() {
  closeStream();
  trackingActive = true;
  followCamera = true;
  currentFix = null;
  lastAnalyzedFix = null;
  lastAnalyzedAt = 0;
  pendingAnalysis = null;
  trail = [];
  sessionId = null;
  panel.replaceChildren();
  questionBox.value = '';
  marker?.remove();
  marker = null;
  clearLocationLayers();
  updateTrackingButtons();
  if (!locationTracker.start()) {
    trackingActive = false;
    updateTrackingButtons();
  }
}

function stopTracking({ clearCurrent = false } = {}) {
  locationTracker.stop();
  trackingActive = false;
  followCamera = false;
  pendingAnalysis = null;
  if (clearCurrent) {
    currentFix = null;
    trail = [];
    marker?.remove();
    marker = null;
    clearLocationLayers();
  }
  updateTrackingButtons();
}

function queueLocationAnalysis(fix, { force = false } = {}) {
  if (!trackingActive && !force) return;
  if (!shouldAnalyzeFix(fix, lastAnalyzedFix, {
    now: Date.now(),
    lastAnalyzedAt,
    force,
  })) return;
  pendingAnalysis = { fix, force };
  flushLocationAnalysis();
}

async function flushLocationAnalysis() {
  if (busy || !trackingActive || !pendingAnalysis) return;
  const { fix, force } = pendingAnalysis;
  if (!shouldAnalyzeFix(fix, lastAnalyzedFix, {
    now: Date.now(),
    lastAnalyzedAt,
    force,
  })) {
    pendingAnalysis = null;
    return;
  }
  pendingAnalysis = null;
  lastAnalyzedFix = fix;
  lastAnalyzedAt = Date.now();
  await describeCoordinates(fix.latitude, fix.longitude, { live: true });
}

async function describeCoordinates(latitude, longitude, { live = false } = {}) {
  sessionId = null;
  questionBox.value = '';
  if (!live) panel.replaceChildren();
  const progress = addProgressCard(
    live
      ? `Updating your location · ±${Math.round(currentFix?.accuracy || 0)} m`
      : `Working on ${latitude.toFixed(4)}, ${longitude.toFixed(4)}`,
  );
  return openStream('/api/describe/stream', { latitude, longitude }, {
    onStep: progress.step,
    onReport: ({ session_id: newSessionId, report }) => {
      sessionId = newSessionId;
      panel.replaceChildren();
      addReportCard(report);
      questionBox.focus();
    },
    onFailure: () => progress.card.remove(),
  });
}

function onMapClick(event) {
  stopTracking({ clearCurrent: true });
  closeStream();
  const { lat, lng } = event.lngLat;
  sessionId = null;
  panel.replaceChildren();
  questionBox.value = '';
  marker?.remove();
  marker = new maplibregl.Marker({ color: '#6cb2ff' }).setLngLat([lng, lat]).addTo(map);
  setLocationStatus('Explore mode');
  describeCoordinates(lat, lng);
}

followButton.addEventListener('click', startTracking);
stopButton.addEventListener('click', () => stopTracking());
describeButton.addEventListener('click', () => {
  if (currentFix) queueLocationAnalysis(currentFix, { force: true });
});
recenterButton.addEventListener('click', () => {
  if (!currentFix) return;
  followCamera = true;
  recenterButton.hidden = true;
  map.easeTo({ center: [currentFix.longitude, currentFix.latitude], zoom: Math.max(map.getZoom(), 15), duration: 500 });
});

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const question = questionBox.value.trim();
  if (!question || !sessionId || busy) return;
  questionBox.value = '';
  addYourTurn(question);
  const turn = addAgentTurn();
  openStream('/api/ask/stream', { session_id: sessionId, question }, {
    onStep: turn.step,
    onDelta: turn.text,
    onDone: () => { turn.finish(); questionBox.focus(); },
  });
});

questionBox.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function addProgressCard(title) {
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `<p class="eyebrow">${esc(title)}</p><ul class="steps"><li class="active"><span class="dot"></span>Waking the agent up</li></ul>`;
  panel.append(card);
  scrollDown();
  const list = card.querySelector('.steps');
  const labels = [];
  return {
    card,
    step(label) {
      labels.push(label);
      list.innerHTML = labels.map((text, index) => `<li class="${index === labels.length - 1 ? 'active' : 'done'}"><span class="dot"></span>${esc(text)}</li>`).join('');
      scrollDown();
    },
  };
}

function addReportCard(report) {
  const link = report.link
    ? `<a href="${esc(report.link)}" target="_blank" rel="noopener noreferrer">${esc(report.link_title || report.link)} &rarr;</a>`
    : '<span class="hint">No relevant link found.</span>';
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `<p class="label">${esc(report.label)}</p><p class="summary">${esc(report.summary)}</p><p class="coords">${report.latitude.toFixed(5)}, ${report.longitude.toFixed(5)}</p>${link}`;
  panel.append(card);
  scrollDown();
  marker?.setPopup(new maplibregl.Popup({ offset: 28 }).setText(report.label)).togglePopup();
}

function addYourTurn(question) {
  const row = document.createElement('div');
  row.className = 'you-row';
  const bubble = document.createElement('div');
  bubble.className = 'bubble you';
  bubble.textContent = question;
  row.append(bubble);
  panel.append(row);
  scrollDown();
}

function addAgentTurn() {
  const turn = document.createElement('div');
  turn.className = 'turn';
  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = 'Agent';
  turn.append(who);
  const waiting = document.createElement('div');
  waiting.className = 'bubble agent';
  turn.append(waiting);
  panel.append(turn);
  scrollDown();
  let bubble = null;
  let spinner = null;
  const settle = () => { spinner?.classList.replace('active', 'done'); spinner = null; };
  return {
    step(label) {
      waiting.remove();
      settle();
      bubble = null;
      const list = document.createElement('ul');
      list.className = 'steps';
      list.innerHTML = `<li class="active"><span class="dot"></span>${esc(label)}</li>`;
      spinner = list.firstElementChild;
      turn.append(list);
      scrollDown();
    },
    text(chunk) {
      waiting.remove();
      settle();
      if (!bubble) {
        bubble = document.createElement('div');
        bubble.className = 'bubble agent';
        turn.append(bubble);
      }
      bubble.textContent += chunk;
      scrollDown();
    },
    finish() { waiting.remove(); settle(); },
  };
}

function showError(message) {
  const card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = `<p class="error">${esc(message)}</p>`;
  panel.append(card);
  scrollDown();
}

document.addEventListener('visibilitychange', () => {
  if (!trackingActive) return;
  if (document.hidden) {
    setLocationStatus('This tab is in the background · location updates may pause', 'poor');
  } else if (currentFix) {
    setLocationStatus(`Live · ±${Math.round(currentFix.accuracy)} m`, currentFix.accuracy <= 75 ? 'good' : 'poor');
  } else {
    setLocationStatus('Requesting location…');
  }
});

passcodeInput.focus();
