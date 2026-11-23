/**
 * control_harness_entry.tsx — play_control_check.mjs 용 마운트 하네스(WA 셸 없이).
 *
 * 이 엔트리는 §9.4 4-패널(ControlPanels)을 빈 페이지에 마운트한다. orch RO API 응답은
 * play_control_check.mjs 가 window.fetch 를 스텁해 mock 한다(네트워크 0). 하네스는
 * window.__renderControl(opts) 를 노출해 테스트가 재렌더(폴링 없이 1회 조회·pollMs=0)를 트리거한다.
 *
 * ★렌더-트랩 차단: mock 응답값을 바꾸고 __renderControl 을 다시 부르면 배지/큐가 바뀌어야 한다.
 */

import { createRoot, type Root } from "react-dom/client";
import { ControlPanels } from "../src/control/ControlPanels";
import { type TextMessage } from "../src/types/events";

interface RenderOpts {
  chatMessages?: TextMessage[];
  waMapUrl?: string;
}

declare global {
  interface Window {
    __renderControl: (opts?: RenderOpts) => Promise<void>;
    __controlReady: boolean;
  }
}

let root: Root | null = null;

function freshMount(): HTMLElement {
  // 매 렌더마다 마운트 노드를 새로 만든다 — fresh mount 라야 useControlData 의
  // useEffect(1회 조회·pollMs=0)가 다시 돌아 **현재 mock** 을 재조회한다(렌더-트랩 단언의
  // 시나리오 B 가 새 값을 읽도록). 같은 root 재렌더는 effect 미실행 → 옛 값 잔존.
  const old = document.getElementById("control-root");
  if (old) old.remove();
  const el = document.createElement("div");
  el.id = "control-root";
  document.body.appendChild(el);
  return el;
}

// pollMs=0 → 마운트 시 1회만 조회(테스트 결정론). 호출마다 새 마운트로 현재 mock 을 재조회.
window.__renderControl = async (opts: RenderOpts = {}) => {
  if (root !== null) {
    root.unmount();
    root = null;
  }
  root = createRoot(freshMount());
  root.render(
    <ControlPanels
      apiBase=""
      pollMs={0}
      waMapUrl={opts.waMapUrl}
      chatMessages={opts.chatMessages ?? []}
    />,
  );
  // RO fetch(Promise.allSettled) + setState 가 커밋될 시간을 준다(폴링 없음).
  await new Promise((r) => setTimeout(r, 200));
};

window.__controlReady = true;
