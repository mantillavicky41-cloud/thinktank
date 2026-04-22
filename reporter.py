"""Per-cycle reporter: collects stats through one fetch→push cycle and
emits a human-readable Markdown report plus a terminal summary.

The reporter only observes — it never alters push behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TZ_CN = timezone(timedelta(hours=8))


# ---------- Sensitive word dictionary (Taiwan-related, ~25 words) ----------

SENSITIVE_WORDS: dict[str, list[str]] = {
    "政治人物": ["习近平", "蔡英文", "赖清德", "马英九", "韩国瑜"],
    "政治表述": ["台独", "台湾独立", "分裂", "一中", "九二共识", "一国两制", "中华民国"],
    "党派": ["民进党", "国民党", "共产党"],
    "军事冲突": ["军演", "解放军", "武统", "封锁", "入侵"],
    "其他高频": ["制裁", "间谍", "香港", "新疆", "西藏"],
}

_FLAT_WORDS: list[str] = [w for group in SENSITIVE_WORDS.values() for w in group]


def scan_sensitive(text: str) -> dict[str, int]:
    """Count occurrences of each sensitive word in text. Returns non-zero hits only."""
    hits: dict[str, int] = {}
    for w in _FLAT_WORDS:
        c = text.count(w)
        if c > 0:
            hits[w] = c
    return hits


# ---------- Data classes ----------


@dataclass
class FeedStat:
    name: str
    category: str
    article_count: int
    duration_ms: int
    error: str | None = None  # None = success


@dataclass
class TranslationStats:
    batch_total: int = 0
    batch_failed: int = 0
    fallback_used: int = 0
    fallback_failed: int = 0


@dataclass
class PushResult:
    index: int            # 1-based message index within this cycle
    total: int            # total messages in this cycle
    payload_len: int      # markdown text character count
    errcode: int
    errmsg: str
    response_raw: str     # full response body
    sensitive_hits: dict[str, int] = field(default_factory=dict)
    article_titles: list[str] = field(default_factory=list)  # title_zh list


# ---------- Reporter ----------


class CycleReporter:
    """Accumulates cycle state and renders a report on finalize()."""

    def __init__(self, report_dir: Path) -> None:
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

        self._start_at: datetime | None = None
        self._feeds: list[FeedStat] = []
        self._new_count = 0
        self._total_count = 0
        self._per_source_new: dict[str, int] = {}
        self._taiwan_hits: list[tuple[str, str]] = []  # (source, title)
        self._translation: TranslationStats = TranslationStats()
        self._pushes: list[PushResult] = []
        self._errors: list[tuple[str, str]] = []  # (stage, message)

    def start(self) -> None:
        self._start_at = datetime.now(_TZ_CN)

    def record_feeds(self, stats: list[FeedStat]) -> None:
        self._feeds = list(stats)

    def record_new_articles(
        self,
        new_count: int,
        total_count: int,
        per_source: dict[str, int],
    ) -> None:
        self._new_count = new_count
        self._total_count = total_count
        self._per_source_new = dict(per_source)

    def record_taiwan_hits(self, articles: list[tuple[str, str]]) -> None:
        """articles: list of (source, title) tuples."""
        self._taiwan_hits = list(articles)

    def record_translation(self, stats: TranslationStats) -> None:
        self._translation = stats

    def record_push(self, results: list[PushResult]) -> None:
        self._pushes = list(results)

    def record_error(self, stage: str, err: BaseException) -> None:
        self._errors.append((stage, f"{type(err).__name__}: {err}"))

    # ---- finalize ----

    def finalize(self) -> None:
        end_at = datetime.now(_TZ_CN)
        start = self._start_at or end_at
        duration = (end_at - start).total_seconds()

        # Terminal summary (compact)
        self._emit_terminal_summary(start, duration)

        # Markdown report (full)
        try:
            self._append_markdown(start, end_at, duration)
        except Exception:
            logger.exception("Failed to write cycle report to Markdown")

    def _emit_terminal_summary(self, start: datetime, duration: float) -> None:
        feeds_ok = sum(1 for f in self._feeds if f.error is None)
        feeds_total = len(self._feeds)
        feed_failures = [f.name for f in self._feeds if f.error is not None]

        top_sources = sorted(
            self._per_source_new.items(), key=lambda kv: kv[1], reverse=True
        )[:5]
        src_str = ", ".join(f"{s} {n}" for s, n in top_sources) if top_sources else "-"

        push_lines: list[str] = []
        if self._pushes:
            for p in self._pushes:
                hits = (
                    ", ".join(f"{w}×{c}" for w, c in p.sensitive_hits.items())
                    if p.sensitive_hits else "-"
                )
                push_lines.append(
                    f"    msg {p.index}/{p.total}: {p.payload_len} chars, "
                    f"errcode={p.errcode}, errmsg={p.errmsg!r}, sensitive=[{hits}]"
                )
        else:
            push_lines.append("    (no push this cycle)")

        lines = [
            f"=== Cycle {start.strftime('%Y-%m-%d %H:%M:%S')} complete ({duration:.1f}s) ===",
            f"  Feeds:        {feeds_ok}/{feeds_total} ok"
            + (f"   (failed: {', '.join(feed_failures)})" if feed_failures else ""),
            f"  Articles:     {self._new_count} new / {self._total_count} fetched"
            + (f"   ({src_str})" if top_sources else ""),
            f"  Taiwan hits:  {len(self._taiwan_hits)}",
            f"  LLM:          batches {self._translation.batch_total}"
            f" ({self._translation.batch_failed} failed),"
            f" fallback {self._translation.fallback_used}"
            f" ({self._translation.fallback_failed} failed)",
            f"  DingTalk:",
            *push_lines,
        ]
        if self._errors:
            lines.append(f"  Errors:       {len(self._errors)}")
            for stage, msg in self._errors:
                lines.append(f"    [{stage}] {msg}")

        logger.info("\n".join(lines))

    def _append_markdown(
        self,
        start: datetime,
        end: datetime,
        duration: float,
    ) -> None:
        date_str = start.strftime("%Y-%m-%d")
        path = self.report_dir / f"{date_str}.md"

        content = self._render_markdown(start, end, duration)

        # Append with --- separator if file exists
        exists = path.exists()
        with path.open("a", encoding="utf-8") as f:
            if exists:
                f.write("\n---\n\n")
            else:
                f.write(f"# 智库 RSS 周期报告 · {date_str}\n\n")
            f.write(content)
            f.write("\n")

    def _render_markdown(
        self,
        start: datetime,
        end: datetime,
        duration: float,
    ) -> str:
        feeds_ok = sum(1 for f in self._feeds if f.error is None)
        feeds_total = len(self._feeds)

        out: list[str] = []
        out.append(f"## {start.strftime('%Y-%m-%d %H:%M:%S')} 周期报告")
        out.append("")
        out.append(
            f"**用时** {duration:.1f}s ｜ "
            f"**抓取** {feeds_ok}/{feeds_total} ｜ "
            f"**新文章** {self._new_count} ｜ "
            f"**台湾议题** {len(self._taiwan_hits)} ｜ "
            f"**推送** {len(self._pushes)} 条消息"
        )
        out.append("")

        # Errors (if any) — surface at top so they're not missed
        if self._errors:
            out.append("### ⚠️ 执行中错误")
            out.append("")
            for stage, msg in self._errors:
                out.append(f"- **[{stage}]** {msg}")
            out.append("")

        # Feeds table
        out.append("### 抓取结果")
        out.append("")
        if self._feeds:
            out.append("| Feed | 文章 | 耗时 | 状态 |")
            out.append("|---|---:|---:|---|")
            for f in self._feeds:
                status = "✅" if f.error is None else f"❌ {f.error}"
                out.append(
                    f"| {f.name} | {f.article_count} "
                    f"| {f.duration_ms/1000:.1f}s | {status} |"
                )
        else:
            out.append("（本轮未执行抓取）")
        out.append("")

        # New articles per source
        if self._per_source_new:
            out.append("### 新增文章分布")
            out.append("")
            top = sorted(
                self._per_source_new.items(), key=lambda kv: kv[1], reverse=True
            )
            out.append(", ".join(f"**{s}** {n}" for s, n in top))
            out.append("")

        # Taiwan hits
        out.append(f"### 台湾议题命中 ({len(self._taiwan_hits)})")
        out.append("")
        if self._taiwan_hits:
            for i, (src, title) in enumerate(self._taiwan_hits, 1):
                out.append(f"{i}. **[{src}]** {title}")
        else:
            out.append("（本轮无命中）")
        out.append("")

        # Translation
        t = self._translation
        out.append("### LLM 翻译")
        out.append("")
        out.append(
            f"批次 {t.batch_total - t.batch_failed}/{t.batch_total} 成功；"
            f"单篇回退 {t.fallback_used} 次"
            + (f"（其中 {t.fallback_failed} 次失败）" if t.fallback_failed else "")
            + "。"
        )
        out.append("")

        # Push
        out.append("### 钉钉推送")
        out.append("")
        if self._pushes:
            for p in self._pushes:
                out.append(
                    f"**消息 {p.index}/{p.total}** — {p.payload_len} 字 — "
                    f"`errcode={p.errcode} errmsg={p.errmsg}`"
                )
                if p.article_titles:
                    preview = "、".join(p.article_titles[:5])
                    if len(p.article_titles) > 5:
                        preview += f"（共 {len(p.article_titles)} 篇）"
                    out.append(f"- 文章：{preview}")
                if p.sensitive_hits:
                    hits = "、".join(
                        f"`{w} ×{c}`" for w, c in p.sensitive_hits.items()
                    )
                    out.append(f"- 敏感词命中：{hits}")
                else:
                    out.append("- 敏感词命中：无")
                # Truncate extremely long response bodies
                resp = p.response_raw or ""
                if len(resp) > 500:
                    resp = resp[:500] + "...(truncated)"
                out.append(f"- 响应：`{resp}`")
                out.append("")
            out.append(
                "> ⚠️ 钉钉可能静默改写敏感词但仍返回 errcode=0，"
                "如命中较多请对照手机 App 实际内容核对。"
            )
        else:
            out.append("（本轮无推送）")
        out.append("")

        return "\n".join(out)
