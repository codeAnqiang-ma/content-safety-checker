---
name: douyin-sensitive-check
description: 抖音/短视频违禁词和敏感词检测（本地词库版，无需 API Key），支持文案文本检测，也支持视频逐秒截图 OCR 后逐秒检测画面文字。每天首次使用自动从 GitHub 开源词库更新本地缓存，离线检测文案合规性。支持多词库合并（广告极限词、平台限流词、暴恐、色情、涉枪涉爆等）。使用场景：(1) 生成短视频文案后自动检测违禁词，(2) 用户要求检查某段文字是否有问题，(3) 抖音/快手/B站内容合规审核，(4) 直播话术自查，(5) 用户给视频文件要求每秒截图、OCR 画面文字并验证是否含违禁词。触发词：违禁词、敏感词、检测、合规、抖音风控、限流词、能不能发、视频违禁词、逐秒截图、OCR 检查。
---

# 抖音违禁词检测 Skill（开源词库版）

本地词库 + 每日自动更新，无需 API Key，离线可用。

## 脚本路径

```
scripts/
  check.py          # 文案检测脚本（入口）
  check_video.py    # 视频逐秒截图 + OCR + 逐秒检测脚本
  update_words.py   # 词库更新模块（每天首次自动触发）
data/              # 运行时生成，词库缓存目录（.gitignore 排除缓存文件）
  .gitkeep
  sensitive_words.txt      # 首次运行/更新后生成
  .update_state.json       # 首次运行/更新后生成
  tessdata/                # 视频 OCR 需要时缓存 Tesseract 中文语言包
```

## 常用命令

```bash
SKILL=~/.codex/skills/douyin-sensitive-check

# 检测一段文案
python3 $SKILL/scripts/check.py "今天给大家推荐史上最好用的护肤品，加我微信领优惠券"

# 检测文件
python3 $SKILL/scripts/check.py -f /path/to/script.txt

# 管道
echo "文案内容" | python3 $SKILL/scripts/check.py

# 强制更新词库
python3 $SKILL/scripts/check.py --update

# 查看词库状态
python3 $SKILL/scripts/check.py --status

# 视频逐秒截图 + OCR + 违禁词检测
# 依赖: ffmpeg, ffprobe, tesseract。首次缺少中文 OCR 包时自动下载到 data/tessdata/
python3 $SKILL/scripts/check_video.py /path/to/video.mp4 -o /path/to/output_dir
```

## 工作流

### 文案检测

1. **每天首次运行** → 自动调用 `update_words.py` 从 3 个 GitHub 开源词库拉取最新内容合并
2. 加载本地 `data/sensitive_words.txt`（去重合并，含数万词条）
3. 对输入文案做全文子串匹配（长词优先）
4. 输出：🔴 违禁词（必改）/ 🟡 广告极限词（建议改）+ 上下文标注
5. 根据结果帮用户改写文案，改完后再次检测直到通过

### 视频逐秒截图检测

当用户给视频并要求检查画面里的违禁词时，直接运行：

```bash
python3 $SKILL/scripts/check_video.py /path/to/video.mp4 -o /path/to/output_dir
```

脚本流程：

1. 用 `ffprobe` 获取视频时长，用 `ffmpeg` 从 `0s` 到 `floor(duration)s` 每秒抽 1 张 `frames/sec_XXX.png`。
2. 用 Tesseract 对每张截图做 OCR，优先使用 `chi_sim+eng`。若系统没有 `chi_sim`，自动下载到 `data/tessdata/`；如需禁用下载，加 `--no-download-tessdata`。
3. 对每秒 OCR 文本调用 `check.py` 的同一套 `find_hits` 逻辑。
4. 输出：
   - `review_summary.md`：复核摘要，区分需要关注的词和短英文/OCR 噪声等低置信命中
   - `report.md`：完整逐秒 OCR 文本和原始命中明细
   - `hits.csv`：结构化命中结果
   - `ocr.jsonl`：逐秒 OCR 原始结果
   - `frames/`：逐秒截图

复核时注意：词库是子串匹配，`第一步`、`第一版`、`可靠性最高`、`最好依赖服务端` 等教程/评审语境可能被标为广告极限词；`AV`、`BT`、`JS`、`ma`、`sb` 等短英文词常见于 OCR 噪声、代码或 UI 缩写，默认在摘要中列为低置信命中。

## 词库来源

- `konsheng/Sensitive-lexicon`：广告、政治、暴恐、色情、涉枪涉爆、补充词库
- `bigdata-labs/sensitive-stop-words`：广告、政治、色情、涉枪涉爆
- `jkiss/sensitive-words`：广告、政治、色情

## 更新机制

- `data/.update_state.json` 记录最后更新日期
- 每天第一次使用自动触发，当天内后续使用直接读缓存
- 网络失败时保留本地缓存，不影响使用
- 手动强制更新：`--update`

## 重要提示

- 开源词库以通用违禁词为主，抖音平台的部分特有限流词（如"私信"、"加微信"）已内置在 `check.py` 的 `CATEGORY_PATTERNS` 中补充
- 匹配策略是子串匹配，可能有误报；如需精确匹配可编辑 `data/sensitive_words.txt` 删除误报词
- 视频检测依赖 OCR，低对比度、小字号、动态模糊、遮挡字幕会影响识别；重要结果要抽查对应秒数截图
- 改写建议：被标注词优先用谐音、符号分割、同义替换等方式规避
