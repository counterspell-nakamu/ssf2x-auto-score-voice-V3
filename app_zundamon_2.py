import os
import glob
import time
import queue
import threading
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
OBS_TIMEOUT = 3          # OBS接続のタイムアウト秒数

# ★ リプレイ動画の保存先フォルダ ★
REPLAY_DIR = r"C:\Users\count\Videos"
# ★ OBS側で設定しているメディアソースの名前 ★
MEDIA_SOURCE_NAME = "リプレイ動画"
# ★ リプレイ用シーンの名前 ★
REPLAY_SCENE_NAME = "リプレイ"

# ★ リプレイのタイミング設定（試合決着から次の試合まで約12秒を想定） ★
REPLAY_MIN_WAIT = 2.0        # 保存してからリプレイ再生を始めるまでの最低待ち時間（演出のタメ）
REPLAY_FILE_WAIT_LIMIT = 5.0 # 新しいリプレイファイルを待つ上限秒数（超えたらリプレイをスキップ）
REPLAY_TAIL_SECONDS = 10.0   # クリップの「終わりから何秒前」から再生を始めるか
REPLAY_PLAY_SECONDS = 9.0    # リプレイを再生する秒数（TAIL - PLAY の差が、終わりでカットされる秒数）
REPLAY_KEEP_COUNT = 5        # 残すリプレイ動画の数

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

# ==========================================
# --- 読み上げ（裏スレッドで順番に再生） ---
# ==========================================
_voice_queue = queue.Queue()

def _voice_worker():
    """キューに入った読み上げを1つずつ順番に処理する（音声の重なり・ファイル上書きを防止）"""
    while True:
        text = _voice_queue.get()
        try:
            _speak_now(text)
        except Exception as e:
            print(f"VOICEVOX連携エラー: {e}")
        finally:
            _voice_queue.task_done()

def _speak_now(text):
    """VOICEVOXで音声を作って、再生が終わるまで待つ（裏スレッド専用）"""
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

    # 同期再生（再生が終わるまでこのスレッドで待つ。メイン処理は止まらない）
    winsound.PlaySound(temp_wave_path, winsound.SND_FILENAME)

def speak_zundamon(text, allow_random=False):
    """読み上げをキューに入れる（すぐに戻る。実際の発話は裏スレッドが順番に行う）"""
    if not text:
        return

    if allow_random and not DISABLE_RANDOM and random.randint(1, 1) == 1:
        extra = random.choice(RANDOM_PHRASES)
        text = f"{text} {extra}"

    _voice_queue.put(text)

# ==========================================
# --- OBS連携 ---
# ==========================================
_obs_client = None

def get_obs_client():
    """OBSへの接続を返す。未接続なら接続を試みる（接続は使い回す）"""
    global _obs_client
    if _obs_client is not None:
        return _obs_client
    try:
        _obs_client = obsws.ReqClient(
            host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD, timeout=OBS_TIMEOUT
        )
        print("[OBS連携] OBSに接続しました。")
    except Exception as e:
        _obs_client = None
        print(f"[OBS連携エラー] OBSに接続できません: {e}")
    return _obs_client

def drop_obs_client():
    """接続が壊れたときに破棄して、次回つなぎ直せるようにする"""
    global _obs_client
    _obs_client = None

def _norm(path):
    return os.path.normcase(os.path.abspath(path))

def list_replay_files():
    return glob.glob(os.path.join(REPLAY_DIR, "Replay *.mp4"))

def clean_old_replays(keep_count=REPLAY_KEEP_COUNT):
    """最新のkeep_count個だけ残して、古いリプレイ動画を自動削除する関数"""
    try:
        list_of_files = list_replay_files()
        list_of_files.sort(key=os.path.getmtime)

        while len(list_of_files) > keep_count:
            old_file = list_of_files.pop(0)
            os.remove(old_file)
            print(f"[自動お掃除] 古いリプレイを削除しました: {os.path.basename(old_file)}")
    except Exception as e:
        print(f"[自動お掃除エラー] ファイル削除失敗: {e}")

def start_replay_save():
    """★勝利検知の直後に最優先で呼ぶ★ リプレイバッファを保存し、後で使う情報を返す。失敗時はNone"""
    client = get_obs_client()
    if client is None:
        return None

    # 保存前の既存ファイル一覧（保存後に増えたファイル＝新しいリプレイ を見分けるため）
    before_files = {_norm(f) for f in list_replay_files()}

    for attempt in range(2):
        try:
            t_save = time.time()
            client.save_replay_buffer()
            print("[OBS連携] リプレイバッファの保存を要求しました。")
            return {"client_t0": t_save, "before": before_files}
        except Exception as e:
            print(f"[OBS連携エラー] リプレイ保存に失敗しました: {e}")
            print("  → OBSの「リプレイバッファを開始」が押されているか確認してください。")
            drop_obs_client()
            if attempt == 0:
                client = get_obs_client()  # 接続切れの可能性があるので1回だけつなぎ直して再試行
                if client is None:
                    return None
    return None

def find_new_replay(client, before_files):
    """保存前に無かった新しいリプレイファイルを探す。見つからなければNone"""
    # 方法1: OBSに「最後に保存したリプレイのパス」を聞く
    try:
        getter = getattr(client, "get_last_replay_buffer_replay", None)
        if getter is not None:
            resp = getter()
            path = getattr(resp, "saved_replay_path", None)
            if path and _norm(path) not in before_files and os.path.exists(path):
                return path
    except Exception:
        pass  # まだ保存されていない等。方法2に任せる

    # 方法2: フォルダの中で、保存前に無かったファイルを探す
    try:
        new_files = [f for f in list_replay_files() if _norm(f) not in before_files]
        if new_files:
            return max(new_files, key=os.path.getmtime)
    except Exception:
        pass
    return None

def wait_for_new_replay(client, before_files, t0):
    """新しいリプレイファイルが現れて、書き込みが落ち着く（サイズが変わらない）まで待つ"""
    deadline = t0 + REPLAY_FILE_WAIT_LIMIT
    candidate = None
    last_size = -1

    while time.time() < deadline:
        path = find_new_replay(client, before_files)
        if path:
            if path != candidate:
                candidate = path
                last_size = -1
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            if size > 0 and size == last_size:
                return path
            last_size = size
        time.sleep(0.15)
    return None

def get_clip_duration_seconds(path):
    """動画の長さ（秒）を取得する。取得できなければNone"""
    cap = None
    try:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps and fps > 0 and frames and frames > 0:
            return frames / fps
    except Exception:
        pass
    finally:
        if cap is not None:
            cap.release()
    return None

def seek_media_source(client, offset_seconds):
    """メディアソースの再生位置を指定（ms）。シーン切替直後は準備中のことがあるので少し粘る"""
    cursor_ms = int(offset_seconds * 1000)
    for _ in range(8):
        try:
            client.set_media_input_cursor(MEDIA_SOURCE_NAME, cursor_ms)
            return True
        except Exception:
            time.sleep(0.1)
    return False

def ensure_media_playing(client):
    """メディアが再生状態でなければ再生を指示する"""
    try:
        status = client.get_media_input_status(MEDIA_SOURCE_NAME)
        state = getattr(status, "media_state", "")
        if state != "OBS_MEDIA_STATE_PLAYING":
            client.trigger_media_input_action(
                MEDIA_SOURCE_NAME, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
            )
    except Exception as e:
        print(f"[OBS連携] 再生状態の確認に失敗（無視して続行）: {e}")

def play_replay(save_info):
    """保存したリプレイを確認して再生し、元のシーンへ戻す一連の処理"""
    if not save_info:
        print("[OBS連携] リプレイ保存が行われなかったため、リプレイはスキップします。")
        return

    t0 = save_info["client_t0"]
    before_files = save_info["before"]

    client = get_obs_client()
    if client is None:
        return

    original_scene = None
    switch_attempted = False

    try:
        # 現在のシーンを記憶
        original_scene = client.get_current_program_scene().current_program_scene_name
        print(f"[OBS連携] 現在のシーンを記憶しました: {original_scene}")

        # 新しいリプレイファイルが出来るまで待つ（古いリプレイは絶対に流さない）
        latest_replay = wait_for_new_replay(client, before_files, t0)
        if latest_replay is None:
            print(f"[OBS連携エラー] {REPLAY_FILE_WAIT_LIMIT}秒待っても新しいリプレイが見つかりません。今回はスキップします。")
            return
        print(f"[OBS連携] 新しいリプレイを確認（保存要求から {time.time() - t0:.2f}秒）: {os.path.basename(latest_replay)}")

        # 再生開始位置を計算（クリップの「終わりから REPLAY_TAIL_SECONDS 秒前」）
        duration = get_clip_duration_seconds(latest_replay)
        offset = None
        if duration is not None:
            offset = max(0.0, duration - REPLAY_TAIL_SECONDS)
            print(f"[OBS連携] クリップ長 {duration:.2f}秒 → 再生開始位置 {offset:.2f}秒")
        else:
            print("[OBS連携] クリップ長を取得できないため、先頭から再生します。")

        # 演出のタメ：保存要求から最低 REPLAY_MIN_WAIT 秒は待つ
        remaining = (t0 + REPLAY_MIN_WAIT) - time.time()
        if remaining > 0:
            time.sleep(remaining)

        # メディアソースに新しいファイルをセット
        client.set_input_settings(
            name=MEDIA_SOURCE_NAME,
            settings={"local_file": latest_replay},
            overlay=True
        )

        # リプレイシーンへ切り替え
        print(f"[OBS連携] リプレイシーン ({REPLAY_SCENE_NAME}) へ切り替えます！")
        switch_attempted = True
        client.set_current_program_scene(REPLAY_SCENE_NAME)
        t_switch = time.time()
        print(f"[OBS連携] 切り替え完了（保存要求から {t_switch - t0:.2f}秒）")

        # 再生位置を指定して、再生状態を確認
        if offset is not None and offset > 0:
            if not seek_media_source(client, offset):
                print("[OBS連携] 再生位置の指定に失敗（先頭から再生されます）")
        ensure_media_playing(client)

        # リプレイ再生時間
        print(f"[OBS連携] リプレイ再生中（{REPLAY_PLAY_SECONDS}秒）...")
        remaining = (t_switch + REPLAY_PLAY_SECONDS) - time.time()
        if remaining > 0:
            time.sleep(remaining)

    except Exception as e:
        print(f"[OBS連携エラー] リプレイ制御に失敗しました: {e}")
        drop_obs_client()
    finally:
        # ★エラーが起きても必ず元のシーンに復帰させる安全対策★
        if original_scene and switch_attempted:
            try:
                c = get_obs_client()
                if c is not None:
                    print(f"[OBS連携] 元のシーン ({original_scene}) に戻します。")
                    c.set_current_program_scene(original_scene)
            except Exception as e:
                print(f"[OBS連携復帰エラー] 元のシーンに戻せませんでした: {e}")
                drop_obs_client()

        # 再生終了後、古いリプレイ動画の自動お掃除
        clean_old_replays(keep_count=REPLAY_KEEP_COUNT)

# ==========================================
# --- スコア表示・設定ファイル ---
# ==========================================
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
    
    print("★ 実況システム起動 (ずんだもん・リプレイ安定版)...\n")
    
    if not os.path.exists(template_path):
        print(f"エラー: 判定用画像 '{template_path}' が見つかりません。")
        return

    base_template = imread_japanese(template_path, cv2.IMREAD_GRAYSCALE)
    if base_template is None:
        print("エラー: テンプレート画像を読み込めませんでした。")
        return

    # 読み上げ用の裏スレッドを開始
    threading.Thread(target=_voice_worker, daemon=True).start()

    # OBSへ起動時に接続（失敗しても、あとで自動的につなぎ直す）
    get_obs_client()
        
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
                    winner_wins_tmp = 0
                    loser_wins_tmp = 0
                    
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
                        print("[情報] 勝利検知！リプレイ保存を最優先で実行します...")

                        # ★ 1. 最優先：リプレイバッファを保存（保存のタイミングを毎回そろえる） ★
                        save_info = start_replay_save()
                        if save_info:
                            print(f"[情報] 検知→保存要求 {save_info['client_t0'] - current_time:.2f}秒")

                        # ★ 2. 読み上げをキューに入れる（音声合成は裏スレッドで行うので待たない） ★
                        announce_reach_or_win(winner_name_tmp, winner_wins_tmp, loser_wins_tmp, p1_wins, p2_wins, TARGET_NUM, GAME_MODE)

                        # ★ 3. 新しいファイルを確認 → リプレイ再生 → 元のシーンへ復帰 ★
                        play_replay(save_info)

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
