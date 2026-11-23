/**
 * roleNames — 16 역할 키의 한국 개인 이름 + fullLabel 헬퍼 (US-01).
 *
 * KOREAN_NAME: 각 역할에 대응하는 한국 개인 이름(성+이름 3글자).
 * fullLabel:   역할 → { name: 개인 이름, title: 직책 한국어 } 반환(2줄 라벨용).
 *
 * 이름 교체 시 이 파일의 KOREAN_NAME 표만 변경하면 된다(SSOT).
 */

import { ROLE_KEY_NAMES, ROLE_KEYS } from "./sceneLogic";
import type { RoleKey } from "./sceneLogic";

/** 역할 키 → 한국 개인 이름 (성+이름 3글자·§3 로스터). */
export const KOREAN_NAME: Record<RoleKey, string> = {
  ceo:        "김도윤",
  strategist: "이서연",
  cfo:        "박지호",
  legal:      "최예린",
  pm:         "정민준",
  planner:    "강하은",
  data:       "윤시우",
  accountant: "임수아",
  dev:        "한지훈",
  devops:     "오태경",
  designer:   "서유진",
  marketer:   "김지원",
  secretary:  "이수빈",
  sales:      "장현우",
  cs:         "문가영",
  hr:         "신동현",
};

/**
 * 역할 키를 2줄 라벨 데이터로 변환한다(순수·캔버스 무의존).
 *
 * @param role 16 역할 키.
 * @returns { name: 개인 한국 이름, title: 직책 한국어 }
 */
export function fullLabel(role: RoleKey): { name: string; title: string } {
  return { name: KOREAN_NAME[role], title: ROLE_KEY_NAMES[role] };
}

// 타입 재노출 — import 측에서 RoleKey·ROLE_KEYS를 roleNames에서도 쓸 수 있도록.
export type { RoleKey };
export { ROLE_KEYS };
