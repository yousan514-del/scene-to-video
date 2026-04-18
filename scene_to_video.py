#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scene-to-video — テキスト指示から動画まで1コマンドで
=====================================================
日本語シーン説明 → Qwen でプロンプト生成 → ComfyUI t2i → WAN 2.2 I2V → MP4

使い方:
  py -3 scene_to_video.py "メタバース大学の仮想教室。AI研究者が夕暮れの窓際で本を読んでいる"
  py -3 scene_to_video.py "未来都市の街角、雨の夜" --steps 4 --duration 5
  py -3 scene_to_video.py --list-examples

出力:
  output/YYYYMMDD-HHMMSS/
    ├── 00_source.png        ← t2i 生成静止画
    ├── 01_video.mp4         ← I2V 生成動画
    └── meta.json            ← 生成メタデータ（プロンプト・設定・時間）
"""

import argparse
import json
import shutil
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ─── 設定 ─────────────────────────────────────────────────────────────────────
COMFYUI_URL   = "http://127.0.0.1:8188"
COMFYUI_INPUT = Path("D:/ComfyUI/input")
COMFYUI_OUT   = Path("D:/ComfyUI/output")
OUTPUT_DIR    = Path(__file__).parent / "output"
OLLAMA_URL    = "http://127.0.0.1:11434"
QWEN_MODEL    = "qwen2.5vl:7b"

# t2i モデル設定
T2I_CHECKPOINT = "waiIllustriousSDXL_v160.safetensors"
T2I_LORAS = [
    {"name": "Anime_artistic_2.safetensors",     "strength": 0.6},
    {"name": "DetailedEyes_V3.safetensors",      "strength": 0.5},
    {"name": "Smooth_Booster_v4.safetensors",    "strength": 0.3},
    {"name": "cfg_scale_boost.safetensors",      "strength": 0.4},
]

# I2V モデル設定
I2V_UNET      = "Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf"
I2V_CLIP      = "umt5-xxl-encoder-Q5_K_M.gguf"
I2V_CLIP_VIS  = "wan21NSFWClipVisionH_v10.safetensors"
I2V_VAE       = "wan_2.1_vae.safetensors"   # GGUF is WAN2.1 arch (36-ch patch_embed)
# Lightning LoRA (fp16) is incompatible with GGUF Q5: wrong patch_embedding channels
# I2V_LORA_LIGHT = disabled

T2I_QUALITY_SUFFIX = (
    "masterpiece, best quality, ultra detailed, cinematic lighting, "
    "depth of field, soft ambient light"
)
NEGATIVE = "bad quality, bad anatomy, low quality, blurry, worst quality, deformed"

PROMPT_SYSTEM = """You are a master ComfyUI prompt engineer for anime/semi-realistic illustration.
World: 2040 near-future, metaverse university, AI staff members, futuristic cities.
Style: semi-realistic anime, quiet intellectual tone, cinematic.

Convert the Japanese scene description to ONE detailed English ComfyUI prompt:
- Start: main subject → environment → lighting → atmosphere → style tags
- Use booru-style comma-separated tags
- Output ONLY the prompt as a single line, no explanation."""

MOTION_SYSTEM = """You are an anime motion director for image-to-video generation.
Analyze the scene and generate a short motion description (1-2 sentences):
- Focus on: character physics (hair/cloth), camera movement, ambient changes
- Style: subtle, cinematic, anime physics
- Output ONLY the motion description in English."""

EXAMPLES = [
    "メタバース大学の仮想教室。AI研究者が夕暮れの窓際で本を読んでいる",
    "未来都市の街角、雨の夜。傘を持った少女が光るホログラム看板の前で立ち止まる",
    "AI社員（Future Researcher）の研究室。ホログラムデータを操作している",
    "2040年の大学図書館。浮かぶデータの中で眠る学生",
    "メタバース空間に浮かぶ仮想島。夕焼けと海",
]


# ─── API ユーティリティ ────────────────────────────────────────────────────────
def api_post(endpoint: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{COMFYUI_URL}{endpoint}",
        data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[HTTP {e.code}] {e.read().decode('utf-8','replace')[:500]}")
        raise


def wait_done(prompt_id: str, timeout: int = 600) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"{COMFYUI_URL}/history/{prompt_id}", timeout=10
            ) as r:
                h = json.loads(r.read())
            if prompt_id in h and h[prompt_id].get("outputs"):
                return True
        except Exception:
            pass
        time.sleep(4)
    return False


def find_output(prompt_id: str, types=("images", "gifs", "videos")) -> Path | None:
    try:
        with urllib.request.urlopen(
            f"{COMFYUI_URL}/history/{prompt_id}", timeout=10
        ) as r:
            h = json.loads(r.read())
        for node_out in h.get(prompt_id, {}).get("outputs", {}).values():
            for t in types:
                for item in node_out.get(t, []):
                    p = COMFYUI_OUT / item.get("subfolder", "") / item["filename"]
                    if p.exists():
                        return p
    except Exception:
        pass
    return None


# ─── Qwen ─────────────────────────────────────────────────────────────────────
def qwen_text(system: str, prompt: str) -> str:
    payload = {
        "model": QWEN_MODEL,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 256, "temperature": 0.7},
    }
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
        text = result.get("response", "").strip()
        if "<think>" in text:
            text = text.split("</think>")[-1].strip()
        return text
    except Exception as e:
        return f"[QWEN ERROR] {e}"


# ─── ワークフロー ──────────────────────────────────────────────────────────────
def build_t2i_workflow(positive: str, seed: int) -> dict:
    """waiIllustrous + LoRA stack の t2i ワークフロー"""
    wf = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": T2I_CHECKPOINT},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["1", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": NEGATIVE, "clip": ["1", 1]},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "model": None,          # LoRA chain の末端を後で設定
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
                "seed": seed,
                "steps": 30,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m_sde",
                "scheduler": "karras",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["1", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["8", 0], "filename_prefix": "scene2vid_t2i"},
        },
    }

    # LoRA チェーン: 1(model) → L0 → L1 → L2 → L3 → KSampler
    prev = ["1", 0]
    for i, lora in enumerate(T2I_LORAS):
        nid = f"L{i}"
        wf[nid] = {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {
                "lora_name": lora["name"],
                "strength_model": lora["strength"],
                "model": prev,
            },
        }
        prev = [nid, 0]

    wf["3"]["inputs"]["model"] = prev
    return wf


def build_i2v_workflow(image_name: str, steps: int, fps: int,
                       duration: int, seed: int) -> dict:
    """WAN 2.1 GGUF I2V ワークフロー (20-step, LoRA なし)

    Note: Lightning LoRA fp16 は GGUF Q5_K_M と patch_embedding チャネル数不一致のため除外。
          WAN 2.2 VAE (48-ch) の代わりに WAN 2.1 VAE (16-ch) を使用。
    """
    import random
    if seed < 0:
        seed = random.randint(0, 2**32 - 1)
    n = (int(duration * fps) - 1) // 4
    frames = 4 * n + 1

    return {
        "10": {"class_type": "UnetLoaderGGUF",  "inputs": {"unet_name": I2V_UNET}},
        "13": {"class_type": "ModelSamplingSD3", "inputs": {"shift": 5.0, "model": ["10", 0]}},
        "20": {"class_type": "CLIPLoaderGGUF",  "inputs": {"clip_name": I2V_CLIP, "type": "wan"}},
        "21": {"class_type": "VAELoader",        "inputs": {"vae_name": I2V_VAE}},
        "22": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": I2V_CLIP_VIS}},
        "30": {"class_type": "LoadImage",        "inputs": {"image": image_name, "upload": "image"}},
        "31": {"class_type": "CLIPVisionEncode", "inputs": {
            "clip_vision": ["22", 0], "image": ["30", 0], "crop": "center"}},
        "40": {"class_type": "CLIPTextEncode",   "inputs": {"text": T2I_QUALITY_SUFFIX, "clip": ["20", 0]}},
        "41": {"class_type": "CLIPTextEncode",   "inputs": {"text": NEGATIVE, "clip": ["20", 0]}},
        "50": {"class_type": "WanImageToVideo",  "inputs": {
            "positive": ["40", 0], "negative": ["41", 0], "vae": ["21", 0],
            "width": 832, "height": 1216, "length": frames, "batch_size": 1,
            "clip_vision_output": ["31", 0], "start_image": ["30", 0]}},
        "60": {"class_type": "KSamplerSelect",   "inputs": {"sampler_name": "euler"}},
        "61": {"class_type": "BasicScheduler",   "inputs": {
            "model": ["13", 0], "scheduler": "simple", "steps": steps, "denoise": 1.0}},
        "62": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["63", 0], "guider": ["64", 0], "sampler": ["60", 0],
            "sigmas": ["61", 0], "latent_image": ["50", 2]}},
        "63": {"class_type": "RandomNoise",      "inputs": {"noise_seed": seed}},
        "64": {"class_type": "CFGGuider",        "inputs": {
            "model": ["13", 0], "positive": ["50", 0], "negative": ["50", 1], "cfg": 1.0}},
        "70": {"class_type": "VAEDecodeTiled",   "inputs": {
            "samples": ["62", 0], "vae": ["21", 0],
            "tile_size": 256, "overlap": 64, "temporal_size": 64, "temporal_overlap": 8}},
        "80": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["70", 0], "frame_rate": fps, "loop_count": 0,
            "filename_prefix": "scene2vid_i2v", "format": "video/h264-mp4",
            "pingpong": False, "save_output": True}},
    }


# ─── メイン ────────────────────────────────────────────────────────────────────
def run(scene: str, steps: int = 20, fps: int = 16,
        duration: int = 3, seed: int = -1) -> Path:
    """シーン説明 → 静止画 → 動画 の全パイプライン"""
    import random
    if seed < 0:
        seed = random.randint(0, 2**32 - 1)

    ts      = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUTPUT_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "scene": scene, "timestamp": ts,
        "steps": steps, "fps": fps, "duration": duration, "seed": seed,
    }

    # ── Step 1: プロンプト生成 ─────────────────────────────────────────────────
    print(f"\n[1/4] Qwen でプロンプト生成中...")
    positive = qwen_text(PROMPT_SYSTEM, f"シーン: {scene}")
    positive = f"{positive}, {T2I_QUALITY_SUFFIX}"
    motion   = qwen_text(MOTION_SYSTEM, f"シーン: {scene}")
    print(f"  プロンプト: {positive[:80]}...")
    print(f"  モーション: {motion[:80]}...")
    meta["positive_prompt"] = positive
    meta["motion_prompt"]   = motion

    # ── Step 2: t2i 静止画生成 ─────────────────────────────────────────────────
    print(f"\n[2/4] ComfyUI t2i 生成中（1024×1024, 30steps）...")
    wf_t2i    = build_t2i_workflow(positive, seed)
    pid_t2i   = api_post("/prompt", {"prompt": wf_t2i})["prompt_id"]
    print(f"  Queue: {pid_t2i[:8]}...")

    ok = wait_done(pid_t2i, timeout=300)
    if not ok:
        raise TimeoutError("t2i 生成タイムアウト")

    still = find_output(pid_t2i, types=("images",))
    if not still:
        raise FileNotFoundError("t2i 出力が見つかりません")

    still_dest = out_dir / "00_source.png"
    shutil.copy2(still, still_dest)
    shutil.copy2(still, COMFYUI_INPUT / still.name)
    print(f"  → {still_dest.name} ({still.stat().st_size // 1024}KB)")

    # ── Step 3: I2V 動画生成 ─────────────────────────────────────────────────
    print(f"\n[3/4] WAN 2.2 Lightning I2V 生成中（{steps}steps, {duration}s）...")
    wf_i2v  = build_i2v_workflow(still.name, steps, fps, duration, seed + 1)
    pid_i2v = api_post("/prompt", {"prompt": wf_i2v})["prompt_id"]
    print(f"  Queue: {pid_i2v[:8]}...")

    ok = wait_done(pid_i2v, timeout=900)
    if not ok:
        raise TimeoutError("I2V 生成タイムアウト")

    video = find_output(pid_i2v, types=("videos", "gifs"))
    if not video:
        raise FileNotFoundError("I2V 出力が見つかりません")

    video_dest = out_dir / "01_video.mp4"
    shutil.copy2(video, video_dest)
    print(f"  → {video_dest.name} ({video.stat().st_size // 1024}KB)")

    # ── Step 4: メタデータ保存 ────────────────────────────────────────────────
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[4/4] 完了!")
    print(f"  出力: {out_dir}/")
    print(f"  └─ 00_source.png, 01_video.mp4, meta.json")

    return out_dir


def main():
    parser = argparse.ArgumentParser(
        description="シーン説明 → 静止画 → 動画 (ComfyUI × WAN 2.2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n例:\n  py -3 scene_to_video.py \"メタバース大学の夕暮れ、窓際の研究者\""
    )
    parser.add_argument("scene", nargs="?", help="日本語シーン説明")
    parser.add_argument("--steps",    type=int, default=20, help="I2V ステップ数（default: 20）")
    parser.add_argument("--fps",      type=int, default=16, help="FPS（default: 16）")
    parser.add_argument("--duration", type=int, default=3,  help="動画秒数（default: 3）")
    parser.add_argument("--seed",     type=int, default=-1, help="乱数シード（default: random）")
    parser.add_argument("--list-examples", action="store_true", help="サンプルシーン一覧")
    args = parser.parse_args()

    if args.list_examples:
        print("サンプルシーン一覧:")
        for i, ex in enumerate(EXAMPLES, 1):
            print(f"  {i}. {ex}")
        return

    if not args.scene:
        parser.print_help()
        sys.exit(1)

    run(args.scene, args.steps, args.fps, args.duration, args.seed)


if __name__ == "__main__":
    main()
