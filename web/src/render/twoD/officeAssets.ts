/**
 * officeAssets — qwen 단일 스타일 오피스 에셋 번들 주입 시임 (모듈 스코프).
 *
 * ★기존 tiledMapLoader의 OfficeAssets(LPC 레이어 + 타일시트 sub) 모델을 폐기하고,
 * qwen 캐릭터 세트(투명 PNG 1장씩·동일 픽셀 스타일)를 그대로 쓰는 번들로 교체한다.
 *   - floor : office/floor.png(256x256 나무바닥 타일) 베이스.
 *   - desk  : characters/roles/desk.png(투명 책상) 베이스.
 *   - plant : office/plant.png(투명 화분) 베이스.
 *   - rug   : office/rug.png(투명 러그) 베이스.
 *   - roles : 8역할 캐릭터(characters/roles/{role}.png) Texture 맵.
 *
 * TwoDStage가 Assets.load로 비동기 로드 후 setOfficeAssets로 주입한다(주입 시임).
 * applyScene은 getOfficeAssets()로 받아 로스터·배경·데코를 만든다 — 미주입 시(테스트·
 * 로드 실패) 해당 reconcile은 조용히 생략(폴백·환경 무영향).
 *
 * pixi 타입을 직접 의존하지 않기 위해 텍스처는 unknown으로 둔다(테스트 mock 호환).
 */

import type { Role } from "./roleCharacter";

/**
 * 오피스 에셋 번들 — qwen 스타일 텍스처 묶음. pixi Texture류이나 mock 호환 위해 unknown.
 *   floor : 나무바닥 타일 베이스(reconcileBackground가 풀커버 타일링).
 *   desk  : 책상 스프라이트 베이스(각 로스터 멤버 발밑에 1개).
 *   plant : 화분 스프라이트 베이스(4코너 데코).
 *   rug   : 러그 스프라이트 베이스(중앙 미팅존 데코).
 *   roles : 역할별 캐릭터 텍스처(8개 — 로스터 멤버 char 스프라이트).
 */
export interface OfficeAssets {
  floor: unknown;
  desk: unknown;
  plant: unknown;
  rug: unknown;
  roles: Record<Role, unknown>;
}

/** 주입된 오피스 에셋(없으면 null — 폴백). 모듈 스코프. */
let _officeAssets: OfficeAssets | null = null;

/** 오피스 에셋 번들을 주입한다(TwoDStage가 모든 텍스처 로드 후 호출). null로 해제 가능. */
export function setOfficeAssets(a: OfficeAssets | null): void {
  _officeAssets = a;
}

/** 주입된 오피스 에셋을 반환한다(없으면 null). reconcile이 생성 전에 조회. */
export function getOfficeAssets(): OfficeAssets | null {
  return _officeAssets;
}

/** 테스트 격리용 — 주입된 오피스 에셋을 초기화한다. */
export function __resetOfficeAssets(): void {
  _officeAssets = null;
}
