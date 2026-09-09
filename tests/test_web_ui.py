from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path
from shutil import which

import pytest


HTML_PATH = Path(__file__).resolve().parents[1] / "ailamp_runtime/ailamp/web/index.html"


def run_ui_scenario(source: str) -> None:
    if which("node") is None:
        pytest.skip("node is not installed")
    harness = (
        r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const html = fs.readFileSync(process.argv[2], "utf8");
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
assert(scriptMatch, "index.html inline script not found");

class Element {
  constructor(id = "", tagName = "div") {
    this.id = id;
    this.tagName = tagName;
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.disabled = false;
    this.value = "";
    this.onclick = null;
    this.oninput = null;
    this._textContent = "";
  }
  set textContent(value) {
    this._textContent = String(value);
  }
  get textContent() {
    return this._textContent;
  }
  set innerHTML(_value) {
    throw new Error("innerHTML writes are forbidden");
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children = children;
  }
}

const ids = [
  "log", "status", "badges", "recordings", "lanToken", "auto",
  "chat", "send", "observe", "stop", "arm", "disarm", "play",
  "red", "green", "blue", "brightness", "light", "clearLight", "timer"
];
const elements = new Map(ids.map(id => [id, new Element(id)]));
elements.get("log").textContent = "等待状态...";
elements.get("lanToken").value = "";
elements.get("timer").value = "25";
elements.get("recordings").value = "";
elements.get("red").value = "255";
elements.get("green").value = "180";
elements.get("blue").value = "80";
elements.get("brightness").value = "128";

const modeButtons = ["manual", "focus", "rest"].map(mode => {
  const button = new Element("", "button");
  button.dataset.mode = mode;
  return button;
});

function response(payload, ok = true) {
  return {
    ok,
    statusText: ok ? "OK" : "Bad Request",
    json: async () => payload,
  };
}

const context = {
  console,
  Date: class extends Date {
    constructor(...args) {
      super(...(args.length ? args : ["2026-09-05T10:00:00Z"]));
    }
    static now() {
      return new Date("2026-09-05T10:00:00Z").getTime();
    }
  },
  document: {
    createElement: tagName => new Element("", tagName),
    getElementById: id => elements.get(id),
    querySelectorAll: selector => {
      assert.equal(selector, "[data-mode]");
      return modeButtons;
    },
  },
  sessionStorage: {setItem() {}},
  setInterval() { return 1; },
  clearInterval() {},
  setTimeout(callback) {
    callback();
    return 1;
  },
  fetch: async (_path, _options) => response({
    csrf_token: "initial-token",
    controller: {mode: "manual", recordings: []},
    brain: {auto_enabled: false},
  }),
};
ids.forEach(id => {
  context[id] = elements.get(id);
});
context.globalThis = context;
vm.createContext(context);
vm.runInContext(scriptMatch[1], context, {filename: "index.html"});

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}
function base(overrides = {}) {
  return {
    csrf_token: "token",
    controller: {
      mode: "manual",
      with_outputs: false,
      armed: false,
      motor_busy: false,
      recordings: [],
      focus_remaining_s: null,
      ...overrides.controller,
    },
    brain: {
      brain_enabled: true,
      provider: "openai",
      model: "gpt-4.1-mini",
      api_call_count: 0,
      max_calls: 10,
      auto_enabled: false,
      inflight: false,
      ...overrides.brain,
    },
    ...overrides,
  };
}
function logText() {
  return elements.get("log").textContent;
}
function logEntryCount(marker = "回复：") {
  return (logText().match(new RegExp(marker, "g")) || []).length;
}
function statusText() {
  return elements.get("status").children.map(child => child.textContent).join("\n");
}
async function post(path, payload, ok = true) {
  const calls = [];
  context.fetch = async (actualPath, options) => {
    calls.push({path: actualPath, body: JSON.parse(options.body)});
    return response(payload, ok);
  };
  const result = await context.api(path, {});
  await flush();
  return {result, calls};
}
"""
        + "\n(async () => {\n"
        + textwrap.dedent(source)
        + "\n})().catch(err => { console.error(err && err.stack || err); process.exit(1); });\n"
    )
    result = subprocess.run(
        ["node", "-", str(HTML_PATH)],
        input=harness,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_state_renders_controller_last_outcome_and_ignores_stale_brain_outcome():
    run_ui_scenario(
        """
        await flush();
        elements.get("log").textContent = "";
        context.render(base({
          controller: {
            last_outcome: {
              request_id: "auto-1",
              accepted: true,
              sent: true,
              completed: true,
              reply: "<img src=x onerror=alert(1)>",
              action_results: [{name: "set_light", sent: true, completed: true}],
            },
          },
          brain: {
            last_outcome: {
              request_id: "auto-1",
              accepted: true,
              sent: true,
              completed: false,
              reply: "stale brain reply",
              action_results: [{name: "move_joints", sent: false, completed: false}],
            },
          },
        }));

        assert(logText().includes("<img src=x onerror=alert(1)>"));
        assert(!logText().includes("stale brain reply"));
        assert(logText().includes("set_light sent=true completed=true"));
        assert(!logText().includes("move_joints"));
        assert.equal(logEntryCount(), 1);
        """
    )


def test_immediate_post_and_identical_poll_do_not_duplicate_outcome():
    run_ui_scenario(
        """
        await flush();
        elements.get("log").textContent = "";
        const outcome = {
          request_id: "chat-1",
          accepted: true,
          sent: true,
          completed: true,
          reply: "已完成",
          action_results: [{name: "play_recording", sent: true, completed: true}],
        };
        await post("/api/chat", base({outcome}));
        context.render(base({controller: {last_outcome: outcome}}));
        context.render(base({controller: {last_outcome: outcome}}));

        assert.equal(logEntryCount(), 1);
        assert(logText().includes("已完成"));
        """
    )


def test_same_request_id_pending_completed_error_updates_once_without_spam():
    run_ui_scenario(
        """
        await flush();
        elements.get("log").textContent = "";
        const pending = {
          request_id: "motion-1",
          accepted: true,
          sent: true,
          completed: false,
          reply: "开始移动",
          action_results: [{name: "move_joints", sent: true, completed: false}],
        };
        const completed = {
          ...pending,
          completed: true,
          action_results: [{name: "move_joints", sent: true, completed: true}],
        };
        const errored = {
          ...completed,
          completed: false,
          error: "<script>alert(1)</script>",
          action_results: [{name: "move_joints", sent: true, completed: false, error: "motor fault"}],
        };

        context.render(base({controller: {last_outcome: pending}}));
        context.render(base({controller: {last_outcome: pending}}));
        context.render(base({controller: {last_outcome: completed}}));
        context.render(base({controller: {last_outcome: completed}}));
        context.render(base({controller: {last_outcome: errored}}));
        context.render(base({controller: {last_outcome: errored}}));

        assert.equal(logEntryCount(), 3);
        assert.equal((logText().match(/开始移动/g) || []).length, 3);
        assert(logText().includes("完成=false"));
        assert(logText().includes("完成=true"));
        assert(logText().includes("<script>alert(1)</script>"));
        assert(logText().includes("move_joints sent=true completed=false error=motor fault"));
        """
    )


def test_no_fake_completion_and_action_results_remain_authoritative():
    run_ui_scenario(
        """
        await flush();
        elements.get("log").textContent = "";
        context.render(base({
          controller: {
            motor_busy: false,
            last_outcome: {
              request_id: "accepted-only",
              accepted: true,
              sent: false,
              reply: "只回复",
              actions: [{name: "do_nothing", sent: true, completed: true}],
              action_results: [
                {name: "set_light", sent: false, completed: false},
                {name: "move_joints", sent: true, completed: false},
              ],
            },
          },
        }));

        assert.equal(logEntryCount(), 1);
        assert(logText().includes("接收=true 发送=false 完成=undefined"));
        assert(logText().includes("set_light sent=false completed=false"));
        assert(logText().includes("move_joints sent=true completed=false"));
        assert(!logText().includes("do_nothing"));
        """
    )


def test_mode_payloads_and_focus_countdown_are_preserved():
    run_ui_scenario(
        """
        await flush();
        const manual = await post("/api/manual", base());
        modeButtons.find(button => button.dataset.mode === "manual").onclick();
        await flush();
        assert.deepEqual(manual.calls.at(-1).body, {action: "set_mode", arguments: {mode: "manual"}});

        const rest = await post("/api/manual", base());
        modeButtons.find(button => button.dataset.mode === "rest").onclick();
        await flush();
        assert.deepEqual(rest.calls.at(-1).body, {action: "set_mode", arguments: {mode: "rest"}});

        elements.get("timer").value = "25";
        const focus = await post("/api/manual", base({controller: {mode: "focus", focus_remaining_s: 1499.1}}));
        modeButtons.find(button => button.dataset.mode === "focus").onclick();
        await flush();
        assert.deepEqual(focus.calls.at(-1).body, {action: "set_mode", arguments: {mode: "focus", timer_minutes: 25}});
        assert(statusText().includes("模式：focus"));
        assert(statusText().includes("计时：1500 秒"));

        context.render(base({controller: {mode: "focus", focus_remaining_s: 60.01}}));
        assert(statusText().includes("计时：61 秒"));
        """
    )
