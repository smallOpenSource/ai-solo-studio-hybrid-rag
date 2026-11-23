# 웹 게임 클라이언트 — 모바일 조작·HTTP 서빙·절차적 오디오 (canvas/Phaser)

자기완결 웹 게임(인라인 Canvas2D + Phaser3 Vite)을 **모바일 웹에서 실제로 플레이 가능**하게 만든 재사용
패턴 — 조작(터치/스와이프/반응형)·HTTP 서빙(폰 접속)·절차적 오디오(BGM/SFX)·헤드리스 검증. clicker /
slide-puzzle / Phaser survivor 3종에 적용·라이브 검증(2026-06-30·커밋 `6b694d4`·`4720ba3`·`fe457b5`).
이후 게임(`games/memoir-room`·`games/rhythm-tale` 등)이 동일 패턴(`_audio.js`·`/games` 마운트·모바일 좌표)을 재사용.

검증 방법론 일반은 [23_renewal_verification_methodology](23_renewal_verification_methodology.md) 참조(이 문서는 게임 클라이언트 *인스턴스*).

## 0. 게임 구조·계약 (공통)
- `games/<name>/index.html` = **인라인 Canvas2D**(자기완결·에셋 0). `game/` = **Phaser3 Vite TS**(`src/`·`dist`=빌드·gitignored).
- 계약 훅: `window.__gameState` + 액션 훅(clicker `__click`/`__buyUpgrade`·puzzle `__move`/`__moveDir`/`__neighbors`/`__reshuffle`) → 헤드리스 결정론 단언. 브리프-구동 harness(`vault/specs/*` → `orch.handoff.parse_brief`·`web/scripts/play_{clicker,puzzle}_check.mjs`). 게임 파일=동결셋 무관(자유 편집).
- ★**로드 경로 차이**: 인라인 Canvas 게임은 `file://`·`http` 양쪽 로드. **Phaser dist 는 `<script type=module>` 이라 `file://` 에선 CORS(opaque origin) 차단 → http 서빙 필수**.

## 1. 모바일 조작 (커밋 `6b694d4`)
- **Pointer Events 단일 경로**: `mousedown`→`pointerdown`(PointerEvent 가 clientX/Y 상속·마우스+터치 단일). mouse-only 는 터치서 불안정(합성 의존).
- ★**CSS 스케일 좌표 보정**: 캔버스를 `max-width:96vw;height:auto` 로 축소하면 `rect.width≠canvas.width` → `sx=canvas.width/rect.width; x=(clientX-rect.left)*sx`. naive 차감(`clientX-rect.left`)은 축소 시 적중점이 어긋남(MDN의 `pageX-offsetLeft` 매핑은 deep-research서 0-3 반박). negative-mutation(보정 제거→버튼 빗나가 AC FAIL)로 입증.
- **퍼즐 탭/스와이프 판별**: `pointerup` 에서 이동량 `max(|dx|,|dy|)>10px`(클라 px·2048 keyboard_input_manager 기법) = 스와이프→`moveDir`(방향키 대체)·이하 = 탭→타일 이동. **단일 pointer 경로라 이중발화 0**. 키보드 전용 입력(방향키·`R` 셔플)은 모바일 대체 필수 → 스와이프 + 화면 셔플 버튼.
- **반응형**: `<meta viewport content="width=device-width,initial-scale=1">` + 캔버스 `max-width:96vw;height:auto`. `touch-action`: 스와이프 캔버스=`none`(제스처 소유·스크롤/줌 차단)·탭 게임=`manipulation`(더블탭 줌·~300ms 지연 차단). ★`user-scalable=no` 금지(iOS 10+ 무시·a11y).
- **Phaser**: `scale:{ mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }`(`scene.ts` GAME_CONFIG). 고정 해상도엔 FIT(종횡비 보존·CSS 스트레치)·RESIZE 는 GPU fill-rate 부담. 부모(`#app`)가 뷰포트 크기여야 CENTER 동작. survivor 는 `movePlayer`=자동회피 **오토플레이라 입력 불요**(화면 맞춤만).
- ★**vite `base:'./'`**(`game/vite.config.ts`): 기본 `base:'/'` 는 절대 에셋 경로(`/assets/...`)라 서브경로/`file://` 서빙서 404. 상대 base 로 루트·서브경로·file:// 전부 로드.

## 2. HTTP 서빙 — 폰 접속 (커밋 `4720ba3`)
- `web_server/app.py` `_mount_static` 에 **add-only 마운트**: `/games/survivor`(→`game/dist`·Phaser) 를 먼저, `/games`(→`games/`·런처+canvas 게임) 다음, `/`(SPA 캐치올) 마지막(**Starlette 등록순 prefix 매칭** — 구체경로 우선). `games/index.html`=모바일 런처.
- ★**web 33006 = HTTPS**: `config/endpoint.yaml` `ssl_certfile/ssl_keyfile`=`/etc/studio/ssl/studio.example.com/` → `web_server/__main__.py` 가 uvicorn 에 ssl 주입(**직접 TLS 종단·프록시 아님**). 공개 URL `https://studio.example.com:33006/games/`(LAN/도메인 폰 접속·라이브 검증 4/4).
- ★**http→https 불일치 함정(오진 주의)**: 33006(HTTPS)에 `http://` 요청 → `ERR_EMPTY_RESPONSE`/curl 000. 이는 **네트워크 격리가 아니라 프로토콜 불일치**다(과거 '샌드박스 도달 불가'로 오진). 로컬 테스트는 `https` + cert-ignore(`--ignore-certificate-errors`/`ignoreHTTPSErrors`).
- ★**라이브 = working-tree 서빙**: StaticFiles 가 디스크를 매 요청 읽음 → `games/`·`game/dist` 수정/재빌드 시 **재시작 없이 즉시 반영**(git push 와 무관). dist 는 gitignored(빌드 산출).

## 3. 절차적 오디오 — BGM + SFX (커밋 `fe457b5`)
- **절차적 Web Audio(칩튠)** — 오디오 파일/시크릿/라이선스 0(생성 수단 없을 때 정답·레트로 픽셀 미학 적합). `games/_audio.js`(clicker/puzzle **공유 classic `<script src="../_audio.js">`**·factory `window.GameAudio(opts)`) + `game/src/audio.ts`(Phaser 동형 TS·**raw WebAudio**·Phaser Sound Manager 미사용=미검증+파일 필요). deep-research(MDN/web.dev) 근거.
- ★**autoplay unlock**: AudioContext 는 제스처 전 `'suspended'` → **첫 사용자 제스처 핸들러서 `ctx.resume()`**(`if(state==='suspended'||'interrupted')`). lazy 생성만으론 unlock 보장 안 됨(0-3 반박)·명시 resume 필수. canvas 게임=캔버스 탭·**Phaser 오토플레이=캔버스 탭/뮤트 버튼**으로 unlock. iOS 백그라운드→`'interrupted'`(best-effort resume).
- **SFX**=Oscillator+GainNode 엔벨로프(`setValueAtTime(0)`→`linearRampToValueAtTime(peak,+attack)`→`linearRampToValueAtTime(0,+dur)`)·noise=`AudioBuffer`(`Math.random()*2-1`). **BGM**=look-ahead 스케줄러(`setTimeout(25ms)` + `currentTime+0.1` ahead·별도스레드 클럭이라 메인 stall 무관). **mute**=master GainNode 0(낮은 마스터 ~0.22-0.25). **비차단**=feature-detect(`AudioContext||webkitAudioContext`)+전 try/catch → AudioContext 없어도 게임 그대로(검증).
- **Phaser SFX**=`scene.ts` **state-diff**(프레임마다 `kills`/`level`/`gameOver` 증가 감지→`Audio.play`·**시뮬은 순수 유지**·audio 무의존). 오토플레이 첫 처치 ~5.7s.
- ★`_audio.js` factory 는 확장 가능: 기존 `unlock/play/toggleMute` 외 `now()`(마스터 클럭=`ctx.currentTime`)·`playTone()`(즉시)·`scheduleTone(freq,atT,...)`(절대 ctx 시각 스케줄)을 노출 → **리듬 게임**(정확도 판정·곡 스케줄)이 wall-clock 비의존으로 사용(`games/rhythm-tale` 등).

## 4. 헤드리스 검증 방법론 (재사용 핵심)
- **2-경로 분리**: ① `file://` 데스크톱 harness=게임 *로직/결정론*(window.__* 직접 구동·`play_{clicker,puzzle}_check.mjs`) ② **playwright 모바일 에뮬**(`devices['iPhone 13']`·hasTouch·isMobile)=실 조작(`touchscreen.tap`·스와이프=**CDP `Input.dispatchTouchEvent` 시퀀스**·`play_mobile_controls_check.mjs`).
- ★**헤드리스 오디오 검증(무음)**: `window.__audio`{ctxState,unlocked,muted,bgmStarted,sfx:{count,last}} 훅으로 **unlock·SFX 트리거·BGM 시작·mute 를 결정론 단언**("들리는지"는 실기기 몫). Phaser 는 live sim 훅 `window.__survivorState` 로 state-diff SFX 검증(kills↑까지 폴링·`play_audio_check.mjs`).
- ★**Phaser dist=ES모듈** → `file://` CORS 차단 → 검증은 **in-process http 서버**(node `http`·포트 0)로 dist 서빙 후 로드. classic script(canvas 게임)는 file:// OK.
- **negative-mutation**(스케일보정/SFX 호출 제거→해당 AC FAIL)·**회귀**(기존 harness·game 단위 vitest 50/50 무손)·**라이브 공개 URL**(https+cert-ignore·외부포트 도달 필요 시 `dangerouslyDisableSandbox`).
- **헤드리스 tap→버튼 `click` 합성 불발**: `locator.tap()` 이 표준 `<button>` 의 click 을 안 내보낼 수 있음 → 테스트는 `click()` 폴백(실 모바일은 표준 button+`touch-action:manipulation` 으로 탭=click 정상). 단, canvas 의 pointer 이벤트는 tap 으로 정상 발화.

## 함정 모음 (박제)
1. **http→https 불일치** = ERR_EMPTY_RESPONSE/000 (네트워크 격리 아님·§2).
2. **ES모듈 file:// CORS** 차단 → Phaser 는 http 서빙(§0·§4).
3. **vite `base:'/'`** = 서브경로/file:// 에셋 404 → `base:'./'`(§1).
4. **autoplay**: lazy 생성만으론 unlock 안 됨 → 제스처서 명시 `resume()`(§3).
5. **CSS 스케일 좌표** 미보정 → 적중점 어긋남(§1).
6. **헤드리스 tap→click** 합성 불발 → click 폴백(§4).
