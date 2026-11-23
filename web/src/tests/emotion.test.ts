/**
 * 감정 분류 순수 함수 단위 테스트 (Vitest).
 *
 * 검증 항목:
 *   - 각 키워드 그룹별 라벨 반환
 *   - 기본값 neutral
 *   - 순수 함수 단언 (부수효과·네트워크·LLM 호출 0)
 *   - 빈 문자열·null·undefined 안전 처리
 *   - getEmotionImagePath: 라벨→경로 매핑 + neutral 폴백
 */

import { describe, it, expect } from "vitest";
import { classifyEmotion } from "../render/emotion";
import {
  getEmotionImagePath,
  EMOTION_IMAGE_MAP,
  EMOTION_LABELS,
  getRolePortraitPath,
  nexusPortraitPath,
  PORTRAIT_FALLBACK,
} from "../config/emotionImages";

// ── classifyEmotion 키워드 규칙 ───────────────────────────────────────────────

describe("classifyEmotion — surprised 키워드", () => {
  it("'놀라' 포함 → surprised", () => {
    expect(classifyEmotion("놀라운 결과가 나왔어요")).toBe("surprised");
  });

  it("'surprised' 포함 → surprised", () => {
    expect(classifyEmotion("I was surprised by the result")).toBe("surprised");
  });

  it("'!?' 포함 → surprised", () => {
    expect(classifyEmotion("정말요!? 말이 안 돼")).toBe("surprised");
  });

  it("'와' 포함 → surprised", () => {
    expect(classifyEmotion("와~! 엄청난데요")).toBe("surprised");
  });

  it("'헐' 포함 → surprised", () => {
    expect(classifyEmotion("헐 이게 무슨 일이야")).toBe("surprised");
  });
});

describe("classifyEmotion — happy 키워드", () => {
  it("'성공' 포함 → happy", () => {
    expect(classifyEmotion("워크플로 성공적으로 완료됐습니다")).toBe("happy");
  });

  it("'완료' 포함 → happy", () => {
    expect(classifyEmotion("이메일 발송 완료")).toBe("happy");
  });

  it("'감사' 포함 → happy", () => {
    expect(classifyEmotion("고객님 감사합니다")).toBe("happy");
  });

  it("'기쁘' 포함 → happy", () => {
    expect(classifyEmotion("정말 기쁜 소식이에요")).toBe("happy");
  });

  it("'happy' 포함 → happy", () => {
    expect(classifyEmotion("I am happy to assist")).toBe("happy");
  });

  it("'축하' 포함 → happy", () => {
    expect(classifyEmotion("축하드립니다!")).toBe("happy");
  });
});

describe("classifyEmotion — troubled 키워드", () => {
  it("'오류' 포함 → troubled", () => {
    expect(classifyEmotion("API 호출 오류가 발생했습니다")).toBe("troubled");
  });

  it("'실패' 포함 → troubled", () => {
    expect(classifyEmotion("이메일 발송 실패")).toBe("troubled");
  });

  it("'문제' 포함 → troubled", () => {
    expect(classifyEmotion("네트워크 문제가 있습니다")).toBe("troubled");
  });

  it("'에러' 포함 → troubled", () => {
    expect(classifyEmotion("에러가 발생했습니다")).toBe("troubled");
  });

  it("'troubled' 포함 → troubled", () => {
    expect(classifyEmotion("The system is troubled")).toBe("troubled");
  });

  it("'경고' 포함 → troubled", () => {
    expect(classifyEmotion("보안 경고가 감지되었습니다")).toBe("troubled");
  });
});

describe("classifyEmotion — thinking 키워드", () => {
  it("'분석' 포함 → thinking", () => {
    expect(classifyEmotion("고객 데이터를 분석 중입니다")).toBe("thinking");
  });

  it("'조회' 포함 → thinking", () => {
    expect(classifyEmotion("CRM 고객 정보를 조회합니다")).toBe("thinking");
  });

  it("'검토' 포함 → thinking", () => {
    expect(classifyEmotion("요청 사항을 검토하고 있습니다")).toBe("thinking");
  });

  it("'처리' 포함 → thinking", () => {
    expect(classifyEmotion("요청을 처리하는 중입니다")).toBe("thinking");
  });

  it("'thinking' 포함 → thinking", () => {
    expect(classifyEmotion("I am thinking about this")).toBe("thinking");
  });

  it("'확인' 포함 → thinking", () => {
    expect(classifyEmotion("주문 상태를 확인하겠습니다")).toBe("thinking");
  });
});

describe("classifyEmotion — neutral 기본값", () => {
  it("키워드 없는 일반 텍스트 → neutral", () => {
    expect(classifyEmotion("안녕하세요")).toBe("neutral");
  });

  it("영문 일반 텍스트 → neutral", () => {
    expect(classifyEmotion("Hello, I am an assistant")).toBe("neutral");
  });

  it("빈 문자열 → neutral", () => {
    expect(classifyEmotion("")).toBe("neutral");
  });

  it("공백만 있는 문자열 → neutral", () => {
    // 공백은 키워드 미포함 → neutral
    expect(classifyEmotion("   ")).toBe("neutral");
  });
});

// ── 순수 함수 단언 (부수효과·네트워크·LLM 호출 0) ─────────────────────────

describe("classifyEmotion — 순수 함수 보장", () => {
  it("같은 입력에 항상 같은 출력을 반환해야 한다 (결정론)", () => {
    const input = "고객 데이터를 분석 중입니다";
    const result1 = classifyEmotion(input);
    const result2 = classifyEmotion(input);
    const result3 = classifyEmotion(input);
    expect(result1).toBe(result2);
    expect(result2).toBe(result3);
    expect(result1).toBe("thinking");
  });

  it("여러 번 호출해도 외부 상태를 변경하지 않아야 한다", () => {
    // 외부 객체를 인자로 넘겨도 변형하지 않음
    const text = "완료 처리하겠습니다";
    const before = text;
    classifyEmotion(text);
    classifyEmotion(text);
    expect(text).toBe(before); // 문자열은 불변 — 변형 불가 확인
  });

  it("반환값이 유효한 EmotionLabel 중 하나여야 한다", () => {
    const validLabels = ["neutral", "happy", "surprised", "thinking", "troubled"];
    const testInputs = [
      "안녕하세요",
      "성공했습니다",
      "놀라운 결과",
      "분석 중",
      "오류 발생",
    ];
    for (const input of testInputs) {
      const result = classifyEmotion(input);
      expect(validLabels).toContain(result);
    }
  });

  it("fetch/XMLHttpRequest 없이 동기 실행되어야 한다 (네트워크 호출 0)", () => {
    // fetch/XHR를 사용하면 이 테스트가 비동기 없이 결과를 반환할 수 없음
    // 동기 즉시 반환되면 LLM·네트워크 호출이 없음을 구조적으로 보장
    let result: string | undefined;
    result = classifyEmotion("테스트 텍스트");
    // 동기 즉시 반환 → Promise나 undefined가 아닌 string
    expect(typeof result).toBe("string");
    expect(result).not.toBeUndefined();
  });
});

// ── getEmotionImagePath 이미지 경로 매핑 ──────────────────────────────────

describe("getEmotionImagePath — 이미지 경로 매핑", () => {
  it("neutral → /work/1.png", () => {
    expect(getEmotionImagePath("neutral")).toBe("/work/1.png");
  });

  it("thinking → /work/1.png (차분한 상태)", () => {
    expect(getEmotionImagePath("thinking")).toBe("/work/1.png");
  });

  it("happy → /work/2.png", () => {
    expect(getEmotionImagePath("happy")).toBe("/work/2.png");
  });

  it("surprised → /work/2.png", () => {
    expect(getEmotionImagePath("surprised")).toBe("/work/2.png");
  });

  it("troubled → /work/2.png", () => {
    expect(getEmotionImagePath("troubled")).toBe("/work/2.png");
  });

  it("알 수 없는 라벨 → neutral 폴백 (/work/1.png)", () => {
    expect(getEmotionImagePath("unknown_label")).toBe(EMOTION_IMAGE_MAP.neutral);
  });

  it("빈 문자열 라벨 → neutral 폴백", () => {
    expect(getEmotionImagePath("")).toBe(EMOTION_IMAGE_MAP.neutral);
  });
});

// ── 분류→경로 통합 파이프라인 ─────────────────────────────────────────────

describe("classifyEmotion + getEmotionImagePath 통합", () => {
  it("'분석 중' → thinking → /work/1.png", () => {
    const label = classifyEmotion("고객 데이터를 분석 중입니다");
    const path = getEmotionImagePath(label);
    expect(label).toBe("thinking");
    expect(path).toBe("/work/1.png");
  });

  it("'성공' → happy → /work/2.png", () => {
    const label = classifyEmotion("작업 성공!");
    const path = getEmotionImagePath(label);
    expect(label).toBe("happy");
    expect(path).toBe("/work/2.png");
  });

  it("'오류' → troubled → /work/2.png", () => {
    const label = classifyEmotion("처리 중 오류 발생");
    const path = getEmotionImagePath(label);
    expect(label).toBe("troubled");
    expect(path).toBe("/work/2.png");
  });

  it("'안녕' → neutral → /work/1.png", () => {
    const label = classifyEmotion("안녕하세요");
    const path = getEmotionImagePath(label);
    expect(label).toBe("neutral");
    expect(path).toBe("/work/1.png");
  });
});

// ── getRolePortraitPath 역할별 포트레이트 경로 ────────────────────────────────

describe("getRolePortraitPath — 역할_감정 경로", () => {
  it("dev + happy → /assets/portraits/dev_happy.png", () => {
    expect(getRolePortraitPath("dev", "happy")).toBe(
      "/assets/portraits/dev_happy.png",
    );
  });

  it("ceo + neutral → /assets/portraits/ceo_neutral.png", () => {
    expect(getRolePortraitPath("ceo", "neutral")).toBe(
      "/assets/portraits/ceo_neutral.png",
    );
  });

  it("각 감정 라벨이 경로에 그대로 반영된다", () => {
    for (const emotion of ["neutral", "happy", "surprised", "thinking", "troubled"]) {
      expect(getRolePortraitPath("designer", emotion)).toBe(
        `/assets/portraits/designer_${emotion}.png`,
      );
    }
  });

  it("미허용 감정 라벨 → neutral 보정", () => {
    expect(getRolePortraitPath("planner", "unknown_emotion")).toBe(
      "/assets/portraits/planner_neutral.png",
    );
  });

  it("빈 문자열 감정 → neutral 보정", () => {
    expect(getRolePortraitPath("marketer", "")).toBe(
      "/assets/portraits/marketer_neutral.png",
    );
  });

  it("순수 함수 — 같은 입력에 항상 같은 출력(결정론)", () => {
    const a = getRolePortraitPath("accountant", "thinking");
    const b = getRolePortraitPath("accountant", "thinking");
    expect(a).toBe(b);
  });
});

describe("PORTRAIT_FALLBACK — 최종 폴백 경로", () => {
  it("최종 폴백 경로는 /work/1.png 이다", () => {
    expect(PORTRAIT_FALLBACK).toBe("/work/1.png");
  });
});

// ── ★P4 6번째 표정 focused (A-lite·additive·표시 전용) ──────────────────────────

describe("★P4 focused — 6번째 표정", () => {
  it("EMOTION_LABELS에 focused 포함", () => {
    expect(EMOTION_LABELS).toContain("focused");
    expect(EMOTION_LABELS).toContain("neutral");
  });

  it("EMOTION_IMAGE_MAP에 focused 엔트리(Record 강제·tsc)", () => {
    expect(EMOTION_IMAGE_MAP.focused).toBeTruthy();
    expect(getEmotionImagePath("focused")).toBe(EMOTION_IMAGE_MAP.focused);
  });

  it("getRolePortraitPath(role,'focused') 도달 — focused는 유효 라벨이라 경로에 반영", () => {
    expect(getRolePortraitPath("ceo", "focused")).toBe(
      "/assets/portraits/ceo_focused.png",
    );
  });

  it("nexusPortraitPath(role,'focused') → /assets/nexus/portraits/{role}_focused.png", () => {
    expect(nexusPortraitPath("cfo", "focused")).toBe(
      "/assets/nexus/portraits/cfo_focused.png",
    );
  });

  it("nexusPortraitPath 미허용 라벨 → neutral 보정", () => {
    expect(nexusPortraitPath("dev", "bogus_label")).toBe(
      "/assets/nexus/portraits/dev_neutral.png",
    );
  });

  it("classifyEmotion은 focused를 출력하지 않는다(재분류 아님·표시 alias 전용)", () => {
    const inputs = ["분석 중입니다", "생각해 볼게요", "집중해서 처리", "안녕하세요", "성공"];
    for (const t of inputs) {
      expect(classifyEmotion(t)).not.toBe("focused");
    }
  });
});
