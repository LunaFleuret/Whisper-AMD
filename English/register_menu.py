#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Whisper 英語専用 右クリックコンテキストメニュー登録・削除スクリプト

Windowsのエクスプローラーで音声・動画ファイルやフォルダを右クリックした際に、
「Whisperで文字起こし【英語】」サブメニューを追加・削除します。

HKEY_CURRENT_USER\Software\Classes を使用するため管理者権限は不要です。
"""

import os
import sys
import winreg
import argparse
import tkinter as tk
from tkinter import messagebox

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
MENU_TITLE = "Whisperで文字起こし【英語】"
REG_KEY_NAME = "WhisperTranscribeEnglish"

MODELS = [
    ("large-v3-turbo", "large-v3-turbo (Standard Fast)"),
    ("large-v3-turbo-q5_0", "large-v3-turbo-q5_0 (Recommended / Ultra Fast)"),
    ("large-v3", "large-v3 (Best Accuracy / Slower)"),
]

TARGET_EXTENSIONS = [
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".flv", ".mp3", ".wav", ".m4a", ".aac",
    ".ogg", ".wma", ".flac"
]

def get_script_path():
    """実行対象の transcribe_amd.py の絶対パスを取得"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "transcribe_amd.py")

def get_python_exe():
    """python.exe のパスを取得（pythonwではなくpython）"""
    exe = sys.executable
    if exe.endswith("pythonw.exe"):
        exe = os.path.join(os.path.dirname(exe), "python.exe")
    return exe

# ─────────────────────────────────────────────
# レジストリ操作
# ─────────────────────────────────────────────
def register_context_menu():
    """右クリックメニューをレジストリに登録"""
    unregister_context_menu()  # 古いキーや重複項目を完全にクリーンアップ
    script_path = get_script_path()
    python_exe = get_python_exe()
    
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"スクリプトが見つかりません: {script_path}")

    targets = [f"SystemFileAssociations\\{ext}" for ext in TARGET_EXTENSIONS]
    targets.append("Directory")  # フォルダの右クリックにも対応

    for target_base in targets:
        main_key_path = f"Software\\Classes\\{target_base}\\shell\\{REG_KEY_NAME}"
        
        # 親キー登録 ("Whisperで文字起こし【英語】")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, main_key_path) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, MENU_TITLE)
            winreg.SetValueEx(key, "SubCommands", 0, winreg.REG_SZ, "")
            
        sub_shell_path = f"{main_key_path}\\shell"
        
        for idx, (model_key, model_label) in enumerate(MODELS, start=1):
            item_key_path = f"{sub_shell_path}\\Item{idx}_{model_key}"
            command_key_path = f"{item_key_path}\\command"
            
            # 各モデル用サブメニュー設定
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, item_key_path) as item_key:
                winreg.SetValueEx(item_key, "", 0, winreg.REG_SZ, model_label)
                
            # コマンド設定
            # cmd /k で実行し、結果表示後にウィンドウが残るように設定
            cmd_str = f'cmd.exe /k ""{python_exe}" "{script_path}" --model "{model_key}" "%1""'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, command_key_path) as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd_str)

def unregister_context_menu():
    """右クリックメニューをレジストリから削除"""
    targets = [f"SystemFileAssociations\\{ext}" for ext in TARGET_EXTENSIONS]
    targets.append("Directory")

    for target_base in targets:
        main_key_path = f"Software\\Classes\\{target_base}\\shell\\{REG_KEY_NAME}"
        delete_key_recursive(winreg.HKEY_CURRENT_USER, main_key_path)

def delete_key_recursive(root_key, subkey):
    """キーとその配下のサブキーを再帰的に削除"""
    try:
        with winreg.OpenKey(root_key, subkey, 0, winreg.KEY_ALL_ACCESS) as key:
            info = winreg.QueryInfoKey(key)
            for _ in range(info[0]):
                sub_name = winreg.EnumKey(key, 0)
                delete_key_recursive(root_key, f"{subkey}\\{sub_name}")
        winreg.DeleteKey(root_key, subkey)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"キー削除エラー ({subkey}): {e}")

def is_registered():
    """メニューが登録済みか判定"""
    test_key_path = f"Software\\Classes\\SystemFileAssociations\\.mp4\\shell\\{REG_KEY_NAME}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key_path, 0, winreg.KEY_READ):
            return True
    except FileNotFoundError:
        return False

# ─────────────────────────────────────────────
# GUI (Tkinter)
# ─────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Whisper 英語文字起こし 右クリックメニュー設定")
        self.root.geometry("450x240")
        self.root.resizable(False, False)
        
        frame = tk.Frame(root, padx=20, pady=20)
        frame.pack(fill=tk.BOTH, expand=True)

        title_label = tk.Label(frame, text="Whisper 英語文字起こし 右クリックメニュー登録", font=("Helvetica", 12, "bold"))
        title_label.pack(pady=(0, 10))

        self.status_var = tk.StringVar()
        self.update_status()
        
        status_label = tk.Label(frame, textvariable=self.status_var, font=("Helvetica", 10))
        status_label.pack(pady=(0, 20))

        btn_frame = tk.Frame(frame)
        btn_frame.pack()

        btn_reg = tk.Button(btn_frame, text="右クリックメニューに登録", command=self.on_register, width=22, height=2, bg="#e1f5fe")
        btn_reg.pack(side=tk.LEFT, padx=5)

        btn_unreg = tk.Button(btn_frame, text="登録解除", command=self.on_unregister, width=15, height=2, bg="#ffebee")
        btn_unreg.pack(side=tk.LEFT, padx=5)

    def update_status(self):
        if is_registered():
            self.status_var.set("現在のステータス: 登録済み (右クリックメニューに表示されます)")
        else:
            self.status_var.set("現在のステータス: 未登録")

    def on_register(self):
        try:
            register_context_menu()
            self.update_status()
            messagebox.showinfo("成功", "右クリックメニューに Whisper 英語文字起こしを追加しました！")
        except Exception as e:
            messagebox.showerror("エラー", f"登録に失敗しました:\n{e}")

    def on_unregister(self):
        try:
            unregister_context_menu()
            self.update_status()
            messagebox.showinfo("成功", "右クリックメニューから解除しました。")
        except Exception as e:
            messagebox.showerror("エラー", f"解除に失敗しました:\n{e}")

def main():
    parser = argparse.ArgumentParser(description="Whisper Context Menu Manager (English)")
    parser.add_argument("--register", action="store_true", help="右クリックメニューを登録する")
    parser.add_argument("--unregister", action="store_true", help="右クリックメニューを解除する")
    args = parser.parse_args()

    if args.register:
        register_context_menu()
        print("右クリックメニューへの登録が完了しました。")
    elif args.unregister:
        unregister_context_menu()
        print("右クリックメニューからの登録解除が完了しました。")
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()

if __name__ == "__main__":
    main()
