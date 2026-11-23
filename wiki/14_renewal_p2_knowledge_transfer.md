# Renewal P2 — 무손실 지식·전파 레이어 (핸드오프·provenance·하이브리드·생애주기)

## 개요

SR/renewal P2(지식/협업 토대)를 P2-core → P2.5 → P2-rest-A 순으로 구현하며 얻은 **재사용
패턴·함정**. 핵심 주제 5개: ① §27.1 핸드오프 브리프 + §18.5 DoD, ② P2.5 **파이프라인-품질
provenance**(브리프→구현 인과를 falsifiable하게 증명), ③ §24.5 **하이브리드 검색**(dense+sparse
RRF), ④ **R-INDEX**(라이브 컬렉션 스키마 마이그·실 bge 함정), ⑤ §18.1/18.2 **지식 생애주기**.

커밋: P2-core `0128aae`(+vault `57c2b04`) · P2.5 `e9fc37a`+`4aa289c`(+vault `7187ec7`,`d026a30`)
· P2-rest-A `1c65aa8` · A5+B-prep `b73dbbb`(+vault `46df38a`). 전부 동결 0수정·orch 코어 0수정.
관련: [[09_renewal_langgraph_core]] · [[12_renewal_p1_foundation]].

## 1. 핸드오프 브리프(§27.1) + DoD(§18.5) — `orch/handoff.py`

단계 간 인계를 자유형식 채팅이 아니라 **구조화 브리프**로 넘긴다(telephone game 금지).

- `HandoffBrief`(pydantic v2): §27.1 **11필드 정본**(task_id·from·to·objective·context_refs·
  acceptance_criteria·constraints·reversibility·confidence·priority·artifacts_expected).
  ★`from`은 파이썬 예약어 → `Field(alias="from")` + `populate_by_name=True`(두 이름 허용).
- `render_brief`/`parse_brief`: YAML frontmatter(§27.1) + §18.3 본문 섹션. **라운드트립 동등**
  (`parse_brief(render_brief(b)) == b`). `write_brief(brief, vault_dir)` → `vault/specs/<task_id>.md`.
- `check_dod(artifact, research_evidence, worklog, handoff)`: §18.5 4구성 중 하나라도 누락 시 reject.
  ★`is_file()`로 판정(`exists()` 아님) — 빈 문자열(`Path("")`=`.` 디렉토리)·디렉토리가 거짓 통과하지
  않게(critic이 잡은 함정).
- **extra 키 정책**: §18.3 예시 frontmatter는 type/date/sources 등 부가 키를 쓰므로 파서는
  **관용(extra=ignore)** — forbid이면 §18.3 파일을 거부. 라운드트립은 11필드로 보존.

## 2. P2.5 파이프라인-품질 provenance — "게임이 브리프로부터 나왔다"를 falsifiable하게

P2.5의 존재 이유 = 기존 coop 데모(자유형식·검수 0)와의 **결정적 차이 = 무손실 핸드오프 경로가
실제로 작동함**을 증명. 단순 "브리프 존재 + round-trip"은 **사후 날조로 우회 가능**(브리프를 게임
작성 후 형식만 맞춰 채움) → green-theater. 그래서 **provenance를 구성적으로** 강제한다.

### provenance 3 HARD 게이트 (`verify_phase2_5.sh`)
1. **시계열 `brief_sha ⊰ game_sha`**: 브리프를 게임보다 **먼저 커밋**. ★vault와 메인 repo가 **별도
   git tree**라 ancestry(`merge-base`) 불가 → **commit 타임스탬프 엄격 선행** + 게임의 `PROVENANCE`
   마커에 `brief_sha` 역참조로 cross-repo 강제.
2. **task_id 역참조**: 브리프 task_id가 게임 커밋 메시지 + worklog + `games/<n>/PROVENANCE`에 존재
   (grep). 날조하려면 git 이력·메시지·AC 파싱을 전부 위조해야 해 **감사가능한 거짓**이 됨.
3. **AC-구동 QA**(`web/scripts/play_clicker_check.mjs`): playwright가 **브리프를 파싱**(handoff
   `parse_brief` 재사용)해 acceptance_criteria/`window.__gameState` 계약을 추출 → 단언을 **파생**
   (하드코딩 금지). ★**부정 변이 테스트**: 브리프 AC를 비운 사본으로 재실행 시 **FAIL해야 정상**
   (단언이 정말 브리프에서 나옴을 증명).

### 결합 불변식 (stub theater 차단)
렌더-only나 `canvas.onclick=()=>score++` 단일 카운터도 "score 증가"로 통과한다. 이를 막으려면
**≥2 상태차원의 결합 불변식**을 요구: 업그레이드 구매 시 `score=old−cost` **AND** `clickPower↑`,
이후 클릭이 **새 clickPower**만큼 증가. 단일 카운터로는 불충족.

### writer/reviewer 분리
구현자(=이 Claude Code 세션)가 자기 산출물의 §21.4 정밀검수를 self-approve 금지. 기계적 단언
(playwright)은 객관이라 writer 컨텍스트 OK, **정성 QA 리포트는 별도 리뷰어 레인**(별도 에이전트).

## 3. 하이브리드 검색(§24.5) — `knowledge/store.py`

dense(의미)가 놓치는 **정확-토큰**을 sparse가 회수하도록 RRF 융합.

- 스키마: named dense `"dense"`(1024 COSINE) + named sparse `"text"`(`Modifier.IDF`).
- **sparse 인코더 = Qdrant-native IDF(dep-0)**: 클라이언트가 토큰 TF를 sparse 벡터로 넣고 서버
  IDF가 가중(BM25-ish). 토큰화 = 한글 음절블록·영숫자 런 regex → sha1 4바이트 u32 인덱스.
  ★BGE-M3 원격 엔드포인트는 **dense만 반환**(`dense_embeddings`·sparse 없음) → 네이티브 sparse 불가.
  대안 FastEmbed는 신규 dep → Qdrant-native가 dep-0로 채택(analyst 권장).
- `search_dense` **시그니처·반환 형태 보존**(소비자 4곳 무회귀·내부만 `using="dense"`). 신규
  `search_hybrid`: `Prefetch(dense)+Prefetch(sparse)→FusionQuery(RRF)`. ★**rerank는 store가 아니라
  호출자(eval/service)가** 기존 리랭커로 적용(관심사 분리·헤드라인 post-rerank는 eval에서 계산).
- `:memory:` Qdrant도 sparse+IDF+RRF 지원(테스트 가능).

## 4. R-INDEX — 라이브 컬렉션 스키마 마이그 (`tools/p2rest_reindex.py`)

기존 unnamed-vector 컬렉션 → named dense+sparse로 전환. ★**store.py를 named로 바꾸면 라이브
서비스가 재시작 시 unnamed 컬렉션과 불일치로 깨진다**(결합) → R-INDEX로 컬렉션도 함께 마이그 필수.

### 핵심 함정
- **코퍼스 출처 오인**: 라이브 178pt = **크롤 Wikipedia(123) + wiki/*.md(55)** — vault 아님.
  `ingest(wiki/)` 재실행으론 크롤분 누락 → 정답 = **기존 payload 읽기→실 bge 재임베드→재업서트**
  (재크롤 없이 코퍼스 충실·bge 결정론이라 dense 동등성 보존).
- ★**bge embed 느림 + 단일 대량콜 타임아웃**: bge가 **12s/콜**(부하 시 **37s+**)이라 178 텍스트를
  **한 번의 embed 콜**로 보내면 `httpx.ReadTimeout`(30s 초과). **배치(24/콜) 필수**. 인시던트:
  단일콜 타임아웃으로 컬렉션이 삭제된 채 비어버림 → **삭제 전 payload 백업**(안전망)으로 배치
  재업서트 복구(멱등 `_point_id`). downtime 허용(v5).
- **동등성**: count parity(재업서트수==count) ≠ 이웃 동등. 재인덱스 전/후 동일 쿼리 `search_dense`
  top-k **이웃 동등**(또는 Hit@k 비-감소)을 별도 단언(재인덱싱이 dense를 조용히 망치지 않음).

## 5. C2 측정 — 하이브리드 이득을 정직하게 (`knowledge/eval.py` `c2_summary`)

§24.5 이득을 측정할 때 **흔한 green-theater 함정**:

- ★**`dense_retrieval_fail` 단독비교는 정의상 불변**: 기존 분류는 `store.search_dense`만으로 계산
  →하이브리드 config를 추가해도 dense 브랜치가 그대로라 **카운트가 절대 안 줄어든다**(≥3 감소
  수학적 불가). analyst가 잡은 최대 숨은 갭. → **신규 분류**가 필요.
- **이중 측정**: (헤드라인=**delivered**) dense 단독 top_n 실패였으나 **hybrid 최종 rerank top-K**가
  회복한 keyword 앵커 수 / (진단) RRF **융합 후보셋**이 회복한 수. 헤드라인 < 진단이면 rerank가
  회수를 떨어뜨리는 신호. 융합셋만 보면 rerank 손실을 못 잡는다(Architect 교정).
- **소표본 정직**: n=13 keyword라 **통계 회수율 주장 금지·건수+id 사례만**(`_SMALL_SAMPLE_NOTE`).
  두 sparse 경로 모두 헤드라인 <3이면 **"메커니즘 구축됨·소표본 무신호"로 정직보고**(앵커를 회복
  나오게 고쳐 끼우는 조작 금지). semantic 앵커 회귀 0은 하드.

## 6. 지식 생애주기(§18.1/§18.2) — `orch/knowledge_lifecycle.py` · `orch/librarian.py`

- `ArtifactProvenance`(§18.1·§11.1 IPI 토대): `provenance`(trusted|untrusted)·`source_url`.
  ★**untrusted면 source_url 필수**(외부 적재물 출처 검증·전파는 신뢰가 아니라 검증). render/parse
  라운드트립. `capture_and_index(store, ...)`는 provenance를 `chunk.meta`에 실어 store에 증분 색인.
- ★**searchable-after-emit 왕복**: emit한 아티팩트(고유 토큰)가 emit **직후** `search_hybrid`로
  회수됨을 단언 — §18.2 Index→Retrieve를 *asserted가 아니라 demonstrated*로 닫는다.
- `librarian.dedup_merge`: exact(공백정규화 content-hash) + 주입 near-dup(cosine≥θ) → MOC 링크.
  **LLM 0 결정론**(gemma-affinity). ★false-merge 음성(구별 텍스트 분리) 보장.
- store는 **주입(duck-typed)** — orch 코어 결합 최소(KnowledgeStore 직접 import 안 함).

## 7. 운영·프로세스 교훈

- **bge 엔드포인트 느림/저하**(12–37s/콜·Owner 외부자원): 대량 임베드는 **배치**, C2 같은 ~40min
  실모델 측정은 background + 타임아웃 가드. 저하로 못 끝내면 **BLOCKED-pending-Owner(bge perf)**로
  정직 이연(코드 100%/런타임 이연·green-fake 아님). 메커니즘은 라이브 정성 실증으로 별도 입증 가능.
- **ralplan 합의**: Planner→(별도 analyst 갭분석)→Architect→Critic 반복. analyst가 C2 falsifiability·
  동시 Blackboard 쓰기 등 숨은 갭을 선포착 → 계획이 수렴 전에 강화됨.
- **검증 게이트 동봉**: 각 페이즈가 `SR/renewal/verify_phase{N}.sh`를 동봉(`verify_phase2core.sh`·
  `verify_phase2_5.sh`·`verify_phase2.sh`). "files exist + exit 0"이 아니라 실(實)게이트(런타임
  프로브·라운드트립·provenance·동결 sha256).
- **vault = 별도 git tree(§5.3)**: 메인 repo에서 gitignored·신원 `aigs`. 원격(`VAULT_REMOTE`)은
  Owner 제공 §16(미설정 시 push 불가=BLOCKED≠우리 스코프).

## 잔여 (P2-rest 다음)
형식 C2 재측정(bge 건강 시 `tools/p2rest_c2.py` 1콜) · B 민감부(Blackboard sink `app.py` `_run`
`elif kind=="org"`·OrgState dept-keyed reducer·`org_graph._persona_prompt` 플레이북 wrap·`/tasks`)
= 라이브 orch 터치라 계획 `.omc/plans/ralplan-p2-rest.md`대로 신중 별도 run.
