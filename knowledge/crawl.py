"""웹 URL 크롤 → 지식 ingest (Phase 9, synthesis/O3').

웹 URL 리스트를 크롤(crawl4ai)해 마크다운을 뽑고, 기존 knowledge 공개 API
(`chunk_markdown(md, source=url)` → `store.upsert(chunks)`)를 **직접 호출**해
같은 Qdrant 컬렉션에 ingest한다. 검색 결과의 source가 raw URL이 되도록 한다.

설계 결정(계획 rev2 consensus APPROVED — synthesis 채택):
- ★기존 `knowledge/{ingest,chunk,store,search,service}.py` **0수정**. 확장은 이
  신규 파일이 **공개 API만 호출**하는 방식으로만(소스 0수정 불변식).
- `ingest()`를 경유하지 *않으므로* `chunk_markdown(md, source=url)`로 source를
  자유 지정 → 검색 source==raw URL이 자명(AC-5a). `ingest.py:65`의 무조건적
  `_relative_source(md_path)` seam을 우회한다.
- 직접 `store.upsert`은 신규 우회가 아니라 기존 선례다(`run_eval.py:164`). upsert
  내부가 `ensure_collection`(store.py:160)+dim검증(171-174)을 수행하므로 직접
  호출이 잃는 것 0(AC-8).
- 멱등: `store._point_id(url, chunk_id)`(store.py:228) 결정론 ID → 같은 URL
  재크롤은 덮어쓰기(중복 0·AC-7).

비결정성의 유일한 출처는 네트워크 페치(crawler)다. crawler를 주입 추상화로
격리하면(기본 None·미주입 명시 에러·AC-19), FakeEmbedder와 결합해 전체
파이프라인이 mock 하에 결정론이 된다 → 결정론 게이트(`run_tests.sh knowledge
-m 'not slow'`)가 실 네트워크·실 브라우저 0.

★crawl4ai는 **모듈-top import 금지**(NIT-1) — 실 어댑터(`Crawl4aiCrawler`) 내부
지연 import만 한다. 선례 `office/tools.py:225`(지연 import — env 격리). 이렇게
하면 `crawl()`/`Crawler`/`CrawlFetchError`/`_slug`는 crawl4ai 무관(import 0)이라,
crawl4ai 미설치 env에서도 import·테스트 가능하다(결정론 게이트 보호의 본질).
정상 `pip install -e ".[knowledge]"`는 crawl4ai를 포함하지만, 지연 import가
미설치/부분설치 env(CI·게이트)를 ImportError로부터 막는 핵심 방어다.

★라이브(실 네트워크·실 브라우저·실 LLM 임베딩)는 **사용자 동석 세션 전용**.
에이전트측은 mock crawler 결정론 게이트까지.

CLI:
    python -m knowledge.crawl <url...> [--staging <dir>]
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlparse

from qdrant_client import models

from knowledge.chunk import chunk_markdown

if TYPE_CHECKING:
    from knowledge.store import KnowledgeStore

# 실 crawler 어댑터 페치 타임아웃(밀리초·명시값 — AC-12 무한 대기 금지).
# crawl4ai 자체 페치 timeout은 lint_http_timeout.sh(:20) 정적 린트 비대상이므로
# 여기 명시값이 코드리뷰 게이트 대상이다(무한 대기 회피).
_PAGE_TIMEOUT_MS = 30_000
_TOTAL_TIMEOUT_MS = 60_000

# fit_markdown 폴백 임계(Phase 9.1 AC-Q3·코드 상수 고정 — backfire 차단).
# fit가 "충분"하려면 비어있지 않고 raw 대비 max(_MIN_ABS, raw*_MIN_RATIO) 이상이어야
# 한다. §0.1 실측의 45%(31k/69k) 모범 케이스는 둘 다 통과(fit 유지)·빈/명백 과소만
# raw 폴백. 50%로 잡으면 45% 정상을 raw로 오판하므로 10%/200자로 고정한다.
_MIN_RATIO = 0.10
_MIN_ABS = 200


class Crawler(Protocol):
    """크롤러 계약 — URL을 받아 마크다운 문자열을 동기로 돌려준다.

    테스트는 이 계약을 만족하는 결정론 FakeCrawler를 주입한다(네트워크 0).
    실 구현은 `Crawl4aiCrawler`(crawl4ai 지연 import).
    """

    def fetch(self, url: str) -> str: ...


class CrawlFetchError(Exception):
    """단일 URL 페치 실패 — 그 URL만 스킵하라는 신호(AC-14 부분 실패).

    네트워크는 본질적으로 불안정하므로, N개 중 일부 실패는 예외가 아니라 정상
    운영 상태다(계획 Principle 3). crawler 구현이 한 URL 페치에 실패하면 이
    예외를 던진다 → `crawl()`이 그 URL만 건너뛰고 나머지를 계속한다.
    """


def _select_markdown(md_obj) -> str:
    """`MarkdownGenerationResult`(또는 stub)에서 fit/raw 마크다운을 결정론 선택한다.

    Phase 9.1 AC-Q3 — 순수 함수(crawl4ai import 0·결정론 게이트서 단위테스트).
    `fit_markdown`(PruningContentFilter 적용·보일러플레이트 제거)을 우선하되,
    PruningContentFilter가 sparse 페이지에서 과도제거해 빈/과소가 되면 raw로
    폴백한다. fit 충분 판정은 **코드 상수(`_MIN_RATIO`·`_MIN_ABS`)로 고정**해
    backfire(45% 모범 케이스를 raw로 오판)를 구조적으로 막는다.

    ★fix3(반환 strip): 최종 선택값이 공백("   "·"\n\n")뿐이면 빈 문자열을 돌려준다
    — 공백 raw 폴백도 빈값 취급해 추출부 가드(`if not markdown: raise
    CrawlFetchError`)가 잡게 한다(라이브 N=0 상류 차단). 비-공백 짧은 본문은 보존한다.
    """
    fit = getattr(md_obj, "fit_markdown", None) or ""
    raw = getattr(md_obj, "raw_markdown", None) or (
        md_obj if isinstance(md_obj, str) else ""
    )
    selected = (
        fit
        if (fit.strip() and len(fit) >= max(_MIN_ABS, int(len(raw) * _MIN_RATIO)))
        else raw
    )
    return selected if selected.strip() else ""  # ★fix3: 공백→빈 문자열


def crawl(
    urls: list[str],
    store: KnowledgeStore,
    *,
    crawler: Crawler | None = None,
    staging_dir: str | Path | None = None,
) -> int:
    """URL 리스트를 크롤해 청킹→업서트하고 업서트한 청크 수를 돌려준다.

    각 URL을 순차(동시성 1)로 페치해 마크다운을 얻고, `chunk_markdown(md,
    source=url)`로 청크를 만든 뒤(source==raw URL·AC-5a), 페치 시각을
    `meta["crawled_at"]`에 박아 `store.upsert`로 넣는다(Driver #3).

    멱등: 같은 URL을 2회 크롤해도 동일 상태가 된다(`store._point_id(url,
    chunk_id)` 결정론 ID — 재업서트가 덮어쓰기·AC-7).

    부분 실패(AC-14): 어떤 URL 페치가 `CrawlFetchError`면 그 URL만 스킵하고
    (실패 URL을 stderr 로그·NIT-4 silent skip 금지) 나머지를 계속한다. 반환
    total은 성공분 청크 수만 합산한다.

    Args:
        urls: 크롤할 URL 목록. 호출자가 순서/중복을 통제한다(예: `sorted(urls)`).
        store: 업서트 대상 KnowledgeStore(임베더는 store가 보유·주입).
        crawler: URL→마크다운 크롤러. **기본 None·미주입 시 ValueError**(실
            AsyncWebCrawler 지연생성 절대 금지·AC-19). 결정론 게이트는
            FakeCrawler를 주입한다.
        staging_dir: 옵트인 디버그 스냅샷 디렉토리. None이면(기본) 미작성.
            지정 시 각 URL의 마크다운을 `<staging_dir>/<slug>.md`로 쓴다 —
            이는 **순수 부산물**이며 어떤 ingest의 입력도 아니다(O3 모순 회피).

    Returns:
        업서트한 청크(포인트) 총수(성공분만). URL이 0개거나 전부 실패하면 0.

    Raises:
        ValueError: crawler 미주입(None)일 때(AC-19).
    """
    if crawler is None:
        raise ValueError(
            "crawl()에 crawler를 주입해야 합니다(기본 None·미주입 금지). "
            "결정론 테스트는 FakeCrawler를, 라이브는 Crawl4aiCrawler를 주입하세요 "
            "— 실 AsyncWebCrawler 지연생성은 결정론 게이트를 오염시킵니다(AC-19)."
        )

    staging_path: Path | None = None
    if staging_dir is not None:
        staging_path = Path(staging_dir)
        staging_path.mkdir(parents=True, exist_ok=True)

    total = 0
    for url in urls:  # 동시성 1 순차(임베디드 qdrant 락·결정론)
        try:
            md = crawler.fetch(url)
        except CrawlFetchError as exc:
            # AC-14: 이 URL만 스킵·나머지 계속·전체 크래시 0. NIT-4: 실패 URL을
            # stderr에 로그(silent skip 금지 — discord-adapter hang 전례 정신).
            print(f"[crawl] 페치 실패 스킵: {url} — {exc}", file=sys.stderr)
            continue

        # ★fix1(AC-Q9·CRITICAL): crawl() 레벨 공백 가드. 어댑터 가드
        # (`if not markdown`·_fetch_async 전용)는 mock/게이트 경로(FakeCrawler는
        # _fetch_async 미경유 str 직접 반환)를 못 막고 공백("   ")은 truthy라
        # 통과시킨다. 여기서 chunk_markdown 호출 **전** 공백을 차단해야 전 경로
        # (mock+라이브)에서 N≥1이 보장된다 — 공백 md는 chunk_markdown('   ')=0청크
        # → 아래 tail-delete `Range(gte=0)`=source 전손을 유발하기 때문이다.
        # 스킵된 URL은 기존 청크 보존(=현 upsert-only 동형·갱신만 안 함).
        if not md.strip():
            print(f"[crawl] 빈/공백 마크다운 스킵: {url}", file=sys.stderr)
            continue

        if staging_path is not None:
            # 옵트인 디버그 스냅샷(ingest 입력 아님·순수 부산물). slug 충돌은
            # 파이프라인과 무관(source/멱등은 URL 기반 _point_id로 직결).
            (staging_path / f"{_slug(url)}.md").write_text(md, encoding="utf-8")

        chunks = chunk_markdown(md, source=url)  # AC-5a: source==url 자명
        crawled_at = datetime.now(
            timezone.utc
        ).isoformat()  # Driver #3 정확한 페치 시각
        for chunk in chunks:
            chunk.meta["crawled_at"] = crawled_at
        # ★안전순서(AC-Q8·CRITICAL-2): upsert(0..N-1 덮어씀) **먼저** → 그 다음
        # 꼬리(chunk_id>=N) 삭제. delete-first(upsert 전 source 전체 삭제)는
        # delete 후 크래시 시 URL 전손이라 절대 금지. upsert가 먼저이므로 어느
        # 순간에도 URL 청크 0 창이 없다(크래시 시 옛 꼬리 잔존=현 동작·악화 아님).
        n = store.upsert(chunks)  # 멱등·ensure_collection 내부(store.py:160)
        if len(chunks) >= 1:  # ★fix2(N≥1 가드): N=0이면 Range(gte=0)=전손 → 스킵
            _replace_source_tail(store, url, len(chunks))
        total += n
    return total


def _slug(url: str) -> str:
    """URL을 결정론 파일명 slug로(staging 스냅샷용).

    호스트 + URL sha1 일부로 만든다. staging은 순수 부산물(ingest 입력 아님)이라
    slug 충돌이 나도 파이프라인 무해 — source/멱등은 URL 기반이지 slug 기반이 아님.
    """
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    host = urlparse(url).hostname or "host"
    safe_host = "".join(c if c.isalnum() or c in "-." else "_" for c in host)
    return f"{safe_host}_{digest}"


def _replace_source_tail(store: KnowledgeStore, url: str, new_count: int) -> None:
    """upsert 후 호출 — source==url 중 chunk_id>=new_count 꼬리만 삭제한다(AC-Q8).

    fit_markdown은 raw보다 청크 수가 적으므로(노이즈 제거), 재크롤하면 0..N-1은
    `_point_id` 결정론으로 덮어쓰이지만 이전 N..M-1 청크가 **orphan**으로 잔류한다.
    이 stale 꼬리(노이즈 그 자체)는 `search_dense`의 top_n=20 전역 회수(store.py:204·
    query_filter 없음)에서 교차-source eviction을 일으킨다 → 안전순서 tail-delete로
    선결 해결한다(orphan 0·손실창 0).

    ★`store._client`에 reach-in한다(run_eval.py 패턴 선례·store.py 파일 무변). point/
    range delete는 코드베이스 첫 표면이나 §0.2 라이브 실측(qdrant 1.18.0 `:memory:`
    MatchValue+Range delete `status=completed`)이 실현·결정론 검증 가능을 확정했다.

    ★호출 측이 N≥1을 보장한다(`Range(gte=0)`=source 전손 차단). delete 실패는 upsert가
    이미 성공했으므로 새 fit 청크는 검색되며, stale 꼬리 잔존은 현 동작이라 악화가
    아니다 → 로그 후 진행(CrawlFetchError로 변환하지 않음).
    """
    try:
        store._client.delete(
            collection_name=store.collection,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="source", match=models.MatchValue(value=url)
                    ),
                    models.FieldCondition(
                        key="chunk_id", range=models.Range(gte=new_count)
                    ),
                ]
            ),
        )
    except (
        Exception
    ) as exc:  # delete 실패=로그 후 진행(upsert 이미 성공·꼬리 잔존=현 동작)
        print(
            f"[crawl] 꼬리 삭제 실패(무시·진행): {url} — {exc}",
            file=sys.stderr,
        )


class Crawl4aiCrawler:
    """실 crawl4ai 크롤러 어댑터 — `AsyncWebCrawler`로 URL→마크다운(라이브 전용).

    ★crawl4ai는 **함수 내부 지연 import**(NIT-1·모듈-top 금지). 선례
    `office/tools.py:225`. → `crawl.py` 모듈 자체는 crawl4ai 없이 import 가능.

    ★페치 타임아웃 명시값(AC-12·무한 대기 금지). ★`asyncio.run` 동기 경계는 이
    어댑터/CLI에서만 — 실행중 이벤트루프에서 호출 시 명시 에러(AC-15·hang 0).

    실 브라우저·실 네트워크를 쓰므로 **사용자 동석 세션 전용**. 결정론 게이트는
    이 클래스를 쓰지 않고 FakeCrawler를 주입한다.
    """

    def fetch(self, url: str) -> str:
        """URL을 crawl4ai로 페치해 마크다운을 돌려준다(동기 경계).

        Raises:
            RuntimeError: 실행중 이벤트루프에서 호출되면(AC-15 — asyncio.run은
                running loop에서 불가). `asyncio.run` 경계는 동기 컨텍스트 전용.
            CrawlFetchError: 페치/추출 실패 시(AC-14 — crawl()이 스킵 신호로 사용).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # 정상: 실행중 루프 없음(동기 컨텍스트) → asyncio.run 진행
        else:
            raise RuntimeError(
                "Crawl4aiCrawler.fetch는 실행중 이벤트루프에서 호출할 수 없습니다"
                "(asyncio.run 경계 충돌·AC-15). 동기 컨텍스트나 CLI에서 호출하세요."
            )
        try:
            return asyncio.run(self._fetch_async(url))
        except CrawlFetchError:
            raise
        except Exception as exc:  # 네트워크/브라우저/추출 실패 → 부분실패 신호로 변환
            raise CrawlFetchError(f"crawl4ai 페치 실패: {url} — {exc}") from exc

    async def _fetch_async(self, url: str) -> str:
        """crawl4ai AsyncWebCrawler로 url→markdown(page/total timeout 명시·AC-12).

        ★타임아웃 2중(AC-12): page_timeout은 crawl4ai `CrawlerRunConfig`(문서화된
        정규 경로)로, **total은 `asyncio.wait_for`로 호출자 측에서 강제**한다 —
        total 가드를 crawl4ai 파라미터에 의존하지 않으므로 어댑터 API 버전과
        무관하게 무한 대기를 막는다(code-review MAJOR#1 대응). wait_for 만료는
        asyncio.TimeoutError → fetch()의 except가 CrawlFetchError로 변환 → 그 URL
        스킵(hang 0).

        ★콘텐츠 품질(Phase 9.1): `DefaultMarkdownGenerator(content_filter=
        PruningContentFilter())` + `excluded_tags`(nav/footer/header/aside/form) +
        `exclude_external_links`로 보일러플레이트를 제거한 `fit_markdown`을 만든다
        (§0.1 실측 위키 54% 노이즈 제거). fit/raw 선택은 순수 함수
        `_select_markdown`(임계 상수 고정·과도제거 폴백·★반환 strip). `result.success`가
        False면(예외 대신 실패 플래그) 명시 실패로 변환한다.

        ★crawl4ai 미설치라 에이전트측에서 import-time 검증 불가 → ★사용자 라이브
        첫 발사 전 실 API 1회 대조 필요(arun config 시그니처·markdown 형태·필터 API).
        """
        from crawl4ai import (  # ★지연 import — 모듈-top 금지(NIT-1)
            AsyncWebCrawler,
            CrawlerRunConfig,
            DefaultMarkdownGenerator,
            PruningContentFilter,
        )

        config = CrawlerRunConfig(  # ms(Playwright 계열)·fit_markdown 생성(Phase 9.1)
            page_timeout=_PAGE_TIMEOUT_MS,
            markdown_generator=DefaultMarkdownGenerator(
                content_filter=PruningContentFilter()
            ),
            excluded_tags=["nav", "footer", "header", "aside", "form"],
            exclude_external_links=True,
        )
        async with AsyncWebCrawler() as browser_crawler:
            # total 가드는 API 무관하게 wait_for로 강제(AC-12 무한대기 금지).
            result = await asyncio.wait_for(
                browser_crawler.arun(url=url, config=config),
                timeout=_TOTAL_TIMEOUT_MS / 1000,
            )
        if not getattr(result, "success", True):
            raise CrawlFetchError(
                f"crawl4ai 실패: {url} — {getattr(result, 'error_message', '?')}"
            )
        # fit 우선·과도제거 폴백·공백 차단(순수 함수·결정론 게이트서 단위테스트).
        markdown = _select_markdown(getattr(result, "markdown", None))
        if not markdown:  # ★빈/None/공백 가드 보존(MAJOR-3·fix3 반환 strip와 결합)
            raise CrawlFetchError(f"crawl4ai 마크다운 없음/빈값: {url}")
        return markdown


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점 — `python -m knowledge.crawl <url...> [--staging <dir>]`.

    URL 리스트를 기본 store(endpoint.yaml knowledge — Qdrant 실 path)에
    크롤·ingest하고 업서트한 청크 수를 출력한다(크롤+upsert 한 프로세스).

    ★라이브: 실 brave 브라우저·실 네트워크·실 LLM 임베딩을 부른다 → 사용자
    동석 세션 전용. ★H-13 락 경계: knowledge 서비스 stop 중 실행
    (serve_knowledge.sh:18 — 임베디드 qdrant 동시 쓰기 회피).

    오프라인 테스트는 이 main()을 부르지 않고 crawl(urls, store, crawler=Fake)에
    mock을 주입해 검증한다(실 네트워크 0).
    """
    args = sys.argv[1:] if argv is None else argv
    urls: list[str] = []
    staging_dir: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--staging":
            if i + 1 >= len(args):
                print("사용법: --staging 다음에 디렉토리가 필요합니다", file=sys.stderr)
                return 2
            staging_dir = args[i + 1]
            i += 2
        else:
            urls.append(arg)
            i += 1

    if not urls:
        print(
            "사용법: python -m knowledge.crawl <url...> [--staging <dir>]",
            file=sys.stderr,
        )
        return 2

    from knowledge.store import KnowledgeStore

    store = KnowledgeStore()
    crawler = Crawl4aiCrawler()  # asyncio.run 경계는 어댑터 내부
    count = crawl(urls, store, crawler=crawler, staging_dir=staging_dir)
    print(f"크롤 완료: {count}개 청크 업서트 (컬렉션 '{store.collection}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
