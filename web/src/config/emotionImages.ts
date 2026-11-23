/**
 * 감정 라벨 → 이미지 경로 매핑 (A안 일부 — Phase 4a 이미지 토글).
 *
 * 자산 현실: work/1.png · work/2.png 2장
 *   - neutral / thinking → /work/1.png
 *   - happy / surprised / troubled → /work/2.png
 * 부족 라벨은 neutral 폴백.
 *
 * 백엔드 정적 서빙 경로: /work/ (US-002)
 * Phase 4a에서 실제 캐릭터 세트 생성 시 경로만 교체하면 됨.
 */

/** 허용된 감정 라벨 집합 */
export type EmotionLabel =
  | "neutral"
  | "happy"
  | "surprised"
  | "thinking"
  | "troubled"
  | "focused"; // ★P4 6번째 표정(집중/결의). classifyEmotion은 미출력 — 표시 전용(카드 thinking→focused alias).

/** 모든 EmotionLabel 값의 배열 (타입 가드용) */
export const EMOTION_LABELS: EmotionLabel[] = [
  "neutral",
  "happy",
  "surprised",
  "thinking",
  "troubled",
  "focused",
];

/**
 * 감정 라벨 → 이미지 경로 매핑 테이블.
 * 자산 2장을 라벨 5개에 배분:
 *   /work/1.png → 차분한 상태 (neutral, thinking)
 *   /work/2.png → 활성 상태  (happy, surprised, troubled)
 */
export const EMOTION_IMAGE_MAP: Record<EmotionLabel, string> = {
  neutral: "/work/1.png",
  thinking: "/work/1.png",
  happy: "/work/2.png",
  surprised: "/work/2.png",
  troubled: "/work/2.png",
  focused: "/work/1.png", // 집중=차분 그룹(thinking과 동조).
};

/**
 * 감정 라벨로 이미지 경로를 반환한다.
 * 알 수 없는 라벨이 들어오면 neutral 폴백.
 */
export function getEmotionImagePath(label: EmotionLabel | string): string {
  if (label in EMOTION_IMAGE_MAP) {
    return EMOTION_IMAGE_MAP[label as EmotionLabel];
  }
  // 부족 라벨 → neutral 폴백
  return EMOTION_IMAGE_MAP.neutral;
}

// ── 역할별 클로즈업 표정 포트레이트 (대화 시 발화자 얼굴) ──────────────────────
//
// 텍스트뷰 자산: /assets/portraits/{role}_{emotion}.png (레거시 80장·역할별 neutral~troubled).
// office-v2 카드/다이얼로그는 별도 nexusPortraitPath(/assets/nexus/portraits/·Stitch 픽셀·6표정
// neutral~troubled+focused) 사용 — 두 경로 분리(레거시 경로 규약 불변).
// 자산 미존재 대비 런타임 <img onError> 폴백 체인 필수: role_emotion → role_neutral → PORTRAIT_FALLBACK.

/** 포트레이트 미생성 시 최종 폴백 경로(예시 자산). */
export const PORTRAIT_FALLBACK = "/work/1.png";

/**
 * 역할 + 감정으로 클로즈업 포트레이트 경로를 반환한다.
 *
 * 경로 규약: `/assets/portraits/${role}_${emotion}.png`.
 * emotion이 허용 라벨(EMOTION_LABELS)이 아니면 neutral로 보정한다.
 * (자산 미존재 시 깨짐 방지는 호출부의 <img onError> 폴백 체인이 담당.)
 *
 * @param role  역할 식별자(pickRole 결과 등). 검증 없이 경로에 그대로 사용.
 * @param emotion EmotionLabel 또는 임의 문자열(미허용 시 neutral).
 */
export function getRolePortraitPath(
  role: string,
  emotion: EmotionLabel | string,
): string {
  const safeEmotion = (EMOTION_LABELS as string[]).includes(emotion)
    ? emotion
    : "neutral";
  return `/assets/portraits/${role}_${safeEmotion}.png`;
}

// ── ★Nexus 픽셀 초상 (office-v2 카드/다이얼로그 전용 — US-2c 생성 96장) ───────────
//
// getRolePortraitPath(/assets/portraits/·텍스트뷰·80장)와 분리한다(그 경로는 불변 규약).
// nexus는 16역할×6표정(neutral/happy/thinking/surprised/troubled/focused) Stitch 픽셀아트.

/**
 * 역할 + 감정으로 ★Nexus 픽셀 초상 경로를 반환한다(office-v2 카드/다이얼로그).
 *
 * 경로 규약: `/assets/nexus/portraits/${role}_${emotion}.png`.
 * emotion이 허용 라벨(EMOTION_LABELS)이 아니면 neutral로 보정한다.
 * (자산 미존재 시 깨짐 방지는 호출부의 <img onError> 폴백 체인이 담당.)
 *
 * @param role  역할 식별자(16 역할 키).
 * @param emotion EmotionLabel 또는 임의 문자열(미허용 시 neutral).
 */
export function nexusPortraitPath(
  role: string,
  emotion: EmotionLabel | string,
): string {
  const safeEmotion = (EMOTION_LABELS as string[]).includes(emotion)
    ? emotion
    : "neutral";
  return `/assets/nexus/portraits/${role}_${safeEmotion}.png`;
}
