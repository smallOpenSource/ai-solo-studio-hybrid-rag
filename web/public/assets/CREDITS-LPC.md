# 에셋 출처·라이선스 — office-v2 (LPC 도트 + 절차적 타일)

`CREDITS.md`(베이스 레이어 원출처)의 **보충**. office-v2 트랙에서 신규 산출한 파생 자산의 라이선스 계승·귀속을 명시한다. ★**온프레미스 비배포 전제**: CC-BY-SA share-alike(동일조건변경허락) 의무는 *외부 배포 시* 활성화 — 내부 PoC 사용에는 충족. **외부 공유 전 반드시** 아래 per-아티스트 표기 + 파생물 소스 공개를 완료할 것.

## 1. 캐릭터 스프라이트 — `characters/roles_v2/{16역할}.png` (★파생물)
- **역할(16)**: ceo·dev·designer·accountant·marketer·planner·secretary·strategist · sales·cs·cfo·legal·hr·data·pm·devops.
- **파생 원본**: `characters/{body_light, *_longsleeve, *_pants_male, hair_*}.png` = **LPC (Liberated Pixel Cup) Universal Spritesheet** (출처·아티스트·라이선스는 `CREDITS.md` "캐릭터 — LPC" 절 참조: CC-BY-SA 3.0 및/또는 GPL 3.0 · Stephen "Redshrike" Challener, Johannes Sjölund, Marcel van de Steeg, Manuel Riecke 외 LPC 컨트리뷰터).
- **변형 방법**: `tools/lpc_pipeline/`(compose·recolor·kmeans·build_us3) — LPC 중립 레이어(white_longsleeve/white_pants_male/hair_brown)를 휘도램프 3톤 리컬러 + per-스프라이트 ≤15색 양자화 + 절차적 소품(안경/헤드셋/넥타이) + idle 프레임 추출. 의상색은 절제된 16색 팔레트(채도 상한·JRPG 무드)로 식별 분산(ΔE2000).
- **라이선스(계승)**: LPC가 **CC-BY-SA 3.0 / GPL 3.0** 듀얼이므로 본 파생물도 **동일 라이선스 계승**(share-alike). 귀속 = 위 LPC 아티스트.
- **용도**: 오피스 맵 직원 아바타(¾탑다운 idle 도트).

## 1b. 캐릭터 walk 스프라이트시트 — `characters/roles_walk/{16역할}.png` (★파생물·2026-06-15)
- **생성**: `tools/lpc_pipeline/build_walk_sheets.py` — LPC 중립 레이어(`body_light`·`*_longsleeve`·`*_pants_male`·`hair_*`)를 역할별 결정론 조합(NO PURPLE — magenta 제외)으로 알파 합성 후 **walk 행 8-11(up/left/down/right·각 9프레임·64px)** 만 crop → 역할당 832×256 시트. gather.town급 4방향 walk 애니용(Phaser `generateFrameNumbers`로 방향별 col1~8 재생). 이미지 생성 0(LPC 레이어 합성만).
- **라이선스(계승)**: LPC **CC-BY-SA 3.0 / GPL 3.0**(§1과 동일·share-alike·외부 배포 시 귀속+소스공개 의무). 귀속 = §1 LPC 아티스트(Redshrike·Sjölund·van de Steeg·Riecke 외).
- **용도**: 오피스 맵 직원 아바타의 **4방향 보행 애니**(OfficeScene Sprite·정적 qwen 맵 스프라이트 대체). 챗 클로즈업 표정은 qwen `portraits/` 유지(별개 트랙).

## 2. 오피스 타일셋·타일맵 — `office_v2/tileset.png`, `office_v2/office.tmj` (★자체 저작·CC0)
- **생성**: `tools/lpc_pipeline/build_us4.py` — **절차적 결정론 PIL 드로잉**(외부 에셋 미사용·시드 고정). floor/wall/desk/chair/plant/rug/divider/door 32px 타일 + 9존 Tiled 레이아웃(layers floor/walls/furniture + spawns objectgroup).
- **라이선스**: **CC0 / 자체 저작**(LPC·Tuxemon 등 외부 파생 아님). 캐릭터와 응집 위해 동일 32px·¾탑다운·제한 팔레트·selout로 자체 드로잉.
- ★주의: 기존 `CREDITS.md`의 Tuxemon 타일(`tilesets/*`)과 **무관**(office-v2는 Tuxemon 미사용·신규 절차 타일).

## 3. 대화 포트레이트 (별도 트랙·참고)
- `portraits/*`·`characters/roles/*`(3D 카툰풍 표정 일러스트)는 대화 클로즈업 트랙(qwen 생성). 본 office-v2 맵 도트와 분업. 라이선스/출처는 [[qwen-image-pipeline]] 및 해당 자산 정책 참조.

## 라이선스 체크리스트 (office-v2 신규 추가 시)
- [ ] LPC 파생물 = CC-BY-SA/GPL 계승 명기·아티스트 귀속(CREDITS.md 연속)
- [ ] 절차적 자산 = CC0/자체 저작 명기
- [ ] ★외부 배포 전: per-아티스트 표기 완료 + share-alike 소스 공개(미완 시 비배포 유지)
- [ ] 신규 외부 타일셋(Kenney CC0 등) 도입 시 출처·라이선스 추가

cross-ref: `CREDITS.md`(베이스 원출처)·`.omc/plans/ralplan-office-v2-assets.md`(자산 전략)·`tools/lpc_pipeline/`(생성 파이프라인).
