/**
 * P6c e2e — §9.4 4-패널 데이터바인딩 검증 (WA 셸 없이·렌더-트랩 아님).
 *
 * 검증 대상(ralplan-p6-interface.md §6.3·P6c):
 *   4 패널을 mock orch RO API 응답(/agents/status·/tasks)으로 렌더 → 데이터바인딩 단언:
 *   - Agent Status Overlay: 8-상태 배지가 mock /agents/status 값과 **일치**
 *     (예 research=Coding · qa=Reviewing · 색/라벨도 그 state 파생).
 *   - Task Queue: mock /tasks 의 작업/상태가 행으로 반영.
 *   - Company Chat: TEXT_MESSAGE role/content 반영(동결 events.ts 타입).
 *   ★렌더-트랩 차단(falsifiable): mock 값을 **바꾸면** 배지/큐도 바뀌어야 PASS.
 *     고정 placeholder/하드코딩이면 변경 후에도 그대로 → FAIL.
 *
 * 데이터 소스는 mock(네트워크 0·window.fetch 스텁). WA-위-합성 렌더(라이브 WA 셸 위)는
 * "code 100% / runtime pending Owner: 맵URL+도메인+TLS" — 이 스크립트 범위 밖(정직 표기).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 \
 *       node web/scripts/play_control_check.mjs
 * exit 0 = PASS, 비-0 = FAIL.
 */
import { build } from "esbuild";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(__dirname, "..");
const entry = path.join(WEB, "scripts", "control_harness_entry.tsx");

const fail = (msg) => {
  console.error("FAIL: " + msg);
  process.exit(1);
};

// ── 1) esbuild 로 하네스 IIFE 번들(네트워크 0·React 포함·외부 의존 추가 0) ──────────
const result = await build({
  entryPoints: [entry],
  bundle: true,
  format: "iife",
  platform: "browser",
  write: false,
  jsx: "automatic",
  loader: { ".tsx": "tsx", ".ts": "ts" },
  define: { "process.env.NODE_ENV": '"production"' },
  logLevel: "silent",
});
const bundleJs = result.outputFiles[0].text;

// ── 2) mock 응답(가변 — 두 시나리오로 falsifiable 입증) ─────────────────────────
// 시나리오 A: research=Coding · qa=Reviewing · ceo=Meeting
const SCENARIO_A = {
  agents: {
    agents: [
      { agent: "ceo", state: "Meeting", current_task: "분기 전략 검토", since: "2026-06-22T01:00:00+00:00" },
      { agent: "research", state: "Coding", current_task: "시장조사 R-7", since: "2026-06-22T01:01:00+00:00" },
      { agent: "qa", state: "Reviewing", current_task: "회귀 게이트", since: "2026-06-22T01:02:00+00:00" },
      { agent: "dev", state: "Idle", current_task: null, since: null },
    ],
  },
  tasks: {
    tasks: [
      { task_id: "t-1", status: "in_progress", directive: "클리커 게임 빌드", agent: "dev" },
      { task_id: "t-2", status: "queued", directive: "에셋 제작", agent: "art" },
    ],
    redis: "ok",
  },
};
// 시나리오 B(변이): research=Reviewing · qa=Blocked · 작업 1건 done — 배지/큐가 *바뀌어야* 한다.
const SCENARIO_B = {
  agents: {
    agents: [
      { agent: "ceo", state: "Idle", current_task: null, since: null },
      { agent: "research", state: "Reviewing", current_task: "보고서 검토", since: "2026-06-22T02:01:00+00:00" },
      { agent: "qa", state: "Blocked", current_task: "의존성 대기", since: "2026-06-22T02:02:00+00:00" },
      { agent: "dev", state: "Sleeping", current_task: null, since: null },
    ],
  },
  tasks: {
    tasks: [{ task_id: "t-1", status: "done", directive: "클리커 게임 빌드", agent: "dev" }],
    redis: "ok",
  },
};

const CHAT = [
  {
    run_id: "chat-ceo-1",
    ts: "2026-06-22T01:05:00+00:00",
    schema_v: 1,
    family: "Text",
    type: "TEXT_MESSAGE",
    role: "assistant",
    content: "이번 분기 목표를 공유합니다.",
    image_url: null,
  },
];

// 예상 색/라벨(types.ts STATE_META 와 동일해야 함 — 독립 계산해 바인딩 일치를 교차검증).
const EXPECT_COLOR = {
  Idle: "#6b7280",
  Researching: "#3b82f6",
  Coding: "#22c55e",
  Reviewing: "#a855f7",
  Meeting: "#f59e0b",
  Waiting: "#eab308",
  Blocked: "#ef4444",
  Sleeping: "#475569",
};

// ── 3) playwright 구동 + 데이터바인딩 단언 ──────────────────────────────────────
const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
const fatals = [];
try {
  const page = await browser.newPage();
  page.on("console", (m) => {
    if (m.type() === "error" && !/favicon/i.test(m.text())) fatals.push(m.text());
  });
  page.on("pageerror", (e) => fatals.push(String(e)));

  // 빈 페이지 로드 후 mock fetch 설치 + 번들 주입(네트워크 0).
  await page.goto("about:blank", { waitUntil: "load" });
  await page.addInitScript(() => {
    // 가변 mock 상태 — 테스트가 window.__setMock 으로 교체(렌더-트랩 falsifiable).
    window.__mock = { agents: { agents: [] }, tasks: { tasks: [] } };
    window.__setMock = (m) => {
      window.__mock = m;
    };
    window.fetch = async (url) => {
      const u = String(url);
      let body;
      if (u.includes("/agents/status")) body = window.__mock.agents;
      else if (u.includes("/tasks")) body = window.__mock.tasks;
      else body = {};
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => body,
      };
    };
  });
  // addInitScript 는 다음 내비게이션부터 — 한 번 재로드해 적용.
  await page.goto("about:blank", { waitUntil: "load" });
  await page.addScriptTag({ content: bundleJs });
  await page.waitForFunction(() => window.__controlReady === true, { timeout: 10000 });

  // DOM 에서 직원 배지 {state,label,color} 를 읽는 헬퍼.
  const readBadges = () =>
    page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll(".control-agent-row"));
      const out = {};
      for (const r of rows) {
        const agent = r.getAttribute("data-agent");
        const dot = r.querySelector(".control-state-dot");
        const lbl = r.querySelector(".control-state-label");
        out[agent] = {
          state: r.getAttribute("data-state"),
          label: lbl ? lbl.textContent : null,
          color: dot ? dot.getAttribute("data-color") : null,
        };
      }
      return out;
    });
  const readTasks = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll(".control-task-row")).map((r) => ({
        id: r.getAttribute("data-task-id"),
        status: r.getAttribute("data-status"),
        title: r.querySelector(".control-task-title")?.textContent ?? null,
      })),
    );
  const readChat = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll(".control-chat-line")).map((r) => ({
        role: r.getAttribute("data-role"),
        content: r.querySelector(".control-chat-content")?.textContent ?? null,
      })),
    );

  const assertScenario = async (scn, name) => {
    const badges = await readBadges();
    for (const a of scn.agents.agents) {
      const b = badges[a.agent];
      if (!b) fail(`[${name}] 직원 배지 누락: ${a.agent}`);
      if (b.state !== a.state)
        fail(`[${name}] ${a.agent} state 바인딩 불일치: 기대 ${a.state} 실제 ${b.state}`);
      if (b.label !== a.state)
        fail(`[${name}] ${a.agent} 라벨 바인딩 불일치: 기대 ${a.state} 실제 ${b.label}`);
      if (b.color !== EXPECT_COLOR[a.state])
        fail(`[${name}] ${a.agent} 색 바인딩 불일치(state→색): 기대 ${EXPECT_COLOR[a.state]} 실제 ${b.color}`);
    }
    const tasks = await readTasks();
    if (tasks.length !== scn.tasks.tasks.length)
      fail(`[${name}] task 행 수 불일치: 기대 ${scn.tasks.tasks.length} 실제 ${tasks.length}`);
    for (const t of scn.tasks.tasks) {
      const row = tasks.find((x) => x.id === String(t.task_id));
      if (!row) fail(`[${name}] task 행 누락: ${t.task_id}`);
      if (row.status !== t.status)
        fail(`[${name}] task ${t.task_id} status 바인딩 불일치: 기대 ${t.status} 실제 ${row.status}`);
      if (row.title !== t.directive)
        fail(`[${name}] task ${t.task_id} 제목 바인딩 불일치: 기대 ${t.directive} 실제 ${row.title}`);
    }
    return { badges, tasks };
  };

  // (A) 시나리오 A 렌더 + 바인딩 단언.
  await page.evaluate((m) => window.__setMock(m), SCENARIO_A);
  await page.evaluate((c) => window.__renderControl({ chatMessages: c }), CHAT);
  const a = await assertScenario(SCENARIO_A, "A");

  // Company Chat 바인딩(동결 events.ts TextMessage).
  const chat = await readChat();
  if (chat.length !== CHAT.length) fail(`Company Chat 메시지 수 불일치: 기대 ${CHAT.length} 실제 ${chat.length}`);
  if (chat[0].content !== CHAT[0].content)
    fail(`Company Chat content 바인딩 불일치: 기대 "${CHAT[0].content}" 실제 "${chat[0].content}"`);
  if (chat[0].role !== CHAT[0].role) fail(`Company Chat role 바인딩 불일치: 기대 ${CHAT[0].role} 실제 ${chat[0].role}`);

  // 패널 4종 + WA placeholder(pending-Owner) 존재 확인.
  const present = await page.evaluate(() => ({
    panels: !!document.querySelector('[data-testid="control-panels"]'),
    map: !!document.querySelector('[data-testid="map-avatar-panel"]'),
    overlay: !!document.querySelector('[data-testid="agent-status-overlay"]'),
    queue: !!document.querySelector('[data-testid="task-queue-panel"]'),
    chat: !!document.querySelector('[data-testid="company-chat"]'),
    waPlaceholder: !!document.querySelector('[data-testid="map-placeholder"]'),
    waLive: document.querySelector('[data-testid="map-avatar-panel"]')?.getAttribute("data-wa-live"),
  }));
  for (const k of ["panels", "map", "overlay", "queue", "chat"]) {
    if (!present[k]) fail(`패널 누락: ${k}`);
  }
  if (!present.waPlaceholder || present.waLive !== "0")
    fail(`WA-위-합성 렌더는 pending-Owner 여야 한다(placeholder + data-wa-live=0). 실제 waLive=${present.waLive}`);

  // (B) ★렌더-트랩 차단: mock 을 시나리오 B 로 바꾸고 재렌더 → 배지/큐가 *바뀌어야* PASS.
  await page.evaluate((m) => window.__setMock(m), SCENARIO_B);
  await page.evaluate(() => window.__renderControl({ chatMessages: [] }));
  const b = await assertScenario(SCENARIO_B, "B");

  // 두 시나리오가 실제로 달랐는지(고정 placeholder 면 동일 → 렌더-트랩) 명시 단언.
  const researchChanged = a.badges.research.state !== b.badges.research.state; // Coding → Reviewing
  const qaChanged = a.badges.qa.state !== b.badges.qa.state; // Reviewing → Blocked
  const tasksChanged = a.tasks.length !== b.tasks.length; // 2 → 1
  if (!researchChanged || !qaChanged || !tasksChanged) {
    fail(
      `렌더-트랩 의심(mock 변경에도 배지/큐 불변): researchΔ=${researchChanged} qaΔ=${qaChanged} tasksΔ=${tasksChanged}`,
    );
  }

  if (fatals.length > 0) fail(`콘솔/페이지 에러 ${fatals.length}건: ${fatals.slice(0, 3).join(" | ")}`);

  const verdict = researchChanged && qaChanged && tasksChanged && fatals.length === 0;
  console.log(
    JSON.stringify(
      {
        verdict,
        renderTrapRejected: researchChanged && qaChanged && tasksChanged,
        fatals: fatals.length,
        scenarioA: { research: a.badges.research, qa: a.badges.qa, tasks: a.tasks.length },
        scenarioB: { research: b.badges.research, qa: b.badges.qa, tasks: b.tasks.length },
        chat: chat[0],
        waSynthesis: "pending Owner: START_ROOM_URL + 공개 도메인 + TLS (code 100% / runtime pending)",
      },
      null,
      2,
    ),
  );
  if (!verdict) fail("VERDICT false");
  console.log("PASS: §9.4 4-패널 데이터바인딩 + 렌더-트랩 차단(mock 변경 시 배지/큐 변경) + 무에러");
} finally {
  await browser.close();
}
