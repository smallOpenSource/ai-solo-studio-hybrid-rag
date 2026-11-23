/**
 * P5 트레일러용 — 슬라이드 퍼즐 게임플레이 영상 캡처 (playwright recordVideo·NET-NEW).
 *
 * games/slide-puzzle 를 헤드리스 chromium 으로 열어 BFS 로 보드를 실제 정렬하며 각 이동을
 * ~380ms 간격으로 시연 → 시각적 게임 footage(.webm)를 녹화한다. 이 footage 는 LTX-2 b-roll 과
 * orch.video_assemble.concat_clips 로 합쳐져 §28.1 #7 트레일러가 된다(실 게임 캡처·렌더-trap 아님).
 *
 * 실행: PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers node web/scripts/capture_puzzle_footage.mjs [outPath]
 * exit 0 = CAPTURE_OK, 비-0 = 실패. window.__gameState/__move 는 게임이 노출(play_puzzle_check.mjs 와 동일 계약).
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "..", "..");
const gameUrl = "file://" + path.join(REPO, "games", "slide-puzzle", "index.html");
const outPath = process.argv[2] || "/tmp/p5_game_footage.webm";
const outDir = path.dirname(outPath);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function neighborsIdx(blank, size) {
  const r = Math.floor(blank / size), c = blank % size;
  const out = [];
  if (r > 0) out.push(blank - size);
  if (r < size - 1) out.push(blank + size);
  if (c > 0) out.push(blank - 1);
  if (c < size - 1) out.push(blank + 1);
  return out;
}

// BFS: 결정론 보드를 goal 까지 (빈칸-인접 이동 시퀀스). play_puzzle_check.mjs 와 동일 알고리즘.
function solveBFS(startTiles, goal, size) {
  const goalKey = goal.join(","), startKey = startTiles.join(",");
  if (startKey === goalKey) return [];
  const seen = new Set([startKey]);
  let queue = [{ tiles: startTiles.slice(), blank: startTiles.indexOf(0), path: [] }];
  const MAX = 400000;
  let visited = 0;
  while (queue.length) {
    const next = [];
    for (const cur of queue) {
      if (++visited > MAX) return null;
      for (const nIdx of neighborsIdx(cur.blank, size)) {
        const t = cur.tiles.slice();
        t[cur.blank] = t[nIdx];
        t[nIdx] = 0;
        const key = t.join(",");
        if (seen.has(key)) continue;
        seen.add(key);
        const p = cur.path.concat(nIdx);
        if (key === goalKey) return p;
        next.push({ tiles: t, blank: nIdx, path: p });
      }
    }
    queue = next;
  }
  return null;
}

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
  ],
});
try {
  const context = await browser.newContext({
    viewport: { width: 800, height: 800 },
    recordVideo: { dir: outDir, size: { width: 800, height: 800 } },
  });
  const page = await context.newPage();
  await page.goto(gameUrl, { waitUntil: "load" });
  await sleep(900); // 시작 화면 노출

  const gs = () => page.evaluate(() => window.__gameState);
  const move = (idx) => page.evaluate((k) => window.__move(k), idx);

  const s0 = await gs();
  if (!s0 || !Array.isArray(s0.tiles)) {
    console.error("FAIL: window.__gameState 미노출");
    process.exit(1);
  }
  const size = s0.size;
  const goal = [];
  for (let i = 1; i < size * size; i++) goal.push(i);
  goal.push(0);

  const solvePath = solveBFS(s0.tiles.slice(), goal, size);
  if (!solvePath) {
    console.error("FAIL: BFS 풀이 실패");
    process.exit(1);
  }
  for (const idx of solvePath) {
    await move(idx);
    await sleep(380); // 타일 슬라이드 시연(시각적 footage)
  }
  await sleep(1600); // solved 피날레 유지

  const video = page.video();
  await context.close(); // recordVideo 는 context.close 시 저장 완료
  if (video) await video.saveAs(outPath);
  console.log("CAPTURE_OK out=" + outPath + " moves=" + solvePath.length);
} finally {
  await browser.close();
}
