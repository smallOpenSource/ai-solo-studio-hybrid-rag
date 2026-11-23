/**
 * 감정 분류 순수 함수 (프론트 규칙 기반 — LLM 호출 0).
 *
 * 동시성1 자동 보호: LLM을 전혀 안 쓰므로 백엔드 동시성 제약에 영향 없음.
 * 백엔드 이벤트 무변형: content만 읽고, 이벤트 객체를 절대 수정하지 않음.
 *
 * 분류 규칙 (우선순위 순):
 *   surprised : "놀라|surprised|!?" 포함
 *   happy     : "기쁘|좋아|happy|성공|완료|감사" 포함
 *   troubled  : "곤란|문제|오류|에러|실패|troubled|힘들|어렵" 포함
 *   thinking  : "분석|생각|검토|조회|확인|처리|탐색|thinking" 포함
 *   neutral   : 위 어느 것도 해당 없음 (기본값)
 */

import type { EmotionLabel } from "../config/emotionImages";

/**
 * 각 감정 라벨에 대응하는 키워드 패턴 목록.
 * 배열 순서가 우선순위: 앞쪽 라벨이 먼저 매칭되면 반환.
 */
const EMOTION_RULES: Array<{ label: EmotionLabel; pattern: RegExp }> = [
  // surprised — 놀람·감탄 표현
  {
    label: "surprised",
    pattern: /놀라|surprised|![\?!]|!\?|\?\!|와+[!!]*|헐|대박/i,
  },
  // happy — 긍정·성공·감사 표현
  {
    label: "happy",
    pattern: /기쁘|기쁜|좋아|happy|성공|완료|감사|반갑|환영|축하|최고|훌륭/i,
  },
  // troubled — 부정·오류·곤란 표현
  {
    label: "troubled",
    pattern: /곤란|문제|오류|에러|실패|troubled|힘들|어렵|못|불가|거부|차단|경고|위험/i,
  },
  // thinking — 분석·사고·처리 중 표현
  {
    label: "thinking",
    pattern: /분석|생각|검토|조회|확인|처리|탐색|thinking|고민|판단|평가|계산|추론/i,
  },
];

/**
 * 텍스트의 감정 라벨을 규칙 기반으로 분류한다.
 *
 * 순수 함수 — 부수효과 없음, 네트워크/LLM 호출 0.
 * @param text - 분류할 텍스트 (TextMessage.content 등)
 * @returns EmotionLabel (매칭 없으면 "neutral")
 */
export function classifyEmotion(text: string): EmotionLabel {
  // 빈 문자열·null·undefined 안전 처리
  if (!text || typeof text !== "string") {
    return "neutral";
  }

  // 우선순위 순으로 첫 번째 매칭 규칙의 라벨 반환
  for (const { label, pattern } of EMOTION_RULES) {
    if (pattern.test(text)) {
      return label;
    }
  }

  // 어느 규칙도 매칭되지 않으면 neutral
  return "neutral";
}
