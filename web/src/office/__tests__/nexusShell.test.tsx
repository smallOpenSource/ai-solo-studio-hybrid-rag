/**
 * NexusShell 단위 테스트 (US-1b · @testing-library/react).
 *
 * 검증 항목:
 *   - 셸 3영역 testid(헤더·사이드바·시스템로그 래퍼) 존재.
 *   - 사이드바 내비 5개 + DEPLOY AGENT 텍스트.
 *   - 헤더 borderRadius 0(샤프 코너).
 *   - children/systemLog 슬롯 콘텐츠 렌더.
 *   - sidebarActive 강조(gold) — data-active 토글.
 */

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { NexusShell } from "../NexusShell";

afterEach(() => cleanup());

describe("NexusShell — 셸 섀시 영역/슬롯", () => {
  it("헤더·사이드바·시스템로그 래퍼 testid가 존재한다", () => {
    render(
      <NexusShell systemLog={<div>LOG</div>}>
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByTestId("nexus-header")).toBeDefined();
    expect(screen.getByTestId("nexus-sidebar")).toBeDefined();
    expect(screen.getByTestId("nexus-systemlog")).toBeDefined();
  });

  it("사이드바 내비 5개 + DEPLOY AGENT를 렌더한다", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByText("Dashboard")).toBeDefined();
    expect(screen.getByText("Office")).toBeDefined();
    expect(screen.getByText("Agents")).toBeDefined();
    expect(screen.getByText("Knowledge")).toBeDefined();
    expect(screen.getByText("Settings")).toBeDefined();
    expect(screen.getByText("DEPLOY AGENT")).toBeDefined();
  });

  it("헤더 컨테이너의 borderRadius는 0(샤프 코너·라운드 금지)", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    const header = screen.getByTestId("nexus-header");
    // BORDER_RADIUS===0 → React는 0값을 "0"으로 렌더(value===0 특례·"0px" 아님). 둘 다 허용.
    expect(["0", "0px"]).toContain(header.style.borderRadius);
  });

  it("children과 systemLog 슬롯 콘텐츠를 렌더한다", () => {
    render(
      <NexusShell systemLog={<div>SYS_LOG_SLOT</div>}>
        <div>MAIN_CHILD</div>
      </NexusShell>,
    );
    expect(screen.getByText("MAIN_CHILD")).toBeDefined();
    expect(screen.getByText("SYS_LOG_SLOT")).toBeDefined();
    // systemLog는 nexus-systemlog 래퍼 내부에 위치.
    expect(screen.getByTestId("nexus-systemlog").textContent).toContain("SYS_LOG_SLOT");
  });

  it("sidebarActive와 일치하는 내비만 data-active=true로 강조한다", () => {
    render(
      <NexusShell systemLog={<div />} sidebarActive="Office">
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByText("Office").getAttribute("data-active")).toBe("true");
    expect(screen.getByText("Dashboard").getAttribute("data-active")).toBe("false");
  });
});

// ── 버튼 동작화 콜백(US-N1/N2) ───────────────────────────────────────────────

describe("NexusShell — 내비/CTA 콜백 배선(US-N1/N2)", () => {
  it("내비 클릭 시 onNavSelect(label)을 호출한다(5개 각각)", () => {
    const onNavSelect = vi.fn();
    render(
      <NexusShell systemLog={<div />} onNavSelect={onNavSelect}>
        <div>x</div>
      </NexusShell>,
    );
    for (const label of ["Dashboard", "Office", "Agents", "Knowledge", "Settings"]) {
      fireEvent.click(screen.getByText(label));
      expect(onNavSelect).toHaveBeenCalledWith(label);
    }
    expect(onNavSelect).toHaveBeenCalledTimes(5);
  });

  it("DEPLOY AGENT 클릭 시 onDeploy를 호출한다", () => {
    const onDeploy = vi.fn();
    render(
      <NexusShell systemLog={<div />} onDeploy={onDeploy}>
        <div>x</div>
      </NexusShell>,
    );
    fireEvent.click(screen.getByText("DEPLOY AGENT"));
    expect(onDeploy).toHaveBeenCalledTimes(1);
  });

  it("Log Out 클릭 시 onLogout을 호출한다", () => {
    const onLogout = vi.fn();
    render(
      <NexusShell systemLog={<div />} onLogout={onLogout}>
        <div>x</div>
      </NexusShell>,
    );
    fireEvent.click(screen.getByText("Log Out"));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  it("콜백 미전달이어도 클릭이 throw하지 않는다(옵셔널 가드)", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    expect(() => fireEvent.click(screen.getByTestId("deploy-btn"))).not.toThrow();
    expect(() => fireEvent.click(screen.getByText("Dashboard"))).not.toThrow();
  });
});

// ── US-G3-3: DEPLOY 피드백 ────────────────────────────────────────────────────

describe("NexusShell — DEPLOY 피드백(US-G3-3)", () => {
  it("deployStatus=null이면 피드백/에러 없음·버튼 텍스트 'DEPLOY AGENT'", () => {
    render(
      <NexusShell systemLog={<div />} deployStatus={null}>
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByTestId("deploy-btn").textContent).toBe("DEPLOY AGENT");
    expect(screen.queryByTestId("deploy-feedback")).toBeNull();
    expect(screen.queryByTestId("deploy-error")).toBeNull();
  });

  it("deployStatus='starting'이면 버튼 텍스트 '배포 중…'·deploy-feedback 표시", () => {
    render(
      <NexusShell systemLog={<div />} deployStatus="starting">
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByTestId("deploy-btn").textContent).toBe("배포 중…");
    expect(screen.getByTestId("deploy-feedback")).toBeDefined();
    expect(screen.queryByTestId("deploy-error")).toBeNull();
  });

  it("deployStatus='error'·deployError 있으면 deploy-error 표시", () => {
    render(
      <NexusShell systemLog={<div />} deployStatus="error" deployError="서버 연결 실패">
        <div>x</div>
      </NexusShell>,
    );
    expect(screen.getByTestId("deploy-error")).toBeDefined();
    expect(screen.getByTestId("deploy-error").textContent).toContain("서버 연결 실패");
    expect(screen.queryByTestId("deploy-feedback")).toBeNull();
  });
});

// ── D1: HUD/계정 하드코딩 제거(정직화) ────────────────────────────────────────

describe("NexusShell — 가짜 텔레메트리/계정 제거(D1)", () => {
  it("헤더에 가짜 HUD 값(SYS: OK·GPU·UP·99.9%)이 없다", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    const header = screen.getByTestId("nexus-header");
    expect(header.textContent).not.toContain("SYS: OK");
    expect(header.textContent).not.toContain("GPU");
    expect(header.textContent).not.toContain("99.9%");
  });

  it("사이드바에 가짜 계정 값(Admin Console·Nexus-Root)이 없다", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    const sidebar = screen.getByTestId("nexus-sidebar");
    expect(sidebar.textContent).not.toContain("Admin Console");
    expect(sidebar.textContent).not.toContain("Nexus-Root");
  });
});

// ── C1: 사이드바 접펼 토글 ────────────────────────────────────────────────────

describe("NexusShell — 사이드바 접펼(C1)", () => {
  it("기본은 펼침(data-collapsed=false)·전체 라벨 표시", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    const sidebar = screen.getByTestId("nexus-sidebar");
    expect(sidebar.getAttribute("data-collapsed")).toBe("false");
    // 펼침이면 전체 라벨이 보인다.
    expect(screen.getByText("Dashboard")).toBeDefined();
  });

  it("토글 클릭 → 접힘(data-collapsed=true)·약자 표시·재클릭 → 펼침", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div>x</div>
      </NexusShell>,
    );
    const sidebar = screen.getByTestId("nexus-sidebar");

    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(sidebar.getAttribute("data-collapsed")).toBe("true");
    // 접힘이면 전체 라벨 'Dashboard' 텍스트는 사라지고 약자 'D'가 보인다.
    expect(screen.queryByText("Dashboard")).toBeNull();
    expect(screen.getByText("D")).toBeDefined();

    fireEvent.click(screen.getByTestId("sidebar-toggle"));
    expect(sidebar.getAttribute("data-collapsed")).toBe("false");
    expect(screen.getByText("Dashboard")).toBeDefined();
  });

  it("접힘 상태에서도 내비 클릭이 동작한다(onNavSelect 호출)", () => {
    const onNavSelect = vi.fn();
    render(
      <NexusShell systemLog={<div />} onNavSelect={onNavSelect} sidebarActive="Dashboard">
        <div>x</div>
      </NexusShell>,
    );
    fireEvent.click(screen.getByTestId("sidebar-toggle")); // 접기.
    // 약자 'O'(Office) 클릭 → onNavSelect("Office").
    fireEvent.click(screen.getByText("O"));
    expect(onNavSelect).toHaveBeenCalledWith("Office");
  });
});
