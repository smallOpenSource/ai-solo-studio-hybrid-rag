#!/usr/bin/env python3
"""Render config/app.env (단일 SSoT) from SR/renewal/app.env.example + existing secrets.

신규설계 §23.4/§23.9: app.env.example 템플릿을 정본으로, 기존 .env 시크릿 / config/endpoint.yaml
파라미터 / state/secrets/comfy.env 게이트웨이 / config/ports.json 포트를 오버레이.
★시크릿 값은 stdout에 절대 출력하지 않는다(키 *이름*과 카운트만 보고). 결과 chmod 600.

Path A: /srv/office·/srv/podman 리터럴을 repo 루트·state/podman/data로 retarget.
"""

import json
import os
import re
import secrets as _secrets

ROOT = "/opt/studio"
TEMPLATE = f"{ROOT}/SR/renewal/app.env.example"
OUT = f"{ROOT}/config/app.env"


def parse_env_file(path):
    """KEY=VALUE 파서 (인라인 주석/따옴표 정리·source 안전). 값은 dict로만 보관(미출력)."""
    d = {}
    if not os.path.exists(path):
        return d
    for line in open(path, errors="replace"):
        line = line.rstrip("\n")
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
            continue
        v = v.strip()
        # strip surrounding quotes
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        else:
            # strip trailing inline comment only when value isn't quoted and has ' #'
            v = re.split(r"\s+#", v, 1)[0].strip()
        d[k] = v
    return d


def load_yaml_flat(path):
    """endpoint.yaml에서 스칼라 top-level/1-depth 키만 best-effort(미설치 yaml 환경 대비 정규식)."""
    out = {}
    if not os.path.exists(path):
        return out
    try:
        import yaml

        data = yaml.safe_load(open(path)) or {}

        def walk(prefix, obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    walk(f"{prefix}{k}.", v)
            elif isinstance(obj, (str, int, float)):
                out[prefix[:-1]] = str(obj)

        walk("", data)
    except Exception:
        pass
    return out


def main():
    env = parse_env_file(f"{ROOT}/.env")
    comfy = parse_env_file(f"{ROOT}/state/secrets/comfy.env")
    ep = load_yaml_flat(f"{ROOT}/config/endpoint.yaml")
    ports = json.load(open(f"{ROOT}/config/ports.json")).get("ports", {})

    # alias: app.env.example key  <-  source key
    alias = {
        "REDIS_SENTINELS": env.get("REDIS_SENTINEL_HOSTS"),
        "REDIS_MASTER_NAME": env.get("REDIS_MASTER_NAME"),
        "REDIS_PASSWORD": env.get("REDIS_PASSWORD"),
        "DISCORD_TOKEN": env.get("DISCORD_TOKEN"),
        "DISCORD_GUILD_ID": env.get("DISCORD_GUILD_ID"),
        "OWNER_USER_ID": env.get("OWNER_USER_ID") or env.get("DISCORD_OWNER_ID"),
        "COMFY_BASE": comfy.get("COMFY_BASE"),
        "LLM_GATEWAY": env.get("LLM_GATEWAY"),
        "RO_TOKEN": env.get("RO_TOKEN") or env.get("API_RO_TOKEN"),
        "RW_TOKEN": env.get("RW_TOKEN") or env.get("API_RW_TOKEN"),
    }
    # Path A 경로 override (리터럴 /srv/office·/srv/podman = 비-writable)
    pathA = {
        "OFFICE_ROOT": ROOT,
        "PODMAN_DATA": f"{ROOT}/state/podman/data",
        "VAULT_DIR": f"{ROOT}/vault",
        "STATE_DIR": f"{ROOT}/state",
        "ORCH_DB": f"{ROOT}/state/orch.sqlite",
        "CONDA_BASE": "/opt/conda",
    }
    port_vals = {k: str(v) for k, v in ports.items()}

    def resolve(key, template_val):
        if key in alias and alias[key]:
            return alias[key], "secret/env"
        if key in pathA:
            return pathA[key], "pathA"
        if key in port_vals:
            return port_vals[key], "ports.json"
        if key in ("RO_TOKEN", "RW_TOKEN") and template_val.strip() == "__AUTO__":
            return _secrets.token_urlsafe(32), "generated"
        if key in ep and ("__" in template_val or template_val.strip() == ""):
            return ep[key], "endpoint.yaml"
        return template_val, "template"

    out_lines, filled, placeholders = [], [], []
    for raw in open(TEMPLATE, errors="replace"):
        line = raw.rstrip("\n")
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            out_lines.append(line)
            continue
        key, tval = m.group(1), m.group(2)
        # 템플릿 기본값에서 인라인 주석 제거(값 라인 인라인 # 금지 — systemd EnvironmentFile은
        # 인라인 주석 미제거라 값 오염. 문서 주석은 app.env.example 템플릿에 보존).
        tval_clean = re.split(r"\s+#", tval, 1)[0]
        newval, src = resolve(key, tval_clean)
        if src != "template":
            filled.append(f"{key}<-{src}")
        if "__" in str(newval):  # unresolved [필수] placeholder
            placeholders.append(key)
        out_lines.append(f"{key}={newval}")

    # media gateway 섹션 append (example에 없는 키)
    media = ["", "# == [K] 미디어 게이트웨이 (Owner 제공·P7·comfy.env 통합) =="]
    for k in (
        "COMFY_API_KEY",
        "COMFY_MODULE",
        "COMFY_IMAGE_MODEL",
        "COMFY_IMAGE_EDIT_MODEL",
        "COMFY_VIDEO_MODEL",
        "COMFY_VIDEO_AUDIO_MODEL",
        "COMFY_EP_IMAGE",
        "COMFY_EP_IMAGE_EDIT",
        "COMFY_EP_VIDEO",
        "COMFY_QUALITY_NOTE",
    ):
        if comfy.get(k):
            media.append(f"{k}={comfy[k]}")
            filled.append(f"{k}<-comfy.env")
    out_lines += media

    # WS4 라이브 데모/더미 게이트(discord 재방송 인시던트 2026-06-21·정책 상수·비밀 아님):
    # 라이브 서비스가 더미 의존 워크플로를 enqueue/시드하지 않게 한다(프로덕션=실데이터만).
    out_lines += [
        "",
        "# == [M] 라이브 데모/더미 게이트 (WS4) ==",
        "OFFICE_DISABLED_WORKFLOWS=crm_notify,smoke_email",
        "OFFICE_BUSINESS_SEED=0",
    ]
    filled.append("OFFICE_DISABLED_WORKFLOWS<-policy")
    filled.append("OFFICE_BUSINESS_SEED<-policy")

    # ★app.env ⊇ .env (superset): 렌더에 없는 모든 .env 키를 verbatim 이월 → EnvironmentFile
    # 전환 시 서비스 의존 .env 변수 누락 0(안전 마이그레이션). 값은 .env 그대로(시크릿 미출력).
    emitted = {
        m.group(1)
        for m in (re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", ln) for ln in out_lines)
        if m
    }
    carry = [k for k in env if k not in emitted]
    if carry:
        out_lines += ["", "# == [L+] .env 이월 (superset · EnvironmentFile 안전) =="]
        for k in carry:
            out_lines.append(f"{k}={env[k]}")
            filled.append(f"{k}<-.env-carry")

    with open(OUT, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    os.chmod(OUT, 0o600)

    # ★보고: 키 이름·카운트만 (값 0)
    print(f"WROTE {OUT} (chmod 600)")
    print(f"filled {len(filled)} keys from real sources (names only):")
    for x in sorted(set(filled)):
        print(f"  {x}")
    print(
        f"remaining [필수] placeholders ({len(set(placeholders))}): {sorted(set(placeholders))}"
    )


if __name__ == "__main__":
    main()
