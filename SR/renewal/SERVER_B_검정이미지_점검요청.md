# Server B ComfyUI 게이트웨이 — 이미지 생성 검정(black) 출력 점검 요청

**요약:** 게이트웨이 접근·요청 처리·ComfyUI 워크플로 구성은 **모두 정상**이나, `qwen-image` 이미지
생성 결과가 **전 픽셀 검정(0,0,0)**으로 나옵니다. 워크플로 실행 단계(VAE/모델 추정)의 점검을 요청드립니다.

> **✅ RESOLVED (2026-06-23):** Owner 가 게이트웨이 이미지 VAE 를 수리 완료. 라이브 재검증 결과
> `qwen-image` 1024×1024 = valid PNG **1,467,234 bytes**(과거 검정 출력은 ~4.2KB)·**max_channel=255·
> black_ratio=0.006126·verdict=COLOR**(과거 black_ratio=1.000). 본 문서의 VAE-decode(fp16 NaN) 가설 확정.
> 재검증 도구 = `tools/p7_generate_one.py`(생성·orch) + `tools/p7_black_check.py`(검정판별·art)·
> `verify_phase7.sh` 게이트6 = 라이브 생성+검정판별 PASS. 영상(video/video-audio) 모델은 별개로 상태 미통보.

---

## 1. 환경

- 엔드포인트: `POST <게이트웨이>:8190/v1/images/generations` (OpenAI 호환)
- 인증: `Authorization: Bearer <KEY>` → 200 OK
- 모델: `qwen-image` (`COMFY_IMAGE_MODEL`)

## 2. 재현 (요청 → 응답)

```
요청:  {"model":"qwen-image","prompt":"<텍스트>","n":1,"size":"1024x1024","response_format":"b64_json"}
응답:  200 · application/json · {"created":..., "data":[{"b64_json":"..."}]} · 약 4~5 KB
소요:  54 ~ 115초 (실제 GPU 연산 발생)
결과:  유효한 PNG 1024×1024 이나 모든 픽셀 (0,0,0) = 검정 (black ratio 1.000)
```

| 변형 | 결과 |
|---|---|
| `response_format` = b64_json / url | 둘 다 검정 |
| size = 512 / 1024 | 둘 다 검정 |
| prompt 여러 종류 | 모두 검정 (prompt 무관) |
| cfg 2.5→7.0 · steps 20→30 · seed · negative 추가 | 모두 검정 |

→ 4회 이상 일관되게 검정. (ComfyUI는 `api_gen_NNNN.png`를 정상 증분 저장 = 워크플로는 실제 실행됨)

## 3. 결정적 증거 — 출력 PNG에 박힌 ComfyUI 워크플로 (정상)

생성된 검정 PNG의 메타데이터(`tEXt: prompt`)를 디코드한 워크플로 — **prompt 전달·파라미터 모두 정상**:

| 노드 | class_type | 핵심 inputs |
|---|---|---|
| 1 | UnetLoaderGGUF | `qwen-image-2512-Q4_K_M.gguf` |
| 2 | CLIPLoaderGGUF | `Qwen2.5-VL-7B-Instruct-Q8_0.gguf` (type: qwen_image) |
| 3 | VAELoader | `qwen_image_vae.safetensors` |
| 4 | CLIPTextEncode | **요청 prompt 그대로 전달됨** ✅ |
| 5 | CLIPTextEncode | negative: "blurry, low quality, distorted" |
| 6 | EmptySD3LatentImage | width 1024, height 1024 |
| 7 | KSampler | **steps 20 · cfg 2.5 · euler · simple · denoise 1.0** (표준값) ✅ |
| 8 | VAEDecode | — |
| 9 | SaveImage | api_gen |

## 4. 배제된 원인 (클라이언트/게이트웨이 측 정상 확인)

- ❌ 클라이언트 디코드 문제 아님 — PNG 정상 디코드·워크플로 메타 정상 read
- ❌ prompt 미전달 아님 — 워크플로 노드4에 요청 prompt 정확히 들어감
- ❌ 요청 파라미터 문제 아님 — cfg/steps 변형도 검정·워크플로 파라미터 표준값
- (참고) native ComfyUI API(`/prompt`·`/system_stats`·`/object_info`)는 게이트웨이에서 404 — OpenAI 호환만 노출

## 5. 유력 원인 (점검 대상)

워크플로가 정상인데 출력만 검정 → **ComfyUI 백엔드 실행 단계** 문제로 추정:

- **VAE decode 검정** — `qwen_image_vae`를 fp16으로 디코드 시 NaN → 검정 (qwen-image에서 흔한 증상)
- 또는 GGUF UNet(Q4_K_M) / 정밀도 / GPU 메모리 이슈

## 6. 점검 요청 항목

1. **ComfyUI에서 위 워크플로**(qwen-image-2512-Q4_K_M.gguf + qwen_image_vae.safetensors)를 **직접 실행** → 검정 재현되는지 확인
2. ComfyUI를 **`--fp32-vae`** (또는 VAE bf16)로 실행 후 재시도 — VAE NaN 검정의 일반적 해법
3. ComfyUI **콘솔 로그**에 NaN / VAE / OOM 경고 확인
4. `qwen_image_vae.safetensors` · GGUF 모델 **파일 무결성**
5. 영상(`wan` / `ltx-2`)도 동일 검정 증상인지 (이미지 한정인지 전체인지)

---

> 앱 측 클라이언트·요청은 정상 동작합니다. Server B에서 이미지 출력이 정상화되면 코드 변경 없이 즉시
> 실제 미디어 생성이 가능합니다.
