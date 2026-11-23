# PRD — rhythm-tale (리듬 × 내러티브 어드벤처)

> 차기작 후보 심화 PRD. 정본 계약(AC)은 `vault/specs/rhythm-tale.md`(핸드오프 브리프) + `.omc/plans/all-in-one-game-prototypes.md` §4.2. 본 문서는 서사·시장·매출 레이어이며, 버티컬 슬라이스는 `games/rhythm-tale/`(검증 하네스 `web/scripts/play_rhythm_check.mjs`).
> 근거: `docs/market_analysis.md`(R2.2.4·적대검증 통과 조합).

---

## 1. 컨셉 / 핵심 루프

오리지널 OST 위에서 **연주 정확도(타이밍 판정의 창발 결과)가 엔딩을 직접 변경**하는 리듬-내러티브 어드벤처. 플레이어는 서사 장면 사이의 "무대(곡)"를 연주하고, 그 정확도가 인물 관계·이야기의 끝을 가른다.

- **핵심 루프**: 서사 장면 → 곡 연주(노트 판정: Perfect/Good/Miss) → 정확도 창발 → 정확도 임계가 분기를 결정 → 엔딩 장면(연주 결과가 톤을 바꿈) → 다음 챕터/재연주.
- **판정 파이프라인(슬라이스 구현)**: `탭/키 → judge(deltaMs) → Perfect(±45ms)/Good(±90ms)/Miss → accuracy=(perfect+good)/notesJudged 창발 → resolveEnding(accuracy) → 엔딩 A/B`. accuracy·ending 은 **직접세팅 금지**(판정에서만 창발).
- **오디오**: AudioContext.currentTime 마스터 클럭(드리프트 0). 슬라이스는 절차적 합성(자기완결), 프로덕션은 저작 OST(로드맵).

---

## 2. 차별화 (빈틈 직격)

`market_analysis.md` 가 식별한 리듬-내러티브 니치의 5결함을 정조준한다:

1. **볼륨**: 15편 중 10+가 1~4h "짧다" 평. → 8~12h 유기적 볼륨(인위적 게이팅 없이·챕터 셀렉트).
2. **★리듬 결과 연동 분기(시장 미실현·슬라이스 직격 입증)**: Stray Gods·Afterlove EP 는 **선택지 분기는 있으나 "리듬 결과 연동 분기"는 없다**. "연주 실패=다른 엔딩"의 인과가 시장에 미실현. → 슬라이스가 이를 **결정론으로 입증**(아래 §7 정직성).
3. **리듬-서사 유기 통합**: 대부분 "리듬 파트 ↔ 스토리 파트" 분리. 연주가 서사 톤·감정을 실시간 반영하는 통합은 미개척. → 슬라이스는 **경량(파라미터=state 함수)으로 부분 입증**, 완전 통합은 로드맵(§7).
4. **폴리싱**: UNBEATABLE 의 버그/softlock("failing songs permanently locks story") 회피 — 실패가 막다른 길이 아니라 **다른 엔딩**(분기)이며 재연주(챕터 셀렉트) 보장.
5. **챕터 셀렉트 / 접근성**: 난이도·재시도·챕터 점프로 라이트 리듬층까지 포용.

---

## 3. 타깃

- 내러티브 어드벤처 + **라이트 리듬** 교차층(하드코어 리듬 비전제·접근성 난이도 옵션).
- 비주얼노벨/선택지 분기 애호층 중 "선택이 진짜 결과로 이어지길" 원하는 코어.
- 연령·성향: 광범위(스토리 우선). 리듬은 진입장벽이 아니라 감정 전달 장치로 포지셔닝.

---

## 4. 가격 (근거)

- **$19.99**.
- 앵커: Stray Gods $24.99 가 "6~7h 대비 비싸다" 지적, UNBEATABLE ~$24.99. → **볼륨(8~12h)으로 $19.99 정당화**, 상위 밴드($24.99)보다 낮춰 "가격 대비 짧다" 불만을 선제 회피.

---

## 5. 1년 매출 추정 (3 시나리오)

> **★톤다운(박제·필수)**: 모든 수치는 **추정·앵커 기반**이며 다음 경고를 병기한다.
> - **UNBEATABLE(~87~93k/$2.1M 추정)은 4년 팬덤의 n=1 아웃라이어** — 천장은 입증, 바닥 미입증. 낙관 시나리오의 "클래스"일 뿐 기대값 아님.
> - **리듬 PC 태그는 협소**(상위 99편 ~$1.9M부터 — 롱테일 급락). 내러티브 교차로 모수를 넓히는 전략이나 floor 미입증.
> - **$2.5B "리듬 TAM" 인용 금지**(대부분 모바일 — PC 패키지 시장과 분리). 본 PRD 는 PC/Steam 패키지 기준만 사용.
> - **gross 표기**. 순수령 ≈ **gross × 0.6**(Steam 30% + VAT/환불/세금 ≈ 추가 10%p). wishlist→sale 전환 ~10~20%, review→sale 배수 ~30~60× 가정.

| 시나리오 | 판매량(units, year-1) | **gross** | 순수령 ≈ ×0.6 | 가정·근거 |
|---|---|---|---|---|
| 비관 | ~5~10k | ~$100k~200k | ~$60k~120k | 리듬 PC floor 미입증·노출 실패 시. 솔로/소팀 BEP 하회 위험. |
| 기본 | ~25~45k | ~$500k~900k | ~$300k~540k | 내러티브+리듬 교차 모수·"분기 입증" 입소문 정상 전환. |
| 낙관 | ~85~115k | ~$1.7M~2.3M | ~$1.0M~1.4M | **UNBEATABLE 클래스 — 단 4년 팬덤 outlier 경고 병기**. 재현 보장 아님. |

> 해석: 기본 시나리오가 **솔로/소팀 손익분기 + 차기작 자금** 수준. 낙관은 outlier 재현이라 **계획 근거로 삼지 않는다**(상한 가능성 표시용).

---

## 6. 리스크 + 완화

| 리스크 | 완화 |
|---|---|
| n=1 천장 불확실(UNBEATABLE outlier) | 매출 계획을 **기본 시나리오**에 정렬·낙관 배제. 데모/위시리스트로 floor 선검증. |
| 작곡/차트 제작 실비용 | 슬라이스=절차적 합성으로 메커닉 먼저 입증, 저작 OST 는 메커닉 검증 후 단계 투입(로드맵). |
| 리듬-서사 통합 연출 난이도 | 슬라이스는 **파라메트릭 경량 통합**(파라미터=state 함수)으로 위험 분할, 완전 통합은 로드맵에서 점증. |
| 폴리싱/softlock | "실패=막힘"이 아니라 "실패=다른 엔딩(분기)" 설계 + 챕터 셀렉트·재연주로 softlock 원천 차단. |
| 리듬 진입장벽 | 접근성 난이도·판정 윈도우 옵션·오토/어시스트 모드로 라이트층 포용. |

---

## 7. ★정직성 표기 (슬라이스 입증 범위 — 과대주장 금지)

본 슬라이스가 **무엇을 입증하고 무엇을 이월하는지** 명시한다(헤드라인이 증거를 초과하지 않게):

- **②(연주 정확도 → 엔딩 창발) = 슬라이스가 결정론으로 입증.** `games/rhythm-tale/` + `web/scripts/play_rhythm_check.mjs` 의 AC4 게이트가 (i) accuracy/ending 직접세팅 금지(judge 경유 보정탭), (ii) `accuracy=(perfect+good)/notesJudged` 창발 재계산 일치·`notesJudged==noteCount`·판정 카운트 정합, (iii) **falsifiable 인과쌍**(같은 노트열·같은 judge 경로에서 **오직 탭 타이밍만** 다른 두 런이 엔딩을 가름)을 단언한다. → "대사선택 브랜처에 리듬 의상"이 **아님**을 위조 불가하게 입증.
- **③(리듬-서사 유기 통합) = R2 에서 다층 실배선으로 본체화(슬라이스 충실도).** R1 의 R-AC6(파라미터=state 함수) 위에, R2 는 **다층 성부(멜로디+베이스+퍼커션) 실 스케줄**을 가산한다 — `__activeLayers(state)`(순수·콤보 함수·베이스 ≥8·퍼커션 ≥16)와 `__plannedVoices(state)`(순수·=`scheduleMelody` 가 단일소스 iterate 하는 성부 스케줄)의 **성부집합이 일치하는 결합 불변식**을 게이트하고(우회=RED·`_audio.js` 성부별 호출 카운터 교차검증), 섹션-장면 톤이 **음색(waveform)을 실 변조**한다(③ state 구동). 즉 "레이어 계산되나 실편곡 미합성"(hollow/vestigial)을 구조적으로 차단한다. **R3(셋째 슬라이스)** 는 여기에 (a) **다중트랙 테마형 편곡**(`__sectionTheme(i)` 섹션 distinct·인덱스 파생·rng 비소비 → `__plannedVoices` melody/bass `freq`===`LANE_FREQ[lane]`에 테마 transpose 를 적용한 **독립 기대값과 등치**[PITCH]·음색===`toneToWaveform(__sectionTheme(n.section).tone)`[TIMBRE]·**테마 미소비 raw/identity 테마=RED**·same-lane cross-section freq 상이·성부 카운터 정확값 불변·**신규 성부 0**)와 (b) **풀 ③ 연출**(섹션 장면을 질의가능 DOM `#scene` 에 `render()` 가 textContent 로 **렌더 소비**·계산만=vestigial RED·memoir AC8 동형·per-note 섹션 톤→음색)을 가산해, "테마 계산되나 freq/음색 미배선"·"연출 계산되나 미렌더"를 추가로 구조 차단한다(live-flow LF 게이트가 라이브 전이서도 동일 보장·테마 live hollow 0). **단 R3 테마는 절차적**(인덱스 파생 transpose·작곡 스코어 아님)이며, **완전 통합(연주가 서사 분기·대사·연출·카메라를 실시간 직조)·라이선스 OST 는 본 슬라이스 미입증 → §8 제작 로드맵으로 명시 이월**("필터" 등 미연결 계산값은 도입하지 않음 — 정직성). market_analysis 가 ③을 별개 빈틈으로 명명하며, 슬라이스는 ②(완전) + ③(다층 실배선·state 구동까지)을 instrument 한다.

---

## 8. 프로토타입 범위 (= AC) / 제작 로드맵

### 8.1 프로토타입 범위 (슬라이스 = §4.2 AC1~AC10 과 1:1 — R1 AC1~6 + R2 가산 AC7~10)
- 1곡(~60~90s·노트 40·시드 0x1357 골든) · 판정(judge 경유)+점수+콤보 · 서사 장면 ≥1 · **정확도 임계 엔딩 분기(창발·하드닝·ending A/B 보존)** · 결과/재시도(phase 순환).
- **R2 가산(③ 본체화)**: **3섹션**(노트 section 인덱스·rng 비소비·섹션 전이 게이트·`perSection` 창발) · **다중엔딩 ≥3**(`resolveFinale(perSection)` — 클리어 섹션수 3=radiant·1~2=bittersweet·0=faded·창발·직접세팅 금지) · **③ 다층편곡 실배선**(`__plannedVoices`===`__activeLayers` 결합 불변식·멜로디+베이스+퍼커션·scene→음색) · **섹션 셀렉트**(softlock 회피). **AC 가 천장**(3섹션·엔딩 3·3성 — 초과 금지).
- **R3 가산(③ 심화·셋째 슬라이스·AC11~12+LF)**: **다중트랙 테마형 편곡**(`__sectionTheme(i)` 섹션 distinct → `__plannedVoices` per-note freq=테마 transpose 등치[PITCH]·음색=섹션 톤[TIMBRE]·`__activeTracks` voiceset===`__activeLayers`·테마 미소비/identity=RED·성부 카운터 불변·**신규 성부 0**) · **풀 ③ 연출**(섹션 장면 `#scene` DOM 렌더 소비·vestigial=RED·memoir AC8 동형) · **live-flow**(라이브 전이서 `#scene` 렌더 소비·미래 노트 테마 정합). **AC 가 천장**(모티프 수=섹션 수=3·신규 성부 0·절차적 테마 — 작곡 스코어·실악기·라이선스 OST 는 §8 이월).
- 입력: 키(D/F/J/K) + 탭/포인터 동일 judge 경로(모바일 1급·CSS 스케일 보정). 자기완결 `file://` · 다크 · monospace · 한국어.

### 8.2 제작 로드맵 (슬라이스 초과 = 여기로 이월)
1. **저작 OST + 정식 차트 에디터**(절차적 합성 → 작곡가 협업·다곡).
2. **③ 완전 통합**: 연주 정확도/콤보가 서사 분기·대사·연출·카메라를 실시간 직조(슬라이스의 다층 성부·음색 state 구동 → 연출/카메라/대사까지 완전).
3. **다중 챕터 + 챕터 셀렉트 + 다엔딩 트리**(슬라이스 3섹션·finale ≥3·섹션 셀렉트 → 누적 상태 기반 멀티 엔딩 트리·다곡 확장).
4. 난이도/접근성(판정 윈도우·어시스트·오토)·로컬라이즈(한국어 외)·콘솔 이식.
5. 위시리스트/데모 기반 floor 선검증(매출 계획을 기본 시나리오에 정렬).

---

## 9. 부록 — 슬라이스 검증 매핑

| AC | 게이트(결정론·하드) | 보조 스모크(비게이트) |
|----|------|------|
| C1 | 콘솔/pageerror 0 | — |
| C2 | 실 keydown→judge 경로(taps↑)·실 포인터 CSS 스케일 hit-test(레인 매핑) | 실시간 keydown **판정 라벨**(wall-clock 의존) |
| AC1 | `__noteScreenY(n,t)` 비트시각 판정선 도달 + **순수성**(동일인자→동일출력) | 노트 낙하 스샷 |
| AC2 | judge 경계 ±45/±90 전이 + on-beat→Perfect+score↑/off-beat→Miss | — |
| AC3 | song 정의 + scenesShown≥1 | 장면 스샷 |
| AC4 | **창발**(notesJudged==noteCount·\|acc−a\|≤tol·카운트 정합) + ending A/B + **falsifiable 인과쌍** | 두 엔딩 스샷 |
| AC5 | phase result→`__retry`→play(재무장) | 결과 스샷 |
| AC6 | audioParams/visualParams = state 순수 함수(연주+서사 의존) | 톤 변화 스샷 |
| AC7 (i)순수 | `__activeLayers(state)` 콤보→레이어 집합↑ + `__audioParams` scene→음색(waveform) | — |
| AC7 (ii)결합 | `__plannedVoices`===`__activeLayers`·콤보따라 성부↑·`scheduleMelody` 우회=RED·`_audio.js` 성부 호출카운터 정합 | 가청 레이어 변화 [SMOKE] |
| AC8 | 섹션 전이 1회(섹션 전 노트 판정)·`perSection[i]` 창발 재계산 일치·전곡 autoPlay→finishSong 비파괴 | 섹션 장면 스샷 |
| AC9 | `__autoPlayPerSection` ≥3 distinct finale·단일플립 falsifiable·perSection 창발 3종·`finale===resolveFinale`(직접세팅 불가) | 피날레 스샷 |
| AC10 | `__selectSection(i)`→section=i·armed·phase play | — |
| R-AC11 (i)순수 | `__sectionTheme(i)` 섹션 distinct·인덱스 파생 + `__activeTracks` voiceset===`__activeLayers`·motifId | — |
| R-AC11 (ii)결합 | `__plannedVoices` melody freq===`LANE_FREQ[lane]`에 `__sectionTheme` transpose 적용 독립값[PITCH]·same-lane cross-section freq 상이·TIMBRE 등치·**테마 미소비 raw/identity=RED**·성부 카운터 정확값 불변 | 가청 테마 변화 [SMOKE] |
| R-AC12 | 섹션 전이→`#scene` DOM textContent 렌더 소비(계산만=vestigial RED)·per-note 섹션 톤→음색·`__audioParams` 훅 비회귀 | 오버레이/비트 펄스 [SMOKE] |
| LF-1/2 | 실 start→실클럭→라이브 섹션 전이서 `#scene` 렌더 소비(LF-1)·미래 노트 테마 transpose 등치+음색 정합(LF-2·테마 live hollow 0) | 연출 오버레이·테마 전환 가청 [SMOKE] |
| DRIFT+ | sectionThreshold·콤보경계 ≥8/≥16·finale 티어·**R3 모티프 수=섹션 수=3** brief 파생·게임↔brief 일치·신규라인(sections/finale/layers/sectionSelect/tracks/staging) 부재 시 FAIL | — |
| GOLDEN | `__songNotes` times/lanes === 시드 0x1357 산출(section rng 비소비) | — |

> 검증 실행: `PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers DISPLAY=:99 node web/scripts/play_rhythm_check.mjs` (브리프-구동·anti-hollow·exit 0=PASS·R2 AC7~10 가산). 부정 변이: `RHYTHM_NEGATIVE=1`(계약 파생 실패=FAIL 기대).
