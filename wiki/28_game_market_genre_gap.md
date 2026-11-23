# 차기작 — Steam 장르갭 분석 + memoir-room·rhythm-tale 프로토타입

[[pixel-agent-studio-program]] 차기작 탐색(2026-06-30): Steam 장르갭 시장분석 → 빈틈 2축을 **웹 버티컬 슬라이스**
프로토타입으로 심화. 시장 데이터 원본=`docs/market_analysis.md`(커밋 `8e9c6cd`). 프로토타입=`00e5903`(R1)·`7e58878`(R2).
게임 클라이언트 엔지니어링 패턴=[27_web_games_client](27_web_games_client.md), 검증 교훈=[23_renewal_verification_methodology](23_renewal_verification_methodology.md) ⑦.

## ① 장르갭 분석 방법론 (deep-research 워크플로)
- 16~22 에이전트·870k~1.2M 토큰 deep-research 로 Steam 위시수요↑·양질출시↓ 장르 조합 발굴. 조합당 **경쟁작 15개 개별행** + 게임별 칭찬/불만 키워드 + 종합키워드 + 통합출처(`docs/market_analysis.md`·시장규모↔제작가능성 동등가중).
- ★**R1 단일문화 함정 → R2 직교축 교정**: R1(기회점수 위주)이 후보 21중 ~17 호러로 **단일문화 수렴**(호러/로그라). 사용자 지적 수용 → R2 는 **5직교축**(player·setting·mechanic·format·mashup)·호러 제외·부모장르 중복금지·수요근거 *완화*로 재실행(append·R1 보존). ★교훈: 수요근거를 완화하니 오히려 다양 픽이 **실출시·실매출 근거가 더 강함**(R1=미출시 hype 의존). **시장분석은 기회점수 단일축이 아니라 직교축 분산으로**(단일문화 자가검열).
- ⚠**위시리스트 절대수치 비공개** → SteamDB 태그 팔로워·태그별 출시수·VG Insights/Gamalytic 수익중앙값·디스커버리 분석가(GameDiscoverCo/Chris Zukowski) 글로 **proxy 추정**(한계 명시). 반복 빈틈(불만=기회): 호러 겉핥기·후반 그라인드 붕괴·QoL/저널 부재·DLC 결말 페이월.

## ② 선정 프로토타입 2종 (R2 직교 빈틈)
- **memoir-room**(`games/memoir-room/`·$14.99·PRD `docs/prd_memoir-room.md`): 공간정리 퍼즐 + 인생 내러티브(Unpacking/Hozy 형·**전투 0**). R2=**4챕터 인생호**(유년→학창→사회초년→현재)·**carry-forward**(`choices[]` idempotent 인덱스 덮어쓰기·`resolveEpilogue(choices)` 순수=초반 선택→에필로그)·아이템 배치 시 **비네트 회상**(`#vignette`). 빈틈=8~12h 볼륨·퍼즐↔서사 균형·힌트.
- **rhythm-tale**(`games/rhythm-tale/`·$19.99·PRD `docs/prd_rhythm-tale.md`): 4레인 리듬 × 내러티브 · ★**연주 정확도가 엔딩을 창발적으로 변경**(`judge()` 경유·non-hollow=Stray Gods/Afterlove 대비 유일 차별선). R2=**다층편곡 live 통합**(콤보 8/16 경계서 `escalateLiveLayers` 가 잔여 노트에 베이스/퍼커션 가산·`__plannedVoices`===`__activeLayers` 결합불변식)·3섹션 전이·다중엔딩(`resolveFinale`·noteCount 40·시드 0x1357 보존). 오디오=`games/_audio.js` 가산확장(`now()`·`scheduleTone()` 절대 ctx 시각·[[27_web_games_client]] §3).

## ③ 빌드·검증 패턴
- `/all-in-one`(ralplan 합의→구현→리뷰→재검증) 2회차(R1·R2). 자기완결 static canvas·`window.__state`+훅·file://(web `/games` 마운트 자동노출·[[27_web_games_client]] §2). 하네스 `web/scripts/play_{memoir,rhythm}_check.mjs`(parse_brief 계약파생·결합불변식·AC4 falsifiable 인과쌍·anti-hollow)·clicker/puzzle 무회귀.
- ★**검증 교훈은 [23](23_renewal_verification_methodology.md) ⑦ 으로 일반화**: hollow-vector(메커니즘만·실출력 미게이트)·headless 그린≠live(시간/재진입/실클럭 사각)→live-flow 리뷰+실클럭 스모크. ralplan Architect 의 구현 전 hollow 적발(C-3·AC7③)이 핵심.
- vault 브리프(`vault/specs/{memoir-room,rhythm-tale}.md`)=비공개 별도 트리(로컬·미push). 런처 `games/index.html` 에 2항목 추가(`/games/{memoir-room,rhythm-tale}/`).
