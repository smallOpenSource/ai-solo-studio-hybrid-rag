"""US-AF4 — 페이즈B 에셋공장 (Art Director → Pixel Artist → Asset Generator → Validator).

ChatGPT 제안(에셋 공장 에이전트·회사 성장 시 자동 확장)을 **결정론 파이프라인 재사용**으로 구현.
- ★현 구현 = LPC 합성(build_us3) + 절차 타일/존(build_us4) + 품질게이트(gate)의 4-에이전트 오케스트레이션.
- ★자체생성(SD-piXL/디퓨전)은 **R&D 미확정**(별도 서버 부재·워크시트 미실증) → `generate_self_diffusion` 스텁.
- ★회사 성장 자동확장: `add_role`(신규 직원 스프라이트) · `add_zone`(회의실/서버실 등 신규 존 → 맵 재조립).

세탁/미학 차단은 자동 게이트가 아니라 HITL(사용자 검수)이 단일 방벽 — 본 팩토리는 *품질*(색수/식별)만 자동 보증
(승인본 ralplan-office-v2-assets.md §6-2). 실행: crawl env python.
"""

from __future__ import annotations

import os

import numpy as np

import build_us3 as b3
import build_us4 as b4
import gate
from kmeans import to_hex
from PIL import Image


def _load_role_arr(role: str):
    """배포된 16역할 스프라이트(roles_v2)를 RGBA 배열로 — gate.SPRITE_DIR(구 preview) 대신."""
    return np.array(
        Image.open(os.path.join(b4.CHAR_DIR, role + ".png")).convert("RGBA")
    )


# ── Art Director: 마스터 팔레트·스타일 스펙 소유 + 신규 의상색 배정 ──────────


class ArtDirector:
    """스타일 스펙(¾탑다운·32px·≤15색·selout)과 마스터 팔레트를 소유. 신규 역할 의상색을
    기존 역할과 ΔE2000 최대가 되도록 절제 후보에서 farthest-point 선택(클론군대 회피)."""

    def style_spec(self) -> dict:
        return {
            "cell": 32,
            "view": "three-quarter top-down",
            "max_colors": gate.MAX_COLORS,
            "delta_e_min": gate.DELTA_E_IDENTIFY,
            "selout": True,
        }

    def assign_costume(self, candidates: list[str], existing_roles: list[str]) -> str:
        """candidates(절제 hex 후보) 중 기존 역할 의상색과 min-ΔE가 최대인 색을 반환(결정론)."""
        ex_labs = [
            gate.srgb_to_lab(gate.costume_dominant(_load_role_arr(r)))
            for r in existing_roles
        ]
        best, best_score = candidates[0], -1.0
        for hex_c in candidates:
            lab = gate.srgb_to_lab(b3._hex_to_rgb(hex_c))
            score = min((gate.delta_e_ciede2000(lab, e) for e in ex_labs), default=99.0)
            if score > best_score:
                best, best_score = hex_c, score
        return best


# ── Pixel Artist: 스프라이트 생성 (A=LPC 합성 / B=자체생성 R&D 스텁) ──────────


class PixelArtist:
    def generate(self, costume_hex: str, hair_key: str, prop: str):
        """LPC 합성 idle 스프라이트(¾탑다운·소품 포함·≤15색). build_us3 재사용·결정론."""
        costume = b3._hex_to_rgb(costume_hex)
        hair = np.array(b3.HAIR_PALETTE[hair_key], dtype=float)
        idle = b3.build_role_idle(costume, hair)
        quant = b3.quantize_self(b3.crop_content(idle))
        return b3.quantize_self(b3.add_prop(quant, prop, costume))

    def generate_self_diffusion(self, *args, **kwargs):
        raise NotImplementedError(
            "SD-piXL/디퓨전 자체생성 = R&D 미확정(별도 서버 부재·64×64 정지만 검증·워크시트 미실증). "
            "ralplan-office-v2-assets.md §6-3·US-AF4 참조. 도구확정+사용자 재확인 후 활성."
        )


# ── Asset Generator: 결정론 배치 ──────────────────────────────────────────


class AssetGenerator:
    def deploy_role(self, role: str, sprite) -> str:
        os.makedirs(b4.CHAR_DIR, exist_ok=True)
        path = os.path.join(b4.CHAR_DIR, f"{role}.png")
        sprite.save(path)
        return path


# ── Dot-Art Validator: gate.py 품질게이트(색수≤15 + 기존과 식별 ΔE≥25) ─────


class DotArtValidator:
    def validate(self, role: str, sprite, existing_roles: list[str]) -> dict:
        arr = np.array(sprite)
        ncol = len(gate.unique_opaque_colors(arr))
        clab = gate.srgb_to_lab(gate.costume_dominant(arr))
        min_de = min(
            (
                gate.delta_e_ciede2000(
                    clab, gate.srgb_to_lab(gate.costume_dominant(_load_role_arr(r)))
                )
                for r in existing_roles
            ),
            default=99.0,
        )
        return {
            "role": role,
            "n_colors": int(ncol),
            "colors_ok": bool(ncol <= gate.MAX_COLORS),
            "costume_hex": to_hex(gate.costume_dominant(arr)),
            "min_delta_e": round(float(min_de), 2),
            "identity_ok": bool(min_de >= gate.DELTA_E_IDENTIFY),
        }


# ── 팩토리 오케스트레이션 (회사 성장 자동확장) ─────────────────────────────


def add_role(
    role: str,
    costume_hex: str,
    hair_key: str,
    prop: str,
    existing_roles: list[str] | None = None,
    deploy: bool = True,
) -> dict:
    """신규 직원 1명: PixelArtist 생성 → Validator 검증 → (Asset Generator 배치). 4-에이전트 파이프라인."""
    existing = existing_roles if existing_roles is not None else list(b3.ROLES)
    sprite = PixelArtist().generate(costume_hex, hair_key, prop)
    v = DotArtValidator().validate(role, sprite, existing)
    # ★배치 게이트 = 색수(품질) AND 식별(기존과 ΔE≥25). 식별 미달색은 클론군대 회피 정책상 자동 차단(리뷰 MED-2).
    can_deploy = bool(deploy and v["colors_ok"] and v["identity_ok"])
    if can_deploy:
        v["path"] = AssetGenerator().deploy_role(role, sprite)
    v["deployed"] = can_deploy
    if deploy and not can_deploy:
        v["deploy_blocked"] = "colors" if not v["colors_ok"] else "identity"
    return v


def add_zone(
    zone_id: str,
    zone_name: str,
    roles: list[str],
    rug_key: str,
    height: int = 6,
    rebuild: bool = True,
) -> dict:
    """신규 존 자동확장 — ★단일룸 전환으로 후속 보류(재설계 §12 M1).

    build_us4가 세로밴드 누적(BANDS·BAND_RUG·가변 map_h)에서 단일 개방형 룸
    (고정 MAP_H·리터럴 SEATS·ZONES 7팀구역)으로 재구성되어, "밴드 append→맵 재조립"
    전제가 더는 성립하지 않는다. 신규 존(회의실/서버실 등) 자동확장은 24역할 확장 시점에
    좌석/러그 좌표 재설계와 함께 다시 설계한다(PoC 범위 밖).
    """
    raise NotImplementedError(
        "단일룸 전환: add_zone 후속 보류(24역할 확장 시 재설계). "
        "build_us4가 세로밴드 누적→단일 개방형 룸(고정 MAP_H·리터럴 SEATS)으로 "
        "재구성되어 밴드 append 전제 소멸. ralplan-office-redesign.md §12 M1 참조."
    )
