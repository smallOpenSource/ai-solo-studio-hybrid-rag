"""Phase 9 — 웹수집 크롤(crawl) 단위·통합 테스트 (synthesis/O3').

오프라인 전용 (인메모리 Qdrant `:memory:` + bge mock + FakeCrawler). 실 네트워크·
실 브라우저·실 LLM 접촉 0 → 결정론 게이트(`run_tests.sh knowledge -m 'not slow'`)
에서 실제로 수집·실행된다. ★이 파일은 crawl4ai를 **top-import 하지 않는다**
(FakeCrawler만 씀) → crawl4ai 미설치 게이트 env에서 통째로 수집·실행돼야 한다.

검증(계획 rev2 AC):
- AC-5a: 검색 source == raw URL(`chunk_markdown(md, source=url)` passthrough).
- AC-7: 같은 URL 재크롤 멱등(포인트/검색수 불변·`_point_id` 결정론).
- AC-8: 컬렉션 미존재→첫 crawl이 ensure_collection 통해 생성(무손실).
- AC-14: 부분 실패(CrawlFetchError) 시 그 URL만 스킵·나머지 계속·실패 URL stderr 로그.
- AC-15: 실행중 이벤트루프에서 실 어댑터 fetch → 명시 RuntimeError(hang 0).
- AC-19: crawler 미주입(None) → ValueError(실 네트워크 0 강제).
- crawled_at: 메타 **존재+타입** 단언(NIT-2·wall-clock이라 값-동일성 `==` 금지).
- staging_dir: 옵트인 스냅샷 쓰기(ingest 입력 아님·순수 부산물).
"""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import pytest

from knowledge.crawl import (
    CrawlFetchError,
    _replace_source_tail,
    _select_markdown,
    _slug,
    crawl,
)
from knowledge.search import search
from knowledge.store import KnowledgeStore

_DIM = 1024


# ---------------------------------------------------------------------------
# 결정론 mock — 실서버(bge)·실 네트워크 없이 임베딩·크롤을 흉내낸다.
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """텍스트를 결정론 1024차원 벡터로 바꾸는 mock(test_knowledge_ingest.py:23 패턴).

    같은 텍스트는 항상 같은 벡터(멱등 검증에 필요). 토큰을 1024 버킷에 해싱.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for tok in (t for t in text.lower().split() if t):
            bucket = (hash(tok) % _DIM + _DIM) % _DIM
            vec[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            vec[0] = 1.0
            norm = 1.0
        return [v / norm for v in vec]


class FakeReranker:
    """후보를 그대로(원본 순서·고정 점수) 돌려주는 mock 리랭커(통합 검색용).

    bare-list 형식([{index, score}])으로 응답한다 — search가 회수한 후보를
    필터·반환하는 왕복만 검증하면 되므로 점수 순서는 중요치 않다.
    """

    def rerank(self, query: str, documents: list[str]) -> list[dict[str, object]]:
        return [{"index": i, "score": 1.0} for i in range(len(documents))]


class FakeCrawler:
    """URL→고정 마크다운을 돌려주는 결정론 mock(네트워크 0).

    md_by_url에 매핑이 있으면 그것을, 없으면 URL 기반 기본 마크다운을 만든다.
    fetch 호출 URL을 기록한다(순차·동시성 1 검증용).
    """

    def __init__(self, md_by_url: dict[str, str] | None = None) -> None:
        self.md_by_url = md_by_url or {}
        self.fetched: list[str] = []

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        if url in self.md_by_url:
            return self.md_by_url[url]
        return f"# {url}\n\n이 문서는 {url} 에서 크롤한 본문 내용이다.\n"


class FailingCrawler:
    """특정 URL에 CrawlFetchError를 던지는 mock(AC-14 부분 실패용).

    fail_urls에 든 URL이면 CrawlFetchError, 아니면 기본 마크다운을 돌려준다.
    """

    def __init__(self, fail_urls: set[str]) -> None:
        self.fail_urls = fail_urls
        self.fetched: list[str] = []

    def fetch(self, url: str) -> str:
        self.fetched.append(url)
        if url in self.fail_urls:
            raise CrawlFetchError(f"의도적 실패: {url}")
        return f"# {url}\n\n{url} 의 정상 본문 내용 채우기 텍스트.\n"


def _store() -> KnowledgeStore:
    """인메모리 store(결정론 mock embedder·test_knowledge_ingest.py:71 패턴)."""
    return KnowledgeStore(qdrant_url=":memory:", embedder=FakeEmbedder())


_URL_A = "https://example.com/docs/alpha"
_URL_B = "https://example.com/docs/beta"
_URL_C = "https://other.org/page/gamma"


# ===========================================================================
# unit — crawl() 코어 로직
# ===========================================================================


class TestCrawlCore:
    """URL 루프·source 전달·crawled_at·반환 카운트 기본 경로."""

    def test_crawl_returns_chunk_count_and_populates_store(self) -> None:
        """crawl이 업서트한 청크 수를 돌려주고 store에 반영된다(AC-8 ensure)."""
        store = _store()
        crawler = FakeCrawler()
        n = crawl([_URL_A, _URL_B], store, crawler=crawler)
        assert n > 0
        assert store.count() == n
        # 순차 페치(동시성 1) — 입력 순서대로 호출.
        assert crawler.fetched == [_URL_A, _URL_B]

    def test_source_is_raw_url(self) -> None:
        """AC-5a: 청크 source가 raw URL이다(chunk_markdown(md, source=url) passthrough)."""
        store = _store()
        crawl([_URL_A], store, crawler=FakeCrawler())
        results = store.search_dense(_URL_A, top_n=10)
        assert results
        assert all(c.source == _URL_A for c, _ in results)

    def test_crawled_at_meta_present_and_typed(self) -> None:
        """NIT-2: crawled_at 메타가 존재하고 타입이 맞다(값-동일성 단언 금지)."""
        store = _store()
        crawl([_URL_A], store, crawler=FakeCrawler())
        results = store.search_dense(_URL_A, top_n=10)
        assert results
        # 존재 단언(값 == 단언 금지 — wall-clock이라 플래키).
        assert any("crawled_at" in c.meta for c, _ in results)
        for chunk, _ in results:
            if "crawled_at" in chunk.meta:
                assert isinstance(chunk.meta["crawled_at"], (int, float, str))

    def test_empty_url_list_is_safe(self) -> None:
        """빈 URL 리스트는 0(업서트 0·크래시 0)."""
        store = _store()
        assert crawl([], store, crawler=FakeCrawler()) == 0
        assert store.count() == 0


# ===========================================================================
# unit — AC-19 crawler 미주입 강제
# ===========================================================================


class TestCrawlerRequired:
    """AC-19: crawler 미주입(None) → 명시 에러(실 네트워크 0 강제)."""

    def test_missing_crawler_raises(self) -> None:
        """crawler=None(기본)이면 ValueError — 실 AsyncWebCrawler 지연생성 금지."""
        store = _store()
        with pytest.raises(ValueError, match="crawler"):
            crawl([_URL_A], store)

    def test_missing_crawler_does_not_upsert(self) -> None:
        """미주입 에러 시 store에 아무것도 안 들어간다(부작용 0)."""
        store = _store()
        with pytest.raises(ValueError):
            crawl([_URL_A], store, crawler=None)
        assert store.count() == 0


# ===========================================================================
# unit — AC-14 부분 실패 스킵 + 실패 URL 로그
# ===========================================================================


class TestPartialFailure:
    """AC-14: 일부 URL 페치 실패 → 그 URL만 스킵·나머지 계속·전체 크래시 0."""

    def test_failing_url_skipped_others_continue(self) -> None:
        """한 URL이 CrawlFetchError여도 나머지는 업서트된다(반환 total=성공분)."""
        store = _store()
        crawler = FailingCrawler(fail_urls={_URL_B})
        n = crawl([_URL_A, _URL_B, _URL_C], store, crawler=crawler)
        # 3개 모두 페치 시도(실패도 시도엔 포함).
        assert crawler.fetched == [_URL_A, _URL_B, _URL_C]
        assert n > 0
        # 실패 URL(_URL_B)은 store에 없고, 성공 URL은 있다.
        a_hits = store.search_dense(_URL_A, top_n=10)
        b_hits = store.search_dense(_URL_B, top_n=10)
        assert any(c.source == _URL_A for c, _ in a_hits)
        assert all(c.source != _URL_B for c, _ in b_hits)

    def test_failed_url_logged_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        """NIT-4: 실패 URL이 stderr에 로그된다(silent skip 금지)."""
        store = _store()
        crawl([_URL_A, _URL_B], store, crawler=FailingCrawler(fail_urls={_URL_B}))
        captured = capsys.readouterr()
        # 실패 URL이 stderr에 식별 가능하게 찍힌다.
        assert _URL_B in captured.err
        # 성공 URL은 실패 로그에 안 찍힌다.
        assert "페치 실패" in captured.err

    def test_all_urls_fail_returns_zero(self) -> None:
        """모든 URL 실패면 total 0·크래시 0(빈 store)."""
        store = _store()
        crawler = FailingCrawler(fail_urls={_URL_A, _URL_B})
        assert crawl([_URL_A, _URL_B], store, crawler=crawler) == 0
        assert store.count() == 0


# ===========================================================================
# unit — staging_dir 옵트인 스냅샷
# ===========================================================================


class TestStagingDir:
    """staging_dir: 옵트인 디버그 스냅샷(ingest 입력 아님·순수 부산물)."""

    def test_staging_writes_markdown_snapshot(self, tmp_path: Path) -> None:
        """staging_dir 지정 시 각 URL 마크다운을 <slug>.md로 쓴다."""
        store = _store()
        staging = tmp_path / "crawl_staging"
        md = "# Alpha\n\n알파 문서 본문 스냅샷.\n"
        crawl(
            [_URL_A],
            store,
            crawler=FakeCrawler({_URL_A: md}),
            staging_dir=staging,
        )
        snapshot = staging / f"{_slug(_URL_A)}.md"
        assert snapshot.exists()
        assert snapshot.read_text(encoding="utf-8") == md

    def test_no_staging_by_default(self, tmp_path: Path) -> None:
        """staging_dir 미지정(기본 None)이면 어떤 파일도 안 쓴다."""
        store = _store()
        # tmp_path는 비어 있어야 한다(crawl이 staging 없이 동작).
        crawl([_URL_A], store, crawler=FakeCrawler())
        assert list(tmp_path.iterdir()) == []

    def test_slug_is_deterministic(self) -> None:
        """_slug는 결정론(같은 URL→같은 slug)이고 파일시스템 안전 문자만 쓴다."""
        s1 = _slug(_URL_A)
        s2 = _slug(_URL_A)
        assert s1 == s2
        assert all(c.isalnum() or c in "-._" for c in s1)
        # 다른 URL은 다른 slug(sha1 일부).
        assert _slug(_URL_A) != _slug(_URL_B)


# ===========================================================================
# unit — AC-15 async running-loop 방어(실 어댑터)
# ===========================================================================


class TestAsyncGuard:
    """AC-15: 실행중 이벤트루프에서 실 어댑터 fetch → 명시 RuntimeError(hang 0)."""

    def test_real_adapter_fetch_in_running_loop_raises(self) -> None:
        """running event loop 안에서 Crawl4aiCrawler.fetch → RuntimeError(폴백)."""
        from knowledge.crawl import Crawl4aiCrawler

        async def _inside_loop() -> None:
            # 실행중 루프 안 → asyncio.run 불가 → 명시 RuntimeError(crawl4ai 미import).
            Crawl4aiCrawler().fetch(_URL_A)

        with pytest.raises(RuntimeError, match="이벤트루프"):
            asyncio.run(_inside_loop())


# ===========================================================================
# integration — crawl → store.upsert → search 왕복
# ===========================================================================


class TestCrawlSearchRoundtrip:
    """crawl이 넣은 청크를 search가 source==URL 필터로 회수한다(AC-5a/AC-7)."""

    def test_search_recovers_by_source_url(self) -> None:
        """AC-5a/AC-7: 검색 source filter == raw URL로 크롤 문서를 회수한다."""
        store = _store()
        md_a = "# Alpha\n\n사과 바나나 포도 과일 신선 가게.\n"
        md_b = "# Beta\n\n자동차 엔진 바퀴 정비소 수리 부품.\n"
        crawl(
            [_URL_A, _URL_B],
            store,
            crawler=FakeCrawler({_URL_A: md_a, _URL_B: md_b}),
        )
        out = search(
            "사과 과일 가게",
            k=5,
            filter={"source": _URL_A},
            store=store,
            reranker=FakeReranker(),
        )
        assert out["count"] >= 1
        # filter가 raw URL로 그 출처만 남긴다(AC-5a).
        assert all(r["source"] == _URL_A for r in out["results"])

    def test_recrawl_is_idempotent(self) -> None:
        """AC-7: 같은 URL 2회 크롤 → 포인트 수 불변(멱등 덮어쓰기)."""
        store = _store()
        crawler = FakeCrawler()
        first = crawl([_URL_A, _URL_B], store, crawler=crawler)
        count_after_first = store.count()
        second = crawl([_URL_A, _URL_B], store, crawler=crawler)
        count_after_second = store.count()
        assert first == second  # 같은 청크 수 업서트
        assert count_after_first == count_after_second  # 포인트 수 불변(중복 0)

    def test_recrawl_search_count_stable(self) -> None:
        """재크롤 후에도 source 회수 결과 수가 안정(중복 0)."""
        store = _store()
        crawler = FakeCrawler()
        crawl([_URL_A], store, crawler=crawler)
        hits_first = len(store.search_dense(_URL_A, top_n=50))
        crawl([_URL_A], store, crawler=crawler)
        hits_second = len(store.search_dense(_URL_A, top_n=50))
        assert hits_first == hits_second


# ===========================================================================
# unit — Phase 9.1 _select_markdown 순수 함수(fit/raw 선택·crawl4ai 0·AC-Q3)
# ===========================================================================


class _FakeMarkdown:
    """`MarkdownGenerationResult` stub — `.fit_markdown`/`.raw_markdown` 속성만.

    `_select_markdown`은 crawl4ai 무관 순수 함수라 이 stub으로 결정론 단위테스트한다
    (실 PruningContentFilter·실 브라우저 0). 속성을 생략(None)하면 구버전 md 객체
    (fit 미보유)를 흉내낸다.
    """

    def __init__(self, fit: str | None = None, raw: str | None = None) -> None:
        if fit is not None:
            self.fit_markdown = fit
        if raw is not None:
            self.raw_markdown = raw


class TestSelectMarkdown:
    """AC-Q3: `_select_markdown` 6케이스 — fit 충분/폴백/공백/45% 모범(crawl4ai 0)."""

    def test_normal_fit_is_selected(self) -> None:
        """① 정상 fit(충분: 비빔·임계 이상)이면 fit를 고른다(폴백 안 함)."""
        raw = "원본 " * 500  # 길다(보일러플레이트 포함 가정)
        fit = "본문 " * 200  # 충분(>200자·raw의 ~30%)
        assert _select_markdown(_FakeMarkdown(fit=fit, raw=raw)) == fit

    def test_empty_fit_falls_back_to_raw(self) -> None:
        """② fit가 빈 문자열이면 raw로 폴백한다(과도제거 방어)."""
        raw = "본문 " * 300
        assert _select_markdown(_FakeMarkdown(fit="", raw=raw)) == raw

    def test_undersized_fit_falls_back_to_raw(self) -> None:
        """③ fit가 임계(max(200, raw*0.10)) 미달이면 raw로 폴백한다."""
        raw = "x" * 5000  # 임계 = max(200, 500) = 500
        fit = "y" * 100  # 100 < 500 → 과소
        assert _select_markdown(_FakeMarkdown(fit=fit, raw=raw)) == raw

    def test_missing_fit_attr_falls_back_to_raw(self) -> None:
        """④ fit 속성이 없으면(구버전 md 객체) raw로 폴백한다."""
        raw = "본문 " * 300
        md = _FakeMarkdown(raw=raw)  # fit_markdown 속성 미설정
        assert not hasattr(md, "fit_markdown")
        assert _select_markdown(md) == raw

    def test_both_blank_returns_empty(self) -> None:
        """⑤ fit·raw 둘다 빈값/공백이면 빈 문자열을 돌려준다(★fix3 반환 strip).

        공백 raw 폴백("   ")도 빈값 취급해 추출부 가드(if not markdown:
        raise CrawlFetchError)가 잡게 한다(라이브 N=0 상류 차단).
        """
        assert _select_markdown(_FakeMarkdown(fit="", raw="")) == ""
        assert _select_markdown(_FakeMarkdown(fit="   ", raw="  \n\t")) == ""
        # raw가 공백뿐이면 fit 폴백 경로도 공백→빈 문자열.
        assert _select_markdown(_FakeMarkdown(fit="\n\n", raw="   ")) == ""

    def test_fit_at_45pct_is_kept_not_fallback(self) -> None:
        """⑥ §0.1 모범: fit=raw의 45%면 fit를 고른다(50% 오구현 red화·backfire 차단).

        라이브 실측 31k/69k(≈45%)는 정상 노이즈 제거다 — 폴백하면 그 URL 노이즈
        제거가 무효가 된다. 임계 max(200, raw*0.10)=10%라 45%는 충분(fit 유지).
        만약 임계를 50%로 오구현하면 이 케이스가 raw로 폴백해 red가 된다.
        """
        raw = "r" * 1000
        fit = "f" * 450  # 45% — 임계(10%=100) 통과
        assert _select_markdown(_FakeMarkdown(fit=fit, raw=raw)) == fit

    def test_str_input_treated_as_raw(self) -> None:
        """보너스: md_obj가 str이면 raw로 취급한다(버전 관용·구버전 경로)."""
        assert _select_markdown("# 제목\n\n본문 내용 충분히 길게.\n") == (
            "# 제목\n\n본문 내용 충분히 길게.\n"
        )


# ===========================================================================
# unit — Phase 9.1 AC-Q9 crawl() 레벨 공백 가드(fix1·전 경로 mock+라이브 차단)
# ===========================================================================


class TestBlankMarkdownGuard:
    """AC-Q9: crawl()이 공백 md를 chunk_markdown 전에 스킵한다(N=0 전손 원천 차단)."""

    def test_blank_markdown_url_skipped_no_upsert(self) -> None:
        """공백("   ") md URL은 스킵·upsert 0·count 불변(스킵된 URL 부작용 0)."""
        store = _store()
        crawler = FakeCrawler({_URL_A: "   "})
        n = crawl([_URL_A], store, crawler=crawler)
        assert n == 0
        assert store.count() == 0  # 아무것도 안 들어감

    def test_blank_recrawl_preserves_existing_chunks(self) -> None:
        """★핵심: 기존 청크가 있는 URL을 공백 md로 재크롤해도 기존 청크 보존.

        fix1 공백 가드가 chunk_markdown 도달 전 스킵하므로 안전순서 tail-delete
        (Range(gte=0) 전손)에 절대 도달하지 않는다 — 기존 M청크가 그대로 남는다.
        """
        store = _store()
        # 1) 정상 md로 먼저 채운다(M청크).
        crawl([_URL_A], store, crawler=FakeCrawler())
        count_before = store.count()
        assert count_before > 0
        # 2) 공백 md로 재크롤 → 스킵 → 기존 보존.
        crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: "  \n\n"}))
        assert store.count() == count_before  # 전손 없음·count 불변

    def test_blank_skip_logged_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        """공백 스킵이 stderr에 로그된다(silent skip 금지·N=0 진단·observability)."""
        store = _store()
        crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: "   "}))
        captured = capsys.readouterr()
        assert _URL_A in captured.err
        assert "빈/공백" in captured.err

    def test_nonblank_md_processed_normally(self) -> None:
        """비-공백 정상 md는 가드를 통과해 정상 업서트된다(가드 오탐 0)."""
        store = _store()
        n = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: "# 제목\n\n본문.\n"}))
        assert n > 0
        assert store.count() == n


# ===========================================================================
# unit — Phase 9.1 AC-Q8 안전순서 tail-delete + N=0 회귀(:memory: 실 QdrantClient)
# ===========================================================================


def _count_source(store: KnowledgeStore, url: str) -> int:
    """source==url 청크 수를 센다(top_n 넉넉히·tail-delete 검증용)."""
    hits = store.search_dense(url, top_n=100)
    return sum(1 for c, _ in hits if c.source == url)


class TestSafeOrderTailDelete:
    """AC-Q8: upsert→N≥1→chunk_id≥N tail-delete(:memory: 실 delete·§0.2 재현)."""

    def test_recrawl_fewer_chunks_removes_stale_tail(self) -> None:
        """raw M청크 → fit N<M 재크롤 → 0..N-1만 잔존·stale 꼬리(N..M-1) 0.

        ★mock 아닌 실 :memory: QdrantClient로 store._client.delete를 실행한다
        (§0.2 라이브 실측 MatchValue+Range status=completed 재현). delete-first가
        아니라 upsert 먼저이므로 어느 순간에도 청크 0 창이 없다.
        """
        store = _store()
        # 큰 md(여러 청크) — _TARGET_CHARS=500 넘게 여러 문단.
        big_md = "# 큰 문서\n\n" + "\n\n".join(
            f"문단 {i} 내용 채우기 " * 30 for i in range(6)
        )
        m = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: big_md}))
        assert m >= 2  # 여러 청크여야 의미 있는 테스트
        count_big = _count_source(store, _URL_A)
        assert count_big == m

        # 작은 md(1~2청크)로 재크롤 → 꼬리 삭제.
        small_md = "# 작은 문서\n\n짧은 본문 한 단락.\n"
        n = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: small_md}))
        assert n < m  # 청크 수 감소(fit<raw 시뮬)
        count_small = _count_source(store, _URL_A)
        # ★stale 꼬리 0 — source 청크 수가 정확히 N(새 청크)이어야 한다.
        assert count_small == n
        # 전역 count도 N(다른 source 없음).
        assert store.count() == n

    def test_n0_blank_recrawl_does_not_wipe_via_safe_order(self) -> None:
        """★fix4 N=0 회귀: 공백 md 재크롤 시 Range(gte=0) 미발사·기존 M 보존.

        fix1 공백 가드가 상류에서 차단하므로 안전순서 tail-delete가 발사되지
        않는다 — 기존 M청크가 전손되지 않고 count 불변이어야 한다(전손 red 고정).
        """
        store = _store()
        big_md = "# 문서\n\n" + "\n\n".join(
            f"문단 {i} 충분한 본문 " * 30 for i in range(5)
        )
        m = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: big_md}))
        assert m >= 2
        count_before = store.count()
        # 공백 md 재크롤 → 전손 없음.
        crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: "   "}))
        assert store.count() == count_before  # ★전손 0·기존 M 보존

    def test_tail_delete_only_touches_same_source(self) -> None:
        """tail-delete가 다른 source를 건드리지 않는다(source 한정 삭제)."""
        store = _store()
        big_a = "# A\n\n" + "\n\n".join(f"A문단 {i} 본문 " * 30 for i in range(6))
        crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: big_a}))
        crawl([_URL_B], store, crawler=FakeCrawler())  # 다른 source
        count_b_before = _count_source(store, _URL_B)
        assert count_b_before > 0
        # A를 작게 재크롤 → A 꼬리만 삭제·B 무사.
        crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: "# A\n\n짧음.\n"}))
        assert _count_source(store, _URL_B) == count_b_before  # B 불변


# ===========================================================================
# unit — Phase 9.1 NIT-1 분리성: _replace_source_tail 직접 호출 + fix2 N≥1 가드
# ===========================================================================


class TestReplaceSourceTailUnit:
    """fix2 N≥1 가드 단위검증 — fix1을 우회해 직접 호출(분리성·NIT-1 보강)."""

    def test_tail_delete_gte_zero_wipes_source(self) -> None:
        """★전손 실증(fix2 필요 근거): new_count=0이면 source 전체 삭제된다.

        이게 N≥1 가드(fix2)가 막아야 할 정확한 발사다 — _replace_source_tail을
        new_count=0으로 직접 호출하면 Range(gte=0)=chunk_id>=0=source 전손
        (§0.2 실측4 [0..4]→[]). crawl() 경로는 fix1+fix2가 이를 차단한다.
        """
        store = _store()
        crawl([_URL_A], store, crawler=FakeCrawler())
        assert _count_source(store, _URL_A) > 0
        # 직접 호출(가드 우회) → 전손 재현.
        _replace_source_tail(store, _URL_A, 0)
        assert _count_source(store, _URL_A) == 0  # ★전손 확정(fix2가 막는 대상)

    def test_crawl_fix2_guards_n0_when_chunker_empty(self, monkeypatch) -> None:
        """★Minor-1(code-review): crawl()의 fix2 N≥1 가드 분기를 직접 pin한다.

        비-공백 md(fix1 통과)가 0청크를 내는 경우를 chunk_markdown monkeypatch로
        강제해 N=0가 안전순서까지 도달하게 한 뒤, fix2(`if len(chunks) >= 1`)가
        tail-delete를 차단해 전손이 없음을 단언한다. fix1+직접호출 테스트는
        fix2 분기를 안 타므로(돌연변이 시 green) 이 테스트가 fix2를 red-pin한다
        — fix2를 제거하면 N=0가 `_replace_source_tail(.., 0)`→Range(gte=0) 전손→red.
        """
        store = _store()
        big_md = "# 문서\n\n" + "\n\n".join(f"문단 {i} 본문 " * 30 for i in range(6))
        m = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: big_md}))
        assert m >= 2
        count_before = store.count()
        # 비-공백 md지만 청커가 0청크를 반환 → fix1 통과·N=0 → fix2가 막아야 함.
        monkeypatch.setattr("knowledge.crawl.chunk_markdown", lambda md, source: [])
        crawl(
            [_URL_A], store, crawler=FakeCrawler({_URL_A: "비-공백이나 청커가 0청크"})
        )
        assert store.count() == count_before  # ★fix2가 Range(gte=0) 전손 차단

    def test_tail_delete_preserves_head(self) -> None:
        """new_count=N이면 0..N-1 보존·N 이상만 삭제(꼬리만)."""
        store = _store()
        big_md = "# 문서\n\n" + "\n\n".join(f"문단 {i} 본문 " * 30 for i in range(6))
        m = crawl([_URL_A], store, crawler=FakeCrawler({_URL_A: big_md}))
        assert m >= 3
        # 꼬리 1개만 남기고 삭제(new_count=m-1).
        _replace_source_tail(store, _URL_A, m - 1)
        assert _count_source(store, _URL_A) == m - 1


# ===========================================================================
# unit — Phase 9.1 delete 실패 경로(로그 후 진행·crash 0)
# ===========================================================================


class _BrokenDeleteClient:
    """delete만 예외를 던지는 래퍼(다른 호출은 위임) — delete 실패 경로 검증."""

    def __init__(self, real: object) -> None:
        self._real = real

    def delete(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("의도적 delete 실패")

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)


class TestTailDeleteFailureTolerated:
    """delete 실패 시 로그 후 진행(upsert 이미 성공·crash 0)."""

    def test_delete_failure_logged_and_progresses(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """tail-delete 예외가 crawl을 죽이지 않고 stderr 로그 후 진행한다."""
        store = _store()
        # 먼저 정상 업서트(컬렉션 생성).
        crawl([_URL_A], store, crawler=FakeCrawler())
        # store._client.delete를 깨뜨린다(다른 호출은 정상).
        store._client = _BrokenDeleteClient(store._client)  # type: ignore[assignment]
        # 재크롤 → upsert 성공 + tail-delete 실패 → crash 0·로그.
        n = crawl([_URL_A], store, crawler=FakeCrawler())
        assert n > 0  # upsert는 성공(반환 정상)
        captured = capsys.readouterr()
        assert "꼬리 삭제 실패" in captured.err
        assert _URL_A in captured.err


# ===========================================================================
# slow — 실 crawl4ai 어댑터(사용자 세션·실 브라우저·게이트 제외)
# ===========================================================================


@pytest.mark.slow
def test_real_crawl4ai_adapter_smoke() -> None:
    """실 crawl4ai 어댑터 가용성 스모크(실 브라우저·사용자 세션 전용).

    ★게이트(`-m 'not slow'`)에서 제외된다. crawl4ai 미설치면 importorskip로
    스킵 → 결정론 게이트 env(crawl4ai 없음)에서도 안전. 실 네트워크 호출은
    하지 않고 어댑터 생성·인터페이스 존재만 확인한다(실 발사는 사용자 세션).
    """
    pytest.importorskip("crawl4ai")
    from knowledge.crawl import Crawl4aiCrawler

    crawler = Crawl4aiCrawler()
    # Crawler 프로토콜 충족(fetch 존재) — 실 페치는 사용자 동석 세션에서만.
    assert hasattr(crawler, "fetch")
    assert callable(crawler.fetch)
