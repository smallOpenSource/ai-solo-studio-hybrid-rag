# Renewal P7 — 풀 미디어 (§4.2/§17 ComfyUI 클라이언트·캐스케이드·영상 조립) · OpenAI-compat 게이트웨이·degradation 정직 처리·종료성 격리

## 개요

SR/renewal P7(§4.2 미디어 게이트웨이 + §17.1 이미지 캐스케이드 + §17.2 영상 + §24.6 클라이언트)을 구현하며 얻은
**재사용 패턴·함정**. 핵심은 ① **OpenAI-호환 게이트웨이 클라이언트**(sync view + bounded poll·실 호출 1건으로 형태 확정),
② **제공 외부자원 degradation의 정직 처리**(reachable·valid 산출이나 *내용*이 빈 경우 = ACTION pending Owner·green-fake 0),
③ **falsifiable 캐스케이드**(순수 명명 술어 + negative-mutation), ④ **subprocess 손자 orphan 격리**(start_new_session+killpg),
⑤ **동결 보존 Semaphore 주입**(ContextVar caller-decorator).

OD-P7=(B) Owner 승인: 제공 게이트웨이가 OpenAI-호환 전용(`COMFY_EP_*`+`COMFY_*_MODEL`+`/v1`)이라 스펙 §24.6-native
(`/prompt`+workflow-JSON)·§28.3 워크플로 추출은 **이 자원에 구조적 불가** → 본 게이트웨이 한정 스펙 면제·OpenAI-compat
정본 승인(`SR/renewal/COMFY_API_SHAPE.md`에 `OD-P7: B`+`OWNER-SIGNOFF:` 박제). native 경로는 미구현·문서화-only.

## ① OpenAI-compat 게이트웨이 클라이언트 (`orch/comfy.py`)

- **형태는 실 호출 1건으로 확정**(probe는 read-only 재확인·생성 0). 디스커버리 엔드포인트(`/models`)가 404여도
  생성 엔드포인트는 동작 — 최소 생성 1회로 sync/async·응답 키를 관측 후 클라이언트를 그 위에 짓는다.
  실측: `POST /images/generations` → `{"created", "data":[{"b64_json"|"url"}]}` **동기**. 영상(`/videos/generations`)은
  **동기-long**(분 단위 blocking·job-id 아님).
- **단일 `OpenAICompatTransport`**(over-engineering 회피): `data[0].b64_json|url` 있으면 sync view, `id`/`status` 있으면
  bounded poll. `ComfyTransport` Protocol은 1줄 문서화 훅(테스트 제외·YAGNI). native transport는 미구현.
- **종료성 P0 이중가드**: poll = `for _ in range(max_poll_count)` + `deadline = monotonic()+deadline_s`(둘 중 먼저 →
  `TimeoutError`·무한 while 0). sync 경로는 httpx read-timeout(=deadline_s)로 바운드. 영상 1800s·이미지 300s.
- **서버 B 동시성 1**: 모듈-레벨 `asyncio.Semaphore(1)`. **테스트 seam**: `transport=httpx.MockTransport(handler)` 주입으로
  실 HTTP 0 검증(handler는 sync 또는 async·async면 동시성/슬립까지 검증).
- **시크릿 위생**: `_auth_headers`는 헤더만 생성·값 로깅 0·에러는 타입명만 redact. probe는 host를 `<redacted-host>`로
  가리고 경로 구조만 표기. **포트(8190)·엔드포인트 경로(`/images/generations`)는 비밀 아님**(자원표 line313에 이미 공개) —
  api_key·전체 base URL만 금지.
- **관찰성**: comfy는 `httpx` 호출이라 `LangChainInstrumentor` 자동계측 비대상 → `trace.get_tracer(__name__).
  start_as_current_span`으로 **orch 최초 수동 span**(`comfy.submit/poll/view`·OBSERVE-ONLY). P3/P4에서 계승하는 건
  span 코드가 아니라 OBSERVE-ONLY *정책*뿐(P3/P4는 자동 trace·수동 span 선례 없음).

## ② ★게이트웨이 뒤 블랙박스 진단 — 레이어별 책임 분리 (가장 중요한 교훈)

**증상**: 게이트웨이가 reachable·HTTP 200·실 GPU compute(54~115s)·valid PNG·정확 해상도를 반환하나, **내용이 전부
검정**(black ratio 1.000·b64+url 양 경로·size/params/seed/cfg/steps 무관·4회+ 일관). ComfyUI는 `api_gen_NNNN.png` 증분
저장(워크플로 실제 실행됨).

**★함정 — 성급한 단정 금지**: 처음엔 "모델 degradation"으로 *모호하게* 단정했으나, 사용자 도메인 정보("ComfyUI는
정상 같다")를 받아 **재조사**한 결과 책임 레이어를 정밀히 좁힐 수 있었다. valid-format ≠ valid-content를 넘어, **어느
레이어가 문제인지**를 증거로 분리해야 한다(원격 게이트웨이는 블랙박스라 추측이 쉽다).

**진단 4-레이어 (각각 falsifiable 증거로 통과/실패 판정)**:

| 레이어 | 확인법 | 본 사례 |
|---|---|---|
| 1. 접근 | HTTP status·compute 시간 | ✅ 200·54~115s 실연산 |
| 2. prompt/파라미터 전달 | ↓ PNG 메타 디코드 | ✅ 요청 prompt가 워크플로 노드에 정확히 박힘 |
| 3. 워크플로 구성 | ↓ PNG 메타 디코드 | ✅ KSampler steps/cfg/sampler/denoise 표준값 |
| 4. 백엔드 실행(모델/VAE) | 출력 픽셀 분석 | ❌ 검정 → 이 레이어로 격리됨 |

**★핵심 도구 — 출력 PNG의 워크플로 메타데이터**: ComfyUI는 생성 PNG의 **tEXt 청크(`prompt`)에 워크플로 JSON
전체**(노드·class_type·inputs·파라미터)를 박는다. `PIL.Image.open(png).info['prompt']` 를 `json.loads` 하면 **게이트웨이가
ComfyUI에 실제로 넘긴 워크플로**가 그대로 드러난다 → 내 요청 prompt가 `CLIPTextEncode.text` 에 들어갔는지, KSampler
파라미터(steps/cfg/denoise)가 정상인지 확인해 레이어 2·3을 한 번에 검증. 원격 OpenAI-compat 게이트웨이(블랙박스) 뒤의
ComfyUI 동작을 들여다보는 결정적 창. (본 사례: prompt 전달·파라미터 전부 정상 → 검정은 레이어 4=VAE decode NaN 추정.)

**native vs OpenAI-compat 구분 프로브**: 표준 ComfyUI 엔드포인트(`/system_stats`·`/object_info`·`/prompt`·`/queue`·
`/history`)를 **root·`/v1` 양쪽**에서 read-only GET → 전부 404면 native 미노출·OpenAI-compat 래퍼 전용(OD-P7=B 같은
"native 가용성" 결정의 falsifiable 확인). `/v1/models` 단독 404는 디스커버리 부재일 뿐 native 부재 근거가 못 됨.

**분류**(`renewal-spec-is-authority`·anti-green-fake): 책임이 *제공 외부자원(Server B 백엔드)* 으로 격리되면 **ACTION
pending Owner**(≠pass·≠fail·코드 FAIL 아님). 검정 산출을 "성공"으로 통과시키면 green-theater. 코드(클라이언트·캐스케이드·
조립)는 100% 완성·검증하고 라이브 *CONTENT*만 ACTION으로 정직 표기 → 자원 수리 시 코드 변경 0으로 즉시 실 산출. verify
게이트 `PASS/FAIL/ACTION/BLOCKED` 4-카운터·`exit0 ⟺ FAIL==0 AND BLOCKED==0`(ACTION 미합산). 운영자 전달용 점검요청서는
재현 절차+워크플로 증거+점검 포인트로 작성(`SR/renewal/SERVER_B_검정이미지_점검요청.md`·시크릿 redact).

**교훈**: ① "외부자원 제공됨" ≠ "기능 정상" — 라이브 수용은 valid-format이 아니라 **falsifiable content 단언**(black
ratio·픽셀 diff·ffprobe 스트림)으로 정의. ② 원격 게이트웨이 디버깅은 **레이어 분리 + 백엔드가 남긴 메타데이터(PNG
워크플로)** 로 책임을 증거화 — 추측("degradation")으로 끝내지 말 것. ③ 사용자 도메인 정보를 받으면 단정을 재조사해
정밀화. critic은 자체 라이브 호출(115s→black 1.0)로 1차 재현해 "cop-out 아님"을 독립 입증(적대 검증의 모범).

**✅ 후속 — VAE 수리 확인 (2026-06-23·Owner "VAE 수리완료")**: 레이어 4(VAE decode NaN) 가설이 맞았다. 수리 후 라이브
재검증 = `qwen-image` 1024² valid PNG **1.47MB**(과거 검정 ~4.2KB)·**max_channel=255·black_ratio=0.006·verdict=COLOR**(과거
1.000). 재사용 도구로 코드화: `tools/p7_generate_one.py`(생성·orch·`comfy.generate_sync` 1건) + `tools/p7_black_check.py`(검정
판별·art·`ImageChops.lighter` 채널-max 히스토그램·deprecated `getdata()` 회피). 판별 기준(②교훈① 구현·falsifiable): 전 채널
max==0 또는 non-black==0 → BLACK(exit1)·그 외 COLOR(exit0). `verify_phase7.sh` 게이트6 = 하드코딩 ACTION → **라이브 생성+
검정판별 PASS**(재실행마다 fresh gen·black_ratio≪1 단언). 영상도 정상 확인(Server B): plain video=**wan**(vp9 832×480·비검정)·video-audio=**ltx-2**(h264 768×512+aac stereo)·신규
`tools/p7_video_check.py`(art·`video_assemble.has_video/audio_stream` ffmpeg stderr 재사용 + 첫 프레임 검정판별)로 비디오/오디오
스트림+프레임 비검정 단언 → 게이트8 라이브 PASS·게이트10=Server B 실 클립→`concat_clips`→유효 트레일러(게임 캡처 트레일러는
P5 게임녹화 별개). 점검요청서에 RESOLVED 배너. ★교훈: ACTION→라이브 승격은 "수리 통보"만으로 PASS
금지 — **실 생성 + falsifiable content 단언**으로만 전환(valid-format/reachable 재확인으로는 불충분).

## ③ falsifiable 캐스케이드 (`orch/image_ops.py`·T4)

- "tier1/2 불충분 → tier3"를 **결정론 명명 술어**로 정의: X1 min-resolution(`>=`)·X2 transparency(alpha ratio)·
  **X3 슬롯 정수비교**(요청 int vs 산출 수)·**X4 naming 정규식 boolean**. `escalate_to_tier3 == not meets_quality`(엄격 부정).
- **순수/처리 분리**: `meets_quality`/`escalate`/`AssetMeta`/`AssetSpec`는 **순수 파이썬**(PIL 불필요·orch env 테스트).
  실제 이미지 처리(`inspect_asset`/`tier2_process`)는 Pillow/rembg/OpenCV를 **함수 내부 지연 import**(`conda -n art` 전용).
  → 캐스케이드 *결정 로직*은 art env 없이 검증(green-theater-critical 부분), 픽셀 처리는 art env.
- **negative-mutation 입증**: `escalate→False`로 변이 시 정확히 X1-X4 + strict-negation 테스트가 FAIL, 복원 시 통과.
  주관 "불충분" 금지·X3는 정수 경계(N→N-1→escalate)만.

## ④ subprocess 손자 orphan 격리 (`orch/video_assemble.py`·종료성 P0)

- `.omc/guards/safe_run.sh`는 `timeout --kill-after` + `ulimit -v`만·**`setsid`/`start_new_session` 없음** → MoviePy→ffmpeg
  같은 **손자 프로세스 그룹을 보장 못 함**(orphan 누수). 자체 격리: `subprocess.Popen(..., start_new_session=True)`로
  새 프로세스 그룹 생성 → timeout 시 `os.killpg(os.getpgid(pid), SIGTERM)`→5s 후 SIGKILL을 **그룹**에 전송.
- **검증**: 손자 `sleep 30 &`의 PID를 파일로 받아 group kill 후 `os.kill(pid,0)`로 사멸 단언(no-op 아님).
- **static ffmpeg 함정**: johnvansickle static 빌드는 `drawtext`(libfreetype)·`subtitles`(libass) 필터를 **제외**할 수
  있다 → 자막 burn-in 불가. `ffmpeg -filters`로 가용성 확인 후 **best-effort skip**(concat 코어는 유지). ffmpeg는
  `IMAGEIO_FFMPEG_EXE` 또는 `.omc/bin/ffmpeg`(=imageio_ffmpeg v7.0.2 symlink).

## ⑤ 동결 보존 Semaphore 주입 (`orch/graph.py`·§4.1)

- §4.1 스펙은 `asyncio.Semaphore(2)`이나 `_gemma_caller`는 **sync**·langgraph 노드는 thread-executor(org_graph
  map-reduce `Send` fan-out에서 실제 동시 호출) → async sem으로 sync caller 못 감쌈. **충실한 적응** =
  `threading.BoundedSemaphore(2)`.
- **노드 본문 byte 보존 주입**: `_active_caller()`(graph.py)가 `_semaphore_wrapped(_ACTIVE_CALLER.get())`을 반환.
  모든 노드는 여전히 `_timed_call(_active_caller(), ...)` — *반환되는 caller*만 semaphore-wrap(임계구역=caller 호출만·
  release 누수 0·재진입 없음=leaf 호출 → 데드락 불가). 롤백 = `return _ACTIVE_CALLER.get()` 1줄. org_graph도
  `_active_caller` 경유라 fan-out 커버·CEO=qwen은 별도 `_strategy_caller` seam이라 gemma 슬롯 게이트와 무관.
- `graph.py`는 **동결셋 아님**(`.omc/state/protected_pre.sha` = office/{events,event_store,engine,nodes,gating}.py·
  web_server/runner.py·web/src/types/events.ts 7파일만) → `verify_freeze.sh` drift0 유지하며 자유 수정.

## 잡 함정

- **orch env에 python-dotenv 없음**(core.config는 다른 런타임). 코드에서 `from dotenv import ...` 금지 → stdlib
  KEY=VALUE 파서(session.py 선례). `COMFY_*`는 root `.env`가 아니라 `config/app.env`에 있으므로 그 경로를 파싱
  (os.environ 우선 병합). 도구↔코어 중복은 `tools/comfy_probe.py`가 `sys.path` 주입 후 `orch.comfy._load_comfy_env`
  재사용으로 단일화(deslop).
- **conda -n art** 신규 env: Pillow·rembg·opencv-python-headless·moviepy·imageio-ffmpeg + **onnxruntime**(rembg
  백엔드·미설치 시 "No onnxruntime backend"). Wand는 ImageMagick 시스템 의존(부재 시 생략). 워크어라운드
  `CONDA_NO_PLUGINS=1`+classic solver.
- **pytest `-q` 함정**: 이 설정에서 `-q`는 "X passed" 요약줄을 억제(마지막=진행바뿐) → 카운트 grep이 공란.
  카운트가 필요하면 `-q` 제거.

## 관련

- 합의계획 `.omc/plans/ralplan-p7-media.md`(v2·CONSENSUS APPROVED) · 리뷰 `.omc/research/p7-*-{review,rereview}.md`
  + `p7-impl-critic-review.md`. P6 인터페이스 = [19_renewal_p6_interface.md](19_renewal_p6_interface.md).
- ACTION 제3범주·Owner-gated 경계 = P6 wiki와 동일 규율. [[renewal-spec-is-authority]].
