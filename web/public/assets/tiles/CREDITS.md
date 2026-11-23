# 에셋 출처·라이선스 (web/public/assets)

## tiles/office.png · tiles/office.json
- **저작자**: one_man_corp 프로젝트 (자체 저작·`web/scripts/gen_office_tileset.py`로 결정론 생성).
- **라이선스**: **CC0 1.0 Universal** (Public Domain Dedication). 자유로운 사용·수정·배포·상업 이용 가능, 출처 표기 불요.
- **용도**: Phase 1 픽셀 오피스 타일맵 배경(32px 타일·8종: 바닥/카펫/벽top/벽front/책상/화분/러그/화이트보드).
- **교체**: 로더(`web/src/render/twoD/tiledMapLoader.ts`)는 타일셋 무관 → 추후 더 정교한 CC0 팩
  (Kenney furniture-kit CC0 · limezu modernoffice/interiors)으로 PNG+JSON만 바꿔 무코드 교체 가능.

## 라이선스 확인 체크리스트 (PR·신규 에셋 추가 시)
- [ ] 출처 URL·라이선스 명기(이 파일)
- [ ] CC0 또는 호환 라이선스(GPL-only 레이어 배제)
- [ ] web/public/assets 총량 ≤ 1.5MB
