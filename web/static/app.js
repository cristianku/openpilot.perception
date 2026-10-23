// perception-web: the page. Vanilla JS, no libraries; the DOM is built with textContent only.
// The look and the helpers come from openpilot.distill's distill-web (same author, MIT).
(function () {
  'use strict';

  const TABS = ['overview', 'experiments', 'results', 'settings'];
  // The tabs of the bar; Settings opens from the gear at the bottom of the screen.
  const BAR_TABS = TABS.filter((tab) => tab !== 'settings');
  const JOB_PILL = {running: 'run', ok: 'ok', failed: 'bad', stopped: 'todo', unknown: 'muted'};
  const KIND_LABEL = {geometry_audit: 'Geometry audit', mono_baseline: 'Depth baseline'};
  const KIND_EXPLAIN = {
    geometry_audit: 'Looks at the same points in three frames a tenth of a second apart and checks that the car\'s '
      + 'own motion, as the localizer measured it, moves them where the camera sees them. A multi-frame network '
      + 'can only learn distance from motion when this holds.',
    mono_baseline: 'Runs Depth Anything V2 Small, a ready-made depth model, frozen, on single frames, and compares its '
      + 'distances with points measured from the car\'s motion. This is the score the future network has to beat.'};
  const TAB_PERIOD = {results: 30000, settings: 30000, overview: 0, experiments: 0};
  const STATUS_PERIOD = 4000;

  let status = null;
  let firstCode = null;
  let currentTab = 'overview';
  let tabBeforeSettings = 'overview';
  const data = {results: null, settings: null, auth: null};
  const loadedAt = {};
  let chosenResult = null;
  let previousRunning = '';
  let log = {id: null, offset: 0, timer: null};

  // -- small helpers ---------------------------------------------------------------------------
  const byId = (id) => document.getElementById(id);
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }
  function pill(label, kind) { return node('span', 'pill ' + kind, label); }
  function setBar(id, percent, kind) {
    const bar = byId(id);
    bar.style.width = Math.max(0, Math.min(100, percent || 0)) + '%';
    bar.parentElement.className = 'bar' + (kind ? ' ' + kind : '');
  }
  function barNode(percent, kind) {
    const bar = node('div', 'bar' + (kind ? ' ' + kind : ''));
    const fill = node('span');
    fill.style.width = Math.max(0, Math.min(100, percent || 0)) + '%';
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
  function fmtTime(unix) {
    if (unix === null || unix === undefined) return '–';
    const date = new Date(unix * 1000);
    if (isNaN(date.getTime())) return String(unix);
    return date.toLocaleString('en-GB', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'});
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

  async function api(path, options) {
    const response = await fetch(path, Object.assign({credentials: 'same-origin', cache: 'no-store'}, options || {}));
    // Signed out (session ended, or another browser changed the password): the server shows the sign-in page.
    if (response.status === 401) {
      location.replace('/');
      throw new Error('Signed out.');
    }
    let body = null;
    try { body = await response.json(); } catch (error) { body = {message: 'Invalid response (HTTP ' + response.status + ').'}; }
    if (!response.ok) throw new Error(body.message || ('HTTP ' + response.status));
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

  // -- tabs ------------------------------------------------------------------------------------
  function showTab(name) {
    // #results/<id>: the Results tab with that result open.
    const slash = name.indexOf('/');
    if (slash >= 0) {
      const detail = name.slice(slash + 1);
      name = name.slice(0, slash);
      if (name === 'results' && /^[0-9]{8}-[0-9]{6}-[a-z_]+$/.test(detail)) chosenResult = detail;
    }
    if (TABS.indexOf(name) < 0) name = 'overview';
    if (name === 'settings' && currentTab !== 'settings') tabBeforeSettings = currentTab;
    currentTab = name;
    for (const tab of TABS) {
      const button = byId('tab-' + tab);
      const selected = tab === name;
      if (tab === 'settings') {
        button.setAttribute('aria-expanded', String(selected));
      } else {
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
      }
      byId('panel-' + tab).hidden = !selected;
    }
    let hash = '#' + name;
    if (name === 'results' && chosenResult) hash += '/' + chosenResult;
    if (location.hash !== hash) history.replaceState(null, '', hash);
    loadTab(true);
  }
  for (const tab of BAR_TABS) {
    byId('tab-' + tab).addEventListener('click', () => showTab(tab));
    byId('tab-' + tab).addEventListener('keydown', (event) => {
      let index = BAR_TABS.indexOf(tab);
      if (event.key === 'ArrowRight') index = (index + 1) % BAR_TABS.length;
      else if (event.key === 'ArrowLeft') index = (index + BAR_TABS.length - 1) % BAR_TABS.length;
      else return;
      event.preventDefault();
      showTab(BAR_TABS[index]);
      byId('tab-' + BAR_TABS[index]).focus();
    });
  }
  byId('tab-settings').addEventListener('click', () => {
    showTab(currentTab === 'settings' ? tabBeforeSettings : 'settings');
    window.scrollTo(0, 0);
  });
  byId('settings-back').addEventListener('click', () => {
    showTab(tabBeforeSettings);
    window.scrollTo(0, 0);
  });
  window.addEventListener('hashchange', () => showTab(location.hash.slice(1)));
  for (const button of document.querySelectorAll('[data-goto]')) {
    button.addEventListener('click', () => { showTab(button.dataset.goto); window.scrollTo(0, 0); });
  }

  function needs(tab) {
    if (tab === 'results' || tab === 'overview') return ['results'];
    if (tab === 'settings') return ['settings', 'auth'];
    return [];
  }

  async function load(what) {
    try {
      if (what === 'results') data.results = (await api('/api/results')).results;
      else if (what === 'settings') data.settings = await api('/api/settings');
      else if (what === 'auth') data.auth = await api('/api/auth/state');
      loadedAt[what] = Date.now();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function loadTab(force) {
    const tab = currentTab;
    const period = TAB_PERIOD[tab] || 0;
    const pending = [];
    for (const what of needs(tab)) {
      const age = Date.now() - (loadedAt[what] || 0);
      if (force || !loadedAt[what] || (period && age >= period)) pending.push(load(what));
    }
    await Promise.all(pending);
    renderTab();
  }

  function renderTab() {
    if (currentTab === 'experiments') renderWizard();
    else if (currentTab === 'results') renderResults();
    else if (currentTab === 'settings') { renderSources(); renderRadar(); renderAdmin(); }
    updateButtons();
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
      return;
    }
    if (firstCode === null) firstCode = status.code;
    byId('code-banner').hidden = !(status.code && firstCode && status.code !== firstCode);
    byId('code-version').textContent = status.code || 'unknown';
    renderMachine();
    renderDriveTiles();
    renderPlan();
    renderRunning();
    renderJobs();
    renderLiveJobs();
    // A job just ended: its result shows up in the open tab at once.
    const running = status.jobs.filter((job) => job.state === 'running').map((job) => job.id).join(',');
    if (previousRunning && running !== previousRunning) {
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

  function tile(big, what, hint) {
    const box = node('div', 'tile');
    box.appendChild(node('div', 'big', big));
    box.appendChild(node('div', 'what', what));
    if (hint) box.appendChild(node('div', 'hint', hint));
    return box;
  }

  function renderDriveTiles() {
    const drives = status.drives;
    if (same('drive-tiles', drives)) return;
    const box = byId('drive-tiles');
    box.replaceChildren();
    const counts = drives.counts;
    box.appendChild(tile(String(counts.ready_routes), 'drives ready', 'outings of the car, localized'));
    box.appendChild(tile(String(counts.ready), 'segments ready', 'one minute of video each'));
    box.appendChild(tile(String(counts.segments - counts.ready), 'not localized yet', 'localize them in openpilot.distill'));
    box.appendChild(tile('–', 'with car radar', 'not counted yet: only new drives can carry it'));
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

  function planStep(number, title, kind, stateLabel, text, button) {
    const item = node('li', kind === 'ok' ? 'ok' : (kind === 'muted' ? 'muted' : ''));
    item.appendChild(node('span', 'plan-number', String(number)));
    item.appendChild(node('span', 'plan-title', title));
    item.appendChild(pill(stateLabel, kind));
    item.appendChild(node('span', 'plan-text', text));
    if (button) item.appendChild(button);
    return item;
  }

  function planButton(label, action) {
    const button = node('button', 'btn small', label);
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  }

  function renderPlan() {
    const latest = status.latest || {};
    const running = new Set(status.jobs.filter((job) => job.state === 'running').map((job) => job.action));
    const radar = status.radar || {};
    const offset = radar.radar_to_reference_m;
    if (same('plan', [latest, Array.from(running), radar])) return;
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
          planButton('See the result', () => { chosenResult = result.id; showTab('results'); })));
      } else {
        list.appendChild(planStep(index, title, 'todo', 'to run', KIND_EXPLAIN[kind],
          planButton('Run it', () => { chooseKind(kind); showTab('experiments'); })));
      }
    });
    const where = offset === null || offset === undefined
      ? ' Set in Settings how far the comma sits behind the radar.'
      : ' ' + radarSentence(radar);
    list.appendChild(planStep(2, 'Distances from the car\'s radar', 'todo', 'waiting for drives',
      'The radar of the car measures the car ahead in our lane: the real ruler for the camera. It starts with the '
      + 'drives recorded with openpilot longitudinal off on the branch that logs it.' + where,
      offset === null || offset === undefined ? planButton('Set it', () => showTab('settings')) : null));
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
        const details = node('button', 'btn small', 'Details');
        details.type = 'button';
        details.addEventListener('click', () => openLog(job.id, job.label));
        buttons.appendChild(details);
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
        + 'failed. Its log on the Overview tells why.'));
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

  // -- settings: drives --------------------------------------------------------------------------
  function radarSentence(radar) {
    if (!radar || radar.radar_to_reference_m === null || radar.radar_to_reference_m === undefined) return '';
    const correction = Math.round(radar.radar_correction_m * 100);
    return 'Distance from the comma = radar reading ' + (correction < 0 ? '− ' : '+ ') + Math.abs(correction) + ' cm + '
      + radar.radar_to_camera_m.toFixed(2) + ' m: in all, the radar reading + ' + radar.radar_to_reference_m.toFixed(2) + ' m.';
  }
  function renderRadar() {
    const settings = data.settings;
    if (!settings) return;
    const radar = settings.drives;
    if (byId('radar-offset') !== document.activeElement) {
      byId('radar-offset').value = radar.radar_to_camera_m === null ? '' : radar.radar_to_camera_m.toFixed(2);
    }
    if (byId('radar-correction') !== document.activeElement) {
      byId('radar-correction').value = radar.radar_correction_m ? String(Math.round(radar.radar_correction_m * 100)) : '';
    }
    byId('radar-total').textContent = radarSentence(radar);
  }
  byId('btn-radar-save').addEventListener('click', async () => {
    const reason = byId('radar-reason');
    const offsetText = byId('radar-offset').value.trim().replace(',', '.');
    const correctionText = byId('radar-correction').value.trim().replace(',', '.').replace('−', '-');
    const offset = offsetText === '' ? null : Number(offsetText);
    const correction = correctionText === '' ? null : Number(correctionText) / 100;
    reason.textContent = '';
    if (offset !== null && (!isFinite(offset) || offset < 0 || offset > 5)) {
      reason.textContent = 'From the radar to the comma: between 0 and 5 m, for example 1.50.';
      return;
    }
    if (correction !== null && (!isFinite(correction) || correction < -1 || correction > 1)) {
      reason.textContent = 'Correction: between −100 and 100 cm, for example −3.';
      return;
    }
    try {
      const answer = await post('/api/settings/radar', {radar_to_camera_m: offset, radar_correction_m: correction});
      data.settings.drives = answer.drives;
      renderRadar();
      toast(offset === null ? 'Radar values cleared.' : 'Saved. ' + radarSentence(answer.drives));
      refreshStatus();
    } catch (error) {
      reason.textContent = error.message;
    }
  });

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
  showTab(location.hash ? location.hash.slice(1) : 'overview');
  renderWizard();
  refreshStatus();
  setInterval(refreshStatus, STATUS_PERIOD);
  setInterval(() => { if (!document.hidden) loadTab(false); }, 5000);
})();
