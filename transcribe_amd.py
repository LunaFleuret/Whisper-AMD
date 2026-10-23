import os
import sys
import json
import math
import shutil
import urllib.request
import zipfile
import subprocess
import argparse
import re
import time
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

WHISPER_REPO = "Const-me/Whisper"
# モデル・バイナリの保存先 (プロジェクト直下の Whisper-models / bin フォルダ)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
MODELS_DIR = os.path.join(BIN_DIR, "Whisper-models")

# 推論に使用するCPUスレッド数
CPU_THREADS = min(8, os.cpu_count() or 8)

# サポートするモデルの定義
MODELS = {
    "large-v3-turbo": {
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin",
        "name": "ggml-large-v3-turbo.bin",
        "min_size": 1600000000  # 約1.6GB
    },
}
DEFAULT_MODEL = "large-v3-turbo"
EXE_NAME = "whisper-cli.exe"
VULKAN_ENGINE_URL = "https://github.com/eviscerations/whisper-windows-mcp/releases/download/v1.4.0/whisper-vulkan-win-x64.zip"

ORIGINAL_DIR = os.path.join(BASE_DIR, "Original")
OUTPUT_DIR = os.path.join(BASE_DIR, "Output")

def download_file(url, dest_path):
    print(f"ダウンロード中: {url}")
    print(f"保存先: {dest_path}")
    # 巨大なファイルのため、Windows標準のcurlを使ってレジューム可能かつ堅牢にダウンロードします
    cmd = ["curl.exe", "-L", "-C", "-", "-o", dest_path, url]
    try:
        subprocess.run(cmd, check=True)
        print("\nダウンロード完了！")
    except subprocess.CalledProcessError:
        print("\nダウンロード中にエラーが発生しました。ネットワーク接続を確認してください。")
        raise

def setup_environment(model_key):
    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(MODELS_DIR, exist_ok=True)
    
    exe_path = os.path.join(BIN_DIR, EXE_NAME)
    if not os.path.exists(exe_path):
        print("最新の Whisper (Vulkan対応版) が見つかりません。セットアップを開始します。")
        zip_path = "whisper-vulkan.zip"
        download_file(VULKAN_ENGINE_URL, zip_path)
        
        print("Zipファイルを解凍中...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(BIN_DIR)
        os.remove(zip_path)
        print("Whisper のセットアップが完了しました。")

    model_conf = MODELS[model_key]
    model_name = model_conf["name"]
    model_url = model_conf["url"]
    min_size = model_conf["min_size"]
    
    model_path = os.path.join(MODELS_DIR, model_name)
    
    # 完全にダウンロードされていない（サイズが指定された最小値未満の）場合はダウンロードを再開する
    needs_download = True
    if os.path.exists(model_path):
        size = os.path.getsize(model_path)
        if size >= min_size:
            needs_download = False
            
    if needs_download:
        print(f"\nモデルファイルが見つからない、または不完全です。ダウンロードを開始/再開します ({model_name})")
        print("※サイズが大きい(約1.6GB〜3.1GB)ため、回線によっては時間がかかります。")
        download_file(model_url, model_path)
        print("モデルファイルのダウンロードが完了しました。")

def format_time(ms):
    td = timedelta(milliseconds=ms)
    hours, remainder = divmod(td.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def format_duration_ja(seconds):
    seconds = int(round(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}時間{minutes}分{secs}秒"
    else:
        return f"{minutes}分{secs}秒"


def parse_time_to_ms(time_str):
    # 00:00:00,000 format
    time_str = time_str.strip()
    if ',' not in time_str:
        time_str += ',000'
    parts = time_str.split(',')
    hms = parts[0].split(':')
    return int(hms[0])*3600000 + int(hms[1])*60000 + int(hms[2])*1000 + int(parts[1])

def get_audio_duration(file_path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", file_path]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())

def find_silence_split_point(input_file, total_duration, target_sec=None):
    if target_sec is None:
        target_sec = total_duration / 2.0
    search_start = max(0.0, target_sec - 25.0)
    search_dur = min(50.0, total_duration - search_start)
    cmd = [
        "ffmpeg", "-ss", f"{search_start:.2f}", "-t", f"{search_dur:.2f}",
        "-i", input_file, "-af", "silencedetect=noise=-30dB:d=0.3",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    starts = re.findall(r"silence_start:\s*([0-9.]+)", res.stderr)
    if starts:
        candidates = [search_start + float(s) for s in starts]
        candidates.sort(key=lambda x: abs(x - target_sec))
        return candidates[0]
    return target_sec

def parse_srt_file(srt_path, offset_ms=0):
    if not os.path.exists(srt_path):
        return []
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read().strip()
    blocks = re.split(r'\n\n+', content)
    items = []
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            time_line = lines[1]
            text = " ".join(lines[2:]).strip()
            times = time_line.split(" --> ")
            if len(times) == 2 and text:
                start_ms = parse_time_to_ms(times[0]) + offset_ms
                end_ms = parse_time_to_ms(times[1]) + offset_ms
                items.append({"start": start_ms, "end": end_ms, "text": text})
    return items

def save_markdown_output(transcription, full_text_list, input_file, model_key, elapsed_sec, file_dir, base_name, output_dir=None):
    total_ms = transcription[-1]["end"]
    boundaries = [
        total_ms * 0.25,
        total_ms * 0.50,
        total_ms * 0.75
    ]
    current_part = 1
    
    out_dir = output_dir if output_dir else file_dir

    model_short_names = {
        "large-v3-turbo": "v3-T",
    }
    model_short = model_short_names.get(model_key, model_key)
    output_md_path = os.path.join(out_dir, f"{model_short}-{base_name}.md")
    
    with open(output_md_path, "w", encoding="utf-8") as md:
        md.write(f"# Transcription: {os.path.basename(input_file)}\n")
        md.write(f"- **Model**: {model_key}\n")
        md.write(f"- **Processing Time**: {format_duration_ja(elapsed_sec)} ({elapsed_sec:.3f} seconds)\n\n")
        md.write(f"## Part 1/4 (00:00:00 ~)\n\n")
        
        for segment in transcription:
            start_ms = segment["start"]
            end_ms   = segment["end"]
            text     = segment["text"]
            ts_start = format_time(start_ms)
            ts_end   = format_time(end_ms)
            
            if current_part <= len(boundaries) and start_ms >= boundaries[current_part - 1]:
                current_part += 1
                time_str = format_time(start_ms)
                md.write(f"\n\n## Part {current_part}/4 ({time_str} ~)\n\n")
                
            md.write(f"`[{ts_start} --> {ts_end}]` {text}  \n")
    full_text = " ".join(full_text_list)
    char_count = len(full_text.replace(" ", ""))

    audio_sec = total_ms / 1000.0
    speed_factor = (audio_sec / elapsed_sec) if elapsed_sec > 0 else 0
    duration_str = format_duration_ja(elapsed_sec)
    audio_duration_str = format_duration_ja(audio_sec)
    print(f"\n文字起こし完了:")
    print(f"  処理時間 : {duration_str} ({elapsed_sec:.2f}秒)")
    print(f"  音声時間 : {audio_duration_str}")
    print(f"  処理速度 : 約 {speed_factor:.1f} 倍速 (1秒あたり 約 {speed_factor / 60.0:.2f} 分)")
    print(f"  文字数   : {char_count} 文字")
    print(f"  出力先   : {output_md_path}")

    return {
        "md_path": output_md_path,
        "time_sec": elapsed_sec,
        "char_count": char_count,
        "audio_ms": total_ms,
        "text": full_text
    }

def transcribe_single(input_file, model_key, output_dir=None, beam_size=None):
    start_time = time.time()
    
    if beam_size is None:
        beam_size = 2
    else:
        beam_size = min(beam_size, 8)
        
    setup_environment(model_key)
    
    file_dir = os.path.dirname(os.path.abspath(input_file))
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    
    pid = os.getpid()
    temp_wav = os.path.join(file_dir, f"temp_whisper_work_{pid}.wav")
    srt_output_base = os.path.join(file_dir, f"temp_whisper_out_{pid}")
    srt_output_path = srt_output_base + ".srt"

    try:
        print(f"\n音声を抽出・音響最適化中: {input_file}")
        audio_filter = "highpass=f=80,lowpass=f=7500,afftdn=nf=-25,dynaudnorm=f=150:g=25:p=0.95:m=20.0"

        ffmpeg_cmd = [
            "ffmpeg", "-i", input_file,
            "-af", audio_filter,
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            "-y", temp_wav
        ]
        try:
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print("エラー: ffmpegによる音声変換に失敗しました。ffmpegがインストールされているか確認してください。")
            sys.exit(1)
            
        exe_path = os.path.join(BIN_DIR, EXE_NAME)
        model_conf = MODELS[model_key]
        model_path = os.path.join(MODELS_DIR, model_conf["name"])

        env = os.environ.copy()
        env["GGML_VULKAN_DEVICE"] = "0"

        print(f"Radeon GPU (Vulkan) を使用して文字起こしを開始します (単一プロセス)...")
        print(f"  モデル   : {model_key}")
        print(f"  スレッド : {CPU_THREADS}")
        print(f"  ビームサーチ: {beam_size}")
        print(f"  前処理   : 音量動的正規化(小声強化) + 反響/ノイズ低減フィルター")
        print(f"  無音閾値 : 0.50")
        print(f"  GPU      : GGML_VULKAN_DEVICE=0 (RX 9060 XT 指定)")

        if os.path.exists(srt_output_path):
            os.remove(srt_output_path)

        whisper_cmd = [
            exe_path,
            "-m", model_path,
            "-f", temp_wav,
            "-osrt",
            "-of", srt_output_base,
            "-l", "ja",
            "-t", str(CPU_THREADS),
            "-bs", str(beam_size),
            "-nth", "0.50",
            "-et", "2.40",
            "-nf",
            "-mc", "0",
        ]

        subprocess.run(whisper_cmd, check=True, env=env)
        
        if not os.path.exists(srt_output_path):
            print("エラー: SRT出力ファイルが生成されませんでした。")
            sys.exit(1)
            
        print("文字起こしが完了しました。Markdownを生成します...")
        transcription = parse_srt_file(srt_output_path, offset_ms=0)
                    
        if not transcription:
            print("文字起こし結果が空でした。")
            sys.exit(1)
            
        full_text_list = [s["text"] for s in transcription]
        elapsed_sec = time.time() - start_time
        return save_markdown_output(transcription, full_text_list, input_file, model_key, elapsed_sec, file_dir, base_name, output_dir)
    finally:
        for tmp_file in [temp_wav, srt_output_path]:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass

def transcribe_parallel(input_file, model_key, output_dir=None, beam_size=None, num_chunks=4):
    start_time = time.time()
    
    if beam_size is None:
        beam_size = 2
    else:
        beam_size = min(beam_size, 8)

    setup_environment(model_key)
    
    file_dir = os.path.dirname(os.path.abspath(input_file))
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    
    total_duration = get_audio_duration(input_file)
    if total_duration < 60.0:
        print(f"※音声長が短いため（{total_duration:.1f}秒）、単一プロセス処理に切り替えます。")
        return transcribe_single(input_file, model_key, output_dir=output_dir, beam_size=beam_size)

    print(f"\n{num_chunks}分割並列処理を開始します: {os.path.basename(input_file)}")
    print(f"  全体長    : {format_duration_ja(total_duration)} ({total_duration:.1f}秒)")
    
    # 各分割点を検出
    split_points = [0.0]
    for i in range(1, num_chunks):
        target = total_duration * (i / float(num_chunks))
        pt = find_silence_split_point(input_file, total_duration, target_sec=target)
        split_points.append(pt)
    split_points.append(total_duration)

    print(f"  無音分割数: {num_chunks} チャンク")
    for i in range(num_chunks):
        s_from = split_points[i]
        s_to = split_points[i + 1]
        print(f"    - チャンク{i+1}: {s_from:.2f}秒 ~ {s_to:.2f}秒 ({s_to - s_from:.1f}秒)")

    pid = os.getpid()
    wav_files = [os.path.join(file_dir, f"temp_whisper_c{i+1}_{pid}.wav") for i in range(num_chunks)]
    out_bases = [os.path.join(file_dir, f"temp_whisper_out{i+1}_{pid}") for i in range(num_chunks)]
    srt_paths = [b + ".srt" for b in out_bases]

    audio_filter = "highpass=f=80,lowpass=f=7500,afftdn=nf=-25,dynaudnorm=f=150:g=25:p=0.95:m=20.0"

    try:
        print(f"  [1/3] 音声抽出・音響最適化中 ({num_chunks}チャンク並行)...")
        t_ff_start = time.time()
        
        def run_ffmpeg_chunk(idx):
            s_from = split_points[idx]
            s_to = split_points[idx + 1]
            cmd = [
                "ffmpeg", "-ss", f"{s_from:.3f}", "-to", f"{s_to:.3f}", "-i", input_file,
                "-af", audio_filter, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                "-y", wav_files[idx]
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        with ThreadPoolExecutor(max_workers=num_chunks) as ex:
            futures = [ex.submit(run_ffmpeg_chunk, i) for i in range(num_chunks)]
            for f in futures:
                f.result()
        print(f"  FFmpeg抽出完了 ({time.time() - t_ff_start:.2f}秒)")

        exe_path = os.path.join(BIN_DIR, EXE_NAME)
        model_conf = MODELS[model_key]
        model_path = os.path.join(MODELS_DIR, model_conf["name"])

        env = os.environ.copy()
        env["GGML_VULKAN_DEVICE"] = "0"
        
        threads_per_chunk = max(2, CPU_THREADS // num_chunks)

        print(f"  [2/3] Radeon GPU (Vulkan) 並列文字起こし実行中 ({num_chunks}プロセス x {threads_per_chunk}スレッド)...")

        def run_whisper(wav_file, out_base):
            cmd = [
                exe_path,
                "-m", model_path,
                "-f", wav_file,
                "-osrt",
                "-of", out_base,
                "-l", "ja",
                "-t", str(threads_per_chunk),
                "-bs", str(beam_size),
                "-nth", "0.50",
                "-et", "2.40",
                "-nf",
                "-mc", "0",
            ]
            subprocess.run(cmd, check=True, env=env)

        t_inf_start = time.time()
        with ThreadPoolExecutor(max_workers=num_chunks) as ex:
            futures = [ex.submit(run_whisper, wav_files[i], out_bases[i]) for i in range(num_chunks)]
            for f in futures:
                f.result()
        print(f"  Whisper並列推論完了 ({time.time() - t_inf_start:.2f}秒)")

        print("  [3/3] 結果をマージしてMarkdownを生成中...")
        transcription = []
        for i in range(num_chunks):
            offset_ms = int(split_points[i] * 1000)
            segs = parse_srt_file(srt_paths[i], offset_ms=offset_ms)
            transcription.extend(segs)

        if not transcription:
            print("エラー: 文字起こし結果が空でした。")
            sys.exit(1)

        full_text_list = [s["text"] for s in transcription]
        elapsed_sec = time.time() - start_time
        return save_markdown_output(transcription, full_text_list, input_file, model_key, elapsed_sec, file_dir, base_name, output_dir)
    finally:
        for tmp in wav_files + srt_paths:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

def transcribe(input_file, model_key, output_dir=None, beam_size=None, num_chunks=4):
    if not os.path.exists(input_file):
        print(f"エラー: 入力ファイル '{input_file}' が見つかりません。")
        sys.exit(1)
    if num_chunks > 1:
        return transcribe_parallel(input_file, model_key, output_dir=output_dir, beam_size=beam_size, num_chunks=num_chunks)
    else:
        return transcribe_single(input_file, model_key, output_dir=output_dir, beam_size=beam_size)

def process_folder(model_key, beam_size=None, num_chunks=4):
    os.makedirs(ORIGINAL_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    supported_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.flv', '.webm', '.aac', '.ogg')
    files = [f for f in os.listdir(ORIGINAL_DIR) if f.lower().endswith(supported_extensions)]
    
    if not files:
        print("Originalフォルダに処理対象のファイルが見つかりません。")
        print("動画や音声ファイルを Original フォルダに入れてから実行してください。")
        return
        
    print(f"合計 {len(files)} 件のファイルを処理します...")
    for file in files:
        input_file = os.path.join(ORIGINAL_DIR, file)
        print(f"\n=======================================================")
        print(f"処理開始: {file} (モデル: {model_key}, チャンク数: {num_chunks})")
        print(f"=======================================================")
        try:
            transcribe(input_file, model_key, output_dir=OUTPUT_DIR, beam_size=beam_size, num_chunks=num_chunks)
        except SystemExit:
            print(f"ファイル '{file}' の処理中にエラーが発生しスキップしました。")
        except Exception as e:
            print(f"予期せぬエラーが発生しました: {e}")
            
    print("\nすべてのファイルの処理が完了しました！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Whisper Transcription Script for AMD GPU (Vulkan)")
    parser.add_argument(
        "paths",
        nargs="*",
        help="対象ファイルまたはフォルダのパス"
    )
    parser.add_argument(
        "--model", 
        choices=list(MODELS.keys()), 
        default=DEFAULT_MODEL, 
        help=f"使用するWhisperモデル (デフォルト: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--beam-size", "-bs",
        type=int,
        default=None,
        help="ビームサーチ幅 (省略時はlarge-v3が8、その他は5)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=None,
        help="出力先ディレクトリ (省略時は対象ファイルと同じディレクトリ)"
    )
    parser.add_argument(
        "--chunks", "-c",
        type=int,
        choices=list(range(1, 9)),
        default=4,
        help="分割並列処理のチャンク数 (1: 通常, 2〜8: N分割並列, デフォルト: 4)"
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="単一プロセス処理を強制する (chunks=1と同等)"
    )
    args = parser.parse_args()
    
    effective_chunks = 1 if args.single else args.chunks
    
    if args.paths:
        supported_exts = ('.mp4', '.mkv', '.avi', '.mov', '.mp3', '.wav', '.m4a', '.flv', '.webm', '.aac', '.ogg')
        target_files = []
        for p in args.paths:
            p = os.path.abspath(p)
            if os.path.isfile(p):
                target_files.append(p)
            elif os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        if f.lower().endswith(supported_exts):
                            target_files.append(os.path.join(root, f))
        
        if not target_files:
            print("処理対象の音声/動画ファイルが見つかりませんでした。")
            sys.exit(1)
            
        print(f"合計 {len(target_files)} 件のファイルを処理します (チャンク数: {effective_chunks})...")
        for target in target_files:
            print(f"\n=======================================================")
            print(f"処理開始: {os.path.basename(target)} (モデル: {args.model})")
            print(f"=======================================================")
            try:
                transcribe(target, args.model, output_dir=args.output_dir, beam_size=args.beam_size, num_chunks=effective_chunks)
            except SystemExit:
                print(f"ファイル '{target}' の処理中にエラーが発生しました。")
            except Exception as e:
                print(f"予期せぬエラーが発生しました: {e}")
        print("\nすべてのファイルの処理が完了しました！")
    else:
        process_folder(args.model, beam_size=args.beam_size, num_chunks=effective_chunks)


