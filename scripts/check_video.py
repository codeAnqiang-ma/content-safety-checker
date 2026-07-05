#!/usr/bin/env python3
"""
Video OCR forbidden-word checker.

Workflow:
  1. Extract one screenshot per second from a video.
  2. OCR each screenshot with Tesseract.
  3. Run the shared word matcher on each OCR result.
  4. Write a review summary, complete report, CSV hits, OCR JSONL, and frames.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check import find_hits  # noqa: E402
from update_words import load_words, needs_update, update_words  # noqa: E402


SKILL_DIR = Path(__file__).parent.parent
DATA_DIR = SKILL_DIR / "data"
DEFAULT_TESSDATA_DIR = DATA_DIR / "tessdata"
CHI_SIM_URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def require_bin(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"缺少依赖命令: {name}")
    return path


def video_duration_seconds(video: Path) -> float:
    completed = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(video),
        ],
        stdout=subprocess.PIPE,
    )
    return float(completed.stdout.strip())


def default_output_dir(video: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.cwd() / f"{video.stem}_content_check_{stamp}"


def extract_second_frames(video: Path, frames_dir: Path, sample_every: float) -> int:
    duration = video_duration_seconds(video)
    frame_count = max(1, int(math.ceil(duration / sample_every)))
    frames_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    for index in range(frame_count):
        second = round(index * sample_every, 3)
        safe_second = str(second).replace(".", "_")
        out = frames_dir / f"sec_{index:03d}_{safe_second}s.png"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(second),
            "-i",
            str(video),
            "-frames:v",
            "1",
            str(out),
        ]
        subprocess.run(cmd, check=True)
        if out.exists() and out.stat().st_size > 0:
            created += 1

    return created


def tesseract_langs() -> tuple[set[str], Path | None]:
    try:
        completed = run(["tesseract", "--list-langs"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception:
        return set(), None

    lines = completed.stdout.splitlines()
    tessdata_dir = None
    if lines:
        match = re.search(r'"([^"]+)"', lines[0])
        if match:
            tessdata_dir = Path(match.group(1))

    langs = {line.strip() for line in lines[1:] if line.strip()}
    return langs, tessdata_dir


def prepare_tessdata(tessdata_dir: Path, allow_download: bool) -> tuple[Path, str]:
    langs, system_tessdata = tesseract_langs()
    tessdata_dir.mkdir(parents=True, exist_ok=True)

    for lang in ("eng", "osd"):
        target = tessdata_dir / f"{lang}.traineddata"
        if not target.exists() and system_tessdata:
            source = system_tessdata / f"{lang}.traineddata"
            if source.exists():
                shutil.copyfile(source, target)

    chi_target = tessdata_dir / "chi_sim.traineddata"
    if not chi_target.exists() and "chi_sim" in langs and system_tessdata:
        source = system_tessdata / "chi_sim.traineddata"
        if source.exists():
            shutil.copyfile(source, chi_target)

    if not chi_target.exists() and allow_download:
        print("正在下载 Tesseract 简体中文 OCR 语言包 chi_sim.traineddata...", file=sys.stderr)
        urllib.request.urlretrieve(CHI_SIM_URL, chi_target)

    if chi_target.exists():
        return tessdata_dir, "chi_sim+eng"

    print("未找到 chi_sim 中文语言包，将降级为英文 OCR，中文画面文字可能漏检。", file=sys.stderr)
    return tessdata_dir if (tessdata_dir / "eng.traineddata").exists() else (system_tessdata or tessdata_dir), "eng"


def clean_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def ocr_frame(frame: Path, tessdata_dir: Path, language: str, psm: int) -> str:
    cmd = [
        "tesseract",
        str(frame),
        "stdout",
        "-l",
        language,
        "--tessdata-dir",
        str(tessdata_dir),
        "--psm",
        str(psm),
        "--dpi",
        "300",
    ]
    completed = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=90,
    )
    return clean_text(completed.stdout)


def write_ocr_jsonl(frames_dir: Path, ocr_path: Path, tessdata_dir: Path, language: str, psm: int) -> list[dict]:
    sec_re = re.compile(r"sec_(\d+)(?:_([0-9_]+)s)?\.png$")
    results = []

    with ocr_path.open("w", encoding="utf-8") as f:
        for frame in sorted(frames_dir.glob("sec_*.png")):
            match = sec_re.search(frame.name)
            second = int(match.group(1)) if match else -1
            timestamp = float(match.group(2).replace("_", ".")) if match and match.group(2) else float(second)
            item = {
                "second": second,
                "timestamp": timestamp,
                "frame": str(frame.resolve()),
                "text": ocr_frame(frame, tessdata_dir, language, psm),
            }
            results.append(item)
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return results


def analyze_ocr(ocr_items: list[dict], words: set[str]) -> list[dict]:
    analyzed = []
    for item in ocr_items:
        text = item.get("text", "").strip()
        analyzed.append(
            {
                "second": item["second"],
                "frame": item["frame"],
                "text": text,
                "hits": find_hits(text, words) if text else [],
            }
        )
    return analyzed


def flatten_hit_rows(results: list[dict]) -> list[dict]:
    rows = []
    for item in results:
        for hit in item["hits"]:
            for start, end in hit["positions"]:
                rows.append(
                    {
                        "second": item["second"],
                        "timestamp": item.get("timestamp", item["second"]),
                        "word": hit["word"],
                        "category": hit["category"],
                        "source": hit["source"],
                        "start": start,
                        "end": end,
                        "ocr_text": item["text"].replace("\n", " / "),
                        "frame": item["frame"],
                    }
                )
    return rows


def write_hits_csv(rows: list[dict], csv_path: Path) -> None:
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["second", "timestamp", "word", "category", "source", "start", "end", "ocr_text", "frame"],
        )
        writer.writeheader()
        writer.writerows(rows)


def is_low_confidence_short_ascii(word: str) -> bool:
    return len(word) <= 3 and all(ord(ch) < 128 for ch in word)


def seconds_for(rows: list[dict]) -> str:
    return ", ".join(str(second) for second in sorted({row.get("timestamp", row["second"]) for row in rows}))


def write_review_summary(video: Path, results: list[dict], rows: list[dict], summary_path: Path) -> None:
    by_word = defaultdict(list)
    for row in rows:
        by_word[row["word"]].append(row)

    focus_words = []
    low_confidence_words = []
    for word, word_rows in sorted(by_word.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if is_low_confidence_short_ascii(word):
            low_confidence_words.append((word, word_rows))
        else:
            focus_words.append((word, word_rows))

    checked = len(results)
    with_text = sum(1 for item in results if item["text"])
    hit_frames = len([item for item in results if item["hits"]])

    lines = [
        f"# {video.name} 视频 OCR 违禁词复核摘要",
        "",
        f"- 视频：`{video}`",
        f"- 抽帧：共 {checked} 张截图",
        f"- OCR 识别到文字的截图数：{with_text}",
        f"- 命中风险词的截图数：{hit_frames}",
        f"- 原始命中记录：{len(rows)} 条",
        "",
        "## 需要关注的画面文字",
        "",
    ]

    if focus_words:
        lines.extend(["| 词 | 秒数 | 类型 | 复核建议 |", "|---|---:|---|---|"])
        for word, word_rows in focus_words:
            lines.append(
                f"| `{word}` | {seconds_for(word_rows)} | {word_rows[0]['category']} | "
                "真实风险需结合画面语境判断；广告极限词在教程、排序、版本、评审语境中可能是误报。 |"
            )
    else:
        lines.append("未发现需要优先复核的非短英文命中。")

    lines.extend(["", "## 明显误报/低置信命中", ""])
    if low_confidence_words:
        lines.extend(
            [
                "以下命中主要是短英文词、缩写、代码/UI 片段或 OCR 噪声，建议不要直接按真实违禁词处理：",
                "",
                "| 词 | 秒数 | 说明 |",
                "|---|---:|---|",
            ]
        )
        for word, word_rows in low_confidence_words:
            lines.append(f"| `{word}` | {seconds_for(word_rows)} | 短英文词/缩写或 OCR 噪声，低置信。 |")
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## 文件",
            "",
        "- 完整 OCR 与原始命中报告：`report.md`",
            "- 原始命中 CSV：`hits.csv`",
            "- 原始 OCR JSONL：`ocr.jsonl`",
            "- 逐秒截图目录：`frames/`",
        ]
    )
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(video: Path, results: list[dict], rows: list[dict], report_path: Path) -> None:
    with_hits = [item for item in results if item["hits"]]
    unique_words = sorted({hit["word"] for item in with_hits for hit in item["hits"]})

    lines = [
        f"# {video.name} 每秒截图违禁词检测报告",
        "",
        f"- 检测截图数：{len(results)}",
        f"- OCR 识别到文字的截图数：{sum(1 for item in results if item['text'])}",
        f"- 命中风险词的截图数：{len(with_hits)}",
        f"- 命中记录数：{len(rows)}",
        f"- 命中词去重：{', '.join(unique_words) if unique_words else '无'}",
        "",
    ]

    if with_hits:
        lines.extend(["## 命中明细", ""])
        for item in with_hits:
            words_desc = "；".join(
                f"{hit['word']}（{hit['category']}，{hit['source']}）"
                for hit in item["hits"]
            )
            lines.extend(
                [
                    f"### {item['second']}s",
                    "",
                    f"- 命中：{words_desc}",
                    f"- 截图：`{item['frame']}`",
                    "- OCR 文本：",
                    "",
                    "```text",
                    item["text"],
                    "```",
                    "",
                ]
            )
    else:
        lines.extend(["## 结论", "", "未发现违禁词/敏感词命中。", ""])

    lines.extend(["## 每秒 OCR 文本", ""])
    for item in results:
        hit_words = ", ".join(hit["word"] for hit in item["hits"]) if item["hits"] else "无"
        text = item["text"] if item["text"] else "（未识别到文字）"
        lines.extend(
            [
                f"### {item['second']}s",
                "",
                f"- 命中：{hit_words}",
                "",
                "```text",
                text,
                "```",
                "",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抽帧并检测视频画面文字中的违禁词/敏感词")
    parser.add_argument("video", type=Path, help="视频文件路径")
    parser.add_argument("-o", "--output-dir", type=Path, default=None, help="输出目录，默认在当前目录创建")
    parser.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA_DIR, help="Tesseract 语言包目录")
    parser.add_argument("--no-download-tessdata", action="store_true", help="缺少中文 OCR 包时不自动下载")
    parser.add_argument("--psm", type=int, default=11, help="Tesseract 页面分割模式，默认 11")
    parser.add_argument("--sample-every", type=float, default=1.0, help="每隔多少秒抽一张图，默认 1 秒")
    parser.add_argument("--update", action="store_true", help="检测前强制更新敏感词库")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_every <= 0:
        print("--sample-every 必须大于 0", file=sys.stderr)
        return 1
    video = args.video.expanduser().resolve()
    if not video.exists():
        print(f"视频不存在: {video}", file=sys.stderr)
        return 1

    require_bin("ffmpeg")
    require_bin("ffprobe")
    require_bin("tesseract")

    if args.update:
        update_words(force=True)
    elif needs_update():
        update_words()

    words = load_words()
    if not words:
        print("本地词库为空，仅使用内置风险词。可运行 scripts/check.py --update 拉取开源词库。", file=sys.stderr)

    output_dir = (args.output_dir or default_output_dir(video)).expanduser().resolve()
    frames_dir = output_dir / "frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"输出目录: {output_dir}")
    frame_count = extract_second_frames(video, frames_dir, args.sample_every)
    print(f"已抽取截图: {frame_count} 张")

    tessdata_dir, language = prepare_tessdata(args.tessdata_dir, allow_download=not args.no_download_tessdata)
    print(f"OCR 语言: {language}")

    ocr_items = write_ocr_jsonl(frames_dir, output_dir / "ocr.jsonl", tessdata_dir, language, args.psm)
    results = analyze_ocr(ocr_items, words)
    rows = flatten_hit_rows(results)

    write_hits_csv(rows, output_dir / "hits.csv")
    write_report(video, results, rows, output_dir / "report.md")
    write_review_summary(video, results, rows, output_dir / "review_summary.md")

    hit_frames = len([item for item in results if item["hits"]])
    unique_words = sorted({row["word"] for row in rows})
    print(
        json.dumps(
            {
                "checked": len(results),
                "with_text": sum(1 for item in results if item["text"]),
                "hit_frames": hit_frames,
                "hit_rows": len(rows),
                "unique_words": unique_words,
                "review_summary": str(output_dir / "review_summary.md"),
                "report": str(output_dir / "report.md"),
                "csv": str(output_dir / "hits.csv"),
                "ocr_jsonl": str(output_dir / "ocr.jsonl"),
                "frames": str(frames_dir),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
