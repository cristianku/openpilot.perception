// perception-web: the page. Vanilla JS, no libraries; the DOM is built with textContent only.
// The look and the helpers come from openpilot.distill's distill-web (same author, MIT).
(function () {
  'use strict';

  // Panels, and the bar that opens them: Home, Radar, Objects and Lab (Lab spans the experiments and the
  // results); Settings opens from the gear at the bottom of the screen.
  const PANELS = ['overview', 'radar', 'objects', 'experiments', 'results', 'settings'];
  const BAR = ['overview', 'radar', 'objects', 'lab'];
  const BAR_HASH = {overview: 'overview', radar: 'radar', objects: 'objects', lab: 'experiments'};
  const ROUTE_RE = /^(?:[0-9a-f]{18}|[0-9a-f]{8}--[0-9a-f]{10})$/;
  const RESULT_RE = /^[0-9]{8}-[0-9]{6}-[a-z_]+$/;
  const JOB_PILL = {running: 'run', ok: 'ok', failed: 'bad', stopped: 'todo', unknown: 'muted'};
  const KIND_LABEL = {geometry_audit: 'Geometry audit', mono_baseline: 'Depth baseline'};
  const KIND_EXPLAIN = {
    geometry_audit: 'Looks at the same points in three frames a tenth of a second apart and checks that the car\'s '
      + 'own motion, as the localizer measured it, moves them where the camera sees them. A multi-frame network '
      + 'can only learn distance from motion when this holds.',
    mono_baseline: 'Runs Depth Anything V2 Small, a ready-made depth model, frozen, on single frames, and compares its '
      + 'distances with points measured from the car\'s motion. This is the score the future network has to beat.'};
  // How often each kind of data is read again while its tab is open (0: only when the tab opens or a job ends).
  const LOAD_PERIOD = {results: 30000, settings: 30000, auth: 30000, drive: 0};
  const STATUS_PERIOD = 4000;
  const RADAR_ACTIONS = ['radar_index', 'fetch_recordings'];
  // The server's limits (web/radar.py, web/settings.py, web/actions.py).
  const TAPE_RANGE_M = [0.3, 100];
  const OFFSET_RANGE_M = [0, 5];
  const CORRECTION_RANGE_M = [-1, 1];
  const READING_STEP_M = 0.25;
  const DISAGREE_M = 0.5;
  const FAR_TEST_M = 5;
  // A standstill counts from 2 s still (perception.radar.series STANDSTILL_MIN_S).
  const STANDSTILL_MIN_S = 2;
  const MAX_FETCH = 6;
  const SHORT_SEGMENTS = 2;
  const COMMA_EVERY_MS = 30000;
  const MINUS = '−';
  // Between a number and its unit, so "2.25 m" never breaks over two lines.
  const NBSP = '\u00a0';

  let status = null;
  let firstCode = null;
  let currentTab = 'overview';
  let hashBeforeSettings = 'overview';
  const data = {results: null, settings: null, auth: null, radar: null, radarError: false};
  // The readings of one drive or recording (GET /api/radar/drives/<route>), by route: {detail} or {missing}.
  const details = {};
  const detailLoads = {};
  const loadedAt = {};
  let chosenResult = null;
  let radarView = 'list';
  let radarRoute = null;
  let settingsAnchor = null;
  let anchorDone = false;
  let previousRunning = '';
  let lastCalibration = null;
  // +1 whenever a job that reads or brings radar ends: the drive details read before it are read again.
  let radarEpoch = 0;
  let previousRadarJobs = '';
  let log = {id: null, offset: 0, timer: null};

  // -- small helpers ---------------------------------------------------------------------------
  const byId = (id) => document.getElementById(id);
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svgNode(tag, attributes, text) {
    const element = document.createElementNS(SVG_NS, tag);
    for (const key of Object.keys(attributes || {})) element.setAttribute(key, attributes[key]);
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }
  function pill(label, kind) { return node('span', 'pill ' + kind, label); }
  function button(label, className, action) {
    const element = node('button', 'btn' + (className ? ' ' + className : ''), label);
    element.type = 'button';
    if (action) element.addEventListener('click', action);
    return element;
  }
  function linkButton(label, action, className) {
    const element = node('button', 'link-btn' + (className ? ' ' + className : ''), label);
    element.type = 'button';
    if (action) element.addEventListener('click', action);
    return element;
  }
  function art(name, className) {
    const template = byId('art-' + name);
    const picture = template.content.firstElementChild.cloneNode(true);
    if (className) picture.classList.add(className);
    return picture;
  }
  function checkMarkSvg() {
    const mark = node('span', 'choice-check');
    mark.setAttribute('aria-hidden', 'true');
    const svg = svgNode('svg', {viewBox: '0 0 20 20'});
    svg.appendChild(svgNode('path', {d: 'm5 10.5 3.2 3L15 6.5', stroke: '#fff', 'stroke-width': '2.4', 'stroke-linecap': 'round',
      'stroke-linejoin': 'round', fill: 'none'}));
    mark.appendChild(svg);
    return mark;
  }
  function setBar(id, value, kind) {
    const bar = byId(id);
    bar.style.width = Math.max(0, Math.min(100, value || 0)) + '%';
    bar.parentElement.className = 'bar' + (kind ? ' ' + kind : '');
  }
  function barNode(value, kind) {
    const bar = node('div', 'bar' + (kind ? ' ' + kind : ''));
    bar.setAttribute('aria-hidden', 'true');
    const fill = node('span');
    fill.style.width = Math.max(0, Math.min(100, value || 0)) + '%';
    bar.appendChild(fill);
    return bar;
  }
  function num(value, digits) {
    if (value === null || value === undefined || !isFinite(value)) return '–';
    return Number(value).toFixed(digits === undefined ? 2 : digits);
  }
  function percent(value) {
    if (value === null || value === undefined || !isFinite(value)) return '–';
    return Math.round(value * 100) + '%';
  }
  function round2(value) { return Math.round(value * 100) / 100; }
  function isNumber(value) { return typeof value === 'number' && isFinite(value); }
  function mean(values) { return values.reduce((sum, value) => sum + value, 0) / values.length; }
  function signedText(value, digits) {
    const text = Math.abs(value).toFixed(digits);
    if (Number(text) === 0) return text;
    return (value < 0 ? MINUS : '+') + text;
  }
  // Every distance on the radar pages goes through here (docs/design-radar-objects.md, shared rules).
  function fmtM(kind, value) {
    if (!isNumber(value)) return '–';
    if (kind === 'reading' || kind === 'tape') return value.toFixed(2) + NBSP + 'm';
    if (kind === 'comma') return value.toFixed(1) + NBSP + 'm';
    if (kind === 'camera') return '≈' + NBSP + Math.round(value) + NBSP + 'm';
    if (kind === 'correction') return signedText(value, 2) + NBSP + 'm';
    if (kind === 'diff') return Math.abs(value).toFixed(1) + NBSP + 'm apart';
    return String(value);
  }
  // "− 0.89 m" / "+ 1.50 m": a term of a sum.
  function term(value) { return (value < 0 ? MINUS + ' ' : '+ ') + Math.abs(value).toFixed(2) + NBSP + 'm'; }
  function fmtCm(value) { return Math.round(Math.abs(value) * 100) + ' cm'; }
  function thousands(value) { return String(Math.round(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
  function fmtTime(unix) {
    if (unix === null || unix === undefined) return '–';
    const date = new Date(unix * 1000);
    if (isNaN(date.getTime())) return String(unix);
    return date.toLocaleString('en-GB', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'});
  }
  function fmtDay(unix) {
    return new Date(unix * 1000).toLocaleDateString('en-GB', {weekday: 'short', day: 'numeric', month: 'short'});
  }
  function fmtShortDay(unix) {
    return new Date(unix * 1000).toLocaleDateString('en-GB', {day: 'numeric', month: 'short'});
  }
  function fmtDayTime(unix) {
    return fmtDay(unix) + ' · ' + new Date(unix * 1000).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'});
  }
  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined || !isFinite(seconds)) return '–';
    const total = Math.round(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours > 0) return hours + ' h ' + minutes + ' min';
    if (minutes > 0) return minutes + ' min';
    return total + ' s';
  }
  // "48 s", "1 min 05 s", "24 min", "1 h 05 min".
  function fmtSpan(seconds) {
    const total = Math.round(seconds || 0);
    if (total < 60) return total + ' s';
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    if (hours) return hours + ' h ' + String(minutes).padStart(2, '0') + ' min';
    if (total < 600) return minutes + ' min ' + String(total % 60).padStart(2, '0') + ' s';
    return minutes + ' min';
  }
  // Time into a drive: "0:12", "12:31", "1:02:31".
  function fmtClock(seconds) {
    const total = Math.max(0, Math.round(seconds || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = String(total % 60).padStart(2, '0');
    return hours ? hours + ':' + String(minutes).padStart(2, '0') + ':' + rest : minutes + ':' + rest;
  }
  function plural(count, one, many) { return count + ' ' + (count === 1 ? one : many); }
  function same(key, value) {
    // True when `value` is what was rendered last time under `key`: rebuilding the same DOM would
    // lose a click landing at that moment and the keyboard focus.
    const text = JSON.stringify(value);
    if (same.cache[key] === text) return true;
    same.cache[key] = text;
    return false;
  }
  same.cache = {};
  // "3 m apart" style numbers typed with a comma or a dot, a minus or a dash, with or without " m".
  function parseMetres(text) {
    const clean = String(text || '').trim().replace(/\s*m$/i, '').replace(',', '.').replace(/[−–]/g, '-')
      .replace(/^\+/, '').replace(/\s+/g, '');
    if (clean === '') return null;
    if (!/^-?(\d+\.?\d*|\.\d+)$/.test(clean)) return NaN;
    return Number(clean);
  }

  async function api(path, options) {
    const response = await fetch(path, Object.assign({credentials: 'same-origin', cache: 'no-store'}, options || {}));
    // Signed out (session ended, or another browser changed the password): the server shows the sign-in page.
    if (response.status === 401) {
      location.replace('/');
      throw new Error('Signed out.');
    }
    let body = null;
    try { body = await response.json(); } catch (error) { body = {message: 'Invalid response (HTTP ' + response.status + ').'}; }
    if (!response.ok) {
      const error = new Error(body.message || ('HTTP ' + response.status));
      error.status = response.status;
      error.code = body.error || '';
      throw error;
    }
    return body;
  }
  function post(path, payload) {
    return api(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload || {})});
  }

  let toastTimer = null;
  function toast(message, error) {
    const element = byId('toast');
    element.textContent = message;
    element.className = 'toast show' + (error ? ' error' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { element.className = 'toast'; }, error ? 8000 : 3500);
  }

  // -- shared words of the radar pages -------------------------------------------------------------
  const CHIPS = {
    measured: {label: 'measured', kind: 'ok', text: 'Measured by your car\'s radar.'},
    camera: {label: '≈ camera', kind: 'imp', text: 'Guessed from the camera alone: an estimate.'},
    you: {label: 'you', kind: 'rev', text: 'Confirmed by you.'},
    ai: {label: 'AI', kind: 'todo', text: 'Confirmed by an AI agent through the API.'}};
  function chip(kind) {
    const info = CHIPS[kind];
    const element = node('button', 'pill chip ' + info.kind, info.label);
    element.type = 'button';
    element.setAttribute('aria-label', info.label + ': what does it mean?');
    element.addEventListener('click', (event) => { event.stopPropagation(); openTopic('chips', kind); });
    return element;
  }
  const CAL_PILL = {not_set: ['not set', 'bad'], close_only: ['checked close only', 'todo'],
    ready: ['checked at 5–30 m', 'ok'], typed: ['typed by hand', 'todo'], no_radar: ['no radar', 'muted']};
  function calPill(cal) {
    const [label, kind] = CAL_PILL[cal && cal.status] || CAL_PILL.not_set;
    return pill(label, kind);
  }
  function calibrated(cal) { return !!cal && isNumber(cal.radar_to_reference_m); }
  // A kept test at 5 m or more, where the labels will be. Not the same as ready: that also needs the numbers from
  // the tests (not typed by hand) and tests that agree (cal.status, from the server).
  function farTested(cal) { return !!cal && (cal.tested_m || []).some((distance) => distance >= FAR_TEST_M); }
  function tapeRange(distances) {
    if (!distances.length) return '';
    const low = distances[0];
    const high = distances[distances.length - 1];
    const short = (value) => value >= 10 ? String(Math.round(value)) : value.toFixed(1);
    return low === high ? short(low) + ' m' : short(low) + '–' + short(high) + ' m';
  }
  // The one calibration line of a screen with distances, until the calibration is ready.
  function calibrationLine(cal) {
    if (!calibrated(cal) || cal.status === 'ready') return null;
    const tested = cal.tested_m || [];
    let text;
    if (!farTested(cal) && tested.length) {
      text = 'Checked only up to ' + tested[tested.length - 1].toFixed(1) + ' m so far: farther distances are not confirmed yet.';
    } else if (!tested.length) {
      text = 'Typed by hand and not tested with a tape on this site yet: these distances are not confirmed.';
    } else if (cal.source === 'typed') {
      text = 'Typed by hand: your tests are not in use, so these distances are not confirmed yet.';
    } else {
      text = 'Your tests disagree by ' + fmtM('comma', cal.spread_m) + ': these distances are not confirmed yet.';
    }
    return node('p', 'warn-text cal-line', text);
  }
  function commaOffset(cal) { return cal.radar_to_reference_m; }
  function shortFormula(cal) {
    return 'Distance from the comma = reading ' + term(commaOffset(cal)) + '.';
  }
  function longFormula(cal) {
    return 'From the comma = reading ' + term(cal.radar_correction_m) + ' ' + term(cal.radar_to_camera_m)
      + ' = reading ' + term(commaOffset(cal));
  }
  function exampleSentence(cal, arrow) {
    const after = (20 + commaOffset(cal)).toFixed(2) + ' m';
    return arrow ? 'The radar says 20.00 m → ' + after + ' from the comma.'
      : 'A reading of 20.00 m is ' + after + ' from the comma.';
  }
  function correctionWords(value) {
    if (!isNumber(value) || Math.abs(value) < 0.005) return 'no correction';
    return 'the radar reads about ' + Math.abs(value).toFixed(1) + ' m ' + (value < 0 ? 'long' : 'short');
  }
  // Drive names: the GPS start, or the route counter when the comma's clock is all there is.
  function driveTitle(entry) {
    if (entry.kind === 'recording') return 'Recording no. ' + entry.number;
    if (entry.start_from === 'gps' && entry.start_unix) return fmtDayTime(entry.start_unix);
    return 'Drive no. ' + entry.number + ' · time unknown';
  }
  function driveNumber(entry) { return (entry.kind === 'recording' ? 'recording no. ' : 'drive no. ') + entry.number; }
  function radarEntry(route) {
    return data.radar ? data.radar.summary.drives.find((entry) => entry.route === route) || null : null;
  }
  function emptyState(options) {
    const box = node('div', 'empty-state');
    if (options.art) box.appendChild(art(options.art, 'empty-art'));
    if (options.title) box.appendChild(node('h3', '', options.title));
    box.appendChild(node('p', 'empty', options.text));
    if (options.button) box.appendChild(button(options.button[0], options.button[2] === undefined ? 'primary' : options.button[2],
      options.button[1]));
    return box;
  }
  function skeletonRows(count, text) {
    const box = node('div', 'skeleton-box');
    box.setAttribute('aria-busy', 'true');
    for (let index = 0; index < count; index++) box.appendChild(node('div', 'skeleton-row'));
    if (text) box.appendChild(node('p', 'note', text));
    return box;
  }

  // -- the info sheet texts ("?" buttons) ----------------------------------------------------------
  function correctionText() {
    const cal = (data.radar && data.radar.calibration) || (status && status.radar);
    if (calibrated(cal) && cal.radar_correction_m < 0) {
      const how = cal.source === 'tests' ? 'Your tests at a known distance measured how much: about '
        + Math.abs(cal.radar_correction_m).toFixed(1) + ' m.'
        : 'You typed a correction of ' + fmtM('correction', cal.radar_correction_m) + '; a test at a known distance checks it.';
      return 'The radar sits behind the bumper and reads a little long. ' + how + ' The site takes it off every reading, '
        + 'then adds the ' + cal.radar_to_camera_m.toFixed(2) + ' m from the radar to the comma.';
    }
    return 'The radar sits behind the bumper and reads a little long or short. A test in front of a wall measures how '
      + 'much. The site takes it off every reading, then adds the distance from the radar to the comma.';
  }
  const TOPICS = {
    radar: {title: 'How to read the radar', items: [
      'It follows one vehicle: the one ahead in your lane. Nothing beside you, nothing in other lanes.',
      'It gives one number: how far. No side position, no type of object.',
      'It counts in steps of 25 cm: 20.00, 20.25, 20.50 m.',
      'No reading when nothing is ahead, or when it is very close.',
      'The speed of the vehicle ahead has not been sent by your car so far, so the site doesn\'t show it.',
      'Nothing on this site ever drives the car.']},
    correction: {title: 'What is the correction?', text: correctionText},
    new_drives: {title: 'Why only new drives?', text: () => 'Your car sends the radar\'s reading only with the new '
      + 'software, installed on the evening of 23 October. Older drives, and drives where openpilot controls the speed, '
      + 'have no reading.'},
    ahead: {title: 'Why only the vehicle ahead?', text: () => 'The radar gives no side position: a distance can only be '
      + 'paired with the vehicle straight ahead in your lane. Anything else would be a guess.'},
    lane: {title: 'Your lane', text: () => 'The shaded band is where your car is heading, worked out from its motion: about '
      + 'one lane wide. It is drawn on a flat road, so on hills it is only a guide.'},
  };
  function openTopic(key, chipKind) {
    if (key === 'chips') {
      const info = chipKind ? CHIPS[chipKind] : null;
      openInfo(info ? info.label : 'The four chips', info ? info.text : 'Where each number comes from.', (body) => {
        const list = node('ul', 'info-list');
        for (const kind of Object.keys(CHIPS)) {
          const item = node('li');
          const label = node('div');
          label.appendChild(pill(CHIPS[kind].label, CHIPS[kind].kind));
          item.appendChild(label);
          item.appendChild(node('span', '', CHIPS[kind].text));
          list.appendChild(item);
        }
        body.appendChild(list);
      });
      return;
    }
    const topic = TOPICS[key];
    openInfo(topic.title, '', (body) => {
      if (topic.items) {
        const list = node('ul', 'info-list');
        for (const text of topic.items) list.appendChild(node('li', '', text));
        body.appendChild(list);
      } else {
        body.appendChild(node('p', 'info-text', topic.text()));
      }
      // The other questions, one tap away.
      const more = node('div', 'info-more');
      for (const other of ['radar', 'correction', 'new_drives', 'ahead']) {
        if (other !== key) more.appendChild(linkButton(TOPICS[other].title, () => openTopic(other)));
      }
      more.appendChild(linkButton('The four chips', () => openTopic('chips')));
      body.appendChild(more);
    });
  }

  // -- tabs ------------------------------------------------------------------------------------
  function currentHash() {
    if (currentTab === 'radar') {
      if (radarView === 'test') return 'radar/test';
      return radarView === 'drive' && radarRoute ? 'radar/' + radarRoute : 'radar';
    }
    if (currentTab === 'results') return chosenResult ? 'results/' + chosenResult : 'results';
    if (currentTab === 'settings') return settingsAnchor ? 'settings/' + settingsAnchor : 'settings';
    return currentTab;
  }
  // Opens a view with a new history entry, so the browser's back button comes back.
  function go(hash) {
    if (location.hash === '#' + hash) showTab(hash);
    else location.hash = hash;
  }

  function showTab(name) {
    let detail = '';
    const slash = name.indexOf('/');
    if (slash >= 0) {
      detail = name.slice(slash + 1);
      name = name.slice(0, slash);
    }
    if (name === 'lab') name = 'experiments';
    if (PANELS.indexOf(name) < 0) name = 'overview';
    const before = currentHash();
    if (name === 'settings' && currentTab !== 'settings') hashBeforeSettings = before;
    if (name === 'results' && RESULT_RE.test(detail)) chosenResult = detail;
    if (name === 'radar') {
      if (detail === 'test') radarView = 'test';
      else if (ROUTE_RE.test(detail)) { radarView = 'drive'; radarRoute = detail; }
      else radarView = 'list';
    }
    settingsAnchor = name === 'settings' && detail === 'radar' ? 'radar' : null;
    anchorDone = false;
    currentTab = name;
    const barOn = name === 'experiments' || name === 'results' ? 'lab' : name;
    for (const tab of BAR) {
      const element = byId('tab-' + tab);
      const selected = tab === barOn;
      element.setAttribute('aria-selected', String(selected));
      element.tabIndex = selected || (barOn === 'settings' && tab === 'overview') ? 0 : -1;
    }
    byId('tab-lab').setAttribute('aria-controls', name === 'results' ? 'panel-results' : 'panel-experiments');
    byId('tab-settings').setAttribute('aria-expanded', String(name === 'settings'));
    for (const panel of PANELS) byId('panel-' + panel).hidden = panel !== name;
    byId('radar-list').hidden = radarView !== 'list';
    byId('radar-drive').hidden = radarView !== 'drive';
    byId('radar-test').hidden = radarView !== 'test';
    setFocusMode(name === 'radar' && radarView === 'test');
    const hash = '#' + currentHash();
    if (location.hash !== hash) history.replaceState(null, '', hash);
    if (currentHash() !== before && !settingsAnchor) window.scrollTo(0, 0);
    renderTab();
    loadTab(true);
  }
  for (const tab of BAR) {
    byId('tab-' + tab).addEventListener('click', () => { showTab(BAR_HASH[tab]); window.scrollTo(0, 0); });
    byId('tab-' + tab).addEventListener('keydown', (event) => {
      let index = BAR.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % BAR.length;
      else if (event.key === 'ArrowLeft') index = (index + BAR.length - 1) % BAR.length;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = BAR.length - 1;
      else return;
      event.preventDefault();
      showTab(BAR_HASH[BAR[index]]);
      byId('tab-' + BAR[index]).focus();
    });
  }
  byId('tab-settings').addEventListener('click', () => {
    showTab(currentTab === 'settings' ? hashBeforeSettings : 'settings');
    window.scrollTo(0, 0);
  });
  byId('settings-back').addEventListener('click', () => {
    showTab(hashBeforeSettings);
    window.scrollTo(0, 0);
  });
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
  for (const element of document.querySelectorAll('[data-goto]')) {
    element.addEventListener('click', () => { showTab(element.dataset.goto); window.scrollTo(0, 0); });
  }
  // Lab: one bar button over two panels, switched at the top of both.
  const labButtons = Array.from(document.querySelectorAll('[data-lab]'));
  for (const element of labButtons) {
    element.addEventListener('click', () => go(element.dataset.lab));
    element.addEventListener('keydown', (event) => {
      const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      const target = element.dataset.lab === 'results' ? 'experiments' : 'results';
      go(target);
      const next = document.querySelector('#panel-' + target + ' [data-lab="' + target + '"]');
      if (next) next.focus();
    });
  }
  function renderLabSwitch() {
    const count = data.results ? ' (' + data.results.length + ')' : '';
    for (const element of document.querySelectorAll('.lab-count')) element.textContent = count;
    for (const element of labButtons) {
      const on = element.dataset.lab === currentTab;
      element.setAttribute('aria-checked', String(on));
      element.tabIndex = on ? 0 : -1;
    }
  }

  // Focus mode (the radar test): no tabs and no gear, the screen kept awake where the browser can.
  let wakeLock = null;
  async function keepAwake() {
    if (!('wakeLock' in navigator) || wakeLock || document.hidden) return;
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } catch (error) {
      wakeLock = null;
    }
  }
  function letSleep() {
    if (!wakeLock) return;
    wakeLock.release().catch(() => {});
    wakeLock = null;
  }
  function setFocusMode(on) {
    document.body.classList.toggle('focus', on);
    if (on) keepAwake();
    else letSleep();
  }
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    if (document.body.classList.contains('focus')) keepAwake();
    tickMinute();
    refreshStatus();
  });

  function needs(tab) {
    if (tab === 'overview' || tab === 'experiments' || tab === 'results') return ['results'];
    if (tab === 'radar') return radarView === 'drive' ? ['radar', 'drive'] : ['radar'];
    if (tab === 'settings') return ['settings', 'auth', 'radar'];
    return [];
  }
  function radarJobRunning() {
    return !!status && status.jobs.some((job) => job.state === 'running' && RADAR_ACTIONS.indexOf(job.action) >= 0);
  }
  function loadPeriod(what) {
    if (what === 'radar') return radarJobRunning() ? 5000 : 15000;
    return LOAD_PERIOD[what] || 0;
  }

  async function load(what, key) {
    try {
      if (what === 'results') data.results = (await api('/api/results')).results;
      else if (what === 'settings') data.settings = await api('/api/settings');
      else if (what === 'auth') data.auth = await api('/api/auth/state');
      else if (what === 'radar') {
        data.radar = await api('/api/radar');
        data.radarError = false;
      } else if (what === 'drive') {
        await refreshDetail(radarRoute);
      }
      loadedAt[key || what] = Date.now();
    } catch (error) {
      if (what === 'radar') {
        if (!data.radarError) toast(error.message, true);
        data.radarError = true;
        loadedAt.radar = 0;
      } else {
        toast(error.message, true);
      }
    }
  }

  // Read again in place: the view keeps showing the old one until the new one is here.
  async function refreshDetail(route) {
    const epoch = radarEpoch;
    try {
      details[route] = {detail: await api('/api/radar/drives/' + route), epoch: epoch};
    } catch (error) {
      if (error.status !== 404) throw error;
      details[route] = {missing: true, epoch: epoch};
    }
  }
  // A kept detail, or null when it is older than what the server knows: read before a job that reads or brings
  // radar ended, or with fewer segments read than the summary has now (a recording fetched while its radar was
  // still being read). The next ensureDetail reads it again.
  function freshDetail(route) {
    const cached = details[route];
    if (!cached) return null;
    const entry = radarEntry(route);
    const behind = !!entry && (cached.missing || cached.detail.segments.filter((segment) => segment.read).length < entry.read);
    if (behind || cached.epoch !== radarEpoch) {
      delete details[route];
      return null;
    }
    return cached;
  }
  function ensureDetail(route) {
    const cached = freshDetail(route);
    if (cached) return Promise.resolve(cached);
    if (detailLoads[route]) return detailLoads[route];
    const epoch = radarEpoch;
    detailLoads[route] = api('/api/radar/drives/' + route).then((detail) => {
      details[route] = {detail: detail, epoch: epoch};
      return details[route];
    }, (error) => {
      if (error.status === 404) {
        details[route] = {missing: true, epoch: epoch};
        return details[route];
      }
      throw error;
    }).finally(() => { delete detailLoads[route]; });
    return detailLoads[route];
  }
  // Settled: the summary has every segment of it read, or nothing is reading radar any more (a log that can't be
  // read stays unread for good). Before that, a detail would say "no standstill" for a radar not read yet.
  function radarSettled(route) {
    const entry = radarEntry(route);
    return !!entry && (entry.read >= entry.segments || !radarJobRunning());
  }

  async function loadTab(force) {
    const tab = currentTab;
    const pending = [];
    for (const what of needs(tab)) {
      const key = what === 'drive' ? 'drive:' + radarRoute : what;
      const age = Date.now() - (loadedAt[key] || 0);
      const every = loadPeriod(what);
      if (force || !loadedAt[key] || (every && age >= every)) pending.push(load(what, key));
    }
    if (!pending.length) return;
    await Promise.all(pending);
    if (tab === currentTab) renderTab();
  }

  function renderTab() {
    if (currentTab === 'experiments') { renderWizard(); renderLabSwitch(); }
    else if (currentTab === 'results') { renderResults(); renderLabSwitch(); }
    else if (currentTab === 'radar') renderRadar();
    else if (currentTab === 'objects') renderObjects();
    else if (currentTab === 'settings') { renderSources(); renderCalCard(); renderAdmin(); }
    updateButtons();
    if (status) renderLiveJobs();
  }

  // -- status ----------------------------------------------------------------------------------
  async function refreshStatus() {
    try {
      status = await api('/api/status');
      byId('live-dot').className = 'dot';
      byId('live-text').textContent = 'updated ' + new Date().toLocaleTimeString('en-GB');
    } catch (error) {
      byId('live-dot').className = 'dot off';
      byId('live-text').textContent = 'unreachable: ' + error.message;
      markNextStale();
      return;
    }
    if (firstCode === null) firstCode = status.code;
    byId('code-banner').hidden = !(status.code && firstCode && status.code !== firstCode);
    byId('code-version').textContent = status.code || 'unknown';
    // The calibration changed (here or in another browser): every distance from the comma is read again.
    const calibration = status.radar ? JSON.stringify([status.radar.version, status.radar.radar_to_reference_m,
      status.radar.source]) : '';
    const calibrationChanged = lastCalibration !== null && calibration !== lastCalibration;
    lastCalibration = calibration;
    if (calibrationChanged) loadedAt.radar = 0;
    const radarJobs = status.jobs.filter((job) => job.state === 'running' && RADAR_ACTIONS.indexOf(job.action) >= 0)
      .map((job) => job.id).join(',');
    if (previousRadarJobs && radarJobs !== previousRadarJobs) radarEpoch++;
    previousRadarJobs = radarJobs;
    renderMachine();
    renderNext();
    renderDriveTiles();
    renderPlan();
    renderRunning();
    renderJobs();
    renderBadges();
    renderLiveJobs();
    // A job just ended: its result shows up in the open tab at once.
    const running = status.jobs.filter((job) => job.state === 'running').map((job) => job.id).join(',');
    if ((previousRunning && running !== previousRunning) || calibrationChanged) {
      loadedAt.results = 0;
      loadTab(true);
    } else {
      renderTab();
    }
    previousRunning = running;
    if (log.id) renderSheetStatus();
  }

  function statLine(id, text) { byId(id).textContent = text; }

  function renderMachine() {
    const system = status.system;
    const gpu = system.gpu;
    if (gpu) {
      const util = gpu.utilization === null ? 0 : gpu.utilization;
      byId('gpu-value').textContent = util + '%';
      setBar('gpu-bar', util, util > 85 ? 'warn' : '');
      let text = gpu.name.replace(/^Tesla /, '');
      if (gpu.memory_total_mb) text += ' · ' + (gpu.memory_used_mb / 1024).toFixed(1) + ' of ' + Math.round(gpu.memory_total_mb / 1024) + ' GB';
      text += gpu.processes ? ' · busy' : ' · idle';
      statLine('gpu-sub', text);
    } else {
      byId('gpu-value').textContent = 'n/a';
      setBar('gpu-bar', 0, '');
      statLine('gpu-sub', 'no NVIDIA GPU found: the depth baseline needs one');
    }
    if (system.cpu) {
      byId('cpu-value').textContent = Math.round(system.cpu.total) + '%';
      setBar('cpu-bar', system.cpu.total, system.cpu.total > 85 ? 'warn' : '');
    }
    statLine('cpu-sub', (system.cpus || '?') + ' cores');
    const memory = system.memory;
    if (memory) {
      byId('ram-value').textContent = Math.round(memory.used_percent) + '%';
      setBar('ram-bar', memory.used_percent, memory.used_percent > 90 ? 'warn' : '');
      statLine('ram-sub', memory.available_gb + ' GB free of ' + memory.total_gb + ' GB');
    }
    const disk = system.disk;
    if (disk) {
      byId('disk-value').textContent = Math.round(disk.used_percent) + '%';
      setBar('disk-bar', disk.used_percent, disk.used_percent > 90 ? 'warn' : '');
      statLine('disk-sub', disk.free_gb + ' GB free of ' + disk.total_gb + ' GB');
    }
  }

  function renderBadges() {
    const objects = byId('objects-count');
    const waiting = status.objects && status.objects.to_confirm ? status.objects.to_confirm : 0;
    objects.hidden = waiting === 0;
    objects.textContent = String(waiting);
  }

  // -- Home: the next step ---------------------------------------------------------------------
  function farLine(cal) {
    const tested = cal ? cal.tested_m || [] : [];
    const first = tested.length
      ? 'Your tests are all closer than ' + (Math.floor(tested[tested.length - 1] * 2) / 2 + 0.5).toFixed(1) + ' m.'
      : 'Your numbers have not been checked farther away yet.';
    return first + ' One test about 10 m behind a parked car tells whether the distances hold where the labels will be.';
  }
  function nextCopy(next) {
    const cal = status.radar;
    if (next.case === 'radar_not_set') {
      return {art: 'radar', head: 'Set up your car\'s radar', line: 'A tape measure and a wall turn the radar into a ruler for '
        + 'the camera. About 10 minutes.', button: ['Start the test', () => openTest({})]};
    }
    if (next.case === 'test_waiting') {
      return {art: 'draft', head: 'Finish your radar test', line: 'Your tape measurements of '
        + (next.draft_created ? fmtDay(next.draft_created) : 'earlier') + ' are saved. Pick its recording when the comma '
        + 'is back on your Wi-Fi.', button: ['Finish it', () => openTest({draft: 'newest'})]};
    }
    if (next.case === 'no_radar_drive') {
      return {art: 'road', head: 'Record a drive with the radar', line: 'Only drives recorded on the new software, from the '
        + 'evening of 23 October, carry the radar. Just drive: new drives show up here by themselves once openpilot.distill '
        + 'has downloaded and localized them.', button: ['Why only new drives?', () => openTopic('new_drives'), '']};
    }
    if (next.case === 'far_test') {
      return {art: 'car', head: 'Test the radar farther away', line: farLine(cal),
        button: ['Start the test', () => openTest({target: 'car'})]};
    }
    if (next.case === 'tests_disagree') {
      return {art: 'radar', head: 'Your radar tests disagree', line: 'Their corrections are ' + fmtM('diff', cal.spread_m)
        + ', more than the radar\'s 25 cm steps explain. Check the tape, or leave one test out: until then the '
        + 'distances are not confirmed.', button: ['See the tests', () => go('settings/radar')]};
    }
    if (next.case === 'use_tests') {
      const give = cal.tests_give || {};
      return {art: 'check', head: 'Use your radar tests', line: 'Your numbers were typed by hand. Your tests give '
        + fmtM('correction', give.radar_correction_m) + ', with the comma ' + fmtM('tape', give.radar_to_camera_m)
        + ' behind the radar: only numbers from the tests make confirmed pairs count as labels.',
        button: ['Compare them', () => go('settings/radar')]};
    }
    if (next.case === 'no_radar') {
      return {art: 'road', head: 'Your car is set as having no radar', line: 'Drives are used without it: the camera '
        + 'experiments in Lab still run. If it has one after all, a tape test sets it up.',
        button: ['Set it up after all', () => openTest({}), '']};
    }
    return {art: 'check', head: 'All caught up', line: 'New drives with radar show up here by themselves.'};
  }
  const ALSO = {
    radar_not_set: ['set up your car\'s radar', 'Start', () => openTest({})],
    test_waiting: ['finish your radar test', 'Finish it', () => openTest({draft: 'newest'})],
    radar_reading: ['the radar of new segments is being read', 'Show', () => go('radar')],
    no_radar_drive: ['record a drive with the radar', 'Why', () => openTopic('new_drives')],
    far_test: ['test the radar farther away', 'How', () => openTest({target: 'car'})],
    tests_disagree: ['your radar tests disagree', 'See them', () => go('settings/radar')],
    use_tests: ['use your tests in place of the typed numbers', 'Compare', () => go('settings/radar')],
  };

  function renderNext() {
    const box = byId('next-card');
    const next = status.next;
    if (!next) return;
    box.classList.remove('stale');
    const stale = box.querySelector('.next-stale');
    if (stale) stale.remove();
    if (same('next-card', [next, status.radar && [status.radar.tested_m, status.radar.spread_m, status.radar.tests_give]])) return;
    box.replaceChildren();
    if (next.case === 'radar_reading') {
      box.appendChild(node('span', 'eyebrow', 'Next step'));
      box.appendChild(node('h3', 'next-head', 'Reading the radar of new segments'));
      box.appendChild(node('p', 'next-line', 'Drives show up on the Radar tab as their segments are read.'));
      const live = node('div', 'job-live');
      live.dataset.actions = RADAR_ACTIONS.join(',');
      box.appendChild(live);
    } else {
      const copy = nextCopy(next);
      const row = node('div', 'next-row');
      row.appendChild(art(copy.art, 'next-art'));
      const text = node('div', 'next-text');
      text.appendChild(node('span', 'eyebrow', next.case === 'caught_up' ? 'Nothing to do' : 'Next step'));
      text.appendChild(node('h3', 'next-head', copy.head));
      text.appendChild(node('p', 'next-line', copy.line));
      row.appendChild(text);
      box.appendChild(row);
      if (copy.button) {
        const action = button(copy.button[0], (copy.button[2] === undefined ? 'primary' : copy.button[2]) + ' next-button',
          copy.button[1]);
        box.appendChild(action);
      }
    }
    const also = (next.also || []).find((name) => ALSO[name]);
    if (also) {
      const line = node('p', 'next-also');
      line.appendChild(document.createTextNode('Also waiting: ' + ALSO[also][0] + ' '));
      line.appendChild(linkButton(ALSO[also][1], ALSO[also][2]));
      box.appendChild(line);
    }
  }
  function markNextStale() {
    const box = byId('next-card');
    if (box.classList.contains('stale') || !status) return;
    box.classList.add('stale');
    box.appendChild(node('p', 'note next-stale', 'not updated'));
  }

  function tile(big, what, hint) {
    const box = node('div', 'tile');
    box.appendChild(node('div', 'big', big));
    box.appendChild(node('div', 'what', what));
    if (hint) box.appendChild(node('div', 'hint', hint));
    return box;
  }
  function tileButton(big, what, hint, action) {
    const box = node('button', 'tile');
    box.type = 'button';
    box.appendChild(node('div', 'big', big));
    box.appendChild(node('div', 'what', what));
    if (hint) box.appendChild(node('div', 'hint', hint));
    const more = node('span', 'tile-more', '›');
    more.setAttribute('aria-hidden', 'true');
    box.appendChild(more);
    box.addEventListener('click', action);
    return box;
  }

  function renderDriveTiles() {
    const drives = status.drives;
    const radar = status.radar_summary || null;
    if (same('drive-tiles', [drives, radar])) return;
    const box = byId('drive-tiles');
    box.replaceChildren();
    const counts = drives.counts;
    box.appendChild(tile(String(counts.ready_routes), 'drives ready', 'outings of the car, localized'));
    box.appendChild(tile(String(counts.ready), 'segments ready', 'one minute of video each'));
    box.appendChild(tile(String(counts.segments - counts.ready), 'not localized yet', 'localize them in openpilot.distill'));
    if (radar) {
      box.appendChild(tileButton(String(radar.drives_with_radar), 'drives with radar', 'recorded on the new software',
        () => go('radar')));
    } else {
      box.appendChild(tileButton('–', 'drives with radar', 'not read yet: open Radar', () => go('radar')));
    }
  }
  // -- the plan --------------------------------------------------------------------------------
  function geometryLine(summary) {
    const strict = summary.strict || {};
    const view = strict.third_view_px || {};
    const full = view.full_pose ? view.full_pose.median : null;
    const rotation = view.rotation_only ? view.rotation_only.median : null;
    return 'The car\'s motion predicts where points land in the next frames within ' + num(full, 1) + ' px (with '
      + 'the rotation alone: ' + num(rotation, 1) + ' px). Distance scale not checked yet.';
  }
  function baselineLine(summary) {
    const b0 = summary.B0_held_out || summary.B0_all;
    const b1 = summary.B1_held_out;
    let text = 'Off by ' + percent(b0 ? b0.absrel : null) + ' on average';
    if (b1) text += ', ' + percent(b1.absrel) + ' with one correction factor';
    return text + ', on points measured from the car\'s motion (no radar yet).';
  }

  function planStep(number, title, kind, stateLabel, text, buttons) {
    const item = node('li', kind === 'ok' ? 'ok' : (kind === 'muted' ? 'muted' : ''));
    item.appendChild(node('span', 'plan-number', String(number)));
    item.appendChild(node('span', 'plan-title', title));
    item.appendChild(pill(stateLabel, kind));
    item.appendChild(node('span', 'plan-text', text));
    if (buttons && buttons.length) {
      const row = node('div', 'plan-buttons');
      for (const element of buttons) row.appendChild(element);
      item.appendChild(row);
    }
    return item;
  }

  function planButton(label, action) {
    return button(label, 'small', action);
  }

  function renderPlan() {
    const latest = status.latest || {};
    const running = new Set(status.jobs.filter((job) => job.state === 'running').map((job) => job.action));
    const cal = status.radar || {status: 'not_set', tested_m: []};
    const radar = status.radar_summary || null;
    if (same('plan', [latest, Array.from(running), cal, radar])) return;
    const list = byId('plan');
    list.replaceChildren();
    const steps = [['geometry_audit', 'Check the car\'s motion'], ['mono_baseline', 'Measure a ready-made depth model']];
    steps.forEach(([kind, title], index) => {
      const result = latest[kind];
      if (running.has(kind)) {
        list.appendChild(planStep(index, title, 'run', 'running', KIND_EXPLAIN[kind]));
      } else if (result) {
        const text = kind === 'geometry_audit' ? geometryLine(result.summary) : baselineLine(result.summary);
        list.appendChild(planStep(index, title, 'ok', 'done', text + ' ' + fmtTime(result.started) + '.',
          [planButton('See the result', () => { chosenResult = result.id; go('results/' + result.id); })]));
      } else {
        list.appendChild(planStep(index, title, 'todo', 'to run', KIND_EXPLAIN[kind],
          [planButton('Run it', () => { chooseKind(kind); go('experiments'); })]));
      }
    });
    // Step 2: the radar, by how far its calibration was checked.
    const [label, kind] = CAL_PILL[cal.status] || CAL_PILL.not_set;
    const facts = [radar ? plural(radar.drives_with_radar, 'drive with radar', 'drives with radar') : 'radar not read yet'];
    if (cal.status === 'not_set') facts.push('not set up yet');
    else if (cal.status === 'no_radar') facts.push('set as having no radar');
    else if (!farTested(cal)) facts.push('far test missing');
    else if (cal.status === 'typed') facts.push('typed numbers in use, not your tests');
    else if (cal.status === 'close_only') facts.push('your tests disagree');
    list.appendChild(planStep(2, 'Distances from the car\'s radar', kind, label,
      'The car\'s radar measures the vehicle ahead in your lane: the real ruler for the camera. ' + facts.join(' · ') + '.',
      [planButton('Open Radar', () => go('radar'))]));
    list.appendChild(planStep(3, 'A network that sees in 3D over time', 'muted', 'not started',
      'Several frames plus the car\'s motion give distance from parallax, the way people judge it. It is built only '
      + 'once it beats the depth baseline on the radar distances.'));
  }

  // -- jobs ------------------------------------------------------------------------------------
  function jobItem(job) {
    const item = node('li', 'item');
    item.appendChild(pill(job.state_label, JOB_PILL[job.state] || 'muted'));
    const text = node('div', 'grow');
    text.appendChild(node('div', 'what', job.label));
    let sub = job.summary || '';
    sub += (sub ? ' · ' : '') + fmtTime(job.started);
    if (job.state === 'running') sub += ' · for ' + fmtDuration(Date.now() / 1000 - job.started);
    else if (job.finished) sub += ' · took ' + fmtDuration(job.finished - job.started);
    text.appendChild(node('div', 'sub', sub));
    item.appendChild(text);
    if (job.result) {
      const open = node('button', 'btn small', 'Result');
      open.type = 'button';
      open.addEventListener('click', () => { chosenResult = job.result; showTab('results'); });
      item.appendChild(open);
    }
    const view = node('button', 'btn small', 'Log');
    view.type = 'button';
    view.addEventListener('click', () => openLog(job.id, job.label));
    item.appendChild(view);
    if (job.stoppable) {
      const stop = node('button', 'btn small danger', 'Stop');
      stop.type = 'button';
      stop.addEventListener('click', () => stopJob(job));
      item.appendChild(stop);
    }
    if (job.progress) {
      const progress = node('div', 'progress');
      progress.appendChild(barNode(job.progress.percent, 'run'));
      progress.appendChild(node('span', 'sub', job.progress.done + ' / ' + job.progress.total));
      item.appendChild(progress);
    }
    return item;
  }

  function renderRunning() {
    const running = status.jobs.filter((job) => job.state === 'running');
    const count = byId('running-count');
    count.hidden = running.length === 0;
    count.textContent = String(running.length);
    const list = byId('running-jobs');
    const minute = Math.floor(Date.now() / 60000);
    if (!same('running-jobs', [running.map((job) => [job.id, job.progress, job.stoppable]), minute])) {
      list.replaceChildren();
      for (const job of running) list.appendChild(jobItem(job));
    }
    byId('running-empty').hidden = running.length > 0;
  }

  const RECENT_JOBS = 10;
  function renderJobs() {
    const finished = status.jobs.filter((job) => job.state !== 'running').slice(0, RECENT_JOBS);
    if (same('recent-jobs', finished)) return;
    const list = byId('jobs');
    list.replaceChildren();
    byId('jobs-empty').hidden = finished.length > 0;
    for (const job of finished) list.appendChild(jobItem(job));
  }

  // Every action card has a <div class="job-live" data-actions="a,b">: the running job of those actions
  // (bar, percentage, time left, stop) and, for a few minutes after it ends, how it went.
  const RECENT_SECONDS = 600;
  function liveJob(actions) {
    const jobs = (status.jobs || []).filter((job) => actions.indexOf(job.action) >= 0);
    const running = jobs.find((job) => job.state === 'running');
    if (running) return running;
    const now = Date.now() / 1000;
    const recent = jobs.filter((job) => job.finished && now - job.finished < RECENT_SECONDS);
    recent.sort((a, b) => b.finished - a.finished);
    return recent[0] || null;
  }

  function liveTexts(job) {
    const now = Date.now() / 1000;
    if (job.state !== 'running') {
      const took = job.finished ? fmtDuration(job.finished - job.started) : '–';
      if (job.state === 'ok') return {head: 'Done in ' + took, sub: 'Finished at ' + fmtTime(job.finished) + '.'};
      if (job.state === 'stopped') return {head: 'Stopped after ' + took, sub: ''};
      return {head: 'Failed after ' + took, sub: 'Open the details to see what went wrong.'};
    }
    const elapsed = now - job.started;
    const progress = job.progress;
    if (!progress || !progress.total) return {head: 'Starting…', sub: fmtDuration(elapsed) + ' elapsed'};
    let sub = progress.done + ' of ' + progress.total + ' segments · ' + fmtDuration(elapsed) + ' elapsed';
    if (progress.done >= progress.total) sub += ' · finishing';
    else if (progress.done > 0) sub += ' · about ' + fmtDuration(Math.max(1, elapsed * (progress.total - progress.done) / progress.done)) + ' left';
    return {head: Math.floor(progress.percent) + '%', sub: sub};
  }

  function renderLiveJobs() {
    for (const box of document.querySelectorAll('.job-live')) {
      const job = liveJob(box.dataset.actions.split(','));
      if (!job) {
        box.replaceChildren();
        box.dataset.job = '';
        continue;
      }
      const kind = job.state === 'running' ? 'running' : (job.state === 'ok' ? 'ok' : 'bad');
      const key = job.id + ':' + job.state + ':' + job.stoppable + ':' + job.result;
      if (box.dataset.job !== key) {
        box.dataset.job = key;
        const card = node('div', 'jobcard ' + kind);
        const head = node('div', 'jobcard-head');
        if (kind === 'running') head.appendChild(node('span', 'spinner'));
        head.appendChild(node('strong', 'jobcard-label', job.label));
        head.appendChild(node('span', 'jobcard-state'));
        card.appendChild(head);
        const bar = node('div', 'jobcard-bar');
        bar.appendChild(node('span'));
        card.appendChild(bar);
        card.appendChild(node('div', 'jobcard-sub'));
        const buttons = node('div', 'jobcard-buttons');
        if (job.result) {
          const open = node('button', 'btn small primary', 'See the result');
          open.type = 'button';
          open.addEventListener('click', () => { chosenResult = job.result; showTab('results'); });
          buttons.appendChild(open);
        }
        const more = node('button', 'btn small', 'Details');
        more.type = 'button';
        more.addEventListener('click', () => openLog(job.id, job.label));
        buttons.appendChild(more);
        if (job.state === 'running' && job.stoppable) {
          const stop = node('button', 'btn small danger', 'Stop');
          stop.type = 'button';
          stop.addEventListener('click', () => stopJob(job));
          buttons.appendChild(stop);
        }
        card.appendChild(buttons);
        box.replaceChildren(card);
      }
      const texts = liveTexts(job);
      box.querySelector('.jobcard-state').textContent = texts.head;
      box.querySelector('.jobcard-sub').textContent = texts.sub;
      const bar = box.querySelector('.jobcard-bar');
      const known = job.progress && job.progress.total;
      bar.classList.toggle('indeterminate', job.state === 'running' && !known);
      let width = known ? job.progress.percent : 0;
      if (job.state === 'ok') width = 100;
      bar.firstChild.style.width = Math.max(0, Math.min(100, width)) + '%';
    }
  }


  // -- actions ---------------------------------------------------------------------------------
  function actionReason(name) {
    if (!status || !status.actions[name]) return 'server status not read yet';
    const state = status.actions[name];
    return state.allowed ? '' : state.reason;
  }

  async function runAction(name, params, question) {
    if (question && !window.confirm(question)) return null;
    try {
      const answer = await post('/api/run/' + name, params);
      toast('Started: ' + answer.job.label);
      await refreshStatus();
      return answer.job;
    } catch (error) {
      toast(error.message, true);
      refreshStatus();
      return null;
    }
  }

  async function stopJob(job) {
    if (!window.confirm('Stop "' + job.label + '"? It stops within about twenty seconds; nothing is kept.')) return;
    try {
      await post('/api/jobs/' + job.id + '/stop', {});
      toast('Stopping: ' + job.label);
    } catch (error) {
      toast(error.message, true);
    }
    refreshStatus();
  }

  function updateButtons() {
    if (!status) return;
    const kind = chosenKind();
    const reason = actionReason(kind);
    byId('btn-exp-start').disabled = reason !== '';
    byId('reason-exp').textContent = reason;
    const update = actionReason('update');
    byId('btn-update').disabled = update !== '';
    byId('reason-update').textContent = update;
  }

  // -- experiments wizard ----------------------------------------------------------------------
  function wizard(id, count, onShow) {
    const self = {step: 1};
    self.show = (step, focus) => {
      self.step = step;
      for (let index = 1; index <= count; index++) byId(id + '-step-' + index).hidden = index !== step;
      byId(id + '-back').hidden = step === 1;
      const dots = byId(id + '-dots').children;
      for (let index = 0; index < dots.length; index++) dots[index].className = index === step - 1 ? 'on' : '';
      if (onShow) onShow(step);
      updateButtons();
      const heading = byId(id + '-step-' + step).querySelector('.wizard-title');
      if (focus !== false && heading && step > 1) {
        heading.tabIndex = -1;
        heading.focus({preventScroll: true});
        byId(id).scrollIntoView({block: 'start', behavior: 'smooth'});
      }
    };
    byId(id + '-back').addEventListener('click', () => self.show(Math.max(1, self.step - 1)));
    for (let index = 1; index < count; index++) {
      const next = byId(id + '-next-' + index);
      if (next) next.addEventListener('click', () => self.show(index + 1));
    }
    return self;
  }

  function renderChoices(container) {
    for (const label of container.querySelectorAll('.choice')) {
      const input = label.querySelector('input');
      label.classList.toggle('on', input.checked);
      label.classList.toggle('off', input.disabled);
    }
  }

  function chosenKind() {
    const input = document.querySelector('input[name="exp-kind"]:checked');
    return input ? input.value : 'geometry_audit';
  }
  function chooseKind(kind) {
    const input = document.querySelector('input[name="exp-kind"][value="' + kind + '"]');
    if (input) input.checked = true;
    expWizard.show(1, false);
  }
  function chosenSize() {
    const input = document.querySelector('input[name="exp-size"]:checked');
    return input ? input.value : 'standard';
  }

  const expWizard = wizard('exp', 3, () => renderWizard());
  for (const input of document.querySelectorAll('#exp input')) input.addEventListener('change', () => renderWizard());

  function sizeCount(size) {
    return status && status.drives.sizes ? status.drives.sizes[size] : null;
  }

  function renderWizard() {
    renderChoices(byId('exp-kind'));
    renderChoices(byId('exp-size'));
    const kind = chosenKind();
    byId('exp-kind-explain').textContent = KIND_EXPLAIN[kind];
    if (!status) return;
    const counts = status.drives.counts;
    for (const size of ['quick', 'standard', 'all']) {
      const count = sizeCount(size);
      byId('exp-size-' + size).textContent = count === null ? '–' : plural(count, 'segment', 'segments')
        + (size === 'all' ? ' from ' + plural(counts.ready_routes, 'drive', 'drives') : '');
    }
    const summary = byId('exp-summary');
    const rows = [['Experiment', KIND_LABEL[kind]],
      ['Drives', plural(sizeCount(chosenSize()) || 0, 'segment', 'segments') + ', spread over every drive'],
      ['From', status.drives.source ? status.drives.source.name : 'no drives folder']];
    if (kind === 'geometry_audit') {
      rows.push(['Runs on', 'the CPU, ' + plural(status.audit_jobs, 'segment', 'segments') + ' at a time']);
      rows.push(['Measures', 'pose rotation, direction of travel, three-frame reprojection, timing']);
    } else {
      const gpu = status.system.gpu;
      rows.push(['Runs on', gpu ? 'the GPU (' + gpu.name.replace(/^Tesla /, '') + '), alone' : 'a GPU (none found)']);
      rows.push(['Model', 'Depth Anything V2 Small, metric outdoor, frozen (Apache-2.0)']);
      rows.push(['Measures', 'distance error by range, with and without one correction factor']);
    }
    if (same('exp-summary', rows)) return;
    summary.replaceChildren();
    for (const [label, value] of rows) {
      summary.appendChild(node('dt', '', label));
      summary.appendChild(node('dd', '', value));
    }
  }

  byId('btn-exp-start').addEventListener('click', async () => {
    const job = await runAction(chosenKind(), {size: chosenSize()});
    if (job) expWizard.show(1, false);
  });

  // -- results ---------------------------------------------------------------------------------
  function resultTitle(result) {
    return KIND_LABEL[result.kind] || result.label || result.kind;
  }

  function renderResults() {
    const results = data.results;
    if (!results) return;
    const finished = results.filter((result) => result.summary);
    byId('results-empty').hidden = results.length > 0;
    if (!chosenResult || !results.some((result) => result.id === chosenResult)) {
      chosenResult = finished.length ? finished[0].id : (results.length ? results[0].id : null);
    }
    const hash = '#results' + (chosenResult ? '/' + chosenResult : '');
    if (currentTab === 'results' && location.hash !== hash) history.replaceState(null, '', hash);
    if (same('result-picker', [results.map((result) => [result.id, !!result.summary]), chosenResult])) return;
    const picker = byId('result-picker');
    picker.replaceChildren();
    for (const result of results) {
      const card = node('button', 'result-card');
      card.type = 'button';
      card.setAttribute('role', 'radio');
      card.setAttribute('aria-checked', String(result.id === chosenResult));
      card.appendChild(node('strong', '', resultTitle(result)));
      card.appendChild(node('span', '', fmtTime(result.started) + ' · ' + plural(result.segments || 0, 'segment', 'segments')));
      card.appendChild(node('span', '', result.summary ? 'finished' : 'running or failed: no report'));
      card.addEventListener('click', () => { chosenResult = result.id; renderResults(); });
      picker.appendChild(card);
    }
    const view = byId('result-view');
    view.replaceChildren();
    const result = results.find((item) => item.id === chosenResult);
    if (!result) return;
    if (!result.summary) {
      const card = node('div', 'card');
      card.appendChild(node('p', 'empty', 'This experiment has no report: it is still running, it was stopped or it '
        + 'failed. Its log on Home tells why.'));
      view.appendChild(card);
      return;
    }
    if (result.kind === 'geometry_audit') renderGeometry(view, result);
    else renderBaseline(view, result);
  }

  function card(title, lead) {
    const box = node('div', 'card');
    box.appendChild(node('h2', '', title));
    if (lead) box.appendChild(node('p', 'lead', lead));
    return box;
  }

  function table(head, rows, numeric) {
    const wrap = node('div', 'table-wrap');
    const element = node('table', 'bands');
    const thead = node('thead');
    const top = node('tr');
    head.forEach((label, index) => top.appendChild(node('th', numeric[index] ? 'num' : '', label)));
    thead.appendChild(top);
    element.appendChild(thead);
    const body = node('tbody');
    for (const row of rows) {
      const line = node('tr');
      row.forEach((cell, index) => {
        const td = node('td', numeric[index] ? 'num' : '');
        if (cell instanceof Node) td.appendChild(cell);
        else td.textContent = cell;
        line.appendChild(td);
      });
      body.appendChild(line);
    }
    element.appendChild(body);
    wrap.appendChild(element);
    return wrap;
  }

  function renderGeometry(view, result) {
    const summary = result.summary;
    const strict = summary.strict || {};
    const third = strict.third_view_px || {};
    const full = third.full_pose || {};
    const rotation = third.rotation_only || {};
    const gain = strict.third_view_gain_vs_rotation_only;
    const good = full.median !== undefined && full.median < 2 && gain > 0.8;
    const verdict = node('p', 'verdict ' + (good ? 'ok' : 'warn'));
    verdict.textContent = good
      ? 'The car\'s motion explains what the camera sees: predicted from the localizer, points land within '
        + num(full.median, 1) + ' px of where they really are a tenth of a second later, ' + percent(gain)
        + ' better than ignoring the travel. A multi-frame network can rely on these poses. The distance scale '
        + 'still needs one real measurement (a known distance).'
      : 'The poses explain the camera only partly (' + num(full.median, 1) + ' px, ' + percent(gain)
        + ' better than the rotation alone): look at the drives below before relying on them.';
    const head = card(resultTitle(result), fmtTime(result.started) + ' · ' + plural(summary.segments || 0, 'segment', 'segments')
      + ' · ' + plural(strict.pairs || 0, 'clear frame pair', 'clear frame pairs') + ' of ' + (summary.triplets || 0));
    head.appendChild(verdict);
    const tiles = node('div', 'tiles four result-tiles');
    tiles.appendChild(tile(num(full.median, 2) + ' px', 'third view, full pose', 'median; 90% below ' + num(full.p90, 1) + ' px'));
    tiles.appendChild(tile(num(rotation.median, 1) + ' px', 'rotation only', 'what ignoring the travel gives'));
    tiles.appendChild(tile(num((strict.rotation_error_deg || {}).median, 3) + '°', 'rotation error', 'pose vs camera, per pair'));
    tiles.appendChild(tile(num((strict.translation_direction_error_deg || {}).median, 1) + '°', 'direction of travel',
      'error of the heading of the motion'));
    head.appendChild(tiles);
    view.appendChild(head);

    const speeds = strict.third_view_full_pose_px_by_speed || {};
    const speedCard = card('By speed', 'Third-view error with the full pose: faster means more travel between frames.');
    speedCard.appendChild(table(['Speed', 'Pairs', 'Median', '90% below'],
      Object.keys(speeds).map((band) => [band, String(speeds[band].n), num(speeds[band].median, 2) + ' px',
        num(speeds[band].p90, 2) + ' px']), [false, true, true, true]));
    view.appendChild(speedCard);

    const timing = summary.timing || [];
    const timingCard = card('Camera timing', 'The shift of the frame times that best fits the rotations, per segment. '
      + 'On a straight road at a steady turn rate a shift cannot show: a flat curve means "not observable", not "zero".');
    if (timing.length) {
      const shifts = timing.map((row) => row.best_shift_ms).sort((a, b) => a - b);
      const depths = timing.map((row) => row.curve_spread_deg).sort((a, b) => a - b);
      const middle = (list) => list[Math.floor(list.length / 2)];
      const signed = (value) => (value > 0 ? '+' : '') + value;
      timingCard.appendChild(node('p', 'verdict', 'The best shift goes from ' + signed(shifts[0]) + ' to '
        + signed(shifts[shifts.length - 1]) + ' ms between segments (median ' + signed(middle(shifts)) + ' ms), ' + timing.filter((row) => row.at_border).length
        + ' of them at the limit of the search, and the curves are almost flat (median depth ' + num(middle(depths), 3)
        + '°). When the segments disagree this much, these drives do not show the timing.'));
    }
    timingCard.appendChild(table(['Segment', 'Best shift', 'Curve depth', 'At the limit'],
      timing.map((row) => [row.segment, (row.best_shift_ms > 0 ? '+' : '') + row.best_shift_ms + ' ms',
        num(row.curve_spread_deg, 3) + '°', row.at_border ? 'yes (±60 ms)' : '']),
      [false, true, true, false]));
    view.appendChild(timingCard);
    view.appendChild(card('Scale', String(summary.scale || 'not checked')));
  }

  function ratioBar(ratio) {
    // Model / reference on a log scale: the middle line is "exact", left too near, right too far.
    const bar = node('div', 'ratio-bar');
    const fill = node('span');
    const position = Math.max(0, Math.min(1, 0.5 + Math.log2(ratio || 1) / 4));
    fill.style.left = Math.min(50, position * 100) + '%';
    fill.style.width = Math.abs(position * 100 - 50) + '%';
    bar.appendChild(fill);
    bar.title = 'model / reference = ' + num(ratio, 2);
    return bar;
  }

  function renderBaseline(view, result) {
    const summary = result.summary;
    const b0 = summary.B0_held_out || summary.B0_all || {};
    const b1 = summary.B1_held_out || {};
    const bands0 = summary.B0_held_out_by_band || summary.B0_all_by_band || {};
    const bands1 = summary.B1_held_out_by_band || {};
    const near = bands0['10-30 m'];
    const far = bands0['60-80 m'];
    let text = 'Zero-shot, the model is off by ' + percent(b0.absrel) + ' on average on drives it was not tuned on';
    if (b1.absrel !== undefined) text += '; one correction factor (×' + num(summary.B1_scale_from_development, 3) + ') brings it to ' + percent(b1.absrel);
    text += '.';
    if (near && far) {
      text += ' It squeezes distances towards the middle: things at 10–30 m look ×' + num(near.median_ratio_model_over_reference, 2)
        + ' farther, things at 60–80 m ×' + num(far.median_ratio_model_over_reference, 2) + ' as far. One factor cannot fix that.';
    }
    const verdict = node('p', 'verdict');
    verdict.textContent = text;
    const head = card(resultTitle(result), fmtTime(result.started) + ' · ' + plural(summary.segments || 0, 'segment', 'segments')
      + ' from ' + plural(summary.routes || 0, 'drive', 'drives') + ' · reference: ' + (summary.kind || ''));
    head.appendChild(verdict);
    const tiles = node('div', 'tiles four result-tiles');
    tiles.appendChild(tile(percent(b0.absrel), 'average error, as is', 'AbsRel on held-out drives'));
    tiles.appendChild(tile(percent(b1.absrel), 'with one factor', 'factor fitted on the other drives'));
    tiles.appendChild(tile(percent(b0['delta_1.25']), 'within 25%', 'share of points'));
    tiles.appendChild(tile(String(b0.points || 0), 'reference points', 'static points, measured from motion'));
    head.appendChild(tiles);
    view.appendChild(head);

    const bandCard = card('By distance', 'Bar: the model\'s distance against the reference, log scale. Left of the line '
      + 'it sees things too near, right of it too far.');
    const rows = [];
    for (const band of Object.keys(bands0)) {
      const zero = bands0[band];
      if (!zero) continue;
      const one = bands1[band];
      rows.push([band, String(zero.points), percent(zero.absrel), one ? percent(one.absrel) : '–',
        '×' + num(zero.median_ratio_model_over_reference, 2), ratioBar(zero.median_ratio_model_over_reference)]);
    }
    bandCard.appendChild(table(['Range', 'Points', 'Error', 'With factor', 'Model / truth', ''], rows,
      [false, true, true, true, true, false]));
    view.appendChild(bandCard);

    if (result.pictures && result.pictures.length) {
      const gallery = card('What the model sees', 'Each picture: the road camera on top, the model\'s depth below.');
      const scale = node('div', 'depth-scale');
      scale.appendChild(node('span', '', '0 m'));
      scale.appendChild(node('i'));
      scale.appendChild(node('span', '', '80 m'));
      gallery.appendChild(scale);
      const grid = node('div', 'gallery');
      for (const name of result.pictures) {
        const button = node('button');
        button.type = 'button';
        const image = node('img');
        image.loading = 'lazy';
        image.alt = 'Frame ' + name;
        image.src = '/picture/' + result.id + '/' + name;
        button.appendChild(image);
        const [segment, frame] = name.replace('.jpg', '').split('-');
        button.appendChild(node('span', '', 'Segment ' + segment + ' · ' + (Number(frame) / 20).toFixed(1) + ' s'));
        button.addEventListener('click', () => openPicture(result.id, name));
        grid.appendChild(button);
      }
      gallery.appendChild(grid);
      view.appendChild(gallery);
    }
  }

  function openPicture(id, name) {
    openInfo('Depth', name.replace('.jpg', ''), (body) => {
      const image = node('img', 'picture-full');
      image.alt = 'Road camera and depth';
      image.src = '/picture/' + id + '/' + name;
      body.appendChild(image);
      const scale = node('div', 'depth-scale');
      scale.appendChild(node('span', '', 'near'));
      scale.appendChild(node('i'));
      scale.appendChild(node('span', '', '80 m'));
      body.appendChild(scale);
    });
  }


  // -- the comma: its recordings, looked at on demand (never on the poll) --------------------------
  const comma = {state: 'unchecked', at: 0, recordings: [], promise: null};
  let commaTicker = null;
  function commaWait() { return comma.at ? Math.max(0, Math.ceil((comma.at + COMMA_EVERY_MS - Date.now()) / 1000)) : 0; }
  // At most once every 30 s; timeoutMs gives up on the page side (the comma itself may take 10 s to answer).
  function lookOnComma(timeoutMs) {
    if (comma.promise) return comma.promise;
    if (commaWait() > 0) return Promise.resolve(comma);
    const controller = new AbortController();
    const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
    comma.state = 'checking';
    comma.at = Date.now();
    comma.promise = api('/api/comma/recordings', {signal: controller.signal}).then((answer) => {
      comma.state = 'ok';
      comma.recordings = answer.recordings || [];
    }, (error) => {
      comma.state = 'unreachable';
      comma.recordings = [];
      if (error.code && error.code !== 'comma_unreachable') toast(error.message, true);
    }).then(() => {
      clearTimeout(timer);
      comma.promise = null;
      renderTab();
      startCommaTicker();
      return comma;
    });
    renderTab();
    startCommaTicker();
    return comma.promise;
  }
  function startCommaTicker() {
    updateCommaButtons();
    if (commaTicker) return;
    commaTicker = setInterval(() => {
      updateCommaButtons();
      if (!commaWait() && comma.state !== 'checking') {
        clearInterval(commaTicker);
        commaTicker = null;
      }
    }, 1000);
  }
  // A button that looks on the comma, with the reason it waits under it.
  function commaButton(label, className, after) {
    const wrap = node('div', 'action');
    const element = button(label, className, async () => {
      await lookOnComma(15000);
      if (after) after();
    });
    element.dataset.commaButton = '1';
    wrap.appendChild(element);
    wrap.appendChild(node('div', 'reason comma-wait'));
    setTimeout(updateCommaButtons, 0);
    return wrap;
  }
  function updateCommaButtons() {
    const wait = commaWait();
    for (const element of document.querySelectorAll('[data-comma-button]')) {
      element.disabled = comma.state === 'checking' || wait > 0;
      const reason = element.parentElement.querySelector('.comma-wait');
      if (!reason) continue;
      reason.textContent = comma.state === 'checking' ? 'Looking on the comma…'
        : (wait > 0 ? 'You can look again in ' + wait + ' s.' : '');
    }
  }

  // Copying a recording from the comma (fetch_recordings): which one, and how it went.
  let fetching = null;
  async function getRecording(remote) {
    const job = await runAction('fetch_recordings', {recordings: remote.names.slice(0, MAX_FETCH)});
    if (job) fetching = {route: remote.route, job: job.id};
    renderTab();
  }
  function fetchState(route) {
    if (!fetching || fetching.route !== route) return null;
    const job = status ? status.jobs.find((item) => item.id === fetching.job) : null;
    if (job && job.state === 'running') return 'running';
    if (radarEntry(route)) return 'done';
    return job ? 'failed' : 'running';
  }

  // Short recordings: the copied ones (with their radar) and the ones still on the comma, newest first.
  function recordingItems(shortOnly) {
    const items = [];
    if (data.radar) {
      for (const entry of data.radar.summary.drives) {
        if (entry.kind !== 'recording' || (shortOnly && entry.segments > SHORT_SEGMENTS)) continue;
        items.push({route: entry.route, number: entry.number, entry: entry});
      }
    }
    if (comma.state === 'ok') {
      for (const remote of comma.recordings) {
        if (!remote.short || remote.copied || items.some((item) => item.route === remote.route)) continue;
        items.push({route: remote.route, number: remote.counter, remote: remote});
      }
    }
    items.sort((a, b) => b.number - a.number);
    return items;
  }
  function standstillsOf(detail) {
    const list = [];
    for (const segment of detail.segments) {
      if (!segment.read) continue;
      segment.standstills.forEach((still, index) => {
        const length = still.to_s - still.from_s;
        // How long the radar actually gave that reading: it had a target only part of the time standing still,
        // and held this one value for most of that.
        const share = (isNumber(still.target_share) ? still.target_share : 1) * (isNumber(still.steady_share) ? still.steady_share : 1);
        list.push({segment: segment.segment, index: index, from_s: still.from_s, to_s: still.to_s,
          reading_m: still.reading_m, length: length, held: length * share});
      });
    }
    return list;
  }
  function longest(stills) {
    return stills.reduce((best, still) => (!best || still.held > best.held ? still : best), null);
  }
  // "standing still 18 s · reading 2.25 m for 12 s of it"
  function steadyText(still) {
    return 'standing still ' + fmtSpan(still.length) + ' · reading ' + fmtM('reading', still.reading_m) + ' for '
      + fmtSpan(still.held) + ' of it';
  }
  // "Standing still 0:02–0:19 · the radar read 2.25 m for 12 s of it"
  function stillLine(still) {
    return 'Standing still ' + fmtClock(still.from_s) + '–' + fmtClock(still.to_s) + ' · the radar read '
      + fmtM('reading', still.reading_m) + ' for ' + fmtSpan(still.held) + ' of it';
  }
  // What a copied recording holds, once its readings are here.
  function recordingFacts(item) {
    const entry = item.entry;
    if (!radarSettled(item.route)) return 'its radar is being read';
    if (!entry.read) return 'its radar is not read yet';
    if (!entry.has_radar) return entry.no_radar_reason === 'old_software' ? 'no radar in its log' : 'nothing ahead';
    const cached = freshDetail(item.route);
    if (cached && cached.detail) {
      const best = longest(standstillsOf(cached.detail));
      return best ? steadyText(best) : noStillReason(cached.detail, entry, true);
    }
    ensureDetail(item.route).then(() => renderTab(), () => {});
    return 'looking at it…';
  }
  // Why a drive or a recording has no moment for a radar test: no radar in it, never still long enough, or no
  // reading the radar held while still. Short: the words of a list row.
  function noStillReason(detail, entry, short) {
    const noun = detail.kind === 'recording' ? 'recording' : 'drive';
    const read = detail.segments.filter((segment) => segment.read);
    if (!read.length) return short ? 'its radar is not read yet' : 'Its radar is not read yet';
    if (!read.some((segment) => segment.has_radar)) {
      if (entry && entry.no_radar_reason === 'old_software') {
        return short ? 'no radar in its log' : 'There is no radar in the log of this ' + noun;
      }
      return short ? 'nothing ahead' : 'The radar saw nothing ahead in this ' + noun;
    }
    const still = Math.max(0, ...read.map((segment) => segment.longest_still_s || 0));
    if (still < STANDSTILL_MIN_S) {
      return short ? 'never still for ' + STANDSTILL_MIN_S + ' s' : 'The car never stood still for ' + STANDSTILL_MIN_S
        + ' s in this ' + noun + (still >= 0.5 ? ' (' + still.toFixed(1) + ' s at most)' : '');
    }
    return short ? 'no steady reading' : 'The radar never held a steady reading while the car stood still in this ' + noun;
  }

  // -- Radar ------------------------------------------------------------------------------------
  byId('radar-help').addEventListener('click', () => openTopic('radar'));
  let showWithout = false;

  function renderRadar() {
    if (radarView === 'list') renderRadarList();
    else if (radarView === 'drive') renderRadarDrive();
    else renderTest(false);
  }

  function renderRadarList() {
    const radar = data.radar;
    const cards = [byId('radar-drives'), byId('radar-short')];
    if (!radar) {
      if (same('radar-setup', data.radarError ? 'error' : 'loading')) return;
      for (const element of cards) { element.hidden = true; same.cache[element.id] = null; }
      const box = byId('radar-setup');
      box.replaceChildren();
      if (data.radarError) box.appendChild(node('p', 'empty', 'Couldn\'t load the radar. Trying again in a few seconds.'));
      else box.appendChild(skeletonRows(3, 'Looking for the radar in your drives…'));
      return;
    }
    for (const element of cards) element.hidden = false;
    renderSetupCard(radar.calibration);
    renderDrivesCard(radar.summary, radar.calibration);
    renderShortCard(radar.summary);
  }

  function coverageStrip(cal) {
    const figure = node('figure', 'coverage');
    const [low, high] = cal.label_range_m || [5, 30];
    const left = 12;
    const right = 306;
    const axis = 40;
    const x = (metres) => left + Math.min(metres, 40) / 40 * (right - left);
    const tested = cal.tested_m || [];
    const svg = svgNode('svg', {viewBox: '0 0 320 64', class: 'coverage-svg', role: 'img', 'aria-label':
      (tested.length ? 'Tested at ' + tested.map((d) => d.toFixed(2) + ' m').join(', ') : 'No test yet')
      + '. Labels at ' + low + ' to ' + high + ' m.'});
    svg.appendChild(svgNode('rect', {x: x(low), y: 8, width: x(high) - x(low), height: 32, rx: 5, class: 'cov-band'}));
    svg.appendChild(svgNode('text', {x: (x(low) + x(high)) / 2, y: 22, 'text-anchor': 'middle', class: 'cov-band-label'},
      'where the labels will be'));
    svg.appendChild(svgNode('line', {x1: left, x2: right, y1: axis, y2: axis, class: 'cov-axis'}));
    for (let metres = 0; metres <= 40; metres += 10) {
      svg.appendChild(svgNode('line', {x1: x(metres), x2: x(metres), y1: axis, y2: axis + 5, class: 'cov-axis'}));
      svg.appendChild(svgNode('text', {x: x(metres), y: 58, 'text-anchor': metres === 40 ? 'end' : 'middle',
        class: 'cov-label'}, metres === 40 ? '40 m' : String(metres)));
    }
    for (const distance of tested) {
      svg.appendChild(svgNode('circle', {cx: x(distance), cy: axis, r: 5, class: 'cov-dot'}));
    }
    figure.appendChild(svg);
    figure.appendChild(node('figcaption', 'note', 'Tested distances. Confirmed pairs become labels after a test in the '
      + 'shaded band.' + (tested.length ? '' : ' No test saved on this site yet.')));
    return figure;
  }

  function renderSetupCard(cal) {
    const box = byId('radar-setup');
    if (same('radar-setup', cal)) return;
    box.replaceChildren();
    box.classList.toggle('folded', cal.status === 'ready');
    if (cal.status === 'ready') {
      const line = node('button', 'fold-line');
      line.type = 'button';
      line.appendChild(node('span', 'ok-text', 'Set up ✓'));
      line.appendChild(node('span', '', ' · tested ' + tapeRange(cal.tested_m)));
      line.appendChild(node('span', 'chevron', '›'));
      line.addEventListener('click', () => go('settings/radar'));
      box.appendChild(line);
      return;
    }
    const head = node('div', 'card-head');
    head.appendChild(node('h2', '', 'Set-up'));
    head.appendChild(calPill(cal));
    box.appendChild(head);
    const buttons = node('div', 'buttons');
    if (cal.status === 'not_set') {
      box.appendChild(node('p', 'lead', 'The site doesn\'t know yet where your radar sits. Until then, readings are shown as '
        + 'the radar gives them: from the radar, not from the comma.'));
      buttons.appendChild(button('Set it up, about 10 minutes', 'primary', () => openTest({})));
      box.appendChild(buttons);
      return;
    }
    if (cal.status === 'no_radar') {
      box.appendChild(node('p', 'lead', 'Your car is set as having no radar: drives are used without it. If it has one after '
        + 'all, a tape test sets it up.'));
      buttons.appendChild(button('Set it up after all', '', () => openTest({})));
      box.appendChild(buttons);
      return;
    }
    box.appendChild(node('p', 'formula-line', shortFormula(cal)));
    box.appendChild(coverageStrip(cal));
    // Farther away while no kept test is at 5 m or more; once one is, a test is a test.
    const farther = !farTested(cal) && (cal.status === 'close_only' || (cal.tested_m || []).length > 0);
    buttons.appendChild(button(farther ? 'Test it farther away' : 'Test with a tape', 'primary',
      () => openTest(farther ? {target: 'car'} : {})));
    buttons.appendChild(button('Change the numbers', '', () => go('settings/radar')));
    box.appendChild(buttons);
  }

  const NO_RADAR_REASON = {
    same_software: 'Same software as your drives with radar: nothing was ahead.',
    old_software: 'Most likely recorded before the new software.'};

  function driveRow(entry) {
    const row = node('button', 'radar-row');
    row.type = 'button';
    const text = node('span', 'radar-row-text');
    text.appendChild(node('span', 'radar-row-title', driveTitle(entry)));
    const facts = [driveNumber(entry), fmtSpan(entry.duration_s),
      'something ahead ' + percent(entry.target_share) + ' of the time'];
    if (isNumber(entry.minimum_from_comma_m)) {
      facts.push(entry.minimum_from_comma_m.toFixed(1) + '–' + fmtM('comma', entry.maximum_from_comma_m) + ' from the comma');
    } else if (isNumber(entry.minimum_m)) {
      facts.push('readings ' + entry.minimum_m.toFixed(2) + '–' + fmtM('reading', entry.maximum_m));
    }
    text.appendChild(node('span', 'radar-row-detail', facts.join(' · ')));
    text.appendChild(barNode(entry.target_share * 100, 'run'));
    row.appendChild(text);
    const chevron = node('span', 'chevron', '›');
    chevron.setAttribute('aria-hidden', 'true');
    row.appendChild(chevron);
    row.addEventListener('click', () => go('radar/' + entry.route));
    return row;
  }

  function renderDrivesCard(summary) {
    const box = byId('radar-drives');
    const drives = summary.drives.filter((entry) => entry.kind === 'drive');
    const noFolder = !!status && status.drives.source === null;
    if (same('radar-drives', [drives, noFolder, showWithout])) return;
    box.replaceChildren();
    box.appendChild(node('h2', '', 'Drives with radar'));
    const withRadar = drives.filter((entry) => entry.has_radar);
    const without = drives.filter((entry) => !entry.has_radar);
    if (!drives.length && noFolder) {
      box.appendChild(emptyState({text: 'No drives on this server yet. Choose the drives folder in Settings.',
        button: ['Open Settings', () => go('settings')]}));
    } else if (!withRadar.length) {
      box.appendChild(emptyState({art: 'road', title: 'No drive with the radar yet', text: 'Only drives recorded on the new '
        + 'software, from the evening of 23 October, carry it. Just drive: they show up here by themselves once '
        + 'openpilot.distill has downloaded and localized them.',
        button: ['Why only new drives?', () => openTopic('new_drives'), '']}));
    } else {
      const list = node('div', 'radar-rows');
      for (const entry of withRadar) list.appendChild(driveRow(entry));
      box.appendChild(list);
    }
    const unread = drives.reduce((sum, entry) => sum + entry.segments - entry.read, 0);
    if (unread) box.appendChild(node('p', 'note', 'The radar of ' + plural(unread, 'segment', 'segments')
      + ' is still being read: drives show up here as their segments finish.'));
    if (!without.length) return;
    const fold = node('div', 'fold');
    const head = node('div', 'fold-head');
    head.appendChild(node('span', 'fold-title', 'Drives without radar readings (' + without.length + ')'));
    const toggle = button(showWithout ? 'Hide' : 'Show', 'small', () => { showWithout = !showWithout; renderTab(); });
    toggle.setAttribute('aria-expanded', String(showWithout));
    toggle.setAttribute('aria-controls', 'radar-without');
    head.appendChild(toggle);
    fold.appendChild(head);
    const list = node('ul', 'muted-list');
    list.id = 'radar-without';
    list.hidden = !showWithout;
    for (const entry of without) {
      const item = node('li');
      item.appendChild(node('span', 'muted-title', driveTitle(entry) + ' · ' + driveNumber(entry)));
      const reason = entry.read < entry.segments && !entry.no_radar_reason ? 'Its radar is not read yet.'
        : NO_RADAR_REASON[entry.no_radar_reason] || '';
      if (reason) item.appendChild(node('span', 'muted-detail', reason));
      list.appendChild(item);
    }
    const footer = node('li', 'muted-foot', 'They still count for the camera experiments in Lab.');
    list.appendChild(footer);
    fold.appendChild(list);
    box.appendChild(fold);
  }

  function commaPill() {
    if (comma.state === 'checking') return pill('Looking on the comma…', 'run');
    if (comma.state === 'ok') return pill('Comma reachable', 'ok');
    if (comma.state === 'unreachable') return pill('Comma not reachable', 'muted');
    return pill('Not checked yet', 'muted');
  }
  function commaUnreachable(after) {
    const box = node('div', 'callout warn');
    box.appendChild(node('span', '', 'The comma doesn\'t answer. Is it switched on and on the same Wi-Fi as this server? '
      + 'The site uses the comma set up in openpilot.distill.'));
    box.appendChild(commaButton('Try again', 'small', after));
    return box;
  }

  function renderShortCard() {
    const box = byId('radar-short');
    const items = recordingItems(false);
    const facts = items.map((item) => item.entry ? recordingFacts(item) : '');
    const fetchStates = items.map((item) => fetchState(item.route));
    const key = [items, facts, fetchStates, comma.state, comma.recordings,
      status ? actionReason('fetch_recordings') : ''];
    if (same('radar-short', key)) { updateCommaButtons(); return; }
    box.replaceChildren();
    const head = node('div', 'card-head');
    head.appendChild(node('h2', '', 'Short tests'));
    head.appendChild(commaPill());
    box.appendChild(head);
    box.appendChild(node('p', 'lead', 'Recordings made standing still, for example in front of a wall. They stay on the comma '
      + 'until you get them; the site only reads them.'));
    if (comma.state === 'unreachable') box.appendChild(commaUnreachable());
    const list = node('div', 'test-rows');
    items.forEach((item, index) => {
      const row = node('div', 'test-row');
      const text = node('div', 'test-row-text');
      const buttons = node('div', 'test-row-buttons');
      if (item.entry) {
        const parts = ['Recording no. ' + item.number, fmtSpan(item.entry.duration_s), facts[index]];
        text.appendChild(node('span', 'test-row-title', parts.join(' · ')));
        buttons.appendChild(button('Open', 'small', () => go('radar/' + item.route)));
        if (item.entry.standstills && item.entry.segments <= SHORT_SEGMENTS) {
          buttons.appendChild(button('Use for a test', 'small primary', () => openTest({recording: item.route})));
        }
      } else {
        const remote = item.remote;
        const parts = ['Recording no. ' + item.number, 'up to ' + plural(remote.segments.length, 'min', 'min'),
          'on the comma'];
        if (!remote.has_video) parts.push('no video');
        text.appendChild(node('span', 'test-row-title', parts.join(' · ')));
        const state = fetchStates[index];
        if (state === 'running') {
          text.appendChild(node('span', 'test-row-detail', 'Getting it from the comma, then reading its radar…'));
        } else if (state === 'failed') {
          text.appendChild(node('span', 'test-row-detail bad-text', 'Couldn\'t get it: open the job for the details.'));
        }
        const reason = status ? actionReason('fetch_recordings') : '';
        const get = button('Get it', 'small primary', () => getRecording(remote));
        get.disabled = state === 'running' || reason !== '';
        buttons.appendChild(get);
        if (reason) text.appendChild(node('span', 'test-row-detail', 'Not now: ' + reason + '.'));
      }
      row.appendChild(text);
      row.appendChild(buttons);
      list.appendChild(row);
    });
    if (items.length) box.appendChild(list);
    if (comma.state === 'ok') {
      const short = comma.recordings.filter((remote) => remote.short);
      if (!short.length) box.appendChild(node('p', 'empty', 'No short recordings on the comma.'));
      else if (short.every((remote) => remote.copied || items.some((item) => item.entry && item.route === remote.route))) {
        box.appendChild(node('p', 'empty', 'Every short recording on the comma is already here.'));
      }
    } else if (!items.length) {
      box.appendChild(node('p', 'empty', 'No short recording on this server yet. Look on the comma for the ones you made.'));
    }
    const buttons = node('div', 'buttons');
    buttons.appendChild(commaButton('Look on the comma', ''));
    box.appendChild(buttons);
    box.appendChild(node('p', 'note', 'Listed in the order the comma made them: its clock can be wrong.'));
  }

  // -- Radar › one drive or recording ------------------------------------------------------------
  // rv: what the view shows. t is the cursor (seconds into the drive, on a sample of the chart); the picture
  // and its badge follow it once the picture has arrived.
  // loading: the one picture asked of the server at a time (each costs ffmpeg seconds of decoding); the newest
  // wanted one is asked when it is done, the ones stepped over never are.
  const rv = {route: null, index: null, shown: null, mode: 'all', picture: '', wanted: '', loading: '', timer: null,
    dragging: false, el: null, scale: null};
  const CHART_SPAN_S = 120;

  function driveValues(detail) {
    return calibrated(detail.calibration) ? detail.chart.from_comma_m : detail.chart.reading_m;
  }
  function chartTotal(detail) {
    const times = detail.chart.t_s;
    const read = detail.segments.filter((segment) => segment.read);
    const end = read.length ? read[read.length - 1].from_s + read[read.length - 1].duration_s : 0;
    return Math.max(end, times.length ? times[times.length - 1] : 0);
  }
  function sampleSpacing(times) {
    if (times.length < 2) return 0.1;
    return Math.max(0.05, (times[times.length - 1] - times[0]) / (times.length - 1));
  }
  function nearestIndex(times, t) {
    let low = 0;
    let high = times.length - 1;
    if (high < 0) return null;
    while (high - low > 1) {
      const middle = (low + high) >> 1;
      if (times[middle] <= t) low = middle; else high = middle;
    }
    return Math.abs(times[high] - t) < Math.abs(times[low] - t) ? high : low;
  }
  function segmentAt(detail, t) {
    let found = null;
    for (const segment of detail.segments) {
      if (!segment.read) continue;
      if (segment.from_s <= t + 1e-6) found = segment;
    }
    return found;
  }
  function firstReading(detail) {
    const values = driveValues(detail);
    const stills = detail.kind === 'recording' ? standstillsOf(detail) : [];
    if (stills.length) {
      const best = longest(stills);
      const index = nearestIndex(detail.chart.t_s, (best.from_s + best.to_s) / 2);
      if (index !== null) return index;
    }
    const index = values.findIndex((value) => value !== null && value !== undefined);
    return index >= 0 ? index : 0;
  }

  function renderRadarDrive() {
    const box = byId('radar-drive');
    if (rv.route !== radarRoute) {
      rv.route = radarRoute;
      rv.index = null;
      rv.shown = null;
      rv.mode = 'all';
      rv.picture = '';
      rv.wanted = '';
      rv.loading = '';
      same.cache['radar-drive'] = null;
    }
    const cached = details[radarRoute];
    if (!cached) {
      if (same('radar-drive', 'loading')) return;
      box.replaceChildren(driveHead('Radar', ''), skeletonRows(1), node('div', 'skeleton-band'));
      return;
    }
    if (cached.missing) {
      if (same('radar-drive', 'missing')) return;
      const card = node('div', 'card');
      card.appendChild(emptyState({text: 'This drive is no longer on the server.', button: ['Back to Radar', () => go('radar')]}));
      box.replaceChildren(driveHead('Radar', ''), card);
      return;
    }
    const detail = cached.detail;
    const entry = radarEntry(radarRoute);
    if (same('radar-drive', [detail, entry])) return;
    buildDriveView(box, detail, entry);
  }

  function driveHead(title, sub) {
    const head = node('header', 'page-head');
    const back = node('button', 'icon-btn wizard-back');
    back.type = 'button';
    back.setAttribute('aria-label', 'Back to Radar');
    const svg = svgNode('svg', {viewBox: '0 0 20 20', 'aria-hidden': 'true'});
    svg.appendChild(svgNode('path', {d: 'M12.5 4.5 7 10l5.5 5.5', stroke: 'currentColor', 'stroke-width': '2',
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', fill: 'none'}));
    back.appendChild(svg);
    back.addEventListener('click', () => go('radar'));
    head.appendChild(back);
    head.appendChild(node('h2', 'wizard-title', title));
    if (sub) head.appendChild(node('p', 'wizard-sub', sub));
    return head;
  }

  function buildDriveView(box, detail, entry) {
    const cal = detail.calibration;
    const values = driveValues(detail);
    const times = detail.chart.t_s;
    const recording = detail.kind === 'recording';
    const total = chartTotal(detail);
    const named = entry || {kind: detail.kind, number: detail.number, start_from: null, start_unix: null};
    const share = entry ? entry.target_share : (values.length ? values.filter((v) => v !== null).length / values.length : 0);
    const noun = recording ? 'recording' : 'drive';
    // Read, and not one reading: "something ahead 0% of the time" would say the radar ran and saw nothing.
    const radarless = detail.segments.some((segment) => segment.read)
      && !detail.segments.some((segment) => segment.read && segment.has_radar);
    const oldSoftware = radarless && !!entry && entry.no_radar_reason === 'old_software';
    const sub = recording
      ? fmtSpan(total) + ' · copied from the comma'
      : driveNumber(named) + ' · ' + fmtSpan(total) + (radarless ? '' : ' · something ahead ' + percent(share) + ' of the time');
    if (rv.index === null || rv.index >= times.length) rv.index = firstReading(detail);
    const hasVideo = detail.segments.some((segment) => segment.read && segment.video);
    const anyReading = values.some((value) => value !== null && value !== undefined);
    const el = {};
    rv.el = el;
    box.replaceChildren();
    box.appendChild(driveHead(driveTitle(named), sub));
    if (!calibrated(cal)) {
      const callout = node('div', 'callout warn');
      callout.appendChild(node('span', '', cal && cal.status === 'no_radar'
        ? 'Your car is set as having no radar: these are the radar\'s own readings. Set it up to see distances from the comma.'
        : 'These are raw radar readings. Set up the radar to see distances from the comma.'));
      callout.appendChild(button('Set it up', 'small', () => openTest({})));
      box.appendChild(callout);
    }
    const grid = node('div', 'rv-grid');

    // The picture, with what the radar said written on it.
    const pictureCard = node('div', 'card rv-picture-card');
    const picture = node('div', 'rv-picture');
    el.picture = picture;
    el.img = node('img', 'rv-img');
    el.img.alt = 'The road camera at this moment';
    el.img.hidden = true;
    el.img.decoding = 'async';
    picture.appendChild(el.img);
    el.wait = node('div', 'rv-wait');
    el.waitSpinner = node('span', 'spinner');
    el.waitText = node('span', '', 'Preparing the picture…');
    el.wait.appendChild(el.waitSpinner);
    el.wait.appendChild(el.waitText);
    picture.appendChild(el.wait);
    el.busy = node('span', 'spinner rv-busy');
    el.busy.hidden = true;
    picture.appendChild(el.busy);
    el.time = node('span', 'rv-chip rv-time');
    el.speed = node('span', 'rv-chip rv-speed');
    picture.appendChild(el.time);
    picture.appendChild(el.speed);
    el.badge = node('div', 'rv-badge');
    el.badge.setAttribute('aria-live', 'polite');
    picture.appendChild(el.badge);
    pictureCard.appendChild(picture);
    const controls = node('div', 'rv-controls');
    const back = button('◀ 1 s', '', () => stepSeconds(-1));
    back.setAttribute('aria-label', 'One second back');
    const forward = button('1 s ▶', '', () => stepSeconds(1));
    forward.setAttribute('aria-label', 'One second forward');
    const ahead = button('Next vehicle ahead ▶|', '', nextVehicle);
    ahead.disabled = !anyReading;
    controls.appendChild(back);
    controls.appendChild(forward);
    controls.appendChild(ahead);
    pictureCard.appendChild(controls);
    grid.appendChild(pictureCard);

    // The chart: distance over time, with real gaps, and the speed under it.
    const chartCard = node('div', 'card rv-chart-card');
    const chartHead = node('div', 'chart-head');
    chartHead.appendChild(node('h2', '', 'Distance to the vehicle ahead'));
    if (total > CHART_SPAN_S * 1.25) {
      const switcher = node('div', 'segmented small');
      switcher.setAttribute('role', 'radiogroup');
      switcher.setAttribute('aria-label', 'Time shown');
      for (const [mode, label] of [['all', recording ? 'Whole recording' : 'Whole drive'], ['window', '2 minutes']]) {
        const option = node('button', '', label);
        option.type = 'button';
        option.setAttribute('role', 'radio');
        option.setAttribute('aria-checked', String(rv.mode === mode));
        option.addEventListener('click', () => {
          rv.mode = mode;
          for (const other of switcher.children) other.setAttribute('aria-checked', String(other === option));
          drawCharts();
        });
        switcher.appendChild(option);
      }
      chartHead.appendChild(switcher);
    }
    chartCard.appendChild(chartHead);
    el.readout = node('p', 'chart-readout');
    el.readout.setAttribute('aria-live', 'polite');
    chartCard.appendChild(el.readout);
    el.distance = node('div', 'rv-chart-wrap');
    chartCard.appendChild(el.distance);
    el.tooltip = node('div', 'tooltip');
    el.tooltip.hidden = true;
    el.distance.appendChild(el.tooltip);
    chartCard.appendChild(node('div', 'rv-chart-label', 'Your speed (km/h)'));
    el.speedChart = node('div', 'rv-chart-wrap speed');
    chartCard.appendChild(el.speedChart);
    // Only where there are distances: a drive without one reading needs no word on how far they hold.
    const line = anyReading ? calibrationLine(cal) : null;
    if (line) chartCard.appendChild(line);
    if (!anyReading) {
      const reason = oldSoftware ? 'No radar in this log: most likely recorded before the new software.'
        : 'The radar saw nothing ahead in this ' + noun + '.';
      chartCard.appendChild(node('p', 'callout', reason));
    }
    grid.appendChild(chartCard);

    // Beside the picture: the recording's still moments, how the number is made, the facts.
    const side = node('div', 'rv-side');
    if (recording) {
      const stills = standstillsOf(detail);
      const card = node('div', 'card');
      card.appendChild(node('h2', '', 'Standing still'));
      if (stills.length) {
        for (const still of stills) {
          card.appendChild(node('p', 'callout', stillLine(still)));
        }
        card.appendChild(button('Use it for a radar test', 'primary block-mobile', () => openTest({recording: detail.route})));
      } else {
        card.appendChild(node('p', 'empty', noStillReason(detail, entry) + ': it can\'t make a radar test.'));
      }
      if (!hasVideo) card.appendChild(node('p', 'note', 'No video for this recording: only its radar was copied.'));
      side.appendChild(card);
    }
    const how = node('details', 'card how');
    how.appendChild(node('summary', '', 'How this number is made'));
    el.how = node('p', 'how-text');
    how.appendChild(el.how);
    how.appendChild(linkButton('What is the correction?', () => openTopic('correction')));
    side.appendChild(how);
    const factsCard = node('div', 'card');
    factsCard.appendChild(node('h2', '', recording ? 'This recording' : 'This drive'));
    const kv = node('dl', 'kv facts');
    const facts = [];
    if (radarless) {
      facts.push(['Radar', oldSoftware ? 'not in this log' : 'nothing ahead in this ' + noun]);
    } else if (entry) {
      facts.push(['Radar readings', thousands(entry.measurements)]);
      facts.push(['Something ahead', percent(entry.target_share) + ' of the time']);
      if (isNumber(entry.minimum_m)) {
        const fromComma = isNumber(entry.minimum_from_comma_m);
        facts.push(['Closest', fromComma ? fmtM('comma', entry.minimum_from_comma_m) : 'reading ' + fmtM('reading', entry.minimum_m)]);
        facts.push(['Farthest', fromComma ? fmtM('comma', entry.maximum_from_comma_m) : 'reading ' + fmtM('reading', entry.maximum_m)]);
      }
    }
    const relative = detail.segments.some((segment) => segment.read && segment.relative_speed_valid);
    if (!radarless) {
      facts.push(['Speed of the vehicle ahead', relative ? 'sent by the radar, not shown yet' : 'not sent by the radar']);
    }
    facts.push(['Video', hasVideo ? 'on this server' : 'not on this server']);
    for (const [label, value] of facts) {
      kv.appendChild(node('dt', '', label));
      kv.appendChild(node('dd', '', value));
    }
    factsCard.appendChild(kv);
    side.appendChild(factsCard);
    grid.appendChild(side);
    box.appendChild(grid);

    rv.detail = detail;
    rv.values = values;
    rv.calibrated = calibrated(cal);
    drawCharts();
    updateCursor();
    if (rv.picture) {
      // Built again (calibration, new readings): the picture of the browser cache shows at once.
      el.img.src = rv.picture;
      el.img.hidden = false;
      el.wait.hidden = true;
    }
    updateBadge();
    schedulePicture(0);
  }

  function windowRange() {
    const total = chartTotal(rv.detail) || 1;
    if (rv.mode !== 'window' || total <= CHART_SPAN_S) return [0, total];
    const t = rv.detail.chart.t_s[rv.index] || 0;
    let start = Math.max(0, t - CHART_SPAN_S / 2);
    start = Math.min(start, total - CHART_SPAN_S);
    return [start, start + CHART_SPAN_S];
  }
  function niceStep(raw, steps) {
    return steps.find((step) => step >= raw) || steps[steps.length - 1];
  }

  function drawCharts() {
    const el = rv.el;
    if (!el || !rv.detail) return;
    const times = rv.detail.chart.t_s;
    const speeds = rv.detail.chart.speed_kmh;
    const values = rv.values;
    const width = Math.max(260, Math.round(el.distance.clientWidth || 600));
    const phone = window.matchMedia('(max-width: 640px)').matches;
    const height = phone ? 160 : 200;
    const pad = {l: 36, r: 10, t: 12, b: 22};
    const [a, b] = windowRange();
    const fx = (t) => pad.l + (t - a) / (b - a || 1) * (width - pad.l - pad.r);
    let top = 0;
    for (let i = 0; i < times.length; i++) if (times[i] >= a && times[i] <= b && values[i] !== null) top = Math.max(top, values[i]);
    const maxV = Math.max(10, Math.ceil(top / 10) * 10);
    const fy = (value) => pad.t + (1 - value / maxV) * (height - pad.t - pad.b);
    const spacing = sampleSpacing(times);
    rv.scale = {a: a, b: b, fx: fx, fy: fy, width: width, height: height, pad: pad};

    const svg = svgNode('svg', {viewBox: '0 0 ' + width + ' ' + height, width: width, height: height, class: 'rv-chart',
      tabindex: '0', role: 'slider', 'aria-label': 'Moment of the drive', 'aria-valuemin': '0',
      'aria-valuemax': String(Math.round(chartTotal(rv.detail)))});
    const yStep = niceStep(maxV / 4, [1, 2, 2.5, 5, 10, 20, 25, 50, 100]);
    for (let value = 0; value <= maxV + 1e-9; value += yStep) {
      svg.appendChild(svgNode('line', {x1: pad.l, x2: width - pad.r, y1: fy(value), y2: fy(value), class: value ? 'gridline' : 'baseline'}));
      svg.appendChild(svgNode('text', {x: pad.l - 6, y: fy(value) + 3.5, 'text-anchor': 'end', class: 'axis-label'},
        String(Math.round(value * 10) / 10)));
    }
    svg.appendChild(svgNode('text', {x: pad.l, y: 9, class: 'axis-label'}, rv.calibrated ? 'm from the comma' : 'm (radar reading)'));
    const xStep = niceStep((b - a) / 5, [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]);
    for (let t = Math.ceil(a / xStep) * xStep; t <= b + 1e-9; t += xStep) {
      svg.appendChild(svgNode('line', {x1: fx(t), x2: fx(t), y1: height - pad.b, y2: height - pad.b + 4, class: 'baseline'}));
      svg.appendChild(svgNode('text', {x: fx(t), y: height - 6, 'text-anchor': 'middle', class: 'axis-label'}, fmtClock(t)));
    }
    // The step line: each reading held until the next sample; no line across a gap.
    let path = '';
    let open = false;
    let previous = null;
    for (let i = 0; i < times.length; i++) {
      const t = times[i];
      if (t < a - spacing) continue;
      if (t > b) break;
      const value = values[i];
      const jump = previous !== null && t - previous > spacing * 3;
      if (open && (value === null || value === undefined || jump)) {
        path += 'H' + fx(Math.min(b, previous + spacing)).toFixed(1);
        open = false;
      }
      if (value === null || value === undefined) { previous = t; continue; }
      const x = fx(Math.max(a, t)).toFixed(1);
      const y = fy(value).toFixed(1);
      path += open ? 'H' + x + 'V' + y : 'M' + x + ' ' + y;
      open = true;
      previous = t;
    }
    if (open && previous !== null) path += 'H' + fx(Math.min(b, previous + spacing)).toFixed(1);
    if (path) svg.appendChild(svgNode('path', {d: path, class: 'line step'}));
    const cursor = svgNode('line', {y1: pad.t, y2: height - pad.b, class: 'cursor'});
    const dot = svgNode('circle', {r: 4.5, class: 'marker'});
    svg.appendChild(cursor);
    svg.appendChild(dot);
    const hit = svgNode('rect', {x: pad.l, y: 0, width: width - pad.l - pad.r, height: height, class: 'hit'});
    svg.appendChild(hit);
    for (const old of el.distance.querySelectorAll('svg')) old.remove();
    el.distance.insertBefore(svg, el.tooltip);
    el.cursor = cursor;
    el.dot = dot;
    attachScrub(svg, true);

    // Speed.
    const speedHeight = 64;
    const speedPad = {t: 6, b: 18};
    let fastest = 0;
    for (let i = 0; i < times.length; i++) if (times[i] >= a && times[i] <= b) fastest = Math.max(fastest, speeds[i] || 0);
    const maxS = Math.max(20, Math.ceil(fastest / 20) * 20);
    const sy = (value) => speedPad.t + (1 - value / maxS) * (speedHeight - speedPad.t - speedPad.b);
    const speedSvg = svgNode('svg', {viewBox: '0 0 ' + width + ' ' + speedHeight, width: width, height: speedHeight,
      class: 'rv-chart speed', 'aria-hidden': 'true'});
    speedSvg.appendChild(svgNode('line', {x1: pad.l, x2: width - pad.r, y1: sy(0), y2: sy(0), class: 'baseline'}));
    speedSvg.appendChild(svgNode('line', {x1: pad.l, x2: width - pad.r, y1: sy(maxS), y2: sy(maxS), class: 'gridline'}));
    speedSvg.appendChild(svgNode('text', {x: pad.l - 6, y: sy(maxS) + 3.5, 'text-anchor': 'end', class: 'axis-label'}, String(maxS)));
    speedSvg.appendChild(svgNode('text', {x: pad.l - 6, y: sy(0) + 3.5, 'text-anchor': 'end', class: 'axis-label'}, '0'));
    let speedPath = '';
    previous = null;
    for (let i = 0; i < times.length; i++) {
      const t = times[i];
      if (t < a - spacing) continue;
      if (t > b) break;
      const command = previous === null || t - previous > spacing * 3 ? 'M' : 'L';
      speedPath += command + fx(Math.max(a, t)).toFixed(1) + ' ' + sy(speeds[i] || 0).toFixed(1);
      previous = t;
    }
    if (speedPath) speedSvg.appendChild(svgNode('path', {d: speedPath, class: 'line speed-line'}));
    const speedCursor = svgNode('line', {y1: speedPad.t, y2: speedHeight - speedPad.b, class: 'cursor'});
    speedSvg.appendChild(speedCursor);
    speedSvg.appendChild(svgNode('rect', {x: pad.l, y: 0, width: width - pad.l - pad.r, height: speedHeight, class: 'hit'}));
    el.speedChart.replaceChildren(speedSvg);
    el.speedCursor = speedCursor;
    attachScrub(speedSvg, false);
    updateCursor();
  }

  function timeFromEvent(svg, event) {
    const scale = rv.scale;
    const box = svg.getBoundingClientRect();
    const x = (event.clientX - box.left) * (scale.width / (box.width || scale.width));
    const t = scale.a + (x - scale.pad.l) / (scale.width - scale.pad.l - scale.pad.r) * (scale.b - scale.a);
    return Math.max(scale.a, Math.min(scale.b, t));
  }
  function attachScrub(svg, keyboard) {
    svg.addEventListener('pointerdown', (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      rv.dragging = true;
      try { svg.setPointerCapture(event.pointerId); } catch (error) { /* already released */ }
      moveTo(nearestIndex(rv.detail.chart.t_s, timeFromEvent(svg, event)), 300);
      showTooltip(svg, event, rv.index);
    });
    svg.addEventListener('pointermove', (event) => {
      const index = nearestIndex(rv.detail.chart.t_s, timeFromEvent(svg, event));
      if (rv.dragging) moveTo(index, 300);
      if (rv.dragging || event.pointerType === 'mouse') showTooltip(svg, event, index);
    });
    const stop = () => {
      if (!rv.dragging) return;
      rv.dragging = false;
      schedulePicture(300);
      if (rv.mode === 'window') drawCharts();
    };
    svg.addEventListener('pointerup', stop);
    svg.addEventListener('pointercancel', () => { stop(); hideTooltip(); });
    svg.addEventListener('pointerleave', () => { if (!rv.dragging) hideTooltip(); });
    if (keyboard) {
      svg.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowLeft') stepSeconds(-1);
        else if (event.key === 'ArrowRight') stepSeconds(1);
        else if (event.key === 'Home') moveTo(0, 300);
        else if (event.key === 'End') moveTo(rv.detail.chart.t_s.length - 1, 300);
        else return;
        event.preventDefault();
      });
    }
  }

  function momentText(index) {
    const chart = rv.detail.chart;
    const parts = [fmtClock(chart.t_s[index])];
    const reading = chart.reading_m[index];
    if (reading === null || reading === undefined) parts.push('no reading');
    else if (rv.calibrated) parts.push(fmtM('comma', chart.from_comma_m[index]) + ' from the comma (reading ' + fmtM('reading', reading) + ')');
    else parts.push('reading ' + fmtM('reading', reading));
    parts.push(Math.round(chart.speed_kmh[index] || 0) + ' km/h');
    return parts.join(' · ');
  }
  function showTooltip(svg, event, index) {
    const tip = rv.el.tooltip;
    if (!svg.classList.contains('speed') && index !== null) {
      tip.textContent = momentText(index);
      tip.hidden = false;
      const wrap = rv.el.distance.getBoundingClientRect();
      const x = event.clientX - wrap.left;
      const left = Math.max(4, Math.min(wrap.width - tip.offsetWidth - 4, x - tip.offsetWidth / 2));
      tip.style.left = left + 'px';
    }
  }
  function hideTooltip() { if (rv.el) rv.el.tooltip.hidden = true; }

  function moveTo(index, delay) {
    if (index === null || index === undefined) return;
    rv.index = Math.max(0, Math.min(rv.detail.chart.t_s.length - 1, index));
    const [a, b] = rv.scale ? [rv.scale.a, rv.scale.b] : [0, 0];
    const t = rv.detail.chart.t_s[rv.index];
    if (rv.mode === 'window' && !rv.dragging && (t < a || t > b)) drawCharts();
    updateCursor();
    if (!rv.picture && !pictureSegment()) updateBadge();
    schedulePicture(delay);
  }
  function stepSeconds(seconds) {
    const times = rv.detail.chart.t_s;
    let index = nearestIndex(times, times[rv.index] + seconds);
    if (index === rv.index) index += seconds > 0 ? 1 : -1;
    moveTo(index, 300);
  }
  function nextVehicle() {
    const times = rv.detail.chart.t_s;
    const values = rv.values;
    const spacing = sampleSpacing(times);
    for (let i = rv.index + 1; i < times.length; i++) {
      const starts = values[i] !== null && (values[i - 1] === null || times[i] - times[i - 1] > spacing * 3);
      if (starts) { moveTo(i, 0); return; }
    }
    toast('No other stretch with a reading after this one.');
  }

  function updateCursor() {
    const el = rv.el;
    if (!el || !rv.scale || !el.cursor) return;
    const index = rv.index;
    const t = rv.detail.chart.t_s[index];
    const x = rv.scale.fx(t).toFixed(1);
    for (const line of [el.cursor, el.speedCursor]) {
      if (!line) continue;
      line.setAttribute('x1', x);
      line.setAttribute('x2', x);
    }
    const value = rv.values[index];
    el.dot.setAttribute('visibility', value === null || value === undefined ? 'hidden' : 'visible');
    if (value !== null && value !== undefined) {
      el.dot.setAttribute('cx', x);
      el.dot.setAttribute('cy', rv.scale.fy(value).toFixed(1));
    }
    el.readout.textContent = momentText(index);
    const svg = el.distance.querySelector('svg');
    if (svg) {
      svg.setAttribute('aria-valuenow', String(Math.round(t)));
      svg.setAttribute('aria-valuetext', momentText(index));
    }
    updateHow(index);
  }

  function updateHow(index) {
    const chart = rv.detail.chart;
    const cal = rv.detail.calibration;
    let text;
    if (!rv.calibrated) {
      text = 'Without the set-up, the site shows the radar\'s own number, measured from the front bumper. The radar counts '
        + 'in steps of 0.25 m.';
    } else {
      const reading = chart.reading_m[index];
      const said = reading === null || reading === undefined ? 20 : reading;
      text = (reading === null || reading === undefined ? 'For example, radar said ' : 'Radar said ') + fmtM('reading', said)
        + ' ' + term(cal.radar_correction_m) + ' correction ' + term(cal.radar_to_camera_m) + ' to the comma = '
        + fmtM('comma', said + cal.radar_to_reference_m) + '. The radar counts in steps of 0.25 m.';
    }
    rv.el.how.textContent = text;
  }

  // The badge and the chips describe the picture on screen (it follows the cursor once loaded).
  function updateBadge() {
    const el = rv.el;
    if (!el) return;
    const index = rv.shown === null ? rv.index : rv.shown;
    const chart = rv.detail.chart;
    el.time.textContent = fmtClock(chart.t_s[index]);
    el.speed.textContent = Math.round(chart.speed_kmh[index] || 0) + ' km/h';
    const reading = chart.reading_m[index];
    el.badge.replaceChildren();
    el.badge.classList.toggle('none', reading === null || reading === undefined);
    if (reading === null || reading === undefined) {
      el.badge.appendChild(node('strong', '', 'No reading'));
      return;
    }
    if (rv.calibrated) {
      el.badge.appendChild(node('strong', '', fmtM('comma', chart.from_comma_m[index])));
      el.badge.appendChild(node('span', '', 'to the vehicle ahead, from the comma'));
    } else {
      el.badge.appendChild(node('strong', 'raw', 'Reading ' + fmtM('reading', reading)));
      el.badge.appendChild(node('span', '', '(from the radar)'));
    }
    el.badge.appendChild(chip('measured'));
  }

  function pictureSegment() {
    const segment = segmentAt(rv.detail, rv.detail.chart.t_s[rv.index]);
    return segment && segment.video ? segment : null;
  }
  function schedulePicture(delay) {
    clearTimeout(rv.timer);
    rv.timer = setTimeout(loadPicture, delay || 0);
  }
  function loadPicture() {
    const el = rv.el;
    if (!el || currentTab !== 'radar' || radarView !== 'drive') return;
    const index = rv.index;
    const t = rv.detail.chart.t_s[index];
    const segment = pictureSegment();
    if (!segment) {
      rv.shown = null;
      el.busy.hidden = true;
      el.picture.classList.remove('loading');
      el.img.hidden = true;
      el.wait.hidden = false;
      el.waitSpinner.hidden = true;
      el.waitText.textContent = 'No picture here: the video of this minute is not on the server.';
      rv.picture = '';
      rv.wanted = '';
      updateBadge();
      return;
    }
    const ms = Math.max(0, Math.round((t - segment.from_s) * 1000));
    const url = '/picture/radar/' + segment.segment + '/' + ms + '.jpg';
    if (url === rv.picture) {
      // Back on the picture on screen: the one still on its way is not wanted any more.
      rv.wanted = url;
      rv.shown = index;
      el.busy.hidden = true;
      el.picture.classList.remove('loading');
      updateBadge();
      return;
    }
    rv.wanted = url;
    el.picture.classList.add('loading');
    el.busy.hidden = !rv.picture;
    if (!rv.picture) {
      el.wait.hidden = false;
      el.waitSpinner.hidden = false;
      el.waitText.textContent = 'Preparing the picture…';
    }
    if (rv.loading) return;
    rv.loading = url;
    const image = new Image();
    const done = () => {
      // Another drive opened meanwhile: its own request goes on.
      if (rv.loading !== url) return;
      rv.loading = '';
      // Stepped on meanwhile: now the newest one.
      if (rv.wanted && rv.wanted !== rv.picture) loadPicture();
    };
    image.onload = () => {
      if (rv.el === el && rv.wanted === url) {
        rv.picture = url;
        rv.shown = index;
        el.img.src = url;
        el.img.alt = 'The road camera at ' + fmtClock(t);
        el.img.hidden = false;
        el.wait.hidden = true;
        el.busy.hidden = true;
        el.picture.classList.remove('loading');
        updateBadge();
      }
      done();
    };
    image.onerror = () => {
      if (rv.el === el && rv.wanted === url) {
        // Not asked again by itself: the next step asks for another one.
        rv.wanted = '';
        el.busy.hidden = true;
        el.picture.classList.remove('loading');
        if (!rv.picture) {
          el.wait.hidden = false;
          el.waitSpinner.hidden = true;
          el.waitText.textContent = 'This picture can\'t be shown.';
          rv.shown = null;
          updateBadge();
        } else {
          toast('This picture can\'t be shown.', true);
        }
      }
      done();
    };
    image.src = url;
  }
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (currentTab === 'radar' && radarView === 'drive') drawCharts(); }, 150);
  });
  // -- Radar test: two tapes and one still minute become a test (wizard, focus mode) -----------------
  // The state is kept in this tab (sessionStorage), so a phone that reloads the page or locks its screen
  // finds the test where it was; the countdown runs from its start time.
  const RT_KEY = 'perception-radar-test';
  const TARGET_WORDS = {wall: 'Wall', car: 'Parked car'};
  const RING_LENGTH = 2 * Math.PI * 88;
  const MINUTE_S = 60;
  let rt = null;
  let rtBuilt = '';
  let rtTimer = null;
  let rtEl = {};

  function freshTest() {
    return {step: 1, screen: 'step', target: null, targetChosen: false, recording: null, skipMinute: false,
      draftId: null, draftCreated: null, tapeRadar: null, tapeComma: null, marker: null, markerState: 'unchecked',
      minuteStart: null, minuteDone: false, pick: null, from: 'radar', saved: null, keptTyped: false};
  }
  function storeTest() {
    try {
      if (rt) sessionStorage.setItem(RT_KEY, JSON.stringify(rt));
      else sessionStorage.removeItem(RT_KEY);
    } catch (error) {
      // Storage blocked (private window): the test lasts until the page is closed.
    }
  }
  function restoreTest() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(RT_KEY) || 'null');
      if (saved && typeof saved === 'object' && saved.step >= 1 && saved.step <= 6) return Object.assign(freshTest(), saved);
    } catch (error) {
      // Nothing usable kept: a new test.
    }
    return null;
  }
  function routeNumber(route) { return parseInt(route.slice(0, 8), 16); }

  async function openTest(options) {
    options = options || {};
    const here = currentHash();
    const next = freshTest();
    next.from = here === 'radar/test' ? (rt && rt.from) || 'radar' : here;
    if (options.target) {
      next.target = options.target;
      next.targetChosen = true;
    }
    if (options.recording) {
      next.recording = options.recording;
      next.skipMinute = true;
      next.pick = {route: options.recording, segment: null, index: null};
      // Its own standstill says what the radar looked at (a wall close up, or a car farther away) and is the
      // moment of the test: after the tapes comes the result.
      const cached = await ensureDetail(options.recording).catch(() => null);
      const best = cached && cached.detail ? longest(standstillsOf(cached.detail)) : null;
      if (best) {
        next.pick.segment = best.segment;
        next.pick.index = best.index;
        if (!options.target) {
          next.target = best.reading_m < FAR_TEST_M ? 'wall' : 'car';
          next.targetChosen = true;
        }
      }
    }
    if (options.draft) {
      // Read now: a draft saved from another device (the phone in the car park) is not in a list loaded earlier.
      await load('radar', 'radar');
      const drafts = data.radar ? data.radar.drafts : [];
      const draft = options.draft === 'newest' ? drafts[0] : drafts.find((item) => item.id === options.draft);
      if (!draft) {
        toast('This test is no longer waiting: it was finished or deleted.', true);
        return;
      }
      Object.assign(next, {draftId: draft.id, draftCreated: draft.created, target: draft.target, targetChosen: true,
        tapeRadar: draft.tape_radar_m, tapeComma: draft.tape_comma_m, marker: draft.marker_counter,
        markerState: draft.marker_counter === null ? 'failed' : 'ok', skipMinute: true, step: 6});
    }
    closeSheet();
    stopMinute();
    rt = next;
    rtBuilt = '';
    storeTest();
    go('radar/test');
  }

  function leaveTest(ask) {
    const started = rt && rt.screen !== 'saved' && rt.screen !== 'later'
      && (rt.tapeRadar !== null || rt.minuteStart || rt.step > 1);
    if (ask && started && !window.confirm('Leave the radar test? What you entered here is not kept.')) return;
    const back = rt && rt.from && rt.from !== 'radar/test' ? rt.from : 'radar';
    stopMinute();
    rt = null;
    rtBuilt = '';
    storeTest();
    go(back);
  }
  byId('rt-close').addEventListener('click', () => leaveTest(true));
  byId('rt-back').addEventListener('click', () => {
    if (!rt) return;
    if (rt.screen === 'result') gotoStep(6);
    else if (rt.step === 4 && rt.skipMinute) gotoStep(1);
    else if (rt.step > 1) gotoStep(rt.step - 1);
  });

  function gotoStep(step, screen) {
    rt.step = step;
    rt.screen = screen || 'step';
    storeTest();
    renderTest(true, true);
    window.scrollTo(0, 0);
  }

  function updateTestBar() {
    const labels = {result: 'Result', saved: 'Saved', later: 'Saved for later'};
    byId('rt-progress-text').textContent = rt.screen === 'step' ? 'Step ' + rt.step + ' of 6' : labels[rt.screen];
    byId('rt-progress-bar').style.width = (rt.screen === 'step' ? rt.step / 6 * 100 : 100) + '%';
    byId('rt-back').hidden = !((rt.screen === 'step' && rt.step > 1) || rt.screen === 'result');
  }

  function renderTest(force, focusTitle) {
    if (!rt) rt = restoreTest() || freshTest();
    if (rt.screen === 'saved' && !rt.saved) rt.screen = 'step';
    updateTestBar();
    const minute = rt.step === 3 ? (rt.minuteDone ? 'done' : (rt.minuteStart ? 'run' : 'idle')) : '';
    const key = [rt.screen, rt.step, rt.target, minute, rt.saved ? rt.saved.calibration.source : '', rt.keptTyped].join(':');
    if (!force && key === rtBuilt) {
      refreshTestStep();
      return;
    }
    rtBuilt = key;
    rtEl = {};
    same.cache['rt-live'] = null;
    const box = byId('rt');
    box.replaceChildren();
    const section = node('section', 'wizard-step');
    if (rt.screen === 'result') buildResult(section);
    else if (rt.screen === 'saved') buildSaved(section);
    else if (rt.screen === 'later') buildLater(section);
    else [buildStep1, buildStep2, buildStep3, buildStep4, buildStep5, buildStep6][rt.step - 1](section);
    box.appendChild(section);
    storeTest();
    if (rt.step === 3 && rt.minuteStart && !rt.minuteDone) startMinute();
    if (focusTitle) {
      const heading = section.querySelector('.wizard-title');
      if (heading) heading.focus({preventScroll: true});
    }
  }
  function refreshTestStep() {
    if (rt.screen !== 'step') return;
    if (rt.step === 1 && same.cache['rt-1'] !== JSON.stringify(testFacts())) renderTest(true);
    else if (rt.step === 6) renderStep6Live(false);
  }

  function stepHead(section, title, sub) {
    const heading = node('h2', 'wizard-title', title);
    heading.id = 'rt-title';
    heading.tabIndex = -1;
    section.setAttribute('aria-labelledby', 'rt-title');
    section.appendChild(heading);
    if (sub) section.appendChild(node('p', 'wizard-sub', sub));
  }
  function pinnedAction(section, primary, extra) {
    const action = node('div', 'action wizard-action pinned');
    if (primary) action.appendChild(primary);
    const reason = node('div', 'reason');
    reason.setAttribute('aria-live', 'polite');
    action.appendChild(reason);
    if (extra) action.appendChild(extra);
    section.appendChild(action);
    return reason;
  }
  function checklistNode(rows) {
    const list = node('ul', 'checklist');
    for (const [kind, title, detail] of rows) {
      const item = node('li');
      item.appendChild(node('span', 'check-mark ' + kind, kind === 'ok' ? '✓' : (kind === 'muted' ? '•' : '!')));
      const text = node('div', 'check-text');
      text.appendChild(node('div', 'check-title', title));
      if (detail) text.appendChild(node('div', 'check-detail', detail));
      item.appendChild(text);
      list.appendChild(item);
    }
    return list;
  }
  function spinnerLine(text, extra) {
    const line = node('p', 'rt-status');
    line.appendChild(node('span', 'spinner'));
    line.appendChild(node('span', '', text));
    if (extra) line.appendChild(extra);
    return line;
  }

  // A distance in metres, 1 cm steps; typed with a comma or a dot.
  function metreStepper(options) {
    const wrap = node('div', 'stepper big' + (options.signed ? ' signed' : ''));
    const minus = node('button', 'stepper-button', MINUS);
    minus.type = 'button';
    minus.setAttribute('aria-label', 'One centimetre less');
    const field = node('label', 'stepper-value');
    const input = node('input', 'stepper-input');
    input.type = 'text';
    input.inputMode = options.signed ? 'text' : 'decimal';
    input.autocomplete = 'off';
    input.spellcheck = false;
    input.placeholder = '0.00';
    input.enterKeyHint = 'done';
    input.setAttribute('aria-label', options.label);
    if (options.id) input.id = options.id;
    field.appendChild(input);
    field.appendChild(node('span', 'stepper-unit', 'm'));
    const plus = node('button', 'stepper-button', '+');
    plus.type = 'button';
    plus.setAttribute('aria-label', 'One centimetre more');
    wrap.appendChild(minus);
    wrap.appendChild(field);
    wrap.appendChild(plus);
    let value = options.value;
    const show = () => { input.value = isNumber(value) ? (options.signed ? signedText(value, 2) : value.toFixed(2)) : ''; };
    const set = (next, typed) => {
      value = next;
      if (!typed) show();
      wrap.classList.toggle('empty', value === null);
      options.onChange(value);
    };
    const step = (delta) => {
      const start = typeof options.start === 'function' ? options.start() : options.start;
      const base = isNumber(value) ? value : start;
      set(round2(Math.max(options.min, Math.min(options.max, base + (isNumber(value) ? delta : 0)))));
    };
    for (const [element, delta] of [[minus, -0.01], [plus, 0.01]]) {
      let timer = null;
      let repeat = null;
      let repeated = false;
      const stop = () => { clearTimeout(timer); clearInterval(repeat); timer = null; repeat = null; };
      element.addEventListener('pointerdown', () => {
        repeated = false;
        stop();
        timer = setTimeout(() => { repeat = setInterval(() => { repeated = true; step(delta); }, 70); }, 450);
      });
      for (const name of ['pointerup', 'pointerleave', 'pointercancel']) element.addEventListener(name, stop);
      element.addEventListener('click', () => {
        if (repeated) { repeated = false; return; }
        step(delta);
      });
    }
    input.addEventListener('input', () => set(parseMetres(input.value), true));
    input.addEventListener('blur', () => { if (isNumber(value)) show(); });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowUp') step(0.01);
      else if (event.key === 'ArrowDown') step(-0.01);
      else if (event.key === 'Enter') input.blur();
      else return;
      event.preventDefault();
    });
    show();
    wrap.classList.toggle('empty', value === null || value === undefined);
    return {element: wrap, input: input};
  }

  function keptTests() { return data.radar ? data.radar.tests.tests.filter((test) => test.kept) : []; }
  function testFacts() {
    const radar = data.radar;
    return radar ? [keptTests().map((test) => test.tape_radar_m), radar.drafts.map((draft) => draft.id)] : null;
  }

  // Step 1: what the radar will look at.
  function buildStep1(section) {
    same.cache['rt-1'] = JSON.stringify(testFacts());
    const kept = keptTests();
    const needFar = !kept.some((test) => test.tape_radar_m >= FAR_TEST_M);
    if (!rt.targetChosen) rt.target = kept.length && needFar ? 'car' : 'wall';
    const drafts = data.radar ? data.radar.drafts : [];
    if (drafts.length && !rt.draftId) {
      const callout = node('div', 'callout');
      callout.appendChild(node('span', '', drafts.length === 1 ? 'One test is still waiting for its recording.'
        : drafts.length + ' tests are still waiting for their recording.'));
      callout.appendChild(button('Finish it', 'small', () => openTest({draft: drafts[0].id})));
      section.appendChild(callout);
    }
    if (rt.recording) {
      section.appendChild(node('p', 'callout', 'Recording no. ' + routeNumber(rt.recording) + ' is attached' + (chosenStill()
        ? ': after this question, only the two tape measurements are left.' : ': after the two tape measurements, pick the '
        + 'moment it stood still.')));
    }
    stepHead(section, 'What will the radar look at?', 'A test takes about 10 minutes and a tape measure.');
    const grid = node('div', 'choice-grid rt-targets');
    grid.setAttribute('role', 'radiogroup');
    grid.setAttribute('aria-labelledby', 'rt-title');
    const explain = node('p', 'wizard-explain');
    const words = {
      wall: ['A wall, close', '1.5 to 2 m from a garage wall or a closed door. The easiest one.',
        'Tells how much the radar reads too long, close up.'],
      car: ['A parked car, farther', 'About 10 m behind a parked car.' + (needFar ? ' The test still missing.' : ''),
        'Checks the correction where the labels will be, at 5–30 m.']};
    for (const target of ['wall', 'car']) {
      const label = node('label', 'choice');
      const input = node('input');
      input.type = 'radio';
      input.name = 'rt-target';
      input.value = target;
      input.checked = rt.target === target;
      input.addEventListener('change', () => {
        rt.target = target;
        rt.targetChosen = true;
        storeTest();
        renderChoices(grid);
        explain.textContent = words[target][2];
      });
      label.appendChild(input);
      label.appendChild(art(target, 'choice-art'));
      const text = node('span', 'choice-text');
      text.appendChild(node('strong', '', words[target][0]));
      text.appendChild(node('span', '', words[target][1]));
      label.appendChild(text);
      if (target === 'car' && needFar) label.appendChild(node('span', 'pill todo choice-tag', 'Needed'));
      label.appendChild(checkMarkSvg());
      grid.appendChild(label);
    }
    section.appendChild(grid);
    renderChoices(grid);
    explain.textContent = words[rt.target][2];
    section.appendChild(explain);
    pinnedAction(section, button('Continue', 'primary block', () => gotoStep(rt.skipMinute ? 4 : 2)));
  }

  // Step 2: park, then the newest recording number on the comma is the marker.
  function buildStep2(section) {
    const car = rt.target === 'car';
    stepHead(section, car ? 'Park behind the other car' : 'Park facing the wall',
      car ? 'In a straight line, about 10 m behind it, on flat ground.'
        : 'Straight on, with the bumper about 1.5–2 m from the wall.');
    section.appendChild(art(car ? 'park-car' : 'park-wall', 'wizard-art'));
    section.appendChild(checklistNode((car ? ['Both cars straight, one behind the other.',
      'The other car stays still; switched off is fine.', 'You need a 10 m tape or a laser meter.',
      'Once parked, switch your car off.']
      : ['Stop straight, not at an angle.', 'Nobody and nothing between the car and the wall.',
        'Once parked, switch the car off.']).map((text) => ['ok', text])));
    const line = node('div', 'rt-status-area');
    line.setAttribute('aria-live', 'polite');
    section.appendChild(line);
    let skipped = false;
    const primary = button('I\'m parked, car off', 'primary block', async () => {
      primary.disabled = true;
      skipped = false;
      line.replaceChildren(spinnerLine('Looking for the comma…', linkButton('Skip', () => {
        skipped = true;
        rt.marker = null;
        rt.markerState = 'skipped';
        gotoStep(3);
      })));
      const answer = await lookOnComma(10000);
      if (!rt || rt.step !== 2 || skipped) return;
      if (answer.state === 'ok') {
        rt.marker = answer.recordings.length ? answer.recordings[0].counter : 0;
        rt.markerState = 'ok';
        line.replaceChildren(node('p', 'rt-status ok-text', 'Comma reachable ✓: the site will know which recording is new.'));
      } else {
        rt.marker = null;
        rt.markerState = 'failed';
        line.replaceChildren(node('p', 'rt-status', 'Can\'t reach the comma from here. No problem: you\'ll pick the '
          + 'recording later.'));
      }
      storeTest();
      setTimeout(() => { if (rt && rt.step === 2 && rt.screen === 'step' && !skipped) gotoStep(3); }, 1000);
    });
    pinnedAction(section, primary);
  }

  // Step 3: one still minute, counted from its start time.
  function buildStep3(section) {
    const state = rt.minuteDone ? 'done' : (rt.minuteStart ? 'run' : 'idle');
    stepHead(section, state === 'done' ? 'Now switch the car off' : 'Switch on and keep still for one minute',
      state === 'done' ? 'The recording on the comma ends when the car is off.'
        : 'Car on, foot off the pedals, nobody in front of it.');
    const ring = node('div', 'ring');
    const svg = svgNode('svg', {viewBox: '0 0 200 200', 'aria-hidden': 'true'});
    svg.appendChild(svgNode('circle', {cx: 100, cy: 100, r: 88, class: 'ring-track'}));
    const arc = svgNode('circle', {cx: 100, cy: 100, r: 88, class: 'ring-arc' + (state === 'done' ? ' done' : ''),
      transform: 'rotate(-90 100 100)', 'stroke-dasharray': RING_LENGTH.toFixed(1),
      'stroke-dashoffset': '0'});
    svg.appendChild(arc);
    ring.appendChild(svg);
    const text = node('div', 'ring-text', state === 'done' ? '0:00' : '1:00');
    text.setAttribute('role', 'timer');
    ring.appendChild(text);
    section.appendChild(ring);
    rtEl.arc = arc;
    rtEl.clock = text;
    if (!('wakeLock' in navigator) && state !== 'done') section.appendChild(node('p', 'note center', 'Keep the screen on until the end.'));
    if (state === 'idle') {
      pinnedAction(section, button('Start the minute', 'primary block', () => {
        rt.minuteStart = Date.now();
        rt.minuteDone = false;
        storeTest();
        renderTest(true);
      }));
    } else if (state === 'run') {
      const early = linkButton('Finish early', () => {
        rt.minuteDone = true;
        storeTest();
        stopMinute();
        renderTest(true, true);
      });
      early.hidden = Date.now() - rt.minuteStart < 20000;
      rtEl.early = early;
      const wrap = node('div', 'center');
      wrap.appendChild(early);
      pinnedAction(section, null, wrap);
    } else {
      const again = linkButton('Record again', () => {
        rt.minuteStart = null;
        rt.minuteDone = false;
        storeTest();
        renderTest(true, true);
      });
      const wrap = node('div', 'center');
      wrap.appendChild(again);
      pinnedAction(section, button('Done, it\'s off', 'primary block', () => gotoStep(4)), wrap);
    }
  }
  function startMinute() {
    if (!rtTimer) rtTimer = setInterval(tickMinute, 250);
    tickMinute();
  }
  function stopMinute() {
    clearInterval(rtTimer);
    rtTimer = null;
  }
  function tickMinute() {
    if (!rt || rt.screen !== 'step' || rt.step !== 3 || !rt.minuteStart || rt.minuteDone) { stopMinute(); return; }
    const elapsed = (Date.now() - rt.minuteStart) / 1000;
    const left = Math.max(0, MINUTE_S - elapsed);
    if (left <= 0) {
      rt.minuteDone = true;
      storeTest();
      stopMinute();
      if (currentTab === 'radar' && radarView === 'test') renderTest(true, true);
      return;
    }
    if (rtEl.clock) rtEl.clock.textContent = fmtClock(Math.ceil(left));
    if (rtEl.arc) rtEl.arc.setAttribute('stroke-dashoffset', (RING_LENGTH * (1 - left / MINUTE_S)).toFixed(1));
    if (rtEl.early) rtEl.early.hidden = elapsed < 20;
  }

  // Steps 4 and 5: the two tapes.
  function buildStep4(section) {
    const car = rt.target === 'car';
    stepHead(section, car ? 'How far is the other car from the radar?' : 'How far is the wall from the radar?',
      car ? 'From the back of the other car to the front of your bumper, in front of the radar. Keep the tape straight and tight.'
        : 'Tape from the wall to the front of your bumper, right in front of the radar. Use the same spot every time.');
    section.appendChild(art(car ? 'tape-car' : 'tape-wall', 'wizard-art'));
    const check = node('p', 'rt-check');
    check.setAttribute('aria-live', 'polite');
    const primary = button('Continue', 'primary block', () => gotoStep(5));
    const stepper = metreStepper({value: rt.tapeRadar, min: TAPE_RANGE_M[0], max: TAPE_RANGE_M[1], start: car ? 10 : 1.5,
      label: 'From the ' + (car ? 'other car' : 'wall') + ' to the bumper, in metres', id: 'rt-tape-radar',
      onChange: (value) => { rt.tapeRadar = value; storeTest(); update(); }});
    section.appendChild(stepper.element);
    section.appendChild(check);
    const reason = pinnedAction(section, primary);
    function update() {
      const value = rt.tapeRadar;
      let message = '';
      let bad = false;
      if (value === null) {
        reason.textContent = 'Write the distance first.';
      } else if (!isNumber(value) || value < TAPE_RANGE_M[0] || value > TAPE_RANGE_M[1]) {
        message = 'Between 0.30 and 100 m.';
        bad = true;
        reason.textContent = '';
      } else {
        reason.textContent = '';
        if (!car && value < 0.8) message = 'Very close: the radar may see nothing. Back up to 1.5–2 m and measure again.';
        else if (!car && value > 5) message = 'Walls farther than a few metres are often not seen. Try the parked-car test.';
        else if (car && (value < 3 || value > 40)) message = 'For this test, 5 to 30 m works best.';
      }
      check.textContent = message;
      check.className = 'rt-check ' + (bad ? 'bad-text' : 'warn-text');
      primary.disabled = value === null || bad;
    }
    update();
  }

  function buildStep5(section) {
    const car = rt.target === 'car';
    const thing = car ? 'other car' : 'wall';
    stepHead(section, 'And how far is the comma?', car ? 'From the back of the other car to the comma, along the side of your car.'
      : 'From the wall to the comma behind the windshield, along the side of the car.');
    section.appendChild(art(car ? 'comma-car' : 'comma-wall', 'wizard-art'));
    const cal = (data.radar && data.radar.calibration) || (status && status.radar) || {};
    const saved = isNumber(cal.radar_to_camera_m) ? cal.radar_to_camera_m : null;
    const live = node('p', 'rt-live');
    live.setAttribute('aria-live', 'polite');
    const check = node('p', 'rt-check');
    check.setAttribute('aria-live', 'polite');
    // A recording attached with its moment (from its own view): straight to the result, step 6 is a step back.
    const primary = button('Continue', 'primary block', () => gotoStep(6, rt.recording && chosenStill() ? 'result' : 'step'));
    const stepper = metreStepper({value: rt.tapeComma, min: TAPE_RANGE_M[0], max: TAPE_RANGE_M[1],
      start: () => round2((rt.tapeRadar || 0) + (saved === null ? 1.5 : saved)),
      label: 'From the ' + thing + ' to the comma, in metres', id: 'rt-tape-comma',
      onChange: (value) => { rt.tapeComma = value; storeTest(); update(); }});
    section.appendChild(stepper.element);
    section.appendChild(live);
    section.appendChild(check);
    const reason = pinnedAction(section, primary);
    function update() {
      const value = rt.tapeComma;
      const radar = rt.tapeRadar;
      let message = '';
      let bad = false;
      live.textContent = '';
      reason.textContent = '';
      if (value === null) {
        reason.textContent = 'Write the distance first.';
      } else if (!isNumber(value) || value < TAPE_RANGE_M[0] || value > TAPE_RANGE_M[1]) {
        message = 'Between 0.30 and 100 m.';
        bad = true;
      } else {
        const apart = round2(value - radar);
        if (apart <= 0) { message = 'The comma must be farther from the ' + thing + ' than the radar.'; bad = true; }
        else if (apart > OFFSET_RANGE_M[1]) { message = 'More than 5 m from the radar: check the tape.'; bad = true; }
        else {
          live.textContent = 'So the comma sits ' + apart.toFixed(2) + ' m behind the radar.';
          const off = saved === null ? 0 : Math.round(Math.abs(apart - saved) * 100);
          if (saved !== null && off >= 3) {
            message = 'Last time it was ' + saved.toFixed(2) + ' m: this is ' + off + ' cm different. Check that the tape is '
              + 'straight and level.';
          }
        }
      }
      check.textContent = message;
      check.className = 'rt-check ' + (bad ? 'bad-text' : 'warn-text');
      primary.disabled = value === null || bad;
    }
    update();
  }

  // Step 6: which recording; the ones not copied yet are fetched from the comma first.
  function buildStep6(section) {
    stepHead(section, 'Which recording is this test?', 'Newest first, by the comma\'s own count: its clock can be wrong.');
    rtEl.live = node('div', 'rt-live-area');
    section.appendChild(rtEl.live);
    rtEl.primary = button('Use this one', 'primary block', () => gotoStep(6, 'result'));
    let extra = null;
    if (!rt.draftId) {
      extra = node('div', 'center');
      const later = button('Finish later', '', () => finishLater(later));
      extra.appendChild(later);
    }
    rtEl.reason = pinnedAction(section, rtEl.primary, extra);
    renderStep6Live(true);
    if (!commaWait() && comma.state !== 'checking') lookOnComma(10000);
  }

  function chosenStill() {
    const pick = rt && rt.pick;
    if (!pick || pick.remote || pick.segment === null) return null;
    const cached = freshDetail(pick.route);
    if (!cached || !cached.detail) return null;
    return standstillsOf(cached.detail).find((still) => still.segment === pick.segment && still.index === pick.index) || null;
  }

  function renderStep6Live(force) {
    const area = rtEl.live;
    if (!area || !rt) return;
    const items = recordingItems(true);
    // A recording just copied from the comma is a copied one now, once its copy and its radar are done: the
    // folder shows up in the summary while the job still copies the video and reads the radar.
    if (rt.pick && rt.pick.remote && radarSettled(rt.pick.route) && fetchState(rt.pick.route) !== 'running') {
      rt.pick.remote = false;
    }
    if (!rt.pick && rt.marker !== null) {
      const after = items.filter((item) => item.number > rt.marker);
      if (after.length === 1 && after[0].entry) rt.pick = {route: after[0].route, segment: null, index: null};
    }
    const pick = rt.pick;
    const settled = !!pick && !pick.remote && radarSettled(pick.route);
    let cached = settled ? freshDetail(pick.route) : null;
    if (settled && !cached) {
      ensureDetail(pick.route).then(() => renderStep6Live(false), (error) => toast(error.message, true));
    }
    if (cached && cached.detail && pick.segment === null) {
      const best = longest(standstillsOf(cached.detail));
      if (best) { pick.segment = best.segment; pick.index = best.index; }
    }
    if (cached && cached.missing) cached = null;
    const facts = items.map((item) => item.entry ? recordingFacts(item) : '');
    const fetchNow = pick ? fetchState(pick.route) : null;
    const key = [items, facts, pick, cached && cached.detail ? standstillsOf(cached.detail).length : null, fetchNow,
      comma.state, rt.marker,
      status ? actionReason('fetch_recordings') : ''];
    if (!force && same('rt-live', key)) { updateStep6Primary(); return; }
    same.cache['rt-live'] = JSON.stringify(key);
    storeTest();
    area.replaceChildren();
    if (comma.state === 'checking') area.appendChild(spinnerLine('Looking on the comma for new recordings…'));
    else if (comma.state === 'unreachable') {
      const callout = node('div', 'callout warn');
      callout.appendChild(node('span', '', 'The comma isn\'t reachable from here.'));
      callout.appendChild(commaButton('Try again', 'small'));
      area.appendChild(callout);
    }
    const list = node('div', 'pick-list');
    list.setAttribute('role', 'radiogroup');
    list.setAttribute('aria-label', 'Recordings');
    const chosen = node('div', 'rt-chosen');
    chosen.setAttribute('aria-live', 'polite');
    items.forEach((item, index) => {
      list.appendChild(pickCard(item, facts[index]));
      // What the chosen recording holds, right under its card.
      if (pick && item.route === pick.route) list.appendChild(chosen);
    });
    if (items.length) area.appendChild(list);
    else area.appendChild(node('p', 'empty', 'No short recording on this server yet' + (comma.state === 'ok'
      ? ', and none on the comma.' : '.')));
    if (pick) {
      if (fetchNow === 'running' || (pick.remote && fetchNow !== 'failed')) {
        chosen.appendChild(node('p', 'rt-status', 'Getting the recording from the comma, then reading its radar…'));
        const live = node('div', 'job-live');
        live.dataset.actions = 'fetch_recordings';
        chosen.appendChild(live);
      } else if (fetchNow === 'failed' && pick.remote) {
        chosen.appendChild(node('p', 'bad-text', 'Couldn\'t get this recording from the comma. Open the job for the details, '
          + 'or try again.'));
      } else if (!cached) {
        chosen.appendChild(spinnerLine('Reading its radar…'));
      } else {
        const stills = standstillsOf(cached.detail);
        if (!stills.length) {
          const box = node('div', 'callout warn');
          box.appendChild(node('span', '', noStillReason(cached.detail, radarEntry(pick.route)) + '.'));
          const buttons = node('div', 'buttons');
          buttons.appendChild(button('Pick another', 'small', () => { rt.pick = null; storeTest(); renderStep6Live(true); }));
          buttons.appendChild(button('Record again', 'small', () => {
            rt.pick = null;
            rt.minuteStart = null;
            rt.minuteDone = false;
            rt.skipMinute = false;
            gotoStep(3);
          }));
          box.appendChild(buttons);
          chosen.appendChild(box);
        } else if (stills.length === 1) {
          const still = stills[0];
          chosen.appendChild(node('p', 'callout', stillLine(still)));
        } else {
          chosen.appendChild(node('p', 'rt-status', 'It stood still more than once: which moment is the test?'));
          const row = node('div', 'chip-row');
          row.setAttribute('role', 'radiogroup');
          row.setAttribute('aria-label', 'Moments standing still');
          for (const still of stills) {
            const option = node('button', 'chip-choice', fmtClock(still.from_s) + '–' + fmtClock(still.to_s) + ' · '
              + fmtM('reading', still.reading_m));
            option.type = 'button';
            option.setAttribute('role', 'radio');
            option.setAttribute('aria-checked', String(pick.segment === still.segment && pick.index === still.index));
            option.addEventListener('click', () => {
              pick.segment = still.segment;
              pick.index = still.index;
              storeTest();
              renderStep6Live(true);
            });
            row.appendChild(option);
          }
          chosen.appendChild(row);
        }
      }
      if (!chosen.parentNode) area.appendChild(chosen);
    }
    updateStep6Primary();
    if (status) renderLiveJobs();
    updateCommaButtons();
  }

  function pickCard(item, fact) {
    const card = node('button', 'pick-card');
    card.type = 'button';
    card.setAttribute('role', 'radio');
    card.setAttribute('aria-checked', String(!!rt.pick && rt.pick.route === item.route));
    card.appendChild(node('span', 'source-dot'));
    const text = node('span', 'pick-text');
    text.appendChild(node('span', 'pick-title', 'Recording no. ' + item.number + (item.entry ? ' · ' + fmtSpan(item.entry.duration_s) : '')));
    text.appendChild(node('span', 'pick-detail', item.entry ? fact
      : 'on the comma · not copied yet' + (item.remote.has_video ? '' : ' · no video')));
    if (rt.marker !== null && item.number > rt.marker) text.appendChild(pill('made after you parked', 'ok'));
    card.appendChild(text);
    card.addEventListener('click', () => {
      if (item.entry) {
        rt.pick = {route: item.route, segment: null, index: null};
      } else {
        if (fetchState(item.route) === 'running') return;
        const reason = status ? actionReason('fetch_recordings') : 'server status not read yet';
        if (reason) { toast('Can\'t get it now: ' + reason + '.', true); return; }
        rt.pick = {route: item.route, remote: true, segment: null, index: null};
        getRecording(item.remote);
      }
      storeTest();
      renderStep6Live(true);
    });
    return card;
  }

  function updateStep6Primary() {
    if (!rtEl.primary) return;
    const still = chosenStill();
    rtEl.primary.disabled = !still;
    if (still) rtEl.reason.textContent = '';
    else if (!rt.pick) rtEl.reason.textContent = 'Pick the recording of this test.';
    else if (rt.pick.remote) rtEl.reason.textContent = 'Wait until the recording is here.';
    else rtEl.reason.textContent = freshDetail(rt.pick.route) ? 'Pick a moment when the car stood still.' : '';
  }

  async function finishLater(element) {
    element.disabled = true;
    try {
      const answer = await post('/api/radar/drafts', {target: rt.target, tape_radar_m: rt.tapeRadar,
        tape_comma_m: rt.tapeComma, marker_counter: rt.marker});
      if (data.radar) data.radar.drafts = answer.drafts;
      gotoStep(rt.step, 'later');
      refreshStatus();
    } catch (error) {
      rtEl.reason.textContent = error.message;
      element.disabled = false;
    }
  }

  // The result: what the test says alone and with the others, before it is saved.
  function buildResult(section) {
    const still = chosenStill();
    if (!still) {
      rt.screen = 'step';
      rt.step = 6;
      buildStep6(section);
      return;
    }
    const car = rt.target === 'car';
    const reading = still.reading_m;
    const correction = round2(rt.tapeRadar - reading);
    stepHead(section, 'The radar read ' + fmtM('reading', reading), 'Your tape said ' + fmtM('tape', rt.tapeRadar) + '.');
    let verdict = Math.abs(correction) < 0.005 ? 'Reads just right'
      : 'Reads ' + fmtM('tape', Math.abs(correction)) + (correction < 0 ? ' too long' : ' too short');
    if (car) verdict += ' at ' + fmtM('tape', rt.tapeRadar);
    section.appendChild(node('div', 'result-big', verdict));
    const kept = keptTests();
    const corrections = kept.map((test) => test.correction_m).concat([correction]);
    const offsets = kept.map((test) => round2(test.tape_comma_m - test.tape_radar_m)).concat([round2(rt.tapeComma - rt.tapeRadar)]);
    const count = corrections.length;
    const meanCorrection = round2(mean(corrections));
    const meanOffset = round2(mean(offsets));
    const uncertainty = round2(READING_STEP_M / 2 / Math.sqrt(count));
    const fromComma = round2(reading + meanCorrection + meanOffset);
    const apart = Math.abs(round2(fromComma - rt.tapeComma));
    const close = apart <= READING_STEP_M / 2 + 1e-9;
    const rows = [
      ['ok', (count > 1 ? 'With all your tests: ' : 'With this test: ') + 'correction ' + fmtM('correction', meanCorrection)
        + ' ± ' + uncertainty.toFixed(2) + ' m (' + plural(count, 'test', 'tests') + ')'],
      [close ? 'ok' : 'todo', 'From the comma: ' + fmtM('reading', reading) + ' becomes ' + fmtM('tape', fromComma)
        + '. Tape: ' + fmtM('tape', rt.tapeComma) + '. ' + fmtCm(apart) + ' apart' + (close ? ' ✓' : '')],
      ['muted', 'The radar counts in steps of 0.25 m, so one test is good to about ±0.13 m.']];
    const spread = Math.max(...corrections) - Math.min(...corrections);
    if (rt.tapeRadar >= FAR_TEST_M && !kept.some((test) => test.tape_radar_m >= FAR_TEST_M) && spread <= DISAGREE_M) {
      // Ready needs the numbers from the tests: typed ones stay typed when a test is saved.
      const cal = (data.radar && data.radar.calibration) || (status && status.radar) || {};
      rows.push(cal.source === 'typed'
        ? ['todo', 'Your numbers are typed by hand: use your tests, after saving, to make confirmed pairs count as labels.']
        : ['ok', 'With this test, confirmed pairs can become labels.']);
    }
    section.appendChild(checklistNode(rows));
    const others = kept.map((test) => test.correction_m);
    const away = others.length ? Math.abs(correction - mean(others)) : 0;
    const outlier = away > DISAGREE_M;
    const tooFar = correction < CORRECTION_RANGE_M[0] || correction > CORRECTION_RANGE_M[1];
    if (tooFar) {
      section.appendChild(node('p', 'callout bad', 'The tape and the radar reading are more than 1 m apart: check the tape, '
        + 'or that this is the recording of the test.'));
    } else if (outlier) {
      const callout = node('div', 'callout warn');
      callout.appendChild(node('span', '', 'This test is ' + away.toFixed(1) + ' m away from your other tests. Measure again '
        + 'before saving?'));
      callout.appendChild(button('Measure again', 'small', () => gotoStep(4)));
      section.appendChild(callout);
    }
    const save = button(outlier ? 'Save anyway' : 'Save the test', 'primary block', () => saveTest(save, reason));
    save.disabled = tooFar;
    const extra = node('div', 'rt-secondary');
    extra.appendChild(button('Throw it away', '', throwAway));
    extra.appendChild(linkButton('Test again', () => {
      const target = rt.target;
      const from = rt.from;
      const draft = rt.draftId;
      rt = Object.assign(freshTest(), {target: target, targetChosen: true, from: from, draftId: draft});
      gotoStep(2);
    }));
    const reason = pinnedAction(section, save, extra);
  }

  async function saveTest(element, reason) {
    const still = chosenStill();
    if (!still) return;
    element.disabled = true;
    const label = element.textContent;
    element.replaceChildren(node('span', 'spinner'), document.createTextNode(' Saving…'));
    const body = {segment: still.segment, standstill: still.index, tape_radar_m: rt.tapeRadar, tape_comma_m: rt.tapeComma,
      target: rt.target};
    if (rt.draftId) body.draft_id = rt.draftId;
    try {
      const answer = await post('/api/radar/tests', body);
      if (data.radar) {
        data.radar.tests = answer.tests;
        data.radar.calibration = answer.calibration;
        if (rt.draftId) data.radar.drafts = data.radar.drafts.filter((draft) => draft.id !== rt.draftId);
      }
      rt.saved = {calibration: answer.calibration, tests: {kept: answer.tests.kept, correction_m: answer.tests.correction_m}};
      rt.draftId = null;
      const cal = answer.calibration;
      toast(cal.source === 'tests' && calibrated(cal) ? 'Saved. ' + exampleSentence(cal).replace('A reading of 20.00 m is',
        'A reading of 20.00 m is now') : 'Test saved.');
      loadedAt.radar = 0;
      gotoStep(rt.step, 'saved');
      refreshStatus();
    } catch (error) {
      if (error.code === 'unknown_draft') rt.draftId = null;
      reason.textContent = error.message;
      element.textContent = label;
      element.disabled = false;
    }
  }

  async function throwAway() {
    const draft = rt.draftId;
    const question = draft ? 'Throw this test away? Its tape measurements, waiting for a recording, are deleted too.'
      : 'Throw this test away? Nothing is saved.';
    if (!window.confirm(question)) return;
    if (draft) {
      try {
        const answer = await post('/api/radar/drafts/' + draft, {delete: true});
        if (data.radar) data.radar.drafts = answer.drafts;
      } catch (error) {
        if (error.code !== 'unknown_draft') { toast(error.message, true); return; }
      }
      refreshStatus();
    }
    leaveTest(false);
  }

  function buildSaved(section) {
    const saved = rt.saved;
    const cal = saved.calibration;
    section.appendChild(art('check', 'wizard-art'));
    stepHead(section, 'Test saved', cal.source === 'tests' && calibrated(cal)
      ? 'The correction is now ' + fmtM('correction', cal.radar_correction_m) + ', from ' + plural(saved.tests.kept, 'test', 'tests') + '.'
      : 'Your tests give ' + fmtM('correction', saved.tests.correction_m) + ', from ' + plural(saved.tests.kept, 'test', 'tests') + '.');
    if (cal.source === 'typed' && cal.tests_give && !rt.keptTyped) {
      const callout = node('div', 'callout');
      callout.appendChild(node('span', '', 'Your numbers were typed by hand. Use your tests instead?'));
      const buttons = node('div', 'buttons');
      buttons.appendChild(button('Use the tests', 'small primary', async () => {
        try {
          const answer = await post('/api/radar/calibration/apply', {});
          rt.saved.calibration = answer.calibration;
          if (data.radar) data.radar.calibration = answer.calibration;
          toast('Saved. ' + exampleSentence(answer.calibration).replace('A reading of 20.00 m is', 'A reading of 20.00 m is now'));
          storeTest();
          renderTest(true);
          refreshStatus();
        } catch (error) {
          toast(error.message, true);
        }
      }));
      buttons.appendChild(button('Keep my numbers', 'small', () => { rt.keptTyped = true; storeTest(); renderTest(true); }));
      callout.appendChild(buttons);
      section.appendChild(callout);
    }
    const again = node('div', 'rt-secondary');
    again.appendChild(button('Back to Radar', '', () => { rt.from = 'radar'; leaveTest(false); }));
    pinnedAction(section, button('Do another test', 'primary block', () => {
      const from = rt.from;
      rt = Object.assign(freshTest(), {from: from});
      gotoStep(1);
    }), again);
  }

  function buildLater(section) {
    section.appendChild(art('draft', 'wizard-art'));
    stepHead(section, 'Saved for later', 'Your two tape measurements are kept. When the comma is back on your Wi-Fi, tap '
      + 'Finish it on Home or in Settings › Your car\'s radar.');
    pinnedAction(section, button('OK', 'primary block', () => leaveTest(false)));
  }
  // -- Objects: the finder comes next ------------------------------------------------------------
  function renderObjects() {
    const box = byId('objects-empty');
    if (!status) {
      if (!same('objects', 'loading')) box.replaceChildren(skeletonRows(2));
      return;
    }
    const cal = status.radar || {status: 'not_set'};
    const drives = status.next ? status.next.drives_with_radar || 0 : 0;
    if (same('objects', [cal.status, drives])) return;
    let text = 'Finding the cars comes next: first the radar has to be set up and a drive with radar recorded.';
    let action = ['Set up the radar', () => openTest({})];
    if (cal.status === 'no_radar') {
      text = 'Your car is set as having no radar: there is nothing to confirm against.';
      action = ['Open Settings', () => go('settings/radar')];
    } else if (cal.status !== 'not_set' && !drives) {
      text += ' Your radar\'s numbers are in; no drive carries the radar yet.';
      action = ['Go to Radar', () => go('radar')];
    } else if (cal.status !== 'not_set') {
      text = 'Finding the cars comes next: your radar\'s numbers are in and ' + plural(drives, 'drive carries', 'drives carry')
        + ' the radar, ready for the finder.';
      action = ['Open Radar', () => go('radar')];
    }
    box.replaceChildren(emptyState({art: 'boxes', title: 'No objects yet', text: text, button: action}));
  }

  // -- Settings › Your car's radar ------------------------------------------------------------------
  let calEdit = null;

  function renderCalCard() {
    const radar = data.radar;
    const body = byId('cal-body');
    if (!radar) {
      if (!same('cal-card', data.radarError ? 'error' : 'loading')) {
        byId('cal-pill').replaceChildren();
        body.replaceChildren(data.radarError ? node('p', 'empty', 'Couldn\'t load the radar. Trying again in a few seconds.')
          : skeletonRows(2));
      }
      return;
    }
    if (!calEdit && !same('cal-card', [radar.calibration, radar.tests, radar.drafts])) {
      buildCalCard(body, radar.calibration, radar.tests, radar.drafts);
    }
    if (settingsAnchor === 'radar' && !anchorDone && data.settings) {
      anchorDone = true;
      requestAnimationFrame(() => byId('radar-card').scrollIntoView({block: 'start'}));
    }
  }

  function addKv(list, label, value, detail, missing) {
    list.appendChild(node('dt', '', label));
    const dd = node('dd');
    dd.appendChild(node('span', missing ? 'warn-text kv-value' : 'kv-value', missing ? 'not measured yet' : value));
    if (detail) dd.appendChild(node('span', 'kv-detail', detail));
    list.appendChild(dd);
  }

  function buildCalCard(body, cal, tests, drafts) {
    byId('cal-pill').replaceChildren(calPill(cal));
    body.replaceChildren();
    const set = calibrated(cal);
    const kept = tests.tests.filter((test) => test.kept);
    const formula = node('div', 'formula');
    const none = cal.status === 'no_radar';
    formula.appendChild(node('div', 'formula-main', set ? longFormula(cal) : 'From the comma = reading + correction + radar to comma'));
    formula.appendChild(node('div', 'formula-sub', set ? exampleSentence(cal)
      : (none ? 'Your car is set as having no radar: drives are used without it.'
        : 'Neither is measured yet: readings are shown as the radar gives them, from the radar.')));
    body.appendChild(formula);
    if (set) body.appendChild(coverageStrip(cal));
    const kv = node('dl', 'kv stack cal-kv');
    let offsetDetail = '';
    let correctionDetail = '';
    if (cal.source === 'tests') {
      offsetDetail = 'from ' + plural(kept.length, 'test', 'tests') + ': tapes '
        + kept.map((test) => test.tape_comma_m.toFixed(2) + ' and ' + fmtM('tape', test.tape_radar_m)).join(', ');
      const tested = cal.tested_m || [];
      correctionDetail = correctionWords(cal.radar_correction_m) + (tested.length ? '; tested at ' + (tested.length > 1
        ? tested[0].toFixed(2) + '–' + fmtM('tape', tested[tested.length - 1]) : fmtM('tape', tested[0])) : '');
    } else if (cal.source === 'typed') {
      offsetDetail = 'typed by hand';
      correctionDetail = 'typed by hand: ' + correctionWords(cal.radar_correction_m);
    }
    if (none) {
      addKv(kv, 'From the radar to the comma', 'no radar', 'you said your car has no radar', false);
      addKv(kv, 'Correction of the reading', 'no radar', '', false);
    } else {
      addKv(kv, 'From the radar to the comma', set ? fmtM('tape', cal.radar_to_camera_m) : '', offsetDetail, !set);
      addKv(kv, 'Correction of the reading', set ? fmtM('correction', cal.radar_correction_m)
        + (cal.source === 'tests' && isNumber(cal.uncertainty_m) ? ' ± ' + cal.uncertainty_m.toFixed(2) + ' m' : '') : '',
      correctionDetail, !set);
    }
    body.appendChild(kv);
    if (cal.source === 'typed' && cal.tests_give) {
      const line = node('div', 'callout');
      line.appendChild(node('span', '', 'Your tests give ' + fmtM('correction', cal.tests_give.radar_correction_m)
        + ', with the comma ' + fmtM('tape', cal.tests_give.radar_to_camera_m) + ' behind the radar.'
        + (farTested(cal) ? ' Use them to make confirmed pairs count as labels: typed numbers never do.' : '')));
      line.appendChild(button('Use the tests', 'small', applyTests));
      body.appendChild(line);
    }
    if (cal.tests_disagree) {
      body.appendChild(node('p', 'callout warn', 'Your tests disagree by ' + fmtM('comma', cal.spread_m) + ', more than the '
        + 'radar\'s 25 cm steps explain. Check the tape, or leave one test out.'));
    }
    const actions = node('div', 'cal-actions');
    body.appendChild(actions);
    calButtons(actions, cal);
    body.appendChild(node('h3', 'cal-sub', 'Tests at a known distance'));
    body.appendChild(testsList(cal, tests, drafts));
    if (!none) {
      const off = node('div', 'cal-off');
      off.appendChild(linkButton('My car has no radar', noRadar, 'muted'));
      body.appendChild(off);
    }
  }

  function calButtons(actions, cal) {
    actions.replaceChildren();
    const row = node('div', 'buttons');
    row.appendChild(button(cal.status === 'not_set' || cal.status === 'no_radar' ? 'Set it up, about 10 minutes'
      : 'Test with a tape', 'primary', () => openTest({})));
    row.appendChild(button('Type the numbers', '', () => openCalEditor(actions, cal)));
    actions.appendChild(row);
  }

  function openCalEditor(actions, cal) {
    calEdit = {offset: cal.radar_to_camera_m, correction: isNumber(cal.radar_correction_m) ? cal.radar_correction_m : 0};
    actions.replaceChildren();
    const editor = node('div', 'cal-editor');
    const fields = [
      ['cal-offset', 'From the radar to the comma', 'offset', {min: OFFSET_RANGE_M[0], max: OFFSET_RANGE_M[1], start: 1.5}],
      ['cal-correction', 'Correction of the reading', 'correction', {min: CORRECTION_RANGE_M[0], max: CORRECTION_RANGE_M[1],
        start: 0, signed: true}]];
    const notes = {};
    for (const [id, label, key, limits] of fields) {
      const field = node('div', 'stepper-field');
      const title = node('label', 'field-label', label);
      title.htmlFor = id;
      field.appendChild(title);
      const stepper = metreStepper(Object.assign({value: calEdit[key], id: id, label: label + ', in metres',
        onChange: (value) => { calEdit[key] = value; update(); }}, limits));
      field.appendChild(stepper.element);
      notes[key] = node('p', 'bad-text field-error');
      notes[key].setAttribute('aria-live', 'polite');
      field.appendChild(notes[key]);
      editor.appendChild(field);
    }
    const live = node('p', 'rt-live');
    live.setAttribute('aria-live', 'polite');
    editor.appendChild(live);
    const buttons = node('div', 'buttons');
    const save = button('Save', 'primary', saveCal);
    buttons.appendChild(save);
    buttons.appendChild(button('Cancel', '', closeEditor));
    const action = node('div', 'action');
    action.appendChild(buttons);
    const reason = node('div', 'reason');
    reason.setAttribute('aria-live', 'polite');
    action.appendChild(reason);
    editor.appendChild(action);
    actions.appendChild(editor);
    function valid() {
      const offset = calEdit.offset;
      const correction = calEdit.correction;
      return {offset: isNumber(offset) && offset >= OFFSET_RANGE_M[0] && offset <= OFFSET_RANGE_M[1],
        correction: correction === null || (isNumber(correction) && correction >= CORRECTION_RANGE_M[0]
          && correction <= CORRECTION_RANGE_M[1])};
    }
    function update() {
      const ok = valid();
      notes.offset.textContent = ok.offset || calEdit.offset === null ? '' : 'Between 0 and 5 m, for example 1.50.';
      notes.correction.textContent = ok.correction ? '' : 'Between ' + MINUS + '1.00 and +1.00 m, for example ' + MINUS + '0.89.';
      live.textContent = ok.offset && ok.correction ? 'The radar says 20.00 m → '
        + (20 + calEdit.offset + (calEdit.correction || 0)).toFixed(2) + ' m from the comma.' : '';
      save.disabled = !(ok.offset && ok.correction);
      reason.textContent = calEdit.offset === null ? 'Write the distance from the radar to the comma first.' : '';
    }
    async function saveCal() {
      save.disabled = true;
      save.replaceChildren(node('span', 'spinner'), document.createTextNode(' Saving…'));
      try {
        const answer = await post('/api/settings/radar', {radar_to_camera_m: round2(calEdit.offset),
          radar_correction_m: calEdit.correction === null ? null : round2(calEdit.correction)});
        if (data.settings) data.settings.drives = answer.drives;
        if (data.radar) data.radar.calibration = answer.calibration;
        toast('Saved. ' + exampleSentence(answer.calibration, true));
        closeEditor();
        loadedAt.radar = 0;
        refreshStatus();
      } catch (error) {
        reason.textContent = error.message;
        save.textContent = 'Save';
        save.disabled = false;
      }
    }
    update();
    const first = byId('cal-offset');
    if (first) first.focus({preventScroll: true});
  }
  function closeEditor() {
    calEdit = null;
    same.cache['cal-card'] = null;
    renderCalCard();
  }

  function testsList(cal, tests, drafts) {
    const list = node('ul', 'checklist cal-tests');
    const sorted = tests.tests.slice().sort((a, b) => (b.created || 0) - (a.created || 0));
    // action: the row's button, under the text on a phone; side: the ⋯ menu, always beside it.
    const row = (mark, kind, title, detail, side, leftOut, action) => {
      const item = node('li', leftOut ? 'left-out' : '');
      item.appendChild(node('span', 'check-mark ' + kind, mark));
      const text = node('div', 'check-text');
      text.appendChild(node('div', 'check-title', title));
      text.appendChild(node('div', 'check-detail', detail));
      item.appendChild(text);
      if (action) {
        const box = node('div', 'check-action');
        box.appendChild(action);
        item.appendChild(box);
      }
      if (side && side.length) {
        const box = node('div', 'check-side');
        for (const element of side) box.appendChild(element);
        item.appendChild(box);
      }
      list.appendChild(item);
    };
    const more = (label, action) => {
      const element = node('button', 'icon-btn more-btn', '⋯');
      element.type = 'button';
      element.setAttribute('aria-label', label);
      element.addEventListener('click', action);
      return element;
    };
    for (const test of sorted) {
      const long = test.correction_m <= 0;
      const detail = ['reads ' + Math.abs(test.correction_m).toFixed(2) + ' m ' + (long ? 'long' : 'short')];
      if (test.created) detail.push(fmtShortDay(test.created));
      detail.push('recording no. ' + test.recording_number);
      if (!test.kept) detail.push('left out');
      const title = TARGET_WORDS[test.target] + ' · tape ' + fmtM('tape', test.tape_radar_m) + ' · radar ' + fmtM('reading', test.reading_m);
      row(test.kept ? '✓' : '–', test.kept ? 'ok' : 'muted', title, detail.join(' · '),
        [more('More for the test at ' + fmtM('tape', test.tape_radar_m), () => openTestMenu(test, cal))], !test.kept);
    }
    for (const draft of drafts) {
      row('⏳', 'todo', TARGET_WORDS[draft.target] + ' · tape ' + fmtM('tape', draft.tape_radar_m),
        'waiting for its recording' + (draft.created ? ' · ' + fmtShortDay(draft.created) : ''),
        [more('More for the waiting test at ' + fmtM('tape', draft.tape_radar_m), () => openDraftMenu(draft))], false,
        button('Finish it', 'small', () => openTest({draft: draft.id})));
    }
    if (!tests.tests.length && !drafts.length) {
      row('•', 'muted', 'Wall, close', 'not done yet: the easiest one, about 10 minutes', [], false,
        button('Do it', 'small', () => openTest({target: 'wall'})));
    }
    if (!farTested(cal)) {
      row('!', 'todo', 'Parked car at 5–30 m', 'not done yet: needed before confirmed pairs become labels', [], false,
        button('Do it', 'small', () => openTest({target: 'car'})));
    }
    return list;
  }

  function openTestMenu(test, cal) {
    openInfo(TARGET_WORDS[test.target] + ' · tape ' + fmtM('tape', test.tape_radar_m),
      'radar ' + fmtM('reading', test.reading_m) + ' · recording no. ' + test.recording_number, (body) => {
        const actions = node('div', 'info-actions');
        // The calibration follows the kept tests: without the last one it is cleared.
        const kept = data.radar ? data.radar.tests.tests.filter((item) => item.kept).length : 0;
        const onlyOne = cal.source === 'tests' && test.kept && kept === 1;
        const cleared = 'It is your only test in use: without it the radar is set as not measured.';
        actions.appendChild(button(test.kept ? 'Leave it out' : 'Use it again', '', () => {
          if (onlyOne && !window.confirm('Leave this test out? ' + cleared)) return;
          changeTest(test, {kept: !test.kept});
        }));
        actions.appendChild(button('Delete', 'danger', () => {
          const question = onlyOne ? 'Delete this test? ' + cleared
            : (cal.source === 'tests' && test.kept ? 'Delete this test? The correction is worked out again from the others.'
              : 'Delete this test?');
          if (window.confirm(question)) changeTest(test, {delete: true});
        }));
        body.appendChild(actions);
        body.appendChild(node('p', 'note', test.kept ? 'Left out, it stays in the list but the correction no longer uses it.'
          : 'Used again, the correction takes it into account.'));
      });
  }
  async function changeTest(test, change) {
    try {
      const answer = await post('/api/radar/tests/' + test.id, change);
      if (data.radar) {
        data.radar.tests = answer.tests;
        data.radar.calibration = answer.calibration;
      }
      closeSheet();
      const cal = answer.calibration;
      const what = change.delete ? 'Test deleted' : (change.kept ? 'Test used again' : 'Test left out');
      let after = '.';
      if (cal.source === 'tests' && calibrated(cal)) after = ': the correction is now ' + fmtM('correction', cal.radar_correction_m) + '.';
      else if (cal.status === 'not_set') after = ': no test is in use, so the radar is set as not measured.';
      toast(what + after);
      renderTab();
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  }
  function openDraftMenu(draft) {
    openInfo(TARGET_WORDS[draft.target] + ' · tape ' + fmtM('tape', draft.tape_radar_m), 'waiting for its recording', (body) => {
      const actions = node('div', 'info-actions');
      actions.appendChild(button('Finish it', 'primary', () => openTest({draft: draft.id})));
      actions.appendChild(button('Delete', 'danger', async () => {
        if (!window.confirm('Delete this waiting test? Its tape measurements are lost.')) return;
        try {
          const answer = await post('/api/radar/drafts/' + draft.id, {delete: true});
          if (data.radar) data.radar.drafts = answer.drafts;
          closeSheet();
          toast('Waiting test deleted.');
          renderTab();
          refreshStatus();
        } catch (error) {
          toast(error.message, true);
        }
      }));
      body.appendChild(actions);
    });
  }
  async function applyTests() {
    try {
      const answer = await post('/api/radar/calibration/apply', {});
      if (data.radar) data.radar.calibration = answer.calibration;
      toast('Saved. ' + exampleSentence(answer.calibration, true));
      renderTab();
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  }
  async function noRadar() {
    if (!window.confirm('Turn the radar off? Drives will be used without it.')) return;
    try {
      const answer = await post('/api/settings/radar', {radar_to_camera_m: null, radar_correction_m: null});
      if (data.settings) data.settings.drives = answer.drives;
      if (data.radar) data.radar.calibration = answer.calibration;
      toast('The radar is off: drives are used without it.');
      renderTab();
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderSources() {
    const settings = data.settings;
    if (!settings) return;
    const sources = settings.drives.sources;
    byId('sources-empty').hidden = sources.length > 0;
    if (same('sources', sources)) return;
    const list = byId('sources');
    list.replaceChildren();
    for (const source of sources) {
      const button = node('button', 'source');
      button.type = 'button';
      button.setAttribute('role', 'radio');
      button.setAttribute('aria-checked', String(source.chosen));
      button.appendChild(node('span', 'source-dot'));
      const text = node('span', 'grow');
      text.appendChild(node('div', 'what', source.name));
      text.appendChild(node('div', 'sub', plural(source.ready, 'segment', 'segments') + ' ready from '
        + plural(source.ready_routes, 'drive', 'drives') + (source.segments > source.ready
          ? ' · ' + (source.segments - source.ready) + ' not localized yet' : '')));
      button.appendChild(text);
      button.addEventListener('click', async () => {
        if (source.chosen) return;
        try {
          const answer = await post('/api/settings/drives', {drives: source.id});
          data.settings.drives = answer.drives;
          toast('The experiments now read: ' + source.name);
          renderSources();
          refreshStatus();
        } catch (error) {
          toast(error.message, true);
        }
      });
      list.appendChild(button);
    }
  }

  // -- administrator -----------------------------------------------------------------------------
  function renderAdmin() {
    const state = data.auth;
    if (!state) return;
    let text = 'Signed in as ' + state.user + '.';
    if (state.sessions > 1) text += ' Signed in on ' + state.sessions + ' browsers.';
    byId('admin-sub').textContent = text;
  }

  async function signOut(everywhere) {
    if (everywhere && !confirm('Sign out on every browser, this one included?')) return;
    try {
      await post(everywhere ? '/api/auth/logout-all' : '/api/auth/logout', {});
    } catch (error) {
      // Already signed out: the sign-in page follows anyway.
    }
    location.replace('/');
  }
  byId('btn-signout').addEventListener('click', () => signOut(false));
  byId('btn-topbar-signout').addEventListener('click', () => signOut(false));
  byId('btn-signout-all').addEventListener('click', () => signOut(true));

  byId('admin-password-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const reason = byId('admin-reason');
    const current = byId('admin-current').value;
    const fresh = byId('admin-new').value;
    reason.textContent = '';
    if (!current) { reason.textContent = 'Write the current password.'; return; }
    if (fresh.length < 12) { reason.textContent = 'The new password needs at least 12 characters.'; return; }
    if (fresh !== byId('admin-repeat').value) { reason.textContent = 'The two new passwords are different.'; return; }
    const button = byId('btn-admin-password');
    button.disabled = true;
    try {
      const answer = await post('/api/auth/password', {current: current, password: fresh});
      for (const id of ['admin-current', 'admin-new', 'admin-repeat']) byId(id).value = '';
      data.auth = answer.state;
      renderAdmin();
      toast('Password changed: every other browser is signed out.');
    } catch (error) {
      reason.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });

  // -- appearance --------------------------------------------------------------------------------
  const THEME_KEY = 'perception-theme';
  function storedTheme() {
    try {
      const value = localStorage.getItem(THEME_KEY);
      return value === 'light' || value === 'auto' ? value : 'dark';
    } catch (error) {
      return 'dark';
    }
  }
  function applyTheme(choice) {
    if (choice === 'light' || choice === 'auto') document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    try {
      if (choice === 'light' || choice === 'auto') localStorage.setItem(THEME_KEY, choice);
      else localStorage.removeItem(THEME_KEY);
    } catch (error) {
      // Storage blocked (private window): the choice lasts until the page is closed.
    }
    renderThemeChoice(choice);
  }
  function renderThemeChoice(choice) {
    for (const button of document.querySelectorAll('[data-theme-choice]')) {
      const on = button.dataset.themeChoice === choice;
      button.setAttribute('aria-checked', on ? 'true' : 'false');
      button.tabIndex = on ? 0 : -1;
    }
  }
  const themeButtons = Array.from(document.querySelectorAll('[data-theme-choice]'));
  for (const button of themeButtons) {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
    button.addEventListener('keydown', (event) => {
      const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[event.key];
      if (!step) return;
      event.preventDefault();
      const next = themeButtons[(themeButtons.indexOf(button) + step + themeButtons.length) % themeButtons.length];
      applyTheme(next.dataset.themeChoice);
      next.focus();
    });
  }
  renderThemeChoice(storedTheme());

  byId('btn-update').addEventListener('click', () => runAction('update', {},
    'Update the code: pull the latest version, update the libraries and restart the site (a few seconds). Start?'));
  byId('reload-page').addEventListener('click', () => location.reload());

  // -- job sheet -------------------------------------------------------------------------------
  // A job's output made readable: progress lines become the bar, the site's own "== " lines become the
  // status, paths are shown relative to the repo, the JSON summary printed at the end is left out.
  const PROGRESS_LINE = /^PROGRESS (\d+)\/(\d+)$/;
  const MAX_MESSAGES = 400;

  function readLog(text) {
    const messages = [];
    let progress = null;
    let inJson = 0;
    for (const raw of text.split('\n')) {
      const parts = raw.split('\r');
      let line = (parts[parts.length - 1] || '').trim();
      if (!line) continue;
      const match = PROGRESS_LINE.exec(line);
      if (match) { progress = {done: Number(match[1]), total: Number(match[2])}; continue; }
      if (line.startsWith('== ')) continue;
      // The summary JSON (indented, one key per line): the Results tab shows it properly.
      if (line === '{') { inJson += 1; continue; }
      if (inJson) { if (line === '}') inJson -= 1; continue; }
      line = line.replace(/\/[^\s]*\/openpilot\.(perception|distill)\//g, '');
      const kind = /^(ERROR|Traceback|[A-Za-z]*Error)/.test(line) ? 'error' : '';
      messages.push({text: line, kind: kind});
    }
    return {messages: messages.slice(-MAX_MESSAGES), progress: progress};
  }

  function openLog(id, label) {
    clearTimeout(log.timer);
    log = {id: id, offset: 0, timer: null, text: '', view: null};
    byId('term-title').textContent = label;
    byId('term-sub').replaceChildren();
    byId('term-text').replaceChildren(node('li', 'muted', 'Loading…'));
    byId('term-progress').hidden = true;
    byId('term-actions').replaceChildren();
    byId('term-actions').dataset.job = '';
    byId('terminal').classList.add('open');
    byId('sheet-backdrop').classList.add('open');
    document.body.classList.add('sheet-open');
    pollLog();
  }

  function sheetJob() {
    return status && log.id ? status.jobs.find((item) => item.id === log.id) || null : null;
  }

  function renderSheetStatus() {
    const job = sheetJob();
    const sub = byId('term-sub');
    sub.replaceChildren();
    if (!job) return;
    const now = Date.now() / 1000;
    if (job.state === 'running') {
      sub.appendChild(node('span', 'run', 'Running'));
      sub.appendChild(document.createTextNode(' · started ' + fmtTime(job.started) + ' · ' + fmtDuration(now - job.started)));
    } else {
      const took = job.finished ? fmtDuration(job.finished - job.started) : '–';
      const text = job.state === 'ok' ? 'Done' : (job.state === 'stopped' ? 'Stopped' : 'Failed');
      sub.appendChild(node('span', job.state === 'ok' ? 'ok' : 'bad', text));
      sub.appendChild(document.createTextNode(' · ' + fmtTime(job.started) + ' · took ' + took));
    }
    const progress = job.progress || (log.view && log.view.progress);
    byId('term-progress').hidden = !progress || !progress.total;
    if (progress && progress.total) {
      let width = progress.done * 100 / progress.total;
      if (job.state === 'ok') width = 100;
      byId('term-percent').textContent = Math.floor(width) + '%';
      byId('term-bar').style.width = Math.max(0, Math.min(100, width)) + '%';
      let eta = progress.done + ' of ' + progress.total + ' segments';
      if (job.state === 'running' && progress.done > 0 && progress.done < progress.total) {
        eta += ' · about ' + fmtDuration(Math.max(1, (now - job.started) * (progress.total - progress.done) / progress.done)) + ' left';
      }
      byId('term-progress-text').textContent = eta;
    }
    const actions = byId('term-actions');
    const key = job.id + ':' + job.state + ':' + job.stoppable + ':' + job.result;
    if (actions.dataset.job !== key) {
      actions.dataset.job = key;
      actions.replaceChildren();
      if (job.result) {
        const open = node('button', 'btn primary', 'See the result');
        open.type = 'button';
        open.addEventListener('click', () => { closeSheet(); chosenResult = job.result; showTab('results'); });
        actions.appendChild(open);
      }
      if (job.state === 'running' && job.stoppable) {
        const stop = node('button', 'btn danger', 'Stop');
        stop.type = 'button';
        stop.addEventListener('click', () => stopJob(job));
        actions.appendChild(stop);
      }
    }
    const items = byId('term-text').children;
    if (items.length) {
      const last = items[items.length - 1];
      last.classList.toggle('current', job.state === 'running' && !last.classList.contains('muted'));
    }
  }

  function renderTimeline() {
    const list = byId('term-text');
    const body = byId('term-body');
    const atEnd = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
    const job = sheetJob();
    const messages = log.view.messages.slice();
    if (job && job.state === 'ok') messages.push({text: 'Finished', kind: 'done'});
    else if (job && job.state === 'failed') messages.push({text: 'Failed (exit code ' + job.exit_code + ')', kind: 'error'});
    else if (job && job.state === 'stopped') messages.push({text: 'Stopped', kind: 'error'});
    list.replaceChildren();
    if (!messages.length) list.appendChild(node('li', 'muted', 'Starting…'));
    for (const message of messages) list.appendChild(node('li', message.kind, message.text));
    if (atEnd) body.scrollTop = body.scrollHeight;
  }

  async function pollLog() {
    const id = log.id;
    if (!id) return;
    try {
      const chunk = await api('/api/jobs/' + id + '/log?offset=' + log.offset);
      if (log.id !== id) return;
      log.offset = chunk.offset;
      log.text += chunk.text;
      const previous = log.view ? JSON.stringify(log.view) + (log.state || '') : '';
      log.view = readLog(log.text);
      const job = sheetJob();
      log.state = job ? job.state : '';
      if (JSON.stringify(log.view) + log.state !== previous) renderTimeline();
      renderSheetStatus();
    } catch (error) {
      toast(error.message, true);
      return;
    }
    const job = sheetJob();
    log.timer = setTimeout(pollLog, !job || job.state === 'running' ? 1500 : 5000);
  }

  function openInfo(title, sub, build) {
    byId('info-title').textContent = title;
    byId('info-sub').textContent = sub;
    const body = byId('info-body');
    body.replaceChildren();
    build(body);
    body.scrollTop = 0;
    byId('info-sheet').classList.add('open');
    byId('sheet-backdrop').classList.add('open');
    document.body.classList.add('sheet-open');
  }

  function closeSheet() {
    clearTimeout(log.timer);
    log.id = null;
    byId('terminal').classList.remove('open');
    byId('info-sheet').classList.remove('open');
    byId('sheet-backdrop').classList.remove('open');
    document.body.classList.remove('sheet-open');
  }
  byId('term-close').addEventListener('click', closeSheet);
  byId('info-close').addEventListener('click', closeSheet);
  byId('sheet-backdrop').addEventListener('click', closeSheet);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeSheet();
  });

  // -- start -----------------------------------------------------------------------------------
  rt = restoreTest();
  showTab(location.hash ? location.hash.slice(1) : 'overview');
  renderWizard();
  refreshStatus();
  // A tab out of sight does not poll: it reads the status again as soon as it is shown.
  setInterval(() => { if (!document.hidden) refreshStatus(); }, STATUS_PERIOD);
  setInterval(() => { if (!document.hidden) loadTab(false); }, 5000);
})();
