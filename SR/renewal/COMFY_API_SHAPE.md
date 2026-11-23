# COMFY_API_SHAPE — Server B 미디어 게이트웨이 API 형태 + OD-P7 결정 (P7 풀 미디어)

> 이 문서는 `verify_phase7.sh` 게이트0(착수 가드)이 읽는 **권위 기록**이다. OD-P7 결정 토큰
> (`^OD-P7: (A|B|C)`)과 Owner 승인(`^OWNER-SIGNOFF:`)이 둘 다 있어야 P7이 착수 가능하다.
> 시크릿(COMFY_BASE/COMFY_API_KEY 값)은 이 문서에 **절대 기록하지 않는다**(키 이름·경로 구조만).

## OD-P7 결정 (Owner 비준)

OD-P7: B
OWNER-SIGNOFF: Owner approved 2026-06-22 (AskUserQuestion) — OpenAI-compat 정본·§24.6-native·§28.3 추출 면제

art env approved: Owner approved 2026-06-22 (AskUserQuestion) — conda -n art (Pillow/rembg/OpenCV/MoviePy/imageio-ffmpeg/onnxruntime·Wand 생략[ImageMagick 부재]).

## 결정 의미

- **(B) OpenAI-compat 정본**: 제공 Server B 게이트웨이는 OpenAI-호환이며, 본 게이트웨이에 한해
  스펙 §24.6 native ComfyUI(`POST /prompt`+workflow-JSON·dev-mode) 와 §28.3 워크플로-JSON
  **추출**을 **스펙 면제**한다. OpenAI-compat 경로(`COMFY_EP_*` 엔드포인트 + `COMFY_*_MODEL`
  셀렉터로 submit→poll→view)가 **정본**이며 실 미디어 e2e(이미지/image-edit/영상/video-audio)를
  곧바로 구동한다.
- **면제 범위**: native `/prompt` 클라이언트(`NativeComfyTransport`)는 **미구현·문서화-only**.
  §28.3 워크플로 JSON 추출은 **수행하지 않으며 하드코딩 합성도 금지**(green-fake 차단). 추후
  Owner가 native ComfyUI(OD-P7(A))를 제공하면 아래 "native 활성화 절차"로 승격 가능.

## OpenAI-compat 확정 증거 (값 미기록·키 이름/구조만)

`config/app.env`의 미디어 게이트웨이 키 스킴(`tools/render_app_env.py:130-146`):

- 엔드포인트 경로(per-endpoint): `COMFY_EP_IMAGE`·`COMFY_EP_IMAGE_EDIT`·`COMFY_EP_VIDEO`
- 모델 셀렉터(per-model): `COMFY_IMAGE_MODEL`·`COMFY_IMAGE_EDIT_MODEL`·`COMFY_VIDEO_MODEL`·`COMFY_VIDEO_AUDIO_MODEL`
- 기타: `COMFY_BASE`(`/v1` 접미)·`COMFY_API_KEY`·`COMFY_MODULE`·`COMFY_QUALITY_NOTE`

이 **per-endpoint 경로 + per-model 셀렉터** 스킴은 native ComfyUI와 **구조적으로 양립 불가**다 —
native ComfyUI는 단일 `/prompt` 엔드포인트이고 모델 선택이 workflow-JSON 노드에 박혀 있어 "엔드포인트
경로"나 "모델명 파라미터" 개념이 없다. 따라서 게이트웨이는 **OpenAI-호환으로 확정**(자원표 line313:
`COMFY_BASE=…:8190/v1`·"OpenAI-호환"·reachable 0.22s·key valid 와 정합).

## 정본 호출 경로 (OD-P7(B))

- 이미지 생성: `POST {COMFY_BASE}{COMFY_EP_IMAGE}` · model=`COMFY_IMAGE_MODEL`
- 이미지 편집: `POST {COMFY_BASE}{COMFY_EP_IMAGE_EDIT}` · model=`COMFY_IMAGE_EDIT_MODEL`
- 영상 생성: `POST {COMFY_BASE}{COMFY_EP_VIDEO}` · model=`COMFY_VIDEO_MODEL`
- 영상+오디오: `POST {COMFY_BASE}{COMFY_EP_VIDEO}` · model=`COMFY_VIDEO_AUDIO_MODEL`
- 인증: `Authorization: Bearer {COMFY_API_KEY}`
- 클라이언트: `orch/comfy.py` `OpenAICompatTransport` (submit → poll → view · `asyncio.Semaphore(1)`
  · 종료성 이중가드 `max_poll_count`+`deadline_s`). 정확한 submit/poll/응답 형태는 아래 프로브가 확정.

## native 활성화 절차 (OD-P7(A)로 재개 시·현재 미수행)

Owner가 native dev-mode ComfyUI 인스턴스(별도 BASE)를 제공하면: (1) `NativeComfyTransport` 구현
(`POST /prompt {prompt: <workflow-json>, client_id}` → poll `GET /history/{id}` → `GET /view`),
(2) ComfyUI dev-mode → Save(API Format)으로 `apps/art/workflows/{qwen_image,wan_video,ltx_video}.json`
**실 추출**(§28.3·하드코딩 금지·노드ID 주입), (3) `COMFY_API_SHAPE.md`의 `OD-P7:` 토큰을 `A`로
갱신 + 추출본 경유. 본 P7(B)에서는 수행하지 않음.

## Probe 확인 (read-only · `tools/comfy_probe.py` 가 append)

<!-- comfy_probe.py 가 아래에 reachability·auth·shape 확인 결과를 append 한다(값 미기록) -->

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.

### Probe 확인 결과 (read-only · 생성 호출 0)

- 상태: **[OK] reachable (확인 성공)**
- base 구조(redacted): `http://<redacted-host>:8190/v1`
- API key 존재: True
- 엔드포인트 경로: image=`/images/generations` · image_edit=`/images/edits` · video=`/videos/generations`
- `/models` (with key) status: 404
- `/models` (no key) status: 404 · auth_enforced: False

> per-endpoint 경로(`/images`·`/videos` 류) + per-model 셀렉터 = OpenAI-호환 확정 재확인.
