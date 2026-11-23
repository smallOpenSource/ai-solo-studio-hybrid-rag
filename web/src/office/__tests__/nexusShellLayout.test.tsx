/**
 * NexusShell 레이아웃 체인 단언 (US-1e · B-2/S-1) — Phaser 0높이 붕괴 방지.
 *
 * 실패 모드(Architect ★HIGH): NexusShell이 PhaserGame 슬롯을 감쌀 때 중간 컨테이너가
 *   minHeight:0을 전달 안 하면 height:100% 체인이 0으로 해소 → ResizeObserver height>0
 *   가드 미통과 → game.scale.resize 미호출 → 맵 死. 이 테스트가 그 체인을 자동 단언한다.
 *   (실 0높이 붕괴는 실브라우저에서만 발현 → 이 단언은 회귀 자동방지·실 검수는 P1 HITL.)
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { NexusShell } from "../NexusShell";

describe("NexusShell — Phaser 슬롯 minHeight:0 체인(US-1e B-2/S-1)", () => {
  it("children 슬롯과 main 조상이 minHeight:0을 전달한다(0높이 붕괴 방지)", () => {
    render(
      <NexusShell systemLog={<div />}>
        <div data-testid="slot-child" />
      </NexusShell>,
    );
    const child = screen.getByTestId("slot-child");

    // 슬롯 컨테이너(childrenSlot): flex:1 + minHeight:0
    // ★React는 0값을 "0"으로 렌더(value===0 특례·"0px" 아님) → 둘 다 허용.
    const slot = child.parentElement as HTMLElement;
    expect(["0", "0px"]).toContain(slot.style.minHeight);

    // 그 조상 <main>: flex:1 + minHeight:0 (체인 끊김 없음)
    const main = slot.parentElement as HTMLElement;
    expect(main.tagName.toLowerCase()).toBe("main");
    expect(["0", "0px"]).toContain(main.style.minHeight);
  });
});
