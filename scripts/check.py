#!/usr/bin/env python3
"""
Local content safety checker.

The command accepts inline text, text files, or video files. Video input is
automatically routed to the OCR workflow in check_video.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from update_words import load_words, needs_update, status, update_words  # noqa: E402


SCRIPT_DIR = Path(__file__).resolve().parent
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".flv"}
TEXT_EXTS = {".txt", ".md", ".srt", ".ass", ".csv", ".json", ".log"}

BUILTIN_RISK_WORDS = {
    "广告极限词（广告法）": [
        "史上最",
        "最好",
        "第一",
        "唯一",
        "顶级",
        "极致",
        "无敌",
        "全网最",
        "最强",
        "最优",
        "最大",
        "最低",
        "最高",
        "最便宜",
        "最实惠",
        "最划算",
        "专家级",
        "国家级",
        "行业第一",
        "销量第一",
        "NO.1",
        "no.1",
        "绝对",
        "100%",
        "永久",
        "终身",
        "彻底",
        "根治",
    ],
    "平台限流词（内容平台）": [
        "推广",
        "广告",
        "营销",
        "引流",
        "涨粉",
        "买粉",
        "刷量",
        "私信",
        "加微信",
        "加我微信",
        "微信号",
        "扫码",
        "二维码",
        "点链接",
        "点击链接",
        "下单",
        "购买",
        "下载",
        "安装",
        "优惠券",
        "领券",
        "领红包",
        "福利",
        "免费领",
        "秒杀",
        "限时",
        "限量",
        "抢购",
        "团购",
        "代理",
        "招商",
        "加盟",
        "合作",
        "分销",
    ],
    "医疗健康违禁词": [
        "包治",
        "根治",
        "治愈",
        "特效",
        "祖传秘方",
        "偏方",
        "无副作用",
        "无任何副作用",
        "药到病除",
        "立竿见影",
    ],
}


def categorize_word(word: str) -> str:
    for category, keywords in BUILTIN_RISK_WORDS.items():
        if word in keywords or any(keyword in word for keyword in keywords):
            return category
    return "违禁/敏感词"


def find_hits(text: str, words: set[str]) -> list[dict]:
    all_check: list[tuple[str, str]] = []
    for word in words:
        all_check.append((word, "词库"))
    for category, category_words in BUILTIN_RISK_WORDS.items():
        for word in category_words:
            all_check.append((word, category))

    all_check.sort(key=lambda item: len(item[0]), reverse=True)

    hits = []
    found_words = set()
    for word, source in all_check:
        dedupe_key = word.casefold()
        if dedupe_key in found_words:
            continue
        positions = [match.span() for match in re.finditer(re.escape(word), text, re.IGNORECASE)]
        if positions:
            category = source if source != "词库" else categorize_word(word)
            hits.append(
                {
                    "word": word,
                    "positions": positions,
                    "category": category,
                    "source": source,
                    "count": len(positions),
                }
            )
            found_words.add(dedupe_key)

    hits.sort(key=lambda hit: hit["positions"][0][0])
    return hits


def highlight_text(text: str, hits: list[dict]) -> str:
    if not hits:
        return text

    spans = []
    for hit in hits:
        spans.extend(hit["positions"])
    spans.sort()

    result = []
    prev = 0
    for start, end in spans:
        result.append(text[prev:start])
        result.append(f"【{text[start:end]}】")
        prev = end
    result.append(text[prev:])
    return "".join(result)


def get_context(text: str, start: int, end: int, window: int = 10) -> str:
    ctx_start = max(0, start - window)
    ctx_end = min(len(text), end + window)
    prefix = "..." if ctx_start > 0 else ""
    suffix = "..." if ctx_end < len(text) else ""
    return f"{prefix}{text[ctx_start:start]}【{text[start:end]}】{text[end:ctx_end]}{suffix}"


def format_result(text: str, hits: list[dict]) -> str:
    lines = []

    if not hits:
        lines.append("检测通过：未发现违禁词/敏感词")
        lines.append(f"检测字数: {len(text)} 字")
        return "\n".join(lines)

    forbidden = [hit for hit in hits if hit["source"] == "词库"]
    platform = [hit for hit in hits if "平台限流词" in hit["category"]]
    adwords = [hit for hit in hits if "广告极限词" in hit["category"]]
    medical = [hit for hit in hits if "医疗" in hit["category"]]

    lines.append(f"发现 {len(hits)} 个风险词，建议修改后再发布\n")

    if forbidden:
        lines.append("违禁词（高风险，必改）:")
        for hit in forbidden:
            ctx = get_context(text, *hit["positions"][0])
            times = f"（出现{hit['count']}次）" if hit["count"] > 1 else ""
            lines.append(f"   - {hit['word']}{times}  [{hit['category']}]")
            lines.append(f"     上下文: {ctx}")

    if platform:
        lines.append("\n平台限流词（建议替换，影响流量）:")
        for hit in platform:
            ctx = get_context(text, *hit["positions"][0])
            times = f"（出现{hit['count']}次）" if hit["count"] > 1 else ""
            lines.append(f"   - {hit['word']}{times}")
            lines.append(f"     上下文: {ctx}")

    if adwords:
        lines.append("\n广告极限词（广告法风险）:")
        for hit in adwords:
            ctx = get_context(text, *hit["positions"][0])
            times = f"（出现{hit['count']}次）" if hit["count"] > 1 else ""
            lines.append(f"   - {hit['word']}{times}")
            lines.append(f"     上下文: {ctx}")

    if medical:
        lines.append("\n医疗违禁词（广告法）:")
        for hit in medical:
            ctx = get_context(text, *hit["positions"][0])
            lines.append(f"   - {hit['word']}  上下文: {ctx}")

    lines.append("\n-- 标注后文本 --")
    lines.append(highlight_text(text, hits))
    lines.append(f"\n检测字数: {len(text)} 字 | 风险词: {len(hits)} 个")
    return "\n".join(lines)


def write_text_outputs(text: str, hits: list[dict], input_label: str, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = datetime.now().isoformat(timespec="seconds")

    rows = []
    for hit in hits:
        for start, end in hit["positions"]:
            rows.append(
                {
                    "word": hit["word"],
                    "category": hit["category"],
                    "source": hit["source"],
                    "start": start,
                    "end": end,
                    "context": get_context(text, start, end),
                }
            )

    with (output_dir / "hits.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["word", "category", "source", "start", "end", "context"])
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "generated_at": generated,
        "input": input_label,
        "finding_count": len(rows),
        "unique_words": sorted({row["word"] for row in rows}),
        "outputs": {
            "report_md": str(output_dir / "report.md"),
            "hits_csv": str(output_dir / "hits.csv"),
            "report_json": str(output_dir / "report.json"),
        },
    }
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(format_result(text, hits) + "\n", encoding="utf-8")


def print_status() -> None:
    current = status()
    print("词库状态:")
    print(f"  最后更新: {current['last_update']}")
    print(f"  词条数量: {current['word_count']:,}")
    print(f"  词库文件: {current['words_file']}")
    print(f"  今日需更新: {'是' if current['needs_update'] else '否'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local forbidden-word checker for text, files, and videos.")
    parser.add_argument("input", nargs="?", help="Inline text or a text/video file path.")
    parser.add_argument("--text", help="Inline text to scan.")
    parser.add_argument("-f", "--file", type=Path, help="Text file to scan.")
    parser.add_argument("--video", type=Path, help="Video file to scan with OCR.")
    parser.add_argument("-o", "--output-dir", type=Path, help="Output directory for reports.")
    parser.add_argument("--sample-every", type=float, default=1.0, help="Seconds between video screenshots.")
    parser.add_argument("--tessdata-dir", type=Path, help="Tesseract language data directory for video OCR.")
    parser.add_argument("--no-download-tessdata", action="store_true", help="Do not download missing OCR language data.")
    parser.add_argument("--psm", type=int, default=11, help="Tesseract page segmentation mode for video OCR.")
    parser.add_argument("--update", action="store_true", help="Force lexicon update before scanning.")
    parser.add_argument("--status", action="store_true", help="Show local lexicon status.")
    parser.add_argument("--fail-on-hit", action="store_true", help="Exit with code 2 when findings are present.")
    return parser.parse_args()


def infer_input(args: argparse.Namespace) -> tuple[str, str, Path | None]:
    explicit = [bool(args.text), bool(args.file), bool(args.video)]
    if sum(explicit) > 1:
        raise SystemExit("Use only one of --text, --file, or --video.")

    if args.video:
        return "video", str(args.video), args.video.expanduser()
    if args.file:
        return "file", str(args.file), args.file.expanduser()
    if args.text:
        return "text", args.text, None
    if args.input:
        possible_path = Path(args.input).expanduser()
        if possible_path.exists():
            suffix = possible_path.suffix.lower()
            if suffix in VIDEO_EXTS:
                return "video", str(possible_path), possible_path
            if suffix in TEXT_EXTS or possible_path.is_file():
                return "file", str(possible_path), possible_path
        return "text", args.input, None
    if not sys.stdin.isatty():
        return "text", sys.stdin.read(), None
    raise SystemExit("Provide text, a text file, a video file, or stdin.")


def run_video_scan(args: argparse.Namespace, video: Path) -> int:
    if not video.exists():
        print(f"视频不存在: {video}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(SCRIPT_DIR / "check_video.py"), str(video)]
    if args.output_dir:
        cmd.extend(["-o", str(args.output_dir)])
    if args.tessdata_dir:
        cmd.extend(["--tessdata-dir", str(args.tessdata_dir)])
    if args.no_download_tessdata:
        cmd.append("--no-download-tessdata")
    if args.psm:
        cmd.extend(["--psm", str(args.psm)])
    if args.update:
        cmd.append("--update")
    if args.sample_every != 1.0:
        cmd.extend(["--sample-every", str(args.sample_every)])

    completed = subprocess.run(cmd)
    return completed.returncode


def run_text_scan(args: argparse.Namespace, input_kind: str, input_label: str, path: Path | None) -> int:
    if args.update:
        update_words(force=True)
    elif needs_update():
        update_words()
        print()

    words = load_words()
    if not words:
        print("提示：本地词库为空，仅使用内置风险词。可运行 --update 拉取开源词库。", file=sys.stderr)

    if input_kind == "file":
        assert path is not None
        if not path.exists():
            print(f"文件不存在: {path}", file=sys.stderr)
            return 1
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        display_label = str(path)
    else:
        content = input_label.strip()
        display_label = "<inline text>"

    if not content:
        print("内容为空", file=sys.stderr)
        return 1

    hits = find_hits(content, words)
    print(format_result(content, hits))
    if args.output_dir:
        write_text_outputs(content, hits, display_label, args.output_dir)
        print(f"\n报告已写入: {args.output_dir / 'report.md'}")

    return 2 if args.fail_on_hit and hits else 0


def main() -> int:
    args = parse_args()
    if args.status:
        print_status()
        return 0
    if args.update and not any([args.input, args.text, args.file, args.video]) and sys.stdin.isatty():
        update_words(force=True)
        return 0
    if args.sample_every <= 0:
        raise SystemExit("--sample-every must be greater than 0.")

    input_kind, input_label, path = infer_input(args)
    if input_kind == "video":
        assert path is not None
        return run_video_scan(args, path)
    return run_text_scan(args, input_kind, input_label, path)


if __name__ == "__main__":
    raise SystemExit(main())
