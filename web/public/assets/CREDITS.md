# 에셋 출처·라이선스 (web/public/assets)

이 프로젝트의 2D 오피스 비주얼은 아래 오픈 라이선스 에셋을 사용합니다.
재배포/파생 시 각 라이선스(특히 CC-BY-SA의 저작자 표기 + 동일조건변경허락)를 준수하세요.

## 캐릭터 — LPC (Liberated Pixel Cup) Universal Spritesheet
- **경로**: `characters/body_light.png`, `hair_brown.png`, `hair_black.png`,
  `{white,teal,red,magenta}_pants_male.png`, `{white,teal,maroon,brown}_longsleeve.png`
- **출처**: https://github.com/jrconway3/Universal-LPC-spritesheet (LPC 프로젝트)
- **라이선스**: **CC-BY-SA 3.0** 및/또는 **GPL 3.0** (LPC 듀얼 라이선스).
- **저작자**: LPC 기여 아티스트 다수 — 대표: Stephen "Redshrike" Challener, Johannes Sjölund,
  Marcel van de Steeg, Manuel Riecke (MrBeast), 그 외 LPC 컨트리뷰터. 전체 명단은 위 레포의
  `AUTHORS`/각 레이어 라이선스 파일 참조.
- **용도**: 직원 아바타(원 → 옷 입은 humanoid 픽셀 캐릭터·4방향 walk 시트).

## 타일셋 — Tuxemon 프로젝트 (OpenGameArt 기반)
- **경로**: `tilesets/floors.png`, `tilesets/walls.png`, `tilesets/office_interiors.png`,
  (참고용 `interiors.png`)
- **출처**: https://github.com/Tuxemon/Tuxemon (mods/tuxemon/gfx/tilesets)
- **라이선스**: **CC-BY-SA 3.0/4.0**.
- **저작자**: George (Interior_Floors/Walls), ArMM1998, Mike Bramson,
  Stephen "Redshrike" Challener 등(파일명에 명시). OpenGameArt 원본 기여자.
- **용도**: 오피스 바닥·벽·가구(책상/의자/모니터/화분 등).

## (레거시·Phase 1) 자체 생성 타일
- `tiles/office.png` · `tiles/office.json` — `web/scripts/gen_office_tileset.py`로 생성한
  자체 저작 **CC0** 절차 타일(현재 Phase 2에서 실 타일셋으로 대체·미사용·보존만).

## 라이선스 체크리스트 (신규 에셋 추가 시)
- [ ] 출처 URL·라이선스·저작자 명기(이 파일)
- [ ] CC0 또는 CC-BY-SA/GPL 등 호환 라이선스(상충 라이선스 배제)
- [ ] CC-BY-SA는 저작자 표기 + 파생물 동일조건 유지
- [ ] web/public/assets 총량 관리(현 ~524K)
