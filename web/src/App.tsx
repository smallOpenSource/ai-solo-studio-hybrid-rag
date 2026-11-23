/**
 * one_man_corp 메인 앱 컴포넌트.
 *
 * US-006: TextStatusBoard + 이미지 토글 통합.
 * US-007: Agent Inbox 통합 (좌: 상태창, 우: Inbox 2단 레이아웃).
 * US-005: 뷰 토글(텍스트↔오피스 v2) — viewMode 상태로 renderer만 분기.
 * office-v2 US-0f: 뷰 토글에 'office-v2' 추가 — Phaser 오피스 + 접이식 채팅 패널
 *   (WS 구독). 기존 text 경로·SSE 훅은 무변경(병존).
 *   ★구 pixi 2D 맵 뷰('2d')는 컷오버로 완전 제거(office-v2 Phaser가 대체).
 */
import { useEffect, useMemo, useState, type CSSProperties, type FormEvent } from "react";
import { useEventStream } from "./api/sse";
import { useOfficeWebSocket } from "./api/ws";
import { startRun, cancelAll } from "./api/rest";
import { searchKnowledge, type KnowledgeResult } from "./api/knowledge";
import { useSignOut } from "./auth/AuthGate";
import { EventFeed } from "./render/Renderer";
import { TextStatusBoardRenderer } from "./render/TextStatusBoard";
import { Inbox } from "./inbox/Inbox";
import { PhaserGame } from "./office/PhaserGame";
import { ChatPanel } from "./office/ChatPanel";
import {
  EventBus,
  OFFICE_EVENT_CHANNEL,
  AGENT_SELECTED_CHANNEL,
  AGENT_ACTIVITY_CHANNEL,
} from "./office/EventBus";
import { NexusShell } from "./office/NexusShell";
import { SystemLogPanel } from "./office/SystemLogPanel";
import { ActiveTasksPanel } from "./office/ActiveTasksPanel";
import { AgentCard } from "./office/AgentCard";
import {
  deriveAgentActivity,
  deriveActiveTasks,
  latestEmotionForRole,
} from "./office/deriveAgentActivity";
import { ROLE_KEYS, type RoleKey } from "./office/sceneLogic";
import { NEXUS_COLORS, NEXUS_FONTS, pixelPanel, pixelInset, pixelButton } from "./office/nexusTheme";
import { fullLabel } from "./office/roleNames";

/** office-v2 사이드바 섹션 — 맵을 보이는 섹션 집합(나머지는 맵 display:none·패널 교체). */
const MAP_SECTIONS = new Set<string>(["Dashboard", "Office"]);


/**
 * KnowledgeSearch — Knowledge 탭 실 검색 UI (US-G3-5).
 * POST /api/knowledge/search → {results:[{text,source,score}]} 렌더.
 */
function KnowledgeSearch() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);

  async function handleSearch(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!query.trim() || loading) return;
    setLoading(true);
    setError(null);
    setSearched(false);
    try {
      const res = await searchKnowledge(query.trim(), 5);
      setResults(res.results);
      setSearched(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      data-testid="section-knowledge"
      style={{
        ...pixelPanel(),
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        overflowY: "auto",
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div
        style={{
          fontFamily: NEXUS_FONTS.headline,
          fontWeight: 700,
          fontSize: 18,
          color: NEXUS_COLORS.gold,
        }}
      >
        Knowledge Base
      </div>

      {/* 검색 폼 */}
      <form
        onSubmit={(e) => void handleSearch(e)}
        style={{ display: "flex", gap: 8, alignItems: "center" }}
      >
        <input
          data-testid="knowledge-search-input"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="검색 질의를 입력하세요…"
          style={{
            flex: 1,
            padding: "6px 10px",
            fontFamily: NEXUS_FONTS.terminal,
            fontSize: 13,
            color: NEXUS_COLORS.green,
            backgroundColor: NEXUS_COLORS.black,
            border: `1px solid ${NEXUS_COLORS.outlineVariant}`,
            outline: "none",
          }}
        />
        <button
          type="submit"
          data-testid="knowledge-search-submit"
          disabled={loading}
          style={{
            ...pixelButton(),
            padding: "6px 14px",
            fontSize: 13,
            fontWeight: 700,
            color: NEXUS_COLORS.gold,
            cursor: loading ? "not-allowed" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          {loading ? "검색 중…" : "검색"}
        </button>
      </form>

      {/* 에러 */}
      {error && (
        <div
          style={{
            fontFamily: NEXUS_FONTS.terminal,
            fontSize: 12,
            color: NEXUS_COLORS.error,
          }}
        >
          오류: {error}
        </div>
      )}

      {/* 결과 */}
      {searched && results.length === 0 && !error && (
        <div
          style={{
            fontFamily: NEXUS_FONTS.terminal,
            fontSize: 12,
            color: NEXUS_COLORS.muted,
          }}
        >
          결과가 없습니다.
        </div>
      )}
      {results.map((r, i) => (
        <div
          key={i}
          data-testid="knowledge-result"
          style={{
            ...pixelPanel(),
            padding: 10,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <div
            style={{
              fontFamily: NEXUS_FONTS.terminal,
              fontSize: 12,
              color: NEXUS_COLORS.onSurface,
              lineHeight: 1.5,
            }}
          >
            {r.text}
          </div>
          <div
            style={{
              fontFamily: NEXUS_FONTS.terminal,
              fontSize: 11,
              color: NEXUS_COLORS.muted,
            }}
          >
            출처: {r.source} · 점수: {r.score.toFixed(3)}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  // ★SSE 구독 훅 — App 상단에서 1회만 호출(viewMode와 무관·조건부 호출 금지).
  // 두 렌더러가 동일 events를 공유하므로 토글 시 재구독·재연결 0(US-005).
  const { events, lastSeq, connected } = useEventStream("/events/stream");

  // ★WS 구독 훅(office-v2 US-0f) — SSE와 병존. 채팅 패널·Phaser 씬이 소비한다.
  // SSE와 마찬가지로 상단에서 무조건 1회 호출(Rules of Hooks·viewMode 무관).
  const { events: wsEvents, sendJsonMessage: sendOfficeJson } = useOfficeWebSocket("/ws");

  // 뷰 토글 상태 (기본 오피스 v2 뷰·Q4 승인) — 'text' | 'office-v2'
  const [viewMode, setViewMode] = useState<"text" | "office-v2">("office-v2");

  // ★US-N1: office-v2 사이드바 활성 섹션(기본 Dashboard). NexusShell은 이 라벨을 모름 —
  //   App이 섹션별 children을 조립하고 셸엔 강조용 sidebarActive로만 넘긴다(순수 유지).
  const [activeSection, setActiveSection] = useState<string>("Dashboard");

  // ★US-N5: 선택 역할(맵 스프라이트 클릭 → EventBus agent-selected). 기본 ceo.
  const [selectedRole, setSelectedRole] = useState<RoleKey>("ceo");

  // ★C3: 직원 모달 — agent-selected 시 열리는 오버레이 역할(null=닫힘).
  //   모달은 live-office-view의 형제 오버레이로 렌더되므로 Phaser는 절대 언마운트되지 않는다.
  const [modalRole, setModalRole] = useState<RoleKey | null>(null);

  // WS 이벤트를 EventBus로 흘려 OfficeScene이 말풍선/상태를 갱신하게 한다(office-v2).
  // 마지막 이벤트만 발행(append-only라 신규 1건) — viewMode와 무관히 동기화.
  useEffect(() => {
    const last = wsEvents[wsEvents.length - 1];
    if (last) {
      EventBus.emit(OFFICE_EVENT_CHANNEL, last);
    }
  }, [wsEvents]);

  // ★US-N6/N7: wsEvents → 활동 파생(역할 상태·활성수·세션 성공률) + 진행 작업(read-only).
  const activity = useMemo(() => deriveAgentActivity(wsEvents), [wsEvents]);
  const derivedTasks = useMemo(() => deriveActiveTasks(wsEvents), [wsEvents]);
  // 선택 역할의 최근 발화 감정(카드 표정 — thinking→focused는 카드 alias가 처리).
  const selectedEmotion = useMemo(
    () => latestEmotionForRole(wsEvents, selectedRole),
    [wsEvents, selectedRole],
  );

  // ★US-N8: 활동 상태(byRole)를 씬으로 발행 → OfficeScene이 working 역할 글로우.
  useEffect(() => {
    EventBus.emit(AGENT_ACTIVITY_CHANNEL, activity.byRole);
  }, [activity]);

  // ★US-N4/N5 + C3: 맵/그리드에서 선택된 역할을 구독 → 카드 반영 + 직원 모달 open.
  useEffect(() => {
    const onSelected = (role: RoleKey): void => {
      setSelectedRole(role);
      setModalRole(role); // ★C3: 선택 시 모달 오버레이 표시.
    };
    EventBus.on(AGENT_SELECTED_CHANNEL, onSelected);
    return () => {
      EventBus.off(AGENT_SELECTED_CHANNEL, onSelected);
    };
  }, []);

  // ★C3: 모달 열림 중 ESC 키로 닫기(window keydown). 모달이 닫히면(또는 언마운트 시)
  //   리스너를 해제한다(누수 0). modalRole이 의존성이라 열릴 때만 등록된다.
  useEffect(() => {
    if (modalRole === null) return;
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") setModalRole(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [modalRole]);

  // 이미지 토글 상태 (기본 OFF) — 텍스트 뷰에서만 의미 있음
  const [showImage, setShowImage] = useState(false);

  // 워크플로 시작 버튼 상태
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  // ★US-G3-3: 시작 성공 피드백(~3s 지속). starting은 POST 동안만이라 너무 짧게 명멸 →
  //   사람·검증이 못 봄. 성공 후 잠깐 "시작됨"을 보여 가시 피드백을 보장한다.
  const [deploySuccess, setDeploySuccess] = useState(false);

  // ★EventFeed 슬롯 렌더러(고정·M1) — 텍스트 뷰 전용(이미지 토글 반영).
  // office-v2 뷰는 EventFeed를 쓰지 않고 별도 Phaser 레이아웃으로 분기한다(아래 참조).
  const renderer = new TextStatusBoardRenderer(showImage);

  /** 워크플로 시작 버튼 핸들러 (상단 버튼 + 사이드바 DEPLOY AGENT 공유). */
  async function handleStartRun() {
    setStarting(true);
    setStartError(null);
    try {
      await startRun("crm_notify");
      setDeploySuccess(true);
      window.setTimeout(() => setDeploySuccess(false), 3000);
    } catch (err) {
      setStartError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }

  /** ★P1 step3.5: 진행 중 전체 워크플로 일괄 취소(멀티 워커풀 — POST /api/run/cancel-all). */
  async function handleCancelAll() {
    setStartError(null);
    try {
      await cancelAll();
    } catch (err) {
      setStartError(err instanceof Error ? err.message : String(err));
    }
  }

  // ★B2: AuthGate가 내려준 signOut(컨텍스트). 사이드바 Log Out이 호출 →
  //   POST /api/logout → authed=false → AuthGate가 App을 언마운트(SSE/WS 자동 해제).
  //   AuthGate 밖(app.test의 <App/> 직접 렌더)에선 no-op이라 안전하다.
  const signOut = useSignOut();

  /** ★US-N2/B2: Log Out — 백엔드 세션 로그아웃(AuthGate가 App 언마운트로 정리). */
  function handleLogout() {
    signOut();
  }

  return (
    <div style={{ padding: 16 }}>
      {/* ★C4/D2: 타이틀(h1) 제거·큰 회색 툴바 제거. 작은 인디케이터 스트립만 남긴다 —
          연결상태(작은 인디케이터)·뷰 토글(텍스트/오피스). 이미지 토글·워크플로 시작은
          뷰 컨텍스트(텍스트 뷰·office-v2 Settings)로 이동했다. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 12,
          flexWrap: "wrap",
        }}
      >
        {/* 연결 상태 — 작은 인디케이터(가짜 텔레메트리 0·실 연결/seq만). */}
        <span
          data-testid="conn-indicator"
          title={connected ? "SSE 연결됨" : "SSE 끊김"}
          style={{ fontSize: 12, color: "#666", display: "flex", alignItems: "center", gap: 4 }}
        >
          <span aria-hidden>{connected ? "🟢" : "🔴"}</span>
          seq {lastSeq}
        </span>

        {/* 뷰 토글 (텍스트 ↔ 오피스 v2) — 텍스트뷰 전환 수단 보존(어디서나 접근). */}
        <div style={{ display: "flex", gap: 4 }} role="group" aria-label="뷰 전환">
          <button
            data-testid="view-toggle-text"
            onClick={() => setViewMode("text")}
            aria-pressed={viewMode === "text"}
            style={{
              padding: "4px 12px",
              fontSize: 13,
              background: viewMode === "text" ? "#2a7" : "#ddd",
              color: viewMode === "text" ? "#fff" : "#333",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            텍스트
          </button>
          <button
            data-testid="view-toggle-office"
            onClick={() => setViewMode("office-v2")}
            aria-pressed={viewMode === "office-v2"}
            style={{
              padding: "4px 12px",
              fontSize: 13,
              background: viewMode === "office-v2" ? "#2a7" : "#ddd",
              color: viewMode === "office-v2" ? "#fff" : "#333",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            오피스 v2
          </button>
        </div>
      </div>

      {/* office-v2: Nexus OS 대시보드 셸(헤더·사이드바·중앙 맵·우 패널·하단 로그) + 채팅 패널. */}
      {viewMode === "office-v2" ? (
        <div
          data-testid="office-v2-layout"
          style={{
            display: "flex",
            gap: 16,
            alignItems: "stretch",
            // ★UI-1/B-2: 명시 높이로 뷰포트 충전 — NexusShell height:100%·Phaser 100% 체인의 앵커.
            //   minHeight:0과 짝지어 flex 자식(셸→main→슬롯→Phaser) 붕괴 방지(ResizeObserver height>0 보장).
            height: "calc(100vh - 140px)",
            minHeight: 0,
          }}
        >
          {/* Nexus OS 셸 — flex:1로 폭 확장. 내부에 맵·섹션 패널·시스템 로그. */}
          <div style={{ flex: 1, minWidth: 0, minHeight: 0, display: "flex" }}>
            <NexusShell
              sidebarActive={activeSection}
              onNavSelect={setActiveSection}
              onDeploy={() => void handleStartRun()}
              onLogout={handleLogout}
              deployStatus={starting ? "starting" : startError ? "error" : deploySuccess ? "success" : null}
              deployError={startError}
              systemLog={<SystemLogPanel events={wsEvents} />}
            >
              {/* 중앙 main: 맵(항상 마운트·Phaser 영속) + 섹션별 우/대체 패널. */}
              <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
                {/* Live Office View — ★항상 마운트(언마운트 0·WebGL 재생성 차단). 비-맵 섹션에선
                    display:none(PhaserGame ResizeObserver가 0치수 가드로 안전·재마운트 없음). */}
                <div
                  data-testid="live-office-view"
                  style={{
                    ...pixelPanel(),
                    flex: 1,
                    minWidth: 0,
                    minHeight: 0,
                    display: MAP_SECTIONS.has(activeSection) ? "flex" : "none",
                    flexDirection: "column",
                    overflow: "hidden",
                  }}
                >
                  {/* ★US-G3-2: Dashboard에서만 "Live Office View" 헤더 chrome 표시.
                      Office 탭은 헤더 없이 캔버스가 가용영역 전체를 차지(풀스크린). */}
                  {activeSection === "Dashboard" && (
                    <div
                      data-testid="live-office-header"
                      style={{
                        flexShrink: 0,
                        padding: "6px 12px",
                        backgroundColor: NEXUS_COLORS.indigo,
                        color: NEXUS_COLORS.onSurface,
                        fontFamily: NEXUS_FONTS.headline,
                        fontSize: 15,
                        borderBottom: `2px solid ${NEXUS_COLORS.outlineVariant}`,
                      }}
                    >
                      Live Office View
                    </div>
                  )}
                  {/* ★P3 US-3c: 맵 뷰포트를 pixel-inset 베벨 프레임으로(함몰감·셸 다크 톤 일치).
                      box-sizing border-box + flex:1·minHeight:0로 Phaser 100% 높이 체인 보존. */}
                  <div
                    data-testid="office-viewport-frame"
                    style={{
                      ...pixelInset(NEXUS_COLORS.black),
                      flex: 1,
                      minHeight: 0,
                      boxSizing: "border-box",
                      display: "flex",
                    }}
                  >
                    <PhaserGame />
                  </div>
                </div>

                {/* Dashboard 우 컬럼: Active Tasks(워크플로 파생)만 유지.
                    ★C3: 선택 역할 카드는 모달로 통일했다(우 컬럼 카드 제거) — 직원 상세는
                    맵/그리드 클릭 시 모달 오버레이로 표시(아래 agent-modal). */}
                {activeSection === "Dashboard" && (
                  <div
                    data-testid="dashboard-right"
                    style={{
                      width: 300,
                      flexShrink: 0,
                      display: "flex",
                      flexDirection: "column",
                      gap: 16,
                      minHeight: 0,
                      overflowY: "auto",
                    }}
                  >
                    <ActiveTasksPanel
                      tasks={derivedTasks.map((t) => ({
                        title: t.title,
                        pct: t.pct,
                        assignee: t.assignee,
                      }))}
                      onCancelAll={handleCancelAll}
                    />
                  </div>
                )}

                {/* Agents: 16역할 카드 그리드(클릭 → 선택). 맵은 display:none(영속 유지). */}
                {activeSection === "Agents" && (
                  <div
                    data-testid="section-agents"
                    style={{
                      flex: 1,
                      minWidth: 0,
                      minHeight: 0,
                      overflowY: "auto",
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                      gap: 12,
                      alignContent: "start",
                    }}
                  >
                    {ROLE_KEYS.map((role) => (
                      <div
                        key={role}
                        role="button"
                        tabIndex={0}
                        onClick={() => EventBus.emit(AGENT_SELECTED_CHANNEL, role)}
                        style={{ cursor: "pointer" }}
                      >
                        <AgentCard
                          role={role}
                          name={fullLabel(role).name}
                          cls={fullLabel(role).title}
                          emotion={latestEmotionForRole(wsEvents, role)}
                          morale={activity.sessionScore}
                          load={activity.activeCountByRole[role] > 0 ? 100 : 0}
                          status={activity.byRole[role]}
                          activeCount={activity.activeCountByRole[role]}
                        />
                      </div>
                    ))}
                  </div>
                )}

                {/* Knowledge: 실 검색 UI(US-G3-5). */}
                {activeSection === "Knowledge" && <KnowledgeSearch />}
                {/* ★D2: Settings 섹션 — 상단 큰 툴바에서 옮겨온 실제 컨트롤(워크플로 시작·
                    직원 이미지 표시 토글). 뷰 전환은 상단 작은 인디케이터 스트립에 보존. */}
                {activeSection === "Settings" && (
                  <div
                    data-testid="section-settings"
                    style={{
                      ...pixelPanel(),
                      flex: 1,
                      minWidth: 0,
                      minHeight: 0,
                      overflowY: "auto",
                      padding: 16,
                      display: "flex",
                      flexDirection: "column",
                      gap: 12,
                    }}
                  >
                    <div
                      style={{
                        fontFamily: NEXUS_FONTS.headline,
                        fontWeight: 700,
                        fontSize: 18,
                        color: NEXUS_COLORS.gold,
                      }}
                    >
                      Settings
                    </div>

                    {/* 워크플로 시작(POST /api/run — US-004). DEPLOY AGENT와 동일 핸들러. */}
                    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                      <button
                        data-testid="start-run-btn"
                        disabled={starting}
                        onClick={() => void handleStartRun()}
                        style={{
                          ...pixelButton(),
                          padding: "6px 14px",
                          fontSize: 13,
                          fontWeight: 700,
                          color: NEXUS_COLORS.gold,
                          cursor: starting ? "not-allowed" : "pointer",
                          opacity: starting ? 0.6 : 1,
                        }}
                      >
                        {starting ? "시작 중…" : "워크플로 시작"}
                      </button>
                      {startError && (
                        <span style={{ fontSize: 12, color: NEXUS_COLORS.error }}>
                          오류: {startError}
                        </span>
                      )}
                    </div>

                    {/* 직원 이미지 표시 토글(텍스트 뷰 렌더러에 반영). */}
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        cursor: "pointer",
                        fontFamily: NEXUS_FONTS.body,
                        fontSize: 13,
                        color: NEXUS_COLORS.onSurfaceVariant,
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={showImage}
                        onChange={(e) => setShowImage(e.target.checked)}
                        data-testid="image-toggle"
                      />
                      직원 이미지 표시(텍스트 뷰)
                    </label>
                  </div>
                )}
              </div>
            </NexusShell>
          </div>
          {/* 채팅 패널 보존(WS TEXT_MESSAGE 표시·좌/우 토글·접기). */}
          <ChatPanel events={wsEvents} sendJsonMessage={sendOfficeJson} />
        </div>
      ) : (
        /* 텍스트 뷰: 이미지 토글(인라인) + 2단 레이아웃(좌=상태창·우=Inbox). */
        <div>
          {/* 직원 이미지 표시 토글 — 텍스트 뷰 전용(렌더러에 반영). */}
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              cursor: "pointer",
              fontSize: 13,
              marginBottom: 12,
            }}
          >
            <input
              type="checkbox"
              checked={showImage}
              onChange={(e) => setShowImage(e.target.checked)}
              data-testid="image-toggle"
            />
            직원 이미지 표시
          </label>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 380px",
              gap: 16,
              alignItems: "start",
            }}
          >
            {/* 좌: 렌더 슬롯(고정·M1) — 텍스트 상태창 렌더러 */}
            <EventFeed events={events} renderer={renderer} />

            {/* 우: Agent Inbox (US-007) */}
            <Inbox pollIntervalMs={5000} />
          </div>
        </div>
      )}

      {/* ★C3: 직원 모달 오버레이 — office-v2/text 레이아웃의 형제로 렌더(딤 배경·중앙 카드).
          live-office-view는 위 레이아웃 안에 항상 마운트되어 있으므로 Phaser는 영속한다.
          닫기: 배경 클릭·닫기 버튼·ESC(window keydown 위 useEffect). */}
      {modalRole !== null && (
        <div
          data-testid="agent-modal"
          onClick={() => setModalRole(null)}
          style={modalBackdropStyle}
        >
          {/* 카드 영역 클릭은 배경 닫기로 전파되지 않게 stopPropagation. */}
          <div onClick={(e) => e.stopPropagation()} style={modalCardWrapStyle}>
            <button
              type="button"
              data-testid="agent-modal-close"
              onClick={() => setModalRole(null)}
              aria-label="닫기"
              style={modalCloseBtnStyle}
            >
              ✕
            </button>
            <AgentCard
              role={modalRole}
              name={fullLabel(modalRole).name}
              cls={fullLabel(modalRole).title}
              emotion={modalRole === selectedRole ? selectedEmotion : latestEmotionForRole(wsEvents, modalRole)}
              morale={activity.sessionScore}
              load={activity.activeCountByRole[modalRole] > 0 ? 100 : 0}
              status={activity.byRole[modalRole]}
              activeCount={activity.activeCountByRole[modalRole]}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── 모달 스타일(인라인·nexusTheme 토큰 — 고정 오버레이) ──────────────────────────

const modalBackdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 1000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  backgroundColor: "rgba(0, 0, 0, 0.6)",
};

const modalCardWrapStyle: CSSProperties = {
  position: "relative",
  width: 420,
  maxWidth: "92vw",
};

const modalCloseBtnStyle: CSSProperties = {
  ...pixelButton(),
  position: "absolute",
  top: -14,
  right: -14,
  zIndex: 1,
  width: 28,
  height: 28,
  padding: 0,
  fontSize: 13,
  fontWeight: 700,
  color: NEXUS_COLORS.gold,
  cursor: "pointer",
};
