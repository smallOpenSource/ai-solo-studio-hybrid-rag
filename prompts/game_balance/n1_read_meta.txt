이전 단계(게임 설계)가 기록한 game/data/meta.json 파일을 읽어라. game_data_read 도구를 호출하되 path는 "data/meta.json", required는 true로 한다.

meta.json은 밸런스 수치 설계의 입력 계약이다 — 파일이 없으면(이전 설계 단계 미완) 진행할 수 없다(required=true가 명시 실패를 일으킨다). 읽어온 theme·coreLoop·win·lose를 다음 단계의 수치 설계 근거로 삼아라.
