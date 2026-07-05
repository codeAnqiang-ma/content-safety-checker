# content-safety-checker

> 本地内容合规检测 Skill。无需 API Key，支持文本检测、文本文件检测，以及视频抽帧 OCR 后检测画面文字。

## 功能

- 🔴 **违禁词检测**：涵盖政治、暴恐、色情、涉枪涉爆等（3,000+ 词）
- 🟠 **平台限流词**：常见内容平台风险词，如"推广"、"加微信"、"优惠券"等
- 🟡 **广告极限词**：广告法违禁极限词，如"最好"、"第一"、"史上最"等
- 🟡 **医疗违禁词**：如"包治"、"根治"、"无副作用"等
- 📍 **上下文标注**：精确定位词在文案中的位置
- 🎬 **视频 OCR 检测**：按秒或自定义间隔截图，用 Tesseract OCR 识别画面文字后检测
- 🧭 **自动分流**：同一个 `check.py` 入口会根据用户输入自动判断文本、文本文件或视频文件
- 🔄 **每日自动更新**：每天首次使用自动从 GitHub 拉取最新词库

## 依赖

- Python 3.10+
- 文本检测：仅需 Python 标准库
- 视频检测：需安装 `ffmpeg`、`ffprobe`、`tesseract`
- 中文 OCR：脚本会在首次需要时下载 `chi_sim.traineddata` 到 `data/tessdata/`

## 词库来源（开源）

- [konsheng/Sensitive-lexicon](https://github.com/konsheng/Sensitive-lexicon) — MIT License
- [bigdata-labs/sensitive-stop-words](https://github.com/bigdata-labs/sensitive-stop-words)
- [jkiss/sensitive-words](https://github.com/jkiss/sensitive-words)

说明：仓库不再分发合并后的本地词库缓存；`data/sensitive_words.txt` 会在首次运行或执行 `--update` 时从上述公开来源生成。

## 安装

```bash
# 克隆到本机全局 skills 目录
git clone https://github.com/codeAnqiang-ma/content-safety-checker ~/.codex/skills/content-safety-checker

# 可选：暴露给其他本地 agent
ln -sfn ~/.codex/skills/content-safety-checker ~/.agents/skills/content-safety-checker
```

## 使用

安装后直接在 OpenClaw 对话中说：

> "帮我检测这段文案有没有违禁词：今天给大家推广一款产品..."

### 命令行直接使用

```bash
SKILL=~/.codex/skills/content-safety-checker

# 检测文案
python3 $SKILL/scripts/check.py "你的文案内容"

# 检测文件
python3 $SKILL/scripts/check.py -f script.txt

# 强制更新词库
python3 $SKILL/scripts/check.py --update

# 查看词库状态
python3 $SKILL/scripts/check.py --status

# 视频 OCR + 违禁词检测，统一入口会自动识别视频路径
python3 $SKILL/scripts/check.py /path/to/video.mp4 -o /path/to/output_dir

# 兼容入口仍可直接使用
python3 $SKILL/scripts/check_video.py /path/to/video.mp4 -o /path/to/output_dir
```

## 示例输出

```
🚨 发现 3 个风险词，建议修改后再发布

🟠 平台限流词（建议替换，影响流量）:
   ▸ 推广
     上下文: 今天给大家【推广】一款产品…

🟡 广告极限词（广告法风险）:
   ▸ 史上最
     上下文: …【史上最】好用！

── 标注后文案 ──
今天给大家【推广】一款产品，【史上最】好用！

📊 检测字数: 20 字 | 风险词: 3 个
```

## 隐私与网络说明

本 skill 仅在以下情况发起网络请求：

- 每天**首次使用时**自动从 `raw.githubusercontent.com` 拉取词库更新（3 个公开仓库）
- 请求目标均为公开 GitHub 仓库的原始文本文件，**无任何数据上传**
- 网络失败时自动降级为本地缓存，不影响正常使用
- 视频 OCR 首次缺少中文语言包时，会从 GitHub 下载 Tesseract `chi_sim.traineddata`
- 如需**完全离线使用**，可手动维护 `data/sensitive_words.txt`，并将 `data/.update_state.json` 中 `last_update` 设为未来日期以跳过自动更新

```json
// data/.update_state.json — 设置此值可禁用自动更新
{ "last_update": "2099-12-31", "word_count": 12345 }
```

## License

MIT
