/*
 * _audio.js — 게임 공용 절차적 Web Audio(칩튠) 코어. clicker/puzzle 가 classic <script src="../_audio.js">
 * 로 공유(file://·http 양쪽 로드 — classic 스크립트는 ES모듈과 달리 CORS 무관). Phaser 는 game/src/audio.ts(동형 TS).
 *
 * 근거(deep-research wu1iezbr4·MDN/web.dev): ① autoplay 정책 — ctx 는 제스처 전 'suspended'·제스처 핸들러서
 * `if(state==='suspended'||'interrupted') resume()` (lazy 생성만으론 unlock 보장X·0-3 반박). ② SFX = Oscillator+
 * GainNode 엔벨로프(setValueAtTime→linearRampToValueAtTime)·noise = AudioBuffer(Math.random()*2-1). ③ BGM =
 * look-ahead 스케줄러(25ms setTimeout·100ms ahead·ctx.currentTime 별도스레드 클럭). ④ 비차단 = feature-detect+
 * 전 try/catch·실패해도 게임 그대로. ⑤ 마스터 게인 낮게·mute=gain 0.
 *
 * window.GameAudio(opts) → 인스턴스. opts={ master, noteDur, bgm:[Hz...|0=쉼], sfx:({tone,noise,chord})=>({name:fn}) }.
 * window.__audio = { ctxState, unlocked, muted, bgmStarted, sfx:{count,last}, voiceCalls:{melody,bass,perc,ambient} } (무음 검증 훅).
 *
 * ★rhythm-tale 가산 확장(기존 시그니처/전역 보존·clicker/puzzle 비회귀): 인스턴스에
 *   now()=ctx.currentTime(마스터 클럭·미초기화 0) · playTone(freq,dur,type,peak)=즉시 발음(tone 노출) ·
 *   scheduleTone(freq,atT,dur,type,peak,voice)=절대 ctx 시각 atT 에 노트 스케줄. 모두 ctx 없으면 no-op(비차단).
 *   ★voice(선택) 전달 시 hook.voiceCalls[voice]++ 를 ctx 가드 *앞*에 가산(헤드리스 no-op 여도 호출수 결정론·
 *   AC7 다층편곡 결합 교차검증 — hook.sfx.count++ 패턴 동형). voice 미전달이면 기존 동작(clicker/puzzle 비회귀).
 *
 * ★memoir-room 가산 확장(기존 시그니처 호환·전역 보존·clicker/puzzle/rhythm 비회귀): 인스턴스에
 *   ambient(plan[,dur,peak])=절차적 앰비언트 모티프 패드 swell(소프트 사인 다중성부 드론·노스탤지어 톤).
 *   plan=성부열(모티프) — 평면 freq(숫자·기존 호환) 또는 {freq,at,dur,peak}(모티프·시간 엇갈림). ctx 없으면
 *   no-op(비차단)·전 try/catch·master 게인 경유(mute 적용). 하네스 미게이트(비게이트 feel).
 *   ★앰비언트 성부 카운터 hook.voiceCalls.ambient 를 ctx 가드 *앞*에 성부 수만큼 가산(plan 소비 결정론 입증·
 *   헤드리스 호출수 결정론·신규 키라 melody/bass/perc 비간섭). voice 카운터(scheduleTone) 패턴 동형.
 */
(function () {
  "use strict";
  window.GameAudio = function (opts) {
    opts = opts || {};
    var AC = window.AudioContext || window.webkitAudioContext;
    var BGM = opts.bgm || [];
    var NOTE_DUR = opts.noteDur || 0.22;
    var MASTER = typeof opts.master === "number" ? opts.master : 0.25;
    var ctx = null, master = null, bgmTimer = null;
    var muted = false, bgmOn = false, bgmIdx = 0, nextT = 0;

    var hook = { ctxState: "none", unlocked: false, muted: false, bgmStarted: false, sfx: { count: 0, last: null },
      voiceCalls: { melody: 0, bass: 0, perc: 0, ambient: 0 } };   // ★rhythm 성부별 scheduleTone 카운터 + ★memoir 앰비언트 성부 카운터(가산 키·결합 교차검증)
    window.__audio = hook;

    function ensure() {
      if (ctx || !AC) return ctx;
      try {
        ctx = new AC();
        master = ctx.createGain();
        master.gain.value = muted ? 0 : MASTER;
        master.connect(ctx.destination);
        ctx.onstatechange = function () { hook.ctxState = ctx.state; };
        hook.ctxState = ctx.state;
      } catch (e) { ctx = null; }
      return ctx;
    }

    // 첫 사용자 제스처서 호출(autoplay unlock). lazy 생성 + 명시 resume.
    function unlock() {
      if (!AC) return;
      if (!ctx) ensure();
      if (!ctx) return;
      try {
        if (ctx.state === "suspended" || ctx.state === "interrupted") {
          ctx.resume().then(function () { hook.ctxState = ctx.state; }).catch(function () {});
        }
      } catch (e) {}
      hook.unlocked = true;
      hook.ctxState = ctx.state;
      startBgm();
    }

    function tone(freq, dur, type, peak) {
      if (!ctx) return;
      try {
        if (ctx.state !== "running") ctx.resume();
        var t = ctx.currentTime, o = ctx.createOscillator(), g = ctx.createGain();
        o.type = type || "square";
        o.frequency.setValueAtTime(freq, t);
        g.gain.setValueAtTime(0, t);                          // 엔벨로프: 0→peak(attack)→0
        g.gain.linearRampToValueAtTime(peak || 0.5, t + 0.008);
        g.gain.linearRampToValueAtTime(0, t + dur);
        o.connect(g); g.connect(master);
        o.start(t); o.stop(t + dur);
      } catch (e) {}
    }

    function noise(dur, peak) {
      if (!ctx) return;
      try {
        var t = ctx.currentTime, n = Math.floor(ctx.sampleRate * dur);
        var buf = ctx.createBuffer(1, n, ctx.sampleRate), d = buf.getChannelData(0);
        for (var i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;   // 백색잡음(타격/셔플 SFX)
        var s = ctx.createBufferSource(), g = ctx.createGain();
        s.buffer = buf;
        g.gain.setValueAtTime(peak || 0.3, t);
        g.gain.linearRampToValueAtTime(0, t + dur);
        s.connect(g); g.connect(master);
        s.start(t); s.stop(t + dur);
      } catch (e) {}
    }

    function chord(freqs, dur, type, peak, stagger) {
      freqs.forEach(function (f, i) { setTimeout(function () { tone(f, dur, type, peak); }, i * (stagger || 0)); });
    }

    // ★rhythm 가산: 마스터 클럭(ctx.currentTime·미초기화 0) — wall-clock 비의존 판정/스폰의 기준.
    function nowT() { return ctx ? ctx.currentTime : 0; }
    // ★rhythm 가산: 절대 ctx 시각 atT 에 노트 톤을 스케줄(곡 멜로디/다층 — 룩어헤드 없이 곡 시작 시 일괄).
    function scheduleTone(freq, atT, dur, type, peak, voice) {
      // ★성부 호출 카운터: ctx 가드 *앞*(hook.sfx.count++ 동형) — 헤드리스 no-op 여도 호출수 결정론(AC7 결합 교차검증).
      if (voice && hook.voiceCalls[voice] != null) hook.voiceCalls[voice]++;
      if (!ctx) return;
      try {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.type = type || "triangle";
        o.frequency.setValueAtTime(freq, atT);
        g.gain.setValueAtTime(0, atT);
        g.gain.linearRampToValueAtTime(peak || 0.3, atT + 0.01);
        g.gain.linearRampToValueAtTime(0, atT + (dur || 0.18));
        o.connect(g); g.connect(master);
        o.start(atT); o.stop(atT + (dur || 0.18) + 0.02);
      } catch (e) {}
    }

    // ★memoir 가산(기존 시그니처 호환·clicker/puzzle/rhythm 비회귀): 절차적 앰비언트 모티프 패드.
    //   plan = 앰비언트 성부열(모티프) — 각 성부는 평면 freq(숫자·기존 호환) 또는 {freq,at,dur,peak}(모티프·
    //   시간 엇갈림 at 로 정적 동시 코드가 아닌 시퀀스). 소프트 사인 다중성부 드론(느린 어택→릴리스 swell).
    //   ★앰비언트 성부 카운터: hook.voiceCalls.ambient 를 ctx 가드 *앞*(scheduleTone voiceCalls 패턴 동형)에서
    //   성부 수만큼 가산 — 헤드리스 no-op 여도 호출수 결정론(plan 실 소비 교차검증·계산만=hollow→RED).
    //   ctx 없으면 (카운트 후) no-op(헤드리스/미-unlock 비차단)·전 try/catch. master 게인 경유라 mute 도 적용.
    //   비게이트 feel(하네스 미게이트). melody/bass/perc 카운터 비간섭(신규 키 ambient 만 가산).
    function ambientPad(plan, dur, peak) {
      var voices = (plan && plan.length) ? plan : [];
      hook.voiceCalls.ambient += voices.length;            // ★성부 카운터(ctx 가드 앞·결정론·plan 소비 입증)
      if (!ctx || !voices.length) return;
      try {
        var t0 = ctx.currentTime, D = dur || 3.2, P = typeof peak === "number" ? peak : 0.035;
        for (var i = 0; i < voices.length; i++) {
          var v = voices[i], isNum = typeof v === "number";
          var f = isNum ? v : v.freq;
          var at = isNum ? 0 : (v.at || 0);                 // 모티프 = 성부별 시간 엇갈림(stagger·정적 코드 아님)
          var vd = isNum ? D : (v.dur || D);
          var vp = isNum ? P : (typeof v.peak === "number" ? v.peak : P);
          var o = ctx.createOscillator(), g = ctx.createGain();
          o.type = "sine";
          o.frequency.setValueAtTime(f, t0 + at);
          g.gain.setValueAtTime(0, t0 + at);
          g.gain.linearRampToValueAtTime(vp, t0 + at + vd * 0.4);   // 느린 어택
          g.gain.linearRampToValueAtTime(0, t0 + at + vd);          // 느린 릴리스
          o.connect(g); g.connect(master);
          o.start(t0 + at); o.stop(t0 + at + vd + 0.05);
        }
      } catch (e) {}
    }

    var SFX = opts.sfx ? opts.sfx({ tone: tone, noise: noise, chord: chord }) : {};
    function play(name) {                                     // 카운트=트리거(배선)·소리=ctx+비뮤트일 때만
      hook.sfx.count++; hook.sfx.last = name;
      if (ctx && !muted && SFX[name]) { try { SFX[name](); } catch (e) {} }
    }

    function scheduleNote(t) {
      var f = BGM[bgmIdx % BGM.length]; bgmIdx++;
      if (!f) return;                                         // 0 = 쉼표
      try {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.type = "triangle";
        o.frequency.setValueAtTime(f, t);
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(0.1, t + 0.02);
        g.gain.linearRampToValueAtTime(0, t + NOTE_DUR * 0.9);
        o.connect(g); g.connect(master);
        o.start(t); o.stop(t + NOTE_DUR);
      } catch (e) {}
    }
    function scheduler() {                                    // look-ahead(25ms/100ms·currentTime 클럭)
      if (!bgmOn || !ctx) return;
      try { while (nextT < ctx.currentTime + 0.1) { scheduleNote(nextT); nextT += NOTE_DUR; } } catch (e) {}
      bgmTimer = setTimeout(scheduler, 25);
    }
    function startBgm() {
      if (bgmOn || !ctx || !BGM.length) return;
      bgmOn = true; hook.bgmStarted = true;
      nextT = ctx.currentTime + 0.1; scheduler();
    }

    function setMuted(m) { muted = !!m; hook.muted = muted; if (master) { try { master.gain.value = muted ? 0 : MASTER; } catch (e) {} } }
    function toggleMute() { setMuted(!muted); return muted; }

    return {
      unlock: unlock, play: play, toggleMute: toggleMute, setMuted: setMuted,
      isMuted: function () { return muted; }, isUnlocked: function () { return hook.unlocked; },
      // ★rhythm 가산 API(기존 메서드 보존). now=마스터 클럭·playTone=즉시·scheduleTone=절대 스케줄.
      now: nowT, playTone: tone, scheduleTone: scheduleTone,
      // ★memoir 가산 API: ambient(freqs[,dur,peak])=절차적 앰비언트 패드 swell(ctx 없으면 no-op).
      ambient: ambientPad,
    };
  };
})();
