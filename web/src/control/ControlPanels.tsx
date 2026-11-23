/**
 * ControlPanels — §9.4 4-패널 합성 루트 (P6c).
 *
 * 4 패널(§9.4 MVP): (1) 맵+아바타 (2) Agent Status Overlay(도크 상태판) (3) Task Queue (4) Company Chat.
 * orch RO API(/agents/status·/tasks)를 폴링 소비하고, Company Chat 은 SSE TEXT_MESSAGE 를 읽는다.
 *
 * ★WA-위-합성: MapAvatarPanel 이 도크 오버레이(AgentStatusOverlay)를 WA 영역 위에 합성한다.
 *   라이브 WA 셸(waMapUrl)은 pending-Owner — 미설정 시 placeholder 위에 오버레이를 렌더한다.
 * ★데이터 소스는 전부 RO(읽기 전용)·write 0. events.ts(동결)는 SSE 타입 import(읽기)만.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { type AgentStatus, type TaskRecord } from "./types";
import { type TextMessage } from "../types/events";
import { fetchAgentStatus, fetchTasks } from "./api";
import { AgentStatusOverlay } from "./AgentStatusOverlay";
import { TaskQueuePanel } from "./TaskQueuePanel";
import { CompanyChat } from "./CompanyChat";
import { MapAvatarPanel } from "./MapAvatarPanel";

export interface ControlPanelsProps {
  /** RO API base URL(별도 origin 합성 시 주입·기본 same-origin "")。 */
  apiBase?: string;
  /** 폴링 주기(ms·기본 5000). 0 이면 1회만 조회(테스트). */
  pollMs?: number;
  /** 라이브 WA 맵 URL(Owner 제공·미설정 시 placeholder). */
  waMapUrl?: string;
  /** Company Chat 메시지(SSE TEXT_MESSAGE·컨테이너/상위가 주입). */
  chatMessages?: TextMessage[];
}

interface ControlData {
  agents: AgentStatus[];
  tasks: TaskRecord[];
  agentsError: string | null;
  tasksError: string | null;
  loading: boolean;
}

/**
 * orch RO API 폴링 훅. /agents/status·/tasks 를 주기 조회한다(읽기 전용).
 * 각 소스 에러는 격리(한쪽 실패가 다른 패널을 막지 않음). pollMs=0 이면 1회만.
 */
export function useControlData(apiBase = "", pollMs = 5000): ControlData {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    // 두 소스를 독립 조회(한쪽 실패 격리). settled 로 부분 성공 허용.
    const [aRes, tRes] = await Promise.allSettled([
      fetchAgentStatus(apiBase),
      fetchTasks(apiBase),
    ]);
    if (!mounted.current) return;
    if (aRes.status === "fulfilled") {
      setAgents(aRes.value);
      setAgentsError(null);
    } else {
      setAgentsError(String(aRes.reason?.message ?? aRes.reason));
    }
    if (tRes.status === "fulfilled") {
      setTasks(tRes.value);
      setTasksError(null);
    } else {
      setTasksError(String(tRes.reason?.message ?? tRes.reason));
    }
    setLoading(false);
  }, [apiBase]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    if (pollMs > 0) {
      const id = setInterval(() => void refresh(), pollMs);
      return () => {
        mounted.current = false;
        clearInterval(id);
      };
    }
    return () => {
      mounted.current = false;
    };
  }, [refresh, pollMs]);

  return { agents, tasks, agentsError, tasksError, loading };
}

/**
 * 4-패널 합성 루트. 맵+아바타(도크 오버레이 합성) | Task Queue | Company Chat 레이아웃.
 */
export function ControlPanels({
  apiBase = "",
  pollMs = 5000,
  waMapUrl,
  chatMessages = [],
}: ControlPanelsProps) {
  const { agents, tasks, agentsError, tasksError, loading } = useControlData(apiBase, pollMs);

  return (
    <div
      className="control-panels"
      data-testid="control-panels"
      style={{
        display: "flex",
        gap: 8,
        padding: 8,
        background: "#0b1120",
        minHeight: "100%",
        alignItems: "stretch",
        flexWrap: "wrap",
      }}
    >
      {/* 패널 1: 맵 + 아바타 (도크 오버레이 = 패널 2 합성) */}
      <MapAvatarPanel waMapUrl={waMapUrl}>
        <AgentStatusOverlay agents={agents} loading={loading} error={agentsError} />
      </MapAvatarPanel>

      {/* 우측 사이드: Task Queue + Company Chat */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 260 }}>
        <TaskQueuePanel tasks={tasks} loading={loading} error={tasksError} />
        <CompanyChat messages={chatMessages} />
      </div>
    </div>
  );
}
