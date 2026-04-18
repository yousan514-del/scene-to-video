# scene-to-video 🎬

**日本語シーン説明 → アニメ動画まで1コマンドで生成するフルパイプライン**

```
"メタバース大学の夕暮れ、窓際でホログラムを操る研究者"
        ↓  Qwen 2.5 (プロンプト生成 + モーション設計)
        ↓  ComfyUI t2i (waiIllustrous XL + LoRA stack)
        ↓  WAN 2.2 Lightning I2V (4-step, ~30秒/clip)
        ↓
output/20260418-120000/
  ├── 00_source.png    ← 生成静止画
  ├── 01_video.mp4     ← アニメ動画
  └── meta.json        ← プロンプト・設定・シード記録
```

---

## Quick Start

```bash
git clone https://github.com/yousan514-del/scene-to-video
cd scene-to-video
pip install -r requirements.txt

# ComfyUI が起動していること (http://127.0.0.1:8188)
# Ollama + qwen2.5-vl:7b が起動していること

py -3 scene_to_video.py "メタバース大学の仮想教室。AI研究者が夕暮れの窓際で思索している"
```

---

## Usage

```bash
# 基本
py -3 scene_to_video.py "シーン説明（日本語）"

# オプション指定
py -3 scene_to_video.py "未来都市の雨の夜" --steps 4 --duration 5 --fps 16

# サンプル一覧
py -3 scene_to_video.py --list-examples
```

---

## Pipeline Detail

| Step | Process | Time |
|------|---------|------|
| 1 | Qwen 2.5: JP scene → EN prompt + motion desc | ~5s |
| 2 | ComfyUI t2i: waiIllustrous XL (30steps) | ~20-40s |
| 3 | WAN 2.2 Lightning I2V (4steps) | ~30-90s |
| 4 | Output: PNG + MP4 + meta.json | instant |

**Total: ~1-3 minutes per scene** (RTX 4070, GGUF Q5)

---

## Requirements

- Python 3.10+
- ComfyUI at `http://127.0.0.1:8188`
- Ollama with `qwen2.5-vl:7b`
- GPU: RTX 4070+ (12GB VRAM)
- Models: waiIllustriousSDXL, WAN 2.2 I2V GGUF, Lightning LoRA

---

## Tech Stack

- **Qwen 2.5-VL** — scene description → structured prompts
- **ComfyUI API** — t2i pipeline control
- **waiIllustrous XL** — semi-realistic anime base model
- **WAN 2.2 Lightning** — 4-step fast I2V
- **Python 3.11** — pipeline orchestration

---

## Output Example

```json
// meta.json
{
  "scene": "メタバース大学の仮想教室。AI研究者が夕暮れの窓際で思索",
  "positive_prompt": "1girl, futuristic classroom, holographic displays...",
  "motion_prompt": "gentle hair sway in breeze, hologram flickers softly...",
  "steps": 4, "fps": 16, "duration": 3, "seed": 2847391023
}
```

---

## Creation Memo / 制作経緯

`anime-pipeline` の各スクリプトを「個別に使う」のではなく、
**1コマンドで全工程を通す**エンドツーエンドデモが必要だと判断した。

採用担当者やコラボ相手に「これを実行したらどうなるか」を
30秒で理解してもらうためのデモツール。

技術的なポイント：
- t2i と I2V の間で `seed` をずらして動きの多様性を確保
- Qwen のモーションプロンプト生成で「髪・布の物理」「カメラ動作」を自動設計
- メタデータを `meta.json` に保存することで再現性を担保

---

*Part of [AI Content Engineering Portfolio](https://github.com/yousan514-del)*
