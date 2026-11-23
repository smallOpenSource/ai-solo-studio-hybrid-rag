# 발행 텍스트 윤문 (AI 티 제거) — orch.humanize

글 발행 등 한국어 텍스트 콘텐츠 발행 시, AI 특유의 "티"(번역투·결산 피벗 lexicon·이모지·균일 리듬·
hedging 등)를 제거해 자연스러운 한국어로 윤문하는 능력을 **우리 서비스(orch) 안에 구현**한 작업의 재사용
지식. 룰북은 이 저장소에서 직접 작성했다.

관련: [09_renewal_langgraph_core](모델 seam) · [17_renewal_p4_evals](caller 격리 동일 계열) · [25_provider_primary_fallback](`_active_caller` seam·graph-agnostic 패턴).

---

## 설계 — 단일콜 탐지+윤문을 orch 모듈로

- **`orch/humanize.py`**: `humanize_korean(text, *, caller=None) -> {text, applied, change_rate, note}`.
  재사용 함수(어떤 발행 지점이든 import). 모델 seam(ModelCaller)으로 **1콜 탐지+윤문+자체검증**.
- **룰북**: `orch/humanize_rules.md` = 자체 작성 룰북(손대지 않는 항목 + 유형별 "알아보는 법/고치는 법" 표 +
  자체 점검 목록). `humanize_korean` 이 이를 system 프롬프트로 로드 → 룰 갱신은 이 파일만 고치면 됨.
- **배선**: `orch/graph.py stage_content`(콘텐츠 발행 노드)가 산출 텍스트를 humanize_korean 통과(적용 시
  메시지에 `humanized Δ%` 기록). **다른 발행 지점(org finalize·Discord BLUF 등)도 동일 함수 import 로 확장 가능**.

## ★ 안전 불변식 (의미 보존 우선 — 발행 파이프라인 비차단)

발행 텍스트를 모델로 다시 쓰는 것은 **의미 훼손 위험**이 있다. 4중 가드로 "원문 보존 우선":

1. **한국어 가드**(`_korean_ratio` < 0.30 → 스킵·applied=False·**caller 호출 0**): 영문 게임 아티팩트
   (stage_content 가 종종 영문)·코드·식별자에 한국어 윤문을 잘못 적용하지 않음. → stage_content 에 무조건
   배선해도 무해(한국어일 때만 작동).
2. **의미보존 프롬프트**: 사실·주장·수치·날짜·고유명사·큰따옴표 인용 불변 · 비유/정보 추가 금지 · 장르/격식 보존.
3. **과윤문 자동 롤백**: `difflib` 변경률 > 50% → **원문 반환**(note=over-edit-rollback). 모델 폭주/환각 시
   원문이 나간다(나쁜 윤문 > 안 함).
4. **실패 비차단**: 모델 예외·빈 출력·룰북 부재 = 전부 원문 반환. 발행이 humanize 때문에 막히지 않음.

## ★ 결정론·eval 격리 (provider 작업과 동일 패턴)

`humanize_korean(caller=...)` 는 caller 를 **인자로** 받는다 — 기본은 `graph._active_caller()`(provider
primary/fallback·**lazy import** 로 graph↔humanize 순환 0·graph-agnostic). 테스트/eval 은 stub caller 주입으로
ContextVar 없이 결정론 확보. ([25] 의 "기본 caller 가 evals 를 침묵 오측" 함정과 동일 계열 — caller 명시 주입으로 격리.)

## 검증

- 단위 7(`orch/tests/test_humanize.py`): 빈/비한국어 스킵(**모델 미호출 단언**)·한국어 적용(룰북+원문 주입
  확인)·과윤문 롤백·실패 비차단·코드펜스 제거·헬퍼. orch 회귀 신규실패 0(기존 claude_permissions 3=베이스라인).
- 라이브(실 모델·Azure primary): AI 티 샘플("🚀 … 결론적으로 … 시사하는 바가 크다 ✨") → 🚀✨ 제거 ·
  번역투("통해"→"로") · 결산 피벗("결론적으로,") 제거 · **의미보존 Δ10%**(applied·과윤문 아님). green-theater 0.

> 정직 잔여: 단일 콜 윤문은 보수적(샘플서 4 tell 중 1 잔존) — 더 강한 제거는 다단계 탐지/윤문/검수
> 파이프라인이 필요(미구현).
>
> ★**발행 지점 확장(2026-06-24·D)**: `stage_content`(graph cycle) 에 더해 **`org_graph.finalize`(BLUF·Discord 등
> 외부로 발행되는 두 번째 지점)** 배선 완료. 패턴=발행 노드 끝에서 `res = humanize_korean(content, caller=_active_caller())`
> → `res["applied"]` 면 산출 텍스트 교체+Δ 메시지(graph.stage_content 와 동형). 결정론 테스트=org_graph 가 재노출하는
> `use_caller` seam 으로 stub 주입·**non-vacuous**(프롬프트의 "룰북" 유무로 finalize-합성 vs humanize 호출 분기 →
> 배선 제거 시 테스트 FAIL). 다른 발행 지점도 동일 1줄+가드로 추가.
> 코드: `orch/humanize.py` · `orch/humanize_rules.md` · `orch/graph.py`(`stage_content`) · `orch/org_graph.py`(`finalize`) ·
> `orch/tests/test_humanize.py` · `orch/tests/test_org_finalize_humanize.py`.
