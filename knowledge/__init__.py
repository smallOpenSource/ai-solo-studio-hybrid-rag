"""one_man_corp 지식 레이어 패키지 (Phase 2).

직접 작성한 위키 문서를 의미로 검색하는 '한국어 지식 검색 한 벌'.
점진 도입: dense 의미검색 + 리랭커만(sparse·Contextual·크롤링은 후속).
임베딩·리랭크는 core.bge_client(BGEClient)를 재사용한다(복사 금지).

모듈(US-001 이후 채워짐): chunk·store·search·ingest·eval.
"""
