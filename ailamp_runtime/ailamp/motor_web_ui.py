"""Static HTML for the motor-only web page.

One self-contained document: inline CSS and vanilla JS, no external assets, so
it serves unchanged from a Nano with no internet.  Labels tell the truth about
what each control does and does not do.
"""

from __future__ import annotations


STOP_LABEL = "停止（取消后续帧并保持，不断电）"
FAULT_NOTICE = "故障已锁定，程序不会自动重试；请先人工确认机构与供电状态，然后重启进程。"
SIMULATION_BANNER = "模拟模式：内存 fake bus，不会打开串口"
HARDWARE_BANNER = "实机模式：会写入真实电机"

_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AILamp 五轴电机操作页面</title>
<style>
:root { --bg:#f6f7f9; --card:#ffffff; --line:#d9dde3; --text:#1c2128; --muted:#5b6470; --ok:#1f7a3f; --warn:#b3261e; --accent:#2457c5; }
* { box-sizing:border-box; }
body { margin:0; font:15px/1.5 system-ui,-apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif; background:var(--bg); color:var(--text); }
header { padding:16px 20px 8px; max-width:1100px; }
h1 { margin:0 0 8px; font-size:20px; }
.banner { padding:10px 14px; border-radius:8px; font-weight:600; }
.banner.simulation { background:#e3f4e8; color:var(--ok); border:1px solid #9fd3b1; }
.banner.hardware { background:#fbe4e2; color:var(--warn); border:2px solid var(--warn); }
.chips { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 4px; }
.chip { background:var(--card); border:1px solid var(--line); border-radius:999px; padding:4px 12px; font-size:13px; }
main { padding:0 20px 24px; display:grid; gap:14px; grid-template-columns:1fr; max-width:1100px; }
section.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
h2 { margin:0 0 10px; font-size:15px; color:var(--muted); font-weight:600; }
.row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
button { font:inherit; padding:9px 14px; border-radius:8px; border:1px solid var(--line); background:#fff; cursor:pointer; }
button:disabled { opacity:.45; cursor:not-allowed; }
button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
button.danger { background:#fff; color:var(--warn); border:2px solid var(--warn); font-weight:700; }
button.stop { font-size:16px; padding:12px 18px; }
#fault-panel { background:#fbe4e2; border:2px solid var(--warn); color:var(--warn); }
#confirm-panel { border:2px solid var(--accent); }
[hidden] { display:none !important; }
label.inline { display:flex; align-items:center; gap:6px; margin:6px 0; }
input[type=number], input[type=text], input[type=password] { font:inherit; padding:7px 9px; border:1px solid var(--line); border-radius:6px; min-width:200px; }
table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
#events { list-style:none; margin:0; padding:0; max-height:280px; overflow:auto; font-family:ui-monospace,Menlo,monospace; font-size:12.5px; }
#events li { padding:3px 0; border-bottom:1px dashed var(--line); white-space:pre-wrap; }
#message { min-height:1.5em; }
#message.error { color:var(--warn); font-weight:600; }
.note { color:var(--muted); font-size:13px; margin:6px 0 0; }
@media (min-width: 900px) { main { grid-template-columns:1fr 1fr; } section.wide { grid-column:1 / -1; } }
</style>
</head>
<body>
<header>
  <h1>AILamp 五轴电机操作页面</h1>
  <div id="mode-banner" class="banner simulation"><span id="banner-text">@@SIMULATION_BANNER@@</span></div>
  <div class="chips">
    <span class="chip">连接：<b id="chip-connected">—</b></span>
    <span class="chip">Arm：<b id="chip-armed">—</b></span>
    <span class="chip">扭矩：<b id="chip-torque">—</b></span>
    <span class="chip">任务：<b id="chip-task">—</b></span>
    <span class="chip">位置来源：<b id="chip-source">—</b></span>
  </div>
  <div id="connection-note" class="note"></div>
</header>
<main>
  <section id="fault-panel" class="card wide" hidden>
    <h2>故障</h2>
    <p id="fault-text"></p>
    <p>@@FAULT_NOTICE@@</p>
  </section>

  <section class="card wide">
    <h2>会话</h2>
    <div class="row">
      <button id="btn-connect" class="primary" type="button">连接（只读）</button>
      <button id="btn-arm" type="button">Arm…</button>
      <button id="btn-capture-home" type="button">捕获 home…</button>
      <button id="btn-release" type="button">Release…</button>
      <input id="token" type="password" autocomplete="off" placeholder="非 loopback 访问时填写控制令牌">
    </div>
    <p class="note">连接只读，不会自动 arm；arm、捕获 home、release 都要求现场确认机构已被支撑。</p>
  </section>

  <section id="confirm-panel" class="card wide" hidden>
    <h2 id="confirm-title">确认</h2>
    <div id="arm-fields" hidden>
      <label class="inline">Goal_Velocity <input id="arm-goal-velocity" type="number" step="1" inputmode="numeric"></label>
      <label class="inline">Acceleration <input id="arm-acceleration" type="number" step="1" inputmode="numeric"></label>
      <label class="inline">Torque_Limit <input id="arm-torque-limit" type="number" step="1" inputmode="numeric"></label>
      <p class="note">占位符里的范围只是软件边界，不是承重配置；页面不预填数值。</p>
    </div>
    <label class="inline"><input id="confirm-supported" type="checkbox"> 我确认机构已被支撑</label>
    <label class="inline">输入 CONFIRM <input id="confirm-text" type="text" autocomplete="off"></label>
    <div class="row">
      <button id="btn-confirm-submit" class="primary" type="button">执行</button>
      <button id="btn-confirm-cancel" type="button">取消</button>
    </div>
  </section>

  <section class="card wide">
    <h2>动作（来自录制文件，逐个独立触发）</h2>
    <div id="recordings" class="row"></div>
  </section>

  <section class="card wide">
    <h2>剧本与归位</h2>
    <div class="row">
      <button id="btn-script" type="button">完整剧本</button>
      <button id="btn-home" type="button">归位（home）</button>
      <button id="btn-stop" class="danger stop" type="button">@@STOP_LABEL@@</button>
    </div>
    <p class="note">stop 由唯一发送 worker 新读实测位置并保持，不会卸力，也不是物理断电急停。归位需要先捕获真实 home。</p>
  </section>

  <section class="card">
    <h2>关节（归一化 [-100, 100]）</h2>
    <table><thead><tr><th>关节</th><th>指令</th><th>实测反馈</th></tr></thead><tbody id="joints"></tbody></table>
  </section>

  <section class="card">
    <h2>事件（最新在前，最多 50 条）</h2>
    <ul id="events"></ul>
  </section>

  <section class="card wide"><div id="message"></div></section>
</main>
<script>
(function () {
  'use strict';
  var POLL_INTERVAL_MS = 500;
  var CONFIRMATION_VALUE = 'CONFIRM';
  var TOKEN_STORAGE_KEY = 'ailamp-motor-web-token';
  var SIMULATION_BANNER = '@@SIMULATION_BANNER@@';
  var HARDWARE_BANNER = '@@HARDWARE_BANNER@@';
  var JOINTS = ['base_yaw', 'base_pitch', 'elbow_pitch', 'wrist_roll', 'wrist_pitch'];
  var ARM_INPUTS = [
    ['arm-goal-velocity', 'goal_velocity'],
    ['arm-acceleration', 'acceleration'],
    ['arm-torque-limit', 'torque_limit']
  ];
  var CONFIRM_ACTIONS = {
    'arm': { title: 'Arm：按当前实测姿态保持（这不是承重验收）', path: '/api/arm', done: '已按当前实测姿态 arm 并保持；这不是承重验收。' },
    'release': { title: 'Release：确认机构已被支撑后卸力', path: '/api/release', done: '已确认支撑并 release。' },
    'capture-home': { title: '捕获 home：把当前实测姿态保存为真实 home', path: '/api/capture-home', done: '已保存 fresh home。' }
  };

  var csrfToken = null;
  var pendingAction = null;
  var renderedRecordings = '';

  function byId(id) { return document.getElementById(id); }
  function setEnabled(id, enabled) { byId(id).disabled = !enabled; }
  function showMessage(text, isError) {
    var el = byId('message');
    el.textContent = text;
    el.className = isError ? 'error' : '';
  }
  function errorMessage(payload) {
    return payload && payload.error && payload.error.message ? payload.error.message : '未知错误';
  }
  function tokenHeaders() {
    var token = byId('token').value.trim();
    return token ? { 'X-AILamp-Token': token } : {};
  }
  function formatNumber(value) {
    return typeof value === 'number' && isFinite(value) ? value.toFixed(1) : '—';
  }

  function readJson(response) {
    return response.json().then(function (payload) {
      if (!response.ok) { throw new Error('（' + response.status + '）' + errorMessage(payload)); }
      return payload;
    });
  }

  function poll() {
    fetch('/api/state', { headers: tokenHeaders(), cache: 'no-store' })
      .then(readJson)
      .then(function (state) {
        csrfToken = state.csrf_token;
        render(state);
        byId('connection-note').textContent = '';
      })
      .catch(function (error) {
        byId('connection-note').textContent = '状态读取失败：' + error.message;
      })
      .then(function () { window.setTimeout(poll, POLL_INTERVAL_MS); });
  }

  function postAction(path, body, doneText) {
    if (!csrfToken) { showMessage('尚未取得 CSRF 令牌，请等待状态刷新。', true); return; }
    var headers = Object.assign({ 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken }, tokenHeaders());
    fetch(path, { method: 'POST', headers: headers, body: JSON.stringify(body || {}) })
      .then(readJson)
      .then(function (payload) {
        render(payload.state);
        showMessage(doneText + describeExtras(payload), false);
      })
      .catch(function (error) {
        // A refusal is final: the page never re-sends an action on its own.
        showMessage('已拒绝 ' + error.message, true);
      });
  }
  function describeExtras(payload) {
    if (payload.held) { return ' 保持目标：' + JSON.stringify(payload.held); }
    if (payload.home) { return ' home：' + JSON.stringify(payload.home); }
    return '';
  }

  function render(state) {
    var status = state.status;
    var fault = status.fault !== null && status.fault !== undefined;
    renderBanner(state.mode, status);
    renderFault(status, fault);
    renderRecordings(state.recordings);
    renderScriptLabel(state.default_script);
    renderArmLimits(state.arm_limits);
    renderButtons(status, fault);
    renderJoints(status);
    renderEvents(state.events);
  }
  function renderBanner(mode, status) {
    var hardware = mode === 'hardware';
    byId('mode-banner').className = 'banner ' + (hardware ? 'hardware' : 'simulation');
    byId('banner-text').textContent = hardware ? HARDWARE_BANNER : SIMULATION_BANNER;
    byId('chip-connected').textContent = status.connected ? '已连接' : '未连接';
    byId('chip-armed').textContent = status.armed ? '已 arm' : '未 arm';
    byId('chip-torque').textContent = status.torque_state || '—';
    byId('chip-task').textContent = status.busy ? ('忙碌：' + (status.task || '?')) : '空闲';
    byId('chip-source').textContent = status.position_source || '—';
  }
  function renderFault(status, fault) {
    byId('fault-panel').hidden = !fault;
    byId('fault-text').textContent = fault ? String(status.fault) : '';
  }
  function renderRecordings(names) {
    var key = names.join(',');
    if (key === renderedRecordings) { return; }
    renderedRecordings = key;
    var container = byId('recordings');
    container.textContent = '';
    names.forEach(function (name) {
      var button = document.createElement('button');
      button.type = 'button';
      button.id = 'btn-play-' + name;
      button.setAttribute('data-recording', name);
      button.textContent = name;
      button.addEventListener('click', function () {
        postAction('/api/play', { name: name }, '已启动 ' + name + '；sent 与 feedback_reached 分开记录。');
      });
      container.appendChild(button);
    });
  }
  function renderScriptLabel(steps) {
    byId('btn-script').textContent = '完整剧本：' + steps.join(' → ');
  }
  function renderArmLimits(limits) {
    ARM_INPUTS.forEach(function (pair) {
      var range = limits[pair[1]];
      var input = byId(pair[0]);
      input.min = range[0];
      input.max = range[1];
      input.placeholder = range[0] + ' – ' + range[1];
    });
  }
  function renderButtons(status, fault) {
    var connected = !!status.connected;
    var armed = !!status.armed;
    var busy = !!status.busy;
    var idle = connected && armed && !busy && !fault;
    setEnabled('btn-connect', !connected);
    setEnabled('btn-arm', connected && !armed && !busy && !fault);
    setEnabled('btn-capture-home', idle);
    setEnabled('btn-release', idle);
    setEnabled('btn-script', idle);
    setEnabled('btn-home', idle);
    setEnabled('btn-stop', connected && armed && !fault);
    var buttons = byId('recordings').querySelectorAll('button');
    Array.prototype.forEach.call(buttons, function (button) { button.disabled = !idle; });
  }
  function renderJoints(status) {
    var commanded = status.commanded_positions || {};
    var feedback = status.last_feedback || {};
    var body = byId('joints');
    body.textContent = '';
    JOINTS.forEach(function (joint) {
      var row = document.createElement('tr');
      [joint, formatNumber(commanded[joint + '.pos']), formatNumber(feedback[joint + '.pos'])].forEach(function (text) {
        var cell = document.createElement('td');
        cell.textContent = text;
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  }
  function renderEvents(events) {
    var list = byId('events');
    list.textContent = '';
    events.slice().reverse().forEach(function (event) {
      var item = document.createElement('li');
      item.textContent = event.timestamp + '  ' + event.kind + '  ' + event.task + '  ' + JSON.stringify(event.details);
      list.appendChild(item);
    });
  }

  function openConfirm(action) {
    pendingAction = action;
    byId('confirm-title').textContent = CONFIRM_ACTIONS[action].title;
    byId('arm-fields').hidden = action !== 'arm';
    byId('confirm-supported').checked = false;
    byId('confirm-text').value = '';
    byId('confirm-panel').hidden = false;
    byId('confirm-text').focus();
  }
  function closeConfirm() {
    pendingAction = null;
    byId('confirm-panel').hidden = true;
  }
  function armBody() {
    var body = {};
    ARM_INPUTS.forEach(function (pair) {
      var raw = byId(pair[0]).value.trim();
      var value = Number(raw);
      if (raw === '' || !Number.isInteger(value)) { throw new Error(pair[1] + ' 必须填写整数'); }
      body[pair[1]] = value;
    });
    return body;
  }
  function submitConfirm() {
    if (!pendingAction) { return; }
    if (!byId('confirm-supported').checked) { showMessage('请先勾选“我确认机构已被支撑”。', true); return; }
    if (byId('confirm-text').value.trim() !== CONFIRMATION_VALUE) { showMessage('请输入 ' + CONFIRMATION_VALUE + ' 以确认。', true); return; }
    var body = { confirm: CONFIRMATION_VALUE };
    if (pendingAction === 'arm') {
      try { body = Object.assign(armBody(), body); }
      catch (error) { showMessage(error.message, true); return; }
    }
    var action = CONFIRM_ACTIONS[pendingAction];
    closeConfirm();
    postAction(action.path, body, action.done);
  }

  function restoreToken() {
    try {
      var saved = window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
      if (saved) { byId('token').value = saved; }
    } catch (error) {
      // Storage may be blocked; the operator can still type the token each time.
    }
  }
  function persistToken() {
    try { window.sessionStorage.setItem(TOKEN_STORAGE_KEY, byId('token').value.trim()); }
    catch (error) {
      // Same as above: persistence is a convenience, not a requirement.
    }
  }

  byId('btn-connect').addEventListener('click', function () { postAction('/api/connect', {}, '已只读连接；尚未 arm。'); });
  byId('btn-arm').addEventListener('click', function () { openConfirm('arm'); });
  byId('btn-capture-home').addEventListener('click', function () { openConfirm('capture-home'); });
  byId('btn-release').addEventListener('click', function () { openConfirm('release'); });
  byId('btn-script').addEventListener('click', function () { postAction('/api/script', {}, '已启动默认模拟触发剧本。'); });
  byId('btn-home').addEventListener('click', function () { postAction('/api/home', {}, '已启动 home。'); });
  byId('btn-stop').addEventListener('click', function () { postAction('/api/stop', {}, '已 fresh stop-and-hold。'); });
  byId('btn-confirm-submit').addEventListener('click', submitConfirm);
  byId('btn-confirm-cancel').addEventListener('click', closeConfirm);
  byId('token').addEventListener('change', persistToken);

  restoreToken();
  poll();
})();
</script>
</body>
</html>
"""


def _render(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace(f"@@{key}@@", value)
    return result


INDEX_HTML = _render(
    _TEMPLATE,
    {
        "STOP_LABEL": STOP_LABEL,
        "FAULT_NOTICE": FAULT_NOTICE,
        "SIMULATION_BANNER": SIMULATION_BANNER,
        "HARDWARE_BANNER": HARDWARE_BANNER,
    },
)
