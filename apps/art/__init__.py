"""apps.art — 미디어 산출물 트리 (P7·§4.2/§28.3).

``apps/art/workflows/`` 는 스펙 §28.3 의 ComfyUI 워크플로 JSON 저장 경로다(스펙 리터럴). 루트
``workflows/`` (python-agent 패키지)와 **물리적으로 분리**하기 위해 별도 트리로 둔다(T2).

OD-P7=(B) 정본 하에서 워크플로 JSON **추출은 면제**되므로(``SR/renewal/COMFY_API_SHAPE.md``)
``workflows/`` 디렉토리는 비어 있다(``.gitkeep`` 만). OD-P7(A)로 재개 시 native dev-mode 에서
``{qwen_image,wan_video,ltx_video}.json`` 을 실 추출해 채운다(하드코딩 합성 금지).
"""
