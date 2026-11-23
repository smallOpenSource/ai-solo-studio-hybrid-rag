/**
 * audio.ts — Phaser survivor 절차적 Web Audio(칩튠). games/_audio.js(canvas 게임)와 동형의 TS.
 *
 * raw Web Audio 사용(Phaser Sound Manager 미사용 — deep-research wu1iezbr4 에서 미검증 + 오디오 파일 필요).
 * 근거: ① autoplay — ctx 는 제스처 전 'suspended'·제스처서 resume(). ② SFX=Oscillator+Gain 엔벨로프.
 * ③ BGM=look-ahead 스케줄러(25ms/100ms·currentTime). ④ 비차단=feature-detect+try/catch. ⑤ 마스터 게인 낮게.
 * survivor 는 오토플레이라 입력이 없으므로 캔버스 첫 pointerdown(scene) 또는 뮤트버튼 탭으로 unlock.
 */

const ACtor: typeof AudioContext | undefined =
  (window as typeof window & { webkitAudioContext?: typeof AudioContext }).AudioContext ||
  (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;

const MASTER = 0.22;
const NOTE_DUR = 0.15;
const BGM = [330, 330, 392, 294, 330, 440, 392, 330]; // E E G D · E A G E (긴장감)

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let muted = false;
let bgmOn = false;
let bgmIdx = 0;
let nextT = 0;

const hook = {
  ctxState: "none" as string,
  unlocked: false,
  muted: false,
  bgmStarted: false,
  sfx: { count: 0, last: null as string | null },
};
(window as unknown as { __audio: typeof hook }).__audio = hook;

function ensure(): AudioContext | null {
  if (ctx || !ACtor) return ctx;
  try {
    ctx = new ACtor();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : MASTER;
    master.connect(ctx.destination);
    ctx.onstatechange = () => { hook.ctxState = ctx ? ctx.state : "none"; };
    hook.ctxState = ctx.state;
  } catch {
    ctx = null;
  }
  return ctx;
}

function unlock(): void {
  if (!ACtor) return;
  if (!ctx) ensure();
  if (!ctx) return;
  try {
    if (ctx.state === "suspended" || (ctx.state as string) === "interrupted") {
      ctx.resume().then(() => { hook.ctxState = ctx ? ctx.state : "none"; }).catch(() => {});
    }
  } catch { /* non-blocking */ }
  hook.unlocked = true;
  hook.ctxState = ctx.state;
  startBgm();
}

function tone(freq: number, dur: number, type: OscillatorType, peak: number): void {
  if (!ctx || !master) return;
  try {
    if (ctx.state !== "running") ctx.resume();
    const t = ctx.currentTime;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(peak, t + 0.008);
    g.gain.linearRampToValueAtTime(0, t + dur);
    o.connect(g);
    g.connect(master);
    o.start(t);
    o.stop(t + dur);
  } catch { /* non-blocking */ }
}

const SFX: Record<string, () => void> = {
  kill: () => tone(180, 0.06, "sawtooth", 0.4),
  levelup: () => { tone(523, 0.1, "square", 0.5); window.setTimeout(() => tone(880, 0.16, "square", 0.5), 90); },
  gameover: () => { [440, 349, 262].forEach((f, i) => window.setTimeout(() => tone(f, 0.22, "triangle", 0.5), i * 160)); },
};

function play(name: string): void {
  hook.sfx.count++;
  hook.sfx.last = name;
  if (ctx && !muted && SFX[name]) {
    try { SFX[name](); } catch { /* non-blocking */ }
  }
}

function scheduleNote(t: number): void {
  const f = BGM[bgmIdx % BGM.length];
  bgmIdx++;
  if (!f || !ctx || !master) return;
  try {
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "triangle";
    o.frequency.setValueAtTime(f, t);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(0.09, t + 0.02);
    g.gain.linearRampToValueAtTime(0, t + NOTE_DUR * 0.9);
    o.connect(g);
    g.connect(master);
    o.start(t);
    o.stop(t + NOTE_DUR);
  } catch { /* non-blocking */ }
}

function scheduler(): void {
  if (!bgmOn || !ctx) return;
  try {
    while (nextT < ctx.currentTime + 0.1) { scheduleNote(nextT); nextT += NOTE_DUR; }
  } catch { /* non-blocking */ }
  window.setTimeout(scheduler, 25);
}

function startBgm(): void {
  if (bgmOn || !ctx) return;
  bgmOn = true;
  hook.bgmStarted = true;
  nextT = ctx.currentTime + 0.1;
  scheduler();
}

function setMuted(m: boolean): void {
  muted = m;
  hook.muted = m;
  if (master) { try { master.gain.value = m ? 0 : MASTER; } catch { /* ignore */ } }
}
function toggleMute(): boolean { setMuted(!muted); return muted; }

export const Audio = {
  unlock,
  play,
  toggleMute,
  isMuted: () => muted,
  isUnlocked: () => hook.unlocked,
};

// HTML 뮤트 버튼(game/index.html)이 호출.
(window as unknown as { __gameAudio: typeof Audio }).__gameAudio = Audio;
