import os
import glob
import time
import requests
import winsound
import cv2
import numpy as np
import random
from mss import MSS
import obsws_python as obsws

# ==========================================
# --- システム基本設定 ---
# ==========================================
MONITOR_INDEX = 3     # ※使用環境に合わせて 1 や 3 に変更してください
THRESHOLD = 0.6       # 星検知のしきい値
COOLDOWN_SECONDS = 3.0 # 重複検知を防ぐインターバル

# VOICEVOX 設定
VOICEVOX_URL = "http://127.0.0.1:50021"
SPEAKER_ID = 3        # 3: ずんだもん（ノーマル）

# ★ OBS WebSocket 設定 ★
OBS_HOST = "localhost"
OBS_PORT = 4455
OBS_PASSWORD = "123456"  # ご自身のパスワードに合わせて変更してください

# ★ リプレイ動画の保存先フォルダ ★
REPLAY_DIR = r"C:\Users\count\Videos"
# ★ OBS側で設定しているメディアソースの名前 ★
MEDIA_SOURCE_NAME = "リプレイ動画"
# ★ リプレイ用シーンの名前（既存の「リプレイ」シーンを使用） ★
REPLAY_SCENE_NAME = "リプレイ"

# ★ マルチスケール検索の設定 ★
SCALE_MIN = 0.5
SCALE_MAX = 1.1
SCALE_STEP = 0.1

# ランダムお遊びセリフ
RANDOM_PHRASES = [
    "まぁかくりつだからねー！",
    "スタブおおふな！",
    "しょけんはまけねーよーッ！",
    "いいでしょう！",
    "マジやべーっす！",
    "わざがでない！",
    "あるよー！",
    "たのむ！",
    "だいたいわかった！"
]

# ==========================================
# --- 初期状態 ---
# ==========================================
TARGET_NUM = 10        # 初期ルール（10先）
GAME_MODE = "FIRST_TO" # "FIRST_TO" (先取) または "TOTAL_GAMES" (本勝負)
P1_NAME = "1P"         # 初期プレイヤー名
P2_NAME = "2P"
DISABLE_RANDOM = False # ランダムセリフ無効化フラグ

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
template_path = os.path.join(BASE_DIR, 'stars2.png')
set_score_path = os.path.join(BASE_DIR, 'set_score.txt')
scores_path = os.path.join(BASE_DIR, 'scores.txt')

def imread_japanese(filepath, flags=cv2.IMREAD_COLOR):
    """日本語パスでも安全に画像を読み込む関数"""
    try:
        buf = np.fromfile(filepath, dtype=np.uint8)
        img = cv2.imdecode(buf, flags)
        return img
    except Exception as e:
        print(f"画像読み込み例外: {e}")
        return None

def speak_zundamon(text, allow_random=False):
    """VOICEVOX（ずんだもん）に直接音声を送って再生する"""
    if not text:
        return

    if allow_random and not DISABLE_RANDOM and random.randint(1, 1) == 1:
        extra = random.choice(RANDOM_PHRASES)
        text = f"{text} {extra}"

    try:
        print(f"[ずんだもん発話] {text}")
        
        res1 = requests.post(
            f'{VOICEVOX_URL}/audio_query',
            params={'text': text, 'speaker': SPEAKER_ID},
            timeout=10
        )
        if res1.status_code != 200:
            print("VOICEVOXクエリ作成失敗")
            return

        res2 = requests.post(
            f'{VOICEVOX_URL}/synthesis',
            params={'speaker': SPEAKER_ID},
            json=res1.json(),
            timeout=30
        )
        if res2.status_code != 200:
            print("VOICEVOX音声生成失敗")
            return
        
        temp_wave_path = os.path.join(BASE_DIR, "temp_voice.wav")
        with open(temp_wave_path, "wb") as f:
            f.write(res2.content)
            
        winsound.PlaySound(temp_wave_path, winsound.SND_FILENAME | winsound.SND_ASYNC)

    except Exception as e:
        print(f"VOICEVOX連携エラー: {e}")

def clean_old_replays(keep_count=5):
    """最新のkeep_count個だけ残して、古いリプレイ動画を自動削除する関数"""
    try:
        list_of_files = glob.glob(os.path.join(REPLAY_DIR, "Replay *.mp4"))
        list_of_files.sort(key=os.path.getmtime)
        
        while len(list_of_files) > keep_count:
            old_file = list_of_files.pop(0)
            os.remove(old_file)
            print(f"[自動お掃除] 古いリプレイを削除しました: {os.path.basename(old_file)}")
    except Exception as e:
        print(f"[自動お掃除エラー] ファイル削除失敗: {e}")

def trigger_replay_sequence():
    """最新のリプレイファイルを自動取得してOBSにセットし、シーン切り替え・復帰を行う一連の処理"""
    req_client = None
    original_scene = None
    try:
        req_client = obsws.ReqClient(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
        
        # 1. 現在のシーン名を記憶
        current_scene_info = req_client.get_current_program_scene()
        original_scene = current_scene_info.current_program_scene_name
        print(f"[OBS連携] 現在のシーンを記憶しました: {original_scene}")

        # 2. ★最優先でリプレイバッファを保存★
        print("[OBS連携] リプレイバッファを保存中...")
        req_client.save_replay_buffer()
        
        # 3. ファイル書き出し待ち（2秒）
        time.sleep(2.0)

        # 4. 保存先フォルダから一番新しいリプレイ動画ファイルを特定
        list_of_files = glob.glob(os.path.join(REPLAY_DIR, "Replay *.mp4"))
        if not list_of_files:
            print("[OBS連携エラー] リプレイ動画ファイルが見つかりません。")
            return
        
        latest_replay = max(list_of_files, key=os.path.getmtime)
        print(f"[OBS連携] 最新のリプレイファイルを特定しました: {latest_replay}")

        # 5. OBSのメディアソースに新しいファイルパスを設定する
        req_client.set_input_settings(
            name=MEDIA_SOURCE_NAME,
            settings={"local_file": latest_replay},
            overlay=True
        )

        # 6. リプレイシーンへ切り替え（OBS側で設定したカラーフィルターやテロップが適用される）
        print(f"[OBS連携] リプレイシーン ({REPLAY_SCENE_NAME}) へ切り替えます！")
        req_client.set_current_program_scene(REPLAY_SCENE_NAME)

        # 7. リプレイ再生時間（9秒）
        print("[OBS連携] リプレイ再生中...")
        time.sleep(9.0)

    except Exception as e:
        print(f"[OBS連携エラー] リプレイ制御に失敗しました: {e}")
    finally:
        # ★エラーが起きても必ず元のシーンに復帰させる安全対策★
        if req_client and original_scene:
            try:
                print(f"[OBS連携] 元のシーン ({original_scene}) に戻します。")
                req_client.set_current_program_scene(original_scene)
            except Exception as e:
                print(f"[OBS連携復帰エラー] 元のシーンに戻せませんでした: {e}")
        
        # 再生終了後、古いリプレイ動画の自動お掃除を実行（最新5個だけ残す）
        clean_old_replays(keep_count=5)

def create_initial_files():
    if not os.path.exists(set_score_path):
        with open(set_score_path, 'w', encoding='utf-8') as f:
            f.write("0-0")
    if not os.path.exists(scores_path):
        with open(scores_path, 'w', encoding='utf-8') as f:
            f.write("(10先) 1P 0-0 2P")

def update_score_display(p1_wins, p2_wins, target, mode, p1_name, p2_name):
    mode_text = f"{target}先" if mode == "FIRST_TO" else f"全{target}本"
    text = f"({mode_text}) {p1_name} {p1_wins}-{p2_wins} {p2_name}"
    with open(scores_path, 'w', encoding='utf-8') as f:
        f.write(text)

def extract_star_and_frame(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lower_yellow = np.array([10, 60, 60])
    upper_yellow = np.array([35, 255, 255])
    mask_yellow = cv2.inRange(hsv, lower_yellow, upper_yellow)
    
    lower_white = np.array([0, 0, 100])
    upper_white = np.array([180, 70, 255])
    mask_white = cv2.inRange(hsv, lower_white, upper_white)
    
    return cv2.bitwise_or(mask_yellow, mask_white)

def parse_set_score_content(content):
    global TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME, DISABLE_RANDOM
    lines = content.splitlines()
    
    p1_w, p2_w = None, None
    reset_flag = False
    name_changed = False
    rule_changed = False

    if any(k in content for k in ["ランダムなし", "ランダムオフ", "no_random", "norandom"]):
        DISABLE_RANDOM = True
    else:
        DISABLE_RANDOM = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if "," in line or "1P:" in line or "2P:" in line:
            old_p1, old_p2 = P1_NAME, P2_NAME
            if "1P:" in line and "2P:" in line:
                parts = line.split(",")
                for p in parts:
                    if "1P:" in p:
                        P1_NAME = p.replace("1P:", "").strip()
                    elif "2P:" in p:
                        P2_NAME = p.replace("2P:", "").strip()
            elif "," in line:
                parts = line.split(",")
                if len(parts) >= 2:
                    P1_NAME = parts[0].strip()
                    P2_NAME = parts[1].strip()
            if old_p1 != P1_NAME or old_p2 != P2_NAME:
                name_changed = True

        elif "先" in line:
            num_str = "".join(filter(str.isdigit, line))
            if num_str:
                new_target = int(num_str)
                if TARGET_NUM != new_target or GAME_MODE == "FIRST_TO":
                    TARGET_NUM = new_target
                    GAME_MODE = "FIRST_TO"
                    rule_changed = True
        elif "本" in line:
            num_str = "".join(filter(str.isdigit, line))
            if num_str:
                new_target = int(num_str)
                if TARGET_NUM != new_target or GAME_MODE == "TOTAL_GAMES":
                    TARGET_NUM = new_target
                    GAME_MODE = "TOTAL_GAMES"
                    rule_changed = True

        elif line.lower() == "reset" or line == "0-0":
            reset_flag = True
        elif "-" in line:
            parts = line.split("-")
            if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
                p1_w = int(parts[0].strip())
                p2_w = int(parts[1].strip())

    return p1_w, p2_w, reset_flag, name_changed, rule_changed

def announce_reach_or_win(winner_name, winner_wins, loser_wins, p1_wins, p2_wins, target_num, game_mode):
    if game_mode == "FIRST_TO" and winner_wins >= target_num:
        speak_zundamon(f"{winner_name}しょうり！ {p1_wins}対{p2_wins}でしょうしゃは{winner_name}なのだー！おめでとうなのだ！よいしょうぶだったのだ！", allow_random=False)
    elif game_mode == "FIRST_TO" and winner_wins == target_num - 1:
        if loser_wins == target_num - 1:
            speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}おっかけリーチなのだ！", allow_random=True)
        else:
            speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}リーチなのだ！", allow_random=True)
    else:
        speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}なのだ！", allow_random=True)

def main():
    global TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME
    create_initial_files()
    
    print("★ 実況システム起動 (ずんだもん・最適化版)...\n")
    
    if not os.path.exists(template_path):
        print(f"エラー: 判定用画像 '{template_path}' が見つかりません。")
        return

    base_template = imread_japanese(template_path, cv2.IMREAD_GRAYSCALE)
    if base_template is None:
        print("エラー: テンプレート画像を読み込めませんでした。")
        return
        
    p1_wins = 0
    p2_wins = 0
    p1_active = False
    p2_active = False
    last_detected_time = 0.0
    last_set_score_mtime = os.path.getmtime(set_score_path) if os.path.exists(set_score_path) else 0
    
    update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)

    with MSS() as sct:
        monitors = sct.monitors
        active_monitor_index = MONITOR_INDEX if MONITOR_INDEX < len(monitors) else 1
        monitor = monitors[active_monitor_index]

        try:
            while True:
                if os.path.exists(set_score_path):
                    current_mtime = os.path.getmtime(set_score_path)
                    if current_mtime != last_set_score_mtime:
                        last_set_score_mtime = current_mtime
                        time.sleep(0.1)
                        try:
                            with open(set_score_path, 'r', encoding='utf-8') as f:
                                content = f.read().strip()
                            
                            p1_w, p2_w, reset_flag, name_changed, rule_changed = parse_set_score_content(content)
                            mode_str = f"{TARGET_NUM}先" if GAME_MODE == "FIRST_TO" else f"全{TARGET_NUM}本"
                            
                            if reset_flag:
                                p1_wins, p2_wins = 0, 0
                                speak_zundamon(f"1P、{P1_NAME}。2P、{P2_NAME}。{mode_str}なのだ。ぜったいにまけられないたたかいがそこにはあるのだ！", allow_random=False)
                            else:
                                if p1_w is not None and p2_w is not None:
                                    p1_wins, p2_wins = p1_w, p2_w
                                    speak_zundamon(f"スコア修正、{p1_wins}ー{p2_wins}なのだ！", allow_random=False)
                                if name_changed:
                                    speak_zundamon(f"名前変更、{P1_NAME}対{P2_NAME}なのだ！", allow_random=False)
                                if rule_changed:
                                    speak_zundamon(f"形式変更、{mode_str}なのだ！", allow_random=False)

                            update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        except Exception as e:
                            print(f"ファイル読み込みエラー: {e}")

                sct_img = sct.grab(monitor)
                frame = np.array(sct_img)

                processed = extract_star_and_frame(frame)
                mid_x = frame.shape[1] // 2
                detected_p1 = False
                detected_p2 = False

                scales = np.arange(SCALE_MIN, SCALE_MAX + 0.01, SCALE_STEP)
                for scale in scales:
                    w = int(base_template.shape[1] * scale)
                    h = int(base_template.shape[0] * scale)
                    
                    if w > processed.shape[1] or h > processed.shape[0] or w == 0 or h == 0:
                        continue

                    resized_template = cv2.resize(base_template, (w, h), interpolation=cv2.INTER_AREA)
                    res = cv2.matchTemplate(processed, resized_template, cv2.TM_CCOEFF_NORMED)
                    locs = np.where(res >= THRESHOLD)

                    for pt_x in locs[1]:
                        if pt_x < mid_x:
                            detected_p1 = True
                        else:
                            detected_p2 = True

                    if detected_p1 or detected_p2:
                        break

                match_over = (GAME_MODE == "FIRST_TO" and (p1_wins >= TARGET_NUM or p2_wins >= TARGET_NUM))
                current_time = time.time()

                if current_time - last_detected_time > COOLDOWN_SECONDS:
                    winner_detected = False
                    winner_name_tmp = ""
                    p1_wins_tmp, p2_wins_tmp = p1_wins, p2_wins
                    
                    if detected_p1 and not p1_active and not match_over:
                        p1_wins += 1
                        last_detected_time = current_time
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        p1_active = True
                        winner_detected = True
                        winner_name_tmp = P1_NAME
                        winner_wins_tmp = p1_wins
                        loser_wins_tmp = p2_wins
                    elif not detected_p1:
                        p1_active = False

                    if detected_p2 and not p2_active and not match_over:
                        p2_wins += 1
                        last_detected_time = current_time
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        p2_active = True
                        winner_detected = True
                        winner_name_tmp = P2_NAME
                        winner_wins_tmp = p2_wins
                        loser_wins_tmp = p1_wins
                    elif not detected_p2:
                        p2_active = False

                    if winner_detected:
                        print("[情報] 勝利検知！読み上げとリプレイ処理を同時に開始します...")
                        
                        # ★ 1. 先に音声アナウンス（読み上げ）を開始 ★
                        announce_reach_or_win(winner_name_tmp, winner_wins_tmp, loser_wins_tmp, p1_wins, p2_wins, TARGET_NUM, GAME_MODE)

                        # ★ 2. 同時にリプレイ保存＆再生処理を実行 ★
                        trigger_replay_sequence()

                    if winner_detected and GAME_MODE == "FIRST_TO" and (p1_wins >= TARGET_NUM or p2_wins >= TARGET_NUM):
                        print("[情報] 試合決着を検知しました。15秒後にスコアを自動リセットします...")
                        time.sleep(15)
                        
                        p1_wins, p2_wins = 0, 0
                        p1_active, p2_active = False, False
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        
                        speak_zundamon("セットカウント、リセットするのだ！", allow_random=False)
                        print("[情報] スコアを 0-0 に自動リセットしました。")
                
                # 高速監視（0.1秒間隔）
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nシステムを安全に停止しました。")

if __name__ == "__main__":
    main()
