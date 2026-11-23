/**
 * TextStatusBoard 단위 테스트 (Vitest + @testing-library/react).
 *
 * 검증 항목 (수용 기준 11):
 *   - 각 이벤트 타입 렌더 문자열 확인
 *   - 자동 스크롤 ref 존재 (scroll sentinel)
 *   - 이미지 토글 ON → TextMessage에 이미지 슬롯 렌더
 *   - 이미지 토글 OFF → 이미지 슬롯 없음 (텍스트만)
 *   - image_url 빈 값(null) 안전 처리
 *   - image_url 채워진 값 → 그 URL 우선 사용
 *   - neutral 폴백 (키워드 없는 TextMessage)
 *   - TextStatusBoardRenderer (Renderer 인터페이스 구현체)
 */

import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TextStatusBoard, TextStatusBoardRenderer } from "../render/TextStatusBoard";
import { EventFeed } from "../render/Renderer";
import type { OfficeEvent } from "../types/events";
import {
  getRolePortraitPath,
  PORTRAIT_FALLBACK,
} from "../config/emotionImages";
import { pickRole } from "../render/twoD/roleCharacter";

// 픽스처 발화자(role="assistant")의 결정론 역할 — per-row/패널 경로 단언 기준.
const ASSISTANT_ROLE = pickRole("assistant");

// ── 테스트용 이벤트 픽스처 ────────────────────────────────────────────────────

const BASE = {
  run_id: "test-run-1",
  ts: "2026-06-08T10:00:00Z",
  schema_v: 1,
};

/** 각 이벤트 타입의 최소 픽스처 */
const EV_RUN_STARTED: OfficeEvent = {
  ...BASE,
  family: "Lifecycle",
  type: "RUN_STARTED",
  workflow: "crm_notify",
};

const EV_RUN_FINISHED: OfficeEvent = {
  ...BASE,
  family: "Lifecycle",
  type: "RUN_FINISHED",
  workflow: "crm_notify",
  status: "success",
};

const EV_RUN_FINISHED_REJECTED: OfficeEvent = {
  ...BASE,
  family: "Lifecycle",
  type: "RUN_FINISHED",
  workflow: "crm_notify",
  status: "rejected",
};

const EV_RUN_ERROR: OfficeEvent = {
  ...BASE,
  family: "Lifecycle",
  type: "RUN_ERROR",
  error: "LLM 응답 타임아웃",
};

const EV_TEXT_NEUTRAL: OfficeEvent = {
  ...BASE,
  family: "Text",
  type: "TEXT_MESSAGE",
  role: "assistant",
  content: "안녕하세요, 작업을 시작하겠습니다",
  image_url: null,
};

const EV_TEXT_THINKING: OfficeEvent = {
  ...BASE,
  family: "Text",
  type: "TEXT_MESSAGE",
  role: "assistant",
  content: "고객 데이터를 분석 중입니다",
  image_url: null,
};

const EV_TEXT_WITH_IMAGE_URL: OfficeEvent = {
  ...BASE,
  family: "Text",
  type: "TEXT_MESSAGE",
  role: "assistant",
  content: "완료되었습니다",
  image_url: "/work/custom.png",
};

const EV_TOOL_CALL_START: OfficeEvent = {
  ...BASE,
  family: "ToolCall",
  type: "TOOL_CALL_START",
  node_id: "N2",
  name: "crm_lookup",
  args: { customer_id: "C001" },
};

const EV_TOOL_CALL_RESULT: OfficeEvent = {
  ...BASE,
  family: "ToolCall",
  type: "TOOL_CALL_RESULT",
  node_id: "N2",
  name: "crm_lookup",
  result: { name: "홍길동" },
};

const EV_STATE_DELTA: OfficeEvent = {
  ...BASE,
  family: "State",
  type: "STATE_DELTA",
  accumulated: { customer_name: "홍길동", order_id: "O123" },
};

const EV_STATE_SNAPSHOT: OfficeEvent = {
  ...BASE,
  family: "State",
  type: "STATE_SNAPSHOT",
  accumulated: { customer_name: "홍길동" },
};

const EV_INTERRUPT_REQUESTED: OfficeEvent = {
  ...BASE,
  family: "Interrupt",
  type: "INTERRUPT_REQUESTED",
  node_id: "N3",
  tool: "email_send",
  risk: "needs-approval",
  payload: { task_id: 1 },
};

const EV_INTERRUPT_RESOLVED: OfficeEvent = {
  ...BASE,
  family: "Interrupt",
  type: "INTERRUPT_RESOLVED",
  node_id: "N3",
  tool: "email_send",
  decision: "approve",
};

const EV_NODE_FAILED: OfficeEvent = {
  ...BASE,
  family: "Interrupt",
  type: "NODE_FAILED",
  node_id: "N2",
  reason: "escalation",
  detail: "최대 재시도 초과",
};

// ── 각 이벤트 타입 렌더 문자열 확인 ─────────────────────────────────────────

describe("TextStatusBoard — 각 이벤트 타입 렌더", () => {
  it("RUN_STARTED → '워크플로 시작' 텍스트가 있어야 한다", () => {
    render(<TextStatusBoard events={[EV_RUN_STARTED]} />);
    expect(screen.getByText(/워크플로 시작/)).toBeTruthy();
    expect(screen.getByText(/crm_notify/)).toBeTruthy();
  });

  it("RUN_FINISHED(success) → '워크플로 종료' + '성공' 텍스트", () => {
    render(<TextStatusBoard events={[EV_RUN_FINISHED]} />);
    expect(screen.getByText(/워크플로 종료/)).toBeTruthy();
    expect(screen.getByText(/성공/)).toBeTruthy();
  });

  it("RUN_FINISHED(rejected) → '거부됨' 텍스트", () => {
    render(<TextStatusBoard events={[EV_RUN_FINISHED_REJECTED]} />);
    expect(screen.getByText(/거부됨/)).toBeTruthy();
  });

  it("RUN_ERROR → 오류 메시지 텍스트", () => {
    render(<TextStatusBoard events={[EV_RUN_ERROR]} />);
    expect(screen.getByText(/워크플로 오류/)).toBeTruthy();
    expect(screen.getByText(/LLM 응답 타임아웃/)).toBeTruthy();
  });

  it("TEXT_MESSAGE → 역할 + content 텍스트", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} />);
    expect(screen.getByText(/assistant/)).toBeTruthy();
    expect(screen.getByText(/안녕하세요/)).toBeTruthy();
  });

  it("TOOL_CALL_START → 도구 이름 + 노드 ID 텍스트", () => {
    render(<TextStatusBoard events={[EV_TOOL_CALL_START]} />);
    expect(screen.getByText(/도구 호출 시작/)).toBeTruthy();
    expect(screen.getByText(/crm_lookup/)).toBeTruthy();
    expect(screen.getByText(/N2/)).toBeTruthy();
  });

  it("TOOL_CALL_RESULT → 도구 결과 텍스트", () => {
    render(<TextStatusBoard events={[EV_TOOL_CALL_RESULT]} />);
    expect(screen.getByText(/도구 호출 결과/)).toBeTruthy();
    expect(screen.getByText(/crm_lookup/)).toBeTruthy();
  });

  it("STATE_DELTA → 변경 키 목록 텍스트", () => {
    render(<TextStatusBoard events={[EV_STATE_DELTA]} />);
    expect(screen.getByText(/상태 변화/)).toBeTruthy();
    expect(screen.getByText(/customer_name/)).toBeTruthy();
  });

  it("STATE_SNAPSHOT → 키 개수 텍스트", () => {
    render(<TextStatusBoard events={[EV_STATE_SNAPSHOT]} />);
    expect(screen.getByText(/상태 스냅샷/)).toBeTruthy();
    expect(screen.getByText(/1개 키/)).toBeTruthy();
  });

  it("INTERRUPT_REQUESTED → 도구명 + '승인 대기' 텍스트", () => {
    render(<TextStatusBoard events={[EV_INTERRUPT_REQUESTED]} />);
    expect(screen.getByText(/승인 대기/)).toBeTruthy();
    expect(screen.getByText(/email_send/)).toBeTruthy();
    expect(screen.getByText(/needs-approval/)).toBeTruthy();
  });

  it("INTERRUPT_RESOLVED → '승인 완료' + decision 텍스트", () => {
    render(<TextStatusBoard events={[EV_INTERRUPT_RESOLVED]} />);
    expect(screen.getByText(/승인 완료/)).toBeTruthy();
    expect(screen.getByText(/approve/)).toBeTruthy();
  });

  it("NODE_FAILED → 노드 ID + reason + detail 텍스트", () => {
    render(<TextStatusBoard events={[EV_NODE_FAILED]} />);
    expect(screen.getByText(/노드 실패/)).toBeTruthy();
    expect(screen.getByText(/N2/)).toBeTruthy();
    expect(screen.getByText(/escalation/)).toBeTruthy();
    expect(screen.getByText(/최대 재시도 초과/)).toBeTruthy();
  });

  it("빈 이벤트 목록 → '이벤트 대기 중' 메시지", () => {
    render(<TextStatusBoard events={[]} />);
    expect(screen.getByText(/이벤트 대기 중/)).toBeTruthy();
  });

  it("여러 이벤트를 시간순으로 모두 렌더해야 한다", () => {
    render(
      <TextStatusBoard
        events={[EV_RUN_STARTED, EV_TEXT_NEUTRAL, EV_RUN_FINISHED]}
      />
    );
    expect(screen.getByText(/워크플로 시작/)).toBeTruthy();
    expect(screen.getByText(/안녕하세요/)).toBeTruthy();
    expect(screen.getByText(/워크플로 종료/)).toBeTruthy();
  });
});

// ── 자동 스크롤 — scroll sentinel ref 존재 확인 ────────────────────────────

describe("TextStatusBoard — 자동 스크롤", () => {
  it("scroll sentinel(data-testid='scroll-sentinel')이 DOM에 있어야 한다", () => {
    render(<TextStatusBoard events={[EV_RUN_STARTED]} />);
    const sentinel = screen.getByTestId("scroll-sentinel");
    expect(sentinel).toBeTruthy();
  });

  it("빈 이벤트에서도 scroll sentinel이 있어야 한다", () => {
    render(<TextStatusBoard events={[]} />);
    expect(screen.getByTestId("scroll-sentinel")).toBeTruthy();
  });
});

// ── 이미지 토글 OFF (기본) ───────────────────────────────────────────────────

describe("TextStatusBoard — 이미지 토글 OFF (기본)", () => {
  it("showImage 미전달(기본 false) → 이미지 슬롯 없음", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} />);
    const images = screen.queryAllByTestId("emotion-image");
    expect(images).toHaveLength(0);
  });

  it("showImage=false → TextMessage에 이미지 없음", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={false} />);
    expect(screen.queryByTestId("emotion-image")).toBeNull();
  });

  it("showImage=false → 비TextMessage 이벤트에도 이미지 없음", () => {
    render(
      <TextStatusBoard
        events={[EV_RUN_STARTED, EV_TOOL_CALL_START]}
        showImage={false}
      />
    );
    expect(screen.queryByTestId("emotion-image")).toBeNull();
  });
});

// ── 이미지 토글 ON ───────────────────────────────────────────────────────────

describe("TextStatusBoard — 이미지 토글 ON", () => {
  it("showImage=true + TextMessage → 이미지 슬롯(emotion-image)이 있어야 한다", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={true} />);
    const img = screen.getByTestId("emotion-image");
    expect(img).toBeTruthy();
  });

  it("showImage=true + 비TextMessage(RUN_STARTED) → 이미지 없음", () => {
    render(<TextStatusBoard events={[EV_RUN_STARTED]} showImage={true} />);
    expect(screen.queryByTestId("emotion-image")).toBeNull();
  });

  it("showImage=true + TextMessage(thinking 키워드) → 역할_thinking 포트레이트 경로", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    // 발화자 역할(assistant→ASSISTANT_ROLE) + thinking 감정.
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "thinking"));
  });

  it("showImage=true + 여러 TextMessage → 이미지 슬롯이 TextMessage 수만큼", () => {
    const events: OfficeEvent[] = [
      EV_TEXT_NEUTRAL,
      EV_TEXT_THINKING,
      EV_RUN_STARTED, // 비TextMessage — 이미지 없음
    ];
    render(<TextStatusBoard events={events} showImage={true} />);
    const images = screen.getAllByTestId("emotion-image");
    expect(images).toHaveLength(2); // TextMessage 2개만
  });
});

// ── image_url 처리 ───────────────────────────────────────────────────────────

describe("TextStatusBoard — image_url 처리", () => {
  it("image_url=null → 역할 포트레이트 사용 (토글 ON)", () => {
    // EV_TEXT_NEUTRAL: image_url=null, content='안녕하세요' → neutral → 역할_neutral 포트레이트
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });

  it("image_url 채워진 값 → 그 URL 우선 사용 (토글 ON)", () => {
    render(
      <TextStatusBoard events={[EV_TEXT_WITH_IMAGE_URL]} showImage={true} />
    );
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    expect(img.src).toContain("/work/custom.png");
  });

  it("image_url='' (빈 문자열) → 역할 포트레이트 폴백 (토글 ON)", () => {
    const evEmptyUrl: OfficeEvent = {
      ...BASE,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "안녕하세요",
      image_url: "",
    };
    // image_url이 falsy('')이면 역할 포트레이트로 폴백
    render(<TextStatusBoard events={[evEmptyUrl]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    // neutral → 역할_neutral 포트레이트
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });

  it("image_url=null, showImage=false → 이미지 없음", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={false} />);
    expect(screen.queryByTestId("emotion-image")).toBeNull();
  });
});

// ── neutral 폴백 ─────────────────────────────────────────────────────────────

describe("TextStatusBoard — neutral 폴백", () => {
  it("키워드 없는 TextMessage → neutral → 역할_neutral 포트레이트", () => {
    const evNeutral: OfficeEvent = {
      ...BASE,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "알겠습니다",
      image_url: null,
    };
    render(<TextStatusBoard events={[evNeutral]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });

  it("빈 content TextMessage → neutral 폴백", () => {
    const evEmpty: OfficeEvent = {
      ...BASE,
      family: "Text",
      type: "TEXT_MESSAGE",
      role: "assistant",
      content: "",
      image_url: null,
    };
    render(<TextStatusBoard events={[evEmpty]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    // classifyEmotion("") → "neutral" → 역할_neutral 포트레이트
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });
});

// ── 발화자 클로즈업 패널 (speaker-portrait) ───────────────────────────────────

describe("TextStatusBoard — 발화자 클로즈업 패널", () => {
  it("토글 ON + 최근 TEXT_MESSAGE 있음 → speaker-portrait 패널 표시", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={true} />);
    expect(screen.getByTestId("speaker-portrait")).toBeTruthy();
  });

  it("패널에 발화자 대사(content)가 표시되어야 한다", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={true} />);
    const panel = screen.getByTestId("speaker-portrait");
    expect(panel.textContent).toContain("안녕하세요, 작업을 시작하겠습니다");
  });

  it("패널 포트레이트가 발화자 역할_감정 경로여야 한다", () => {
    // EV_TEXT_THINKING: content='고객 데이터를 분석 중입니다' → thinking
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("speaker-portrait-img") as HTMLImageElement;
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "thinking"));
  });

  it("토글 ON + 최근 TEXT_MESSAGE 없음(비TextMessage만) → 패널 숨김", () => {
    render(
      <TextStatusBoard
        events={[EV_RUN_STARTED, EV_TOOL_CALL_START]}
        showImage={true}
      />
    );
    expect(screen.queryByTestId("speaker-portrait")).toBeNull();
  });

  it("토글 ON + 빈 이벤트 → 패널 숨김", () => {
    render(<TextStatusBoard events={[]} showImage={true} />);
    expect(screen.queryByTestId("speaker-portrait")).toBeNull();
  });

  it("토글 OFF → 패널 숨김(최근 TEXT_MESSAGE 있어도)", () => {
    render(<TextStatusBoard events={[EV_TEXT_NEUTRAL]} showImage={false} />);
    expect(screen.queryByTestId("speaker-portrait")).toBeNull();
  });

  it("여러 TEXT_MESSAGE → 가장 최근 1건만 패널에 표시", () => {
    const events: OfficeEvent[] = [
      EV_TEXT_NEUTRAL, // content='안녕하세요...'
      EV_TEXT_THINKING, // content='고객 데이터를 분석 중입니다' (최근)
    ];
    render(<TextStatusBoard events={events} showImage={true} />);
    const panels = screen.getAllByTestId("speaker-portrait");
    expect(panels).toHaveLength(1);
    expect(panels[0].textContent).toContain("고객 데이터를 분석 중입니다");
  });
});

// ── 역할 인식 + onError 폴백 체인 ────────────────────────────────────────────

describe("TextStatusBoard — 역할 포트레이트 폴백 체인", () => {
  it("per-row 1차 onError → role_neutral로 교체", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    // 1차: role_thinking. onError 발생 시 role_neutral로 교체되어야 함.
    fireEvent.error(img);
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });

  it("per-row 2차 onError(role_neutral 실패) → PORTRAIT_FALLBACK로 교체", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    fireEvent.error(img); // → role_neutral
    fireEvent.error(img); // → PORTRAIT_FALLBACK
    expect(img.src).toContain(PORTRAIT_FALLBACK);
  });

  it("최종 폴백(PORTRAIT_FALLBACK)도 실패 → onError 핸들러 제거(루프 차단)", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    fireEvent.error(img); // role_neutral
    fireEvent.error(img); // PORTRAIT_FALLBACK
    fireEvent.error(img); // 루프 차단 — onerror=null
    expect(img.onerror).toBeNull();
  });

  it("image_url 명시 값은 폴백 핸들러가 없어야 한다(명시 경로 신뢰)", () => {
    render(
      <TextStatusBoard events={[EV_TEXT_WITH_IMAGE_URL]} showImage={true} />
    );
    const img = screen.getByTestId("emotion-image") as HTMLImageElement;
    fireEvent.error(img);
    // 폴백 없음 — src가 명시 경로 그대로 유지.
    expect(img.src).toContain("/work/custom.png");
  });

  it("패널 포트레이트도 onError 폴백 체인을 가진다", () => {
    render(<TextStatusBoard events={[EV_TEXT_THINKING]} showImage={true} />);
    const img = screen.getByTestId("speaker-portrait-img") as HTMLImageElement;
    fireEvent.error(img);
    expect(img.src).toContain(getRolePortraitPath(ASSISTANT_ROLE, "neutral"));
  });
});

// ── TextStatusBoardRenderer (Renderer 인터페이스 구현체) ────────────────────

describe("TextStatusBoardRenderer — Renderer 인터페이스", () => {
  it("TextStatusBoardRenderer.render()가 TextStatusBoard를 반환해야 한다", () => {
    const renderer = new TextStatusBoardRenderer();
    render(<>{renderer.render([EV_RUN_STARTED])}</>);
    expect(screen.getByTestId("text-status-board")).toBeTruthy();
    expect(screen.getByText(/워크플로 시작/)).toBeTruthy();
  });

  it("showImage=true로 생성한 렌더러 → 이미지 슬롯 있음", () => {
    const renderer = new TextStatusBoardRenderer(true);
    render(<>{renderer.render([EV_TEXT_NEUTRAL])}</>);
    expect(screen.getByTestId("emotion-image")).toBeTruthy();
  });

  it("showImage=false(기본)로 생성한 렌더러 → 이미지 없음", () => {
    const renderer = new TextStatusBoardRenderer(false);
    render(<>{renderer.render([EV_TEXT_NEUTRAL])}</>);
    expect(screen.queryByTestId("emotion-image")).toBeNull();
  });

  it("EventFeed에 TextStatusBoardRenderer를 주입할 수 있어야 한다", () => {
    const renderer = new TextStatusBoardRenderer();
    render(<EventFeed events={[EV_RUN_STARTED, EV_TEXT_NEUTRAL]} renderer={renderer} />);
    expect(screen.getByTestId("text-status-board")).toBeTruthy();
    expect(screen.getByText(/워크플로 시작/)).toBeTruthy();
    expect(screen.getByText(/안녕하세요/)).toBeTruthy();
  });
});
