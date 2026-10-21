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

# ★ 毎試合リプレイのタイミング設定（試合決着から次の試合まで約12秒を想定） ★
REPLAY_MIN_WAIT = 2.5        # 保存してからリプレイ再生を始めるまでの最低待ち時間（演出のタメ）
REPLAY_FILE_WAIT_LIMIT = 5.0 # 新しいリプレイファイルを待つ上限秒数（超えたらリプレイをスキップ）
REPLAY_TAIL_SECONDS = 9.0    # クリップの「終わりから何秒前」から再生を始めるか（実際の開始はキーフレームの都合で最大1秒ほど手前になる）
REPLAY_PLAY_SECONDS = 10.0   # リプレイを再生する秒数の上限（終わりは下の REPLAY_END_MARGIN で揃える）
REPLAY_END_MARGIN = 0.0      # クリップの「終わりの何秒前」で再生を終えるか（クリップ長がバラついても終わりの場面を揃える）
REPLAY_KEEP_COUNT = 5        # 残すリプレイ動画の数（ハイライト候補のクリップは別枠で守られます）
SEEK_TIMEOUT = 2.0          # 再生位置の指定を粘る上限秒数（超えたら先頭から再生）
SEEK_SKIP_THRESHOLD = 3.0    # 開始位置のズレがこの秒数以下なら、位置指定をせず先頭から再生する（切り替え直後に先頭が一瞬映るのを防ぐ）
SEEK_TOLERANCE = 1.5         # 指定位置からこの秒数以内に着地していれば成功とみなす（OBSは直前のキーフレームへ戻るため）
SEEK_VERIFY_WAIT = 0.25      # 位置を指定してから、実際に効いたか確認するまでの待ち秒数

# ★ ハイライト（セット決着後にまとめて再生）の設定 ★
HIGHLIGHT_COUNT = 3           # 選ぶ本数
HIGHLIGHT_START_DELAY = 5.0   # セット決着の保存から、ハイライトを始めるまでの待ち時間（秒）
HIGHLIGHT_PLAY_SECONDS = 10.0 # 1本あたりの再生秒数
HIGHLIGHT_TAIL_SECONDS = 10.0 # クリップの「終わりから何秒前」から再生を始めるか
HIGHLIGHT_END_MARGIN = 0.0    # クリップの「終わりの何秒前」で再生を終えるか
HIGHLIGHT_MAX_CLIPS = 25      # 1セットで溜めておくクリップの上限（超えたら点数の低い古いものから外す）
HIGHLIGHT_RESET_DELAY = 3.0   # ハイライト終了後、スコアをリセットするまでの秒数
NO_HIGHLIGHT_RESET_DELAY = 15.0 # ハイライトを再生できなかったときのリセット待ち秒数（従来どおり）
ONE_SIDED_DIFF = 3            # 「一方的な勝利」とみなす勝ち数の差
CLOSE_GAME_BONUS = 5          # 接戦（差が1以内）のときの加点

# 山場の種類ごとの点数（高いほどハイライトに選ばれやすい）
HIGHLIGHT_SCORES = {
    "final": 100,          # 最終戦
    "double_reach": 80,    # おっかけリーチ
    "comeback_lead": 70,   # 逆転（リードを奪った）
    "comeback_tie": 60,    # 追いついた（同点）
    "reach_block": 50,     # リーチ返し
    "reach_made": 40,      # リーチをかけた試合
    "one_sided": 20,       # 一方的な勝利
    "normal": 0,           # 通常
}

# 山場の種類ごとのセリフ（{勝者} {N}=試合番号 {スコア} が置き換わります）
HIGHLIGHT_PHRASES = {
    "final": [
        "{勝者}がしょうりをきめた、さいごのいっせん！",
        "しょうぶをきめたいっせん！",
    ],
    "double_reach": [
        "おたがいリーチ、しびれるたたかい！",
        "リーチどうしのせめぎあい！",
    ],
    "comeback_lead": [
        "{勝者}がぎゃくてんした！",
        "{勝者}がぎゃくてん！",
    ],
    "comeback_tie": [
        "{勝者}がおいついた！",
        "{勝者}がどうてんにした！",
    ],
    "reach_block": [
        "{勝者}がリーチをはばんだ！",
        "あぶないところで{勝者}がたえた！",
    ],
    "reach_made": [
        "{勝者}リーチのいっぱつ！",
        "{勝者}がリーチにのった！",
    ],
    "one_sided": [
        "{勝者}がおしきった！",
        "{勝者}のあっとう！",
    ],
    "normal": [
        "{勝者}しょうり、{スコア}！",
        "{勝者}がとった！",
    ],
}
HIGHLIGHT_ORDINALS = ["いっぽんめ", "にほんめ", "さんぼんめ", "よんほんめ", "ごほんめ"]
HIGHLIGHT_OPENING = "しあいしゅうりょう！ここからハイライトをおとどけするのだ！"
HIGHLIGHT_CLOSING = "いじょう、ハイライトでしたのだ！"

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
# --- 読み上げ ---
# ==========================================
_voice_queue = queue.Queue()

def _synthesize_to_file(text, wav_path):
    """VOICEVOXで音声を作ってファイルに保存する。成功したらTrue"""
    res1 = requests.post(
        f'{VOICEVOX_URL}/audio_query',
        params={'text': text, 'speaker': SPEAKER_ID},
        timeout=10
    )
    if res1.status_code != 200:
        print("VOICEVOXクエリ作成失敗")
        return False

    res2 = requests.post(
        f'{VOICEVOX_URL}/synthesis',
        params={'speaker': SPEAKER_ID},
        json=res1.json(),
        timeout=30
    )
    if res2.status_code != 200:
        print("VOICEVOX音声生成失敗")
        return False

    with open(wav_path, "wb") as f:
        f.write(res2.content)
    return True

def _speak_now(text):
    """VOICEVOXで音声を作って、再生が終わるまで待つ（裏スレッド専用）"""
    print(f"[ずんだもん発話] {text}")
    temp_wave_path = os.path.join(BASE_DIR, "temp_voice.wav")
    if _synthesize_to_file(text, temp_wave_path):
        # 同期再生（再生が終わるまでこのスレッドで待つ。メイン処理は止まらない）
        winsound.PlaySound(temp_wave_path, winsound.SND_FILENAME)

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

def speak_zundamon(text, allow_random=False):
    """読み上げをキューに入れる（すぐに戻る。実際の発話は裏スレッドが順番に行う）"""
    if not text:
        return

    if allow_random and not DISABLE_RANDOM and random.randint(1, 1) == 1:
        extra = random.choice(RANDOM_PHRASES)
        text = f"{text} {extra}"

    _voice_queue.put(text)

def prepare_voice(text, key):
    """ハイライト用：音声を先に作っておく（再生はしない）。作れなければNone"""
    path = os.path.join(BASE_DIR, f"hl_voice_{key}.wav")
    try:
        print(f"[ずんだもん発話(準備)] {text}")
        if _synthesize_to_file(text, path):
            return path
    except Exception as e:
        print(f"VOICEVOX連携エラー: {e}")
    return None

def play_voice_file(path):
    """準備済みの音声を、再生が終わるまで待って流す"""
    if not path:
        return
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME)
    except Exception as e:
        print(f"音声再生エラー: {e}")

# ==========================================
# --- ハイライト用クリップの記録 ---
# ==========================================
set_clips = []   # 今のセットで溜めているクリップ情報
_clip_seq = 0    # クリップの記録順（試合の順番）

def classify_game(winner_wins, loser_wins):
    """勝利後のスコアから、その試合の山場の種類を決める（先取りモード用）"""
    target = TARGET_NUM
    prev_winner = winner_wins - 1
    prev_loser = loser_wins

    if winner_wins >= target:
        return "final"
    if winner_wins == target - 1 and loser_wins == target - 1:
        return "double_reach"
    if prev_winner < prev_loser and winner_wins >= loser_wins:
        return "comeback_lead" if winner_wins > loser_wins else "comeback_tie"
    if loser_wins == target - 1 and winner_wins < target - 1:
        return "reach_block"
    if winner_wins == target - 1:
        return "reach_made"
    if winner_wins - loser_wins >= ONE_SIDED_DIFF:
        return "one_sided"
    return "normal"

def trim_set_clips():
    """クリップが上限を超えたら、点数の低い古いものから外す"""
    while len(set_clips) > HIGHLIGHT_MAX_CLIPS:
        victim = min(set_clips, key=lambda c: (c["score"], c["seq"]))
        set_clips.remove(victim)
        print(f"[ハイライト] 上限を超えたため、第{victim['game_no']}試合のクリップを候補から外しました。")

def record_set_clip(path, duration, winner_name, winner_wins, loser_wins, p1_wins, p2_wins):
    """勝利した試合のクリップを、ハイライト候補として記録する"""
    global _clip_seq
    _clip_seq += 1
    kind = classify_game(winner_wins, loser_wins)
    score = HIGHLIGHT_SCORES.get(kind, 0)
    if abs(winner_wins - loser_wins) <= 1:
        score += CLOSE_GAME_BONUS
    game_no = p1_wins + p2_wins

    set_clips.append({
        "seq": _clip_seq,
        "game_no": game_no,
        "kind": kind,
        "score": score,
        "tiebreak": random.random(),  # 同点のときだけ効く（0〜1）
        "path": path,
        "duration": duration,
        "winner_name": winner_name,
        "p1_wins": p1_wins,
        "p2_wins": p2_wins,
    })
    print(f"[ハイライト] 第{game_no}試合を候補に記録: {kind}（{score}点）")
    trim_set_clips()

def clear_set_clips():
    """今のセットのクリップ記録をクリアする（ファイルは次回のお掃除で古い順に消える）"""
    global _clip_seq
    set_clips.clear()
    _clip_seq = 0

def select_highlights():
    """点数の高い順に選び、試合の順番どおりに並べて返す"""
    available = [c for c in set_clips if os.path.exists(c["path"])]
    if not available:
        return []
    ranked = sorted(available, key=lambda c: c["score"] + c["tiebreak"], reverse=True)
    chosen = ranked[:HIGHLIGHT_COUNT]
    chosen.sort(key=lambda c: c["seq"])
    for c in chosen:
        print(f"[ハイライト] 選出: 第{c['game_no']}試合 {c['kind']}（{c['score']}点）")
    return chosen

def build_intro_text(index, clip, used_phrases):
    """ハイライト1本ごとの紹介セリフを作る（同じ種類でも違うセリフを優先）"""
    variants = HIGHLIGHT_PHRASES.get(clip["kind"], HIGHLIGHT_PHRASES["normal"])
    candidates = [v for v in variants if v not in used_phrases] or list(variants)
    phrase = candidates[0] if DISABLE_RANDOM else random.choice(candidates)
    used_phrases.add(phrase)

    if index < len(HIGHLIGHT_ORDINALS):
        ordinal = HIGHLIGHT_ORDINALS[index]
    else:
        ordinal = f"{index + 1}ほんめ"

    text = f"ハイライト、{ordinal}。だい{clip['game_no']}しあい。{phrase}"
    text = text.replace("{勝者}", clip["winner_name"])
    text = text.replace("{N}", str(clip["game_no"]))
    text = text.replace("{スコア}", f"{clip['p1_wins']}ー{clip['p2_wins']}")
    return text

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
    """古いリプレイ動画を自動削除する。ハイライト候補のクリップは消さない"""
    try:
        protected = {_norm(c["path"]) for c in set_clips}
        list_of_files = [f for f in list_replay_files() if _norm(f) not in protected]
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

def secure_replay_file(save_info):
    """保存したリプレイの新しいファイルを確認して、パスを返す。失敗したらNone（古いリプレイは返さない）"""
    if not save_info:
        print("[OBS連携] リプレイ保存が行われなかったため、リプレイはスキップします。")
        return None
    client = get_obs_client()
    if client is None:
        return None

    t0 = save_info["client_t0"]
    try:
        path = wait_for_new_replay(client, save_info["before"], t0)
    except Exception as e:
        print(f"[OBS連携エラー] リプレイファイルの確認に失敗しました: {e}")
        drop_obs_client()
        return None

    if path is None:
        print(f"[OBS連携エラー] {REPLAY_FILE_WAIT_LIMIT}秒待っても新しいリプレイが見つかりません。今回はスキップします。")
        return None

    print(f"[OBS連携] 新しいリプレイを確認（保存要求から {time.time() - t0:.2f}秒）: {os.path.basename(path)}")
    return path

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

def _get_media_status(client):
    """メディアソースの（状態, 現在位置ms）を返す。取得できなければ（""，None）"""
    try:
        st = client.get_media_input_status(MEDIA_SOURCE_NAME)
        return getattr(st, "media_state", ""), getattr(st, "media_cursor", None)
    except Exception:
        return "", None

def seek_media_source(client, offset_seconds):
    """メディアソースの再生位置を指定（ms）し、本当に効いたか読み戻して確認する。
    OBSは指定位置の直前のキーフレームへ戻るため、近くに着地していれば成功とする。
    先頭に戻されていたときだけやり直す。
    戻り値: (成功したか, 確認した現在位置[秒]またはNone, 確認した時刻)"""
    target_ms = int(offset_seconds * 1000)
    tolerance_ms = SEEK_TOLERANCE * 1000
    deadline = time.time() + SEEK_TIMEOUT
    attempt = 0
    while time.time() < deadline:
        attempt += 1

        # 再生中／一時停止中でないと位置指定できないので、先に状態を確認
        state, _ = _get_media_status(client)
        if state and state not in ("OBS_MEDIA_STATE_PLAYING", "OBS_MEDIA_STATE_PAUSED"):
            try:
                client.trigger_media_input_action(
                    MEDIA_SOURCE_NAME, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
                )
            except Exception:
                pass
            time.sleep(0.1)
            continue

        try:
            client.set_media_input_cursor(MEDIA_SOURCE_NAME, target_ms)
        except Exception:
            time.sleep(0.1)
            continue
        t_set = time.time()

        # 効いたかどうかを、少し待ってから読み戻して確認
        time.sleep(SEEK_VERIFY_WAIT)
        _, cursor = _get_media_status(client)
        t_read = time.time()
        if cursor is None:
            return True, None, t_read  # 確認できないときは、指定できたものとして続行
        elapsed_ms = (t_read - t_set) * 1000
        if target_ms - tolerance_ms <= cursor <= target_ms + elapsed_ms + tolerance_ms:
            print(f"[OBS連携] 再生位置を指定しました: {target_ms / 1000:.2f}秒 → 確認 {cursor / 1000:.2f}秒（{attempt}回目）")
            return True, cursor / 1000.0, t_read
        print(f"[OBS連携] 再生位置がずれています（指定 {target_ms / 1000:.2f}秒 / 実際 {cursor / 1000:.2f}秒）。やり直します（{attempt}回目）")
    return False, None, time.time()

def seek_and_report(client, offset_seconds):
    """位置を指定し、成功したら（現在位置[秒], 確認時刻）を返す。失敗したらNone（先頭から再生される）"""
    ok, cursor_sec, t_read = seek_media_source(client, offset_seconds)
    if not ok:
        print("[OBS連携] 再生位置の指定に失敗（先頭から再生されます）")
        return None
    if cursor_sec is None:
        cursor_sec = offset_seconds
    return cursor_sec, t_read

def calc_play_end(t_base, max_seconds, end_margin, duration, seek_result):
    """再生を終える時刻を計算する。
    クリップの「終わりの end_margin 秒前」で終わるようにしつつ、t_base から max_seconds を上限にする"""
    play_end = t_base + max_seconds
    if duration is not None:
        end_pos = max(duration - end_margin, 1.0)
        if seek_result is not None:
            cursor_sec, t_read = seek_result
            by_clip = t_read + max(0.0, end_pos - cursor_sec)
        else:
            by_clip = t_base + end_pos
        play_end = min(play_end, max(by_clip, t_base + 2.0))
    return play_end

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

def play_instant_replay(t0, latest_replay, duration):
    """毎試合のリプレイ：確認済みのファイルを再生し、元のシーンへ戻す"""
    client = get_obs_client()
    if client is None:
        return

    original_scene = None
    switch_attempted = False

    try:
        # 現在のシーンを記憶
        original_scene = client.get_current_program_scene().current_program_scene_name
        print(f"[OBS連携] 現在のシーンを記憶しました: {original_scene}")

        # 再生開始位置を計算（クリップの「終わりから REPLAY_TAIL_SECONDS 秒前」）
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
        seek_result = None
        if offset is not None and offset > SEEK_SKIP_THRESHOLD:
            seek_result = seek_and_report(client, offset)
        elif offset is not None:
            print("[OBS連携] 開始位置のズレが小さいため、位置指定は省略して先頭から再生します。")
        ensure_media_playing(client)

        # リプレイ再生時間（クリップの終わりの REPLAY_END_MARGIN 秒前で終わる。上限は REPLAY_PLAY_SECONDS）
        play_end = calc_play_end(t_switch, REPLAY_PLAY_SECONDS, REPLAY_END_MARGIN, duration, seek_result)
        print(f"[OBS連携] リプレイ再生中（切り替えから約{play_end - t_switch:.1f}秒）...")
        remaining = play_end - time.time()
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

def run_highlights(t0):
    """セット決着後のハイライト：山場のクリップを選び、紹介セリフ→映像 の順で続けて再生する。再生できたらTrue"""
    clips = select_highlights()
    if not clips:
        print("[ハイライト] 再生できるクリップがありません。")
        return False

    client = get_obs_client()
    if client is None:
        return False

    # 紹介セリフを作り、最初の2つ（開き＋1本目）の音声を待ち時間のうちに準備する
    used_phrases = set()
    intros = [build_intro_text(i, c, used_phrases) for i, c in enumerate(clips)]
    voices = {
        "opening": prepare_voice(HIGHLIGHT_OPENING, "opening"),
        0: prepare_voice(intros[0], "intro0"),
    }

    # ★ 自動でハイライトを始めるまでの待ち時間（段階3で手動選択に差し替える場所） ★
    remaining = (t0 + HIGHLIGHT_START_DELAY) - time.time()
    if remaining > 0:
        print(f"[ハイライト] {remaining:.1f}秒後にハイライトを開始します...")
        time.sleep(remaining)

    # 勝利アナウンスなど、読み上げ待ちのものがあれば終わるまで待つ
    _voice_queue.join()

    original_scene = None
    switched = False
    played = 0

    try:
        original_scene = client.get_current_program_scene().current_program_scene_name
        print(f"[OBS連携] 現在のシーンを記憶しました: {original_scene}")

        # 開きのセリフ ＋ 1本目の紹介（元のシーンのまま話す）
        play_voice_file(voices.get("opening"))
        play_voice_file(voices.get(0))

        for i, clip in enumerate(clips):
            if i > 0:
                play_voice_file(voices.get(i))  # 前のクリップ再生中に準備しておいた紹介

            client.set_input_settings(
                name=MEDIA_SOURCE_NAME,
                settings={"local_file": clip["path"]},
                overlay=True
            )

            if not switched:
                print(f"[OBS連携] リプレイシーン ({REPLAY_SCENE_NAME}) へ切り替えます！")
                switched = True
                client.set_current_program_scene(REPLAY_SCENE_NAME)

            t_start = time.time()
            duration = clip.get("duration")
            seek_result = None
            if duration is not None:
                offset = max(0.0, duration - HIGHLIGHT_TAIL_SECONDS)
                if offset > SEEK_SKIP_THRESHOLD:
                    seek_result = seek_and_report(client, offset)
            ensure_media_playing(client)
            play_end = calc_play_end(t_start, HIGHLIGHT_PLAY_SECONDS, HIGHLIGHT_END_MARGIN, duration, seek_result)
            played += 1
            print(f"[ハイライト] {i + 1}/{len(clips)} 再生中: 第{clip['game_no']}試合 {clip['kind']}")

            # 再生中に、次の紹介セリフの音声を先に作っておく
            if i + 1 < len(clips):
                voices[i + 1] = prepare_voice(intros[i + 1], f"intro{i + 1}")

            remaining = play_end - time.time()
            if remaining > 0:
                time.sleep(remaining)

    except Exception as e:
        print(f"[OBS連携エラー] ハイライト再生に失敗しました: {e}")
        drop_obs_client()
    finally:
        # ★エラーが起きても必ず元のシーンに復帰させる安全対策★
        if original_scene and switched:
            try:
                c = get_obs_client()
                if c is not None:
                    print(f"[OBS連携] 元のシーン ({original_scene}) に戻します。")
                    c.set_current_program_scene(original_scene)
            except Exception as e:
                print(f"[OBS連携復帰エラー] 元のシーンに戻せませんでした: {e}")
                drop_obs_client()

    if played > 0:
        speak_zundamon(HIGHLIGHT_CLOSING, allow_random=False)
    return played > 0

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
        speak_zundamon(f"{winner_name}しょうり！ {p1_wins}対{p2_wins}でしょうしゃは{winner_name}！おめでとう、よいしょうぶだった！", allow_random=False)
    elif game_mode == "FIRST_TO" and winner_wins == target_num - 1:
        if loser_wins == target_num - 1:
            speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}おっかけリーチ！", allow_random=True)
        else:
            speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}、{winner_name}リーチ！", allow_random=True)
    else:
        speak_zundamon(f"{winner_name}しょうり、{p1_wins}ー{p2_wins}！", allow_random=True)

def main():
    global TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME
    create_initial_files()
    
    print("★ 実況システム起動 (ずんだもん・ハイライト対応版)...\n")
    
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
                                clear_set_clips()  # 新しいセットなので、溜めていたクリップ記録も破棄
                                speak_zundamon(f"1P、{P1_NAME}。2P、{P2_NAME}。{mode_str}。ぜったいにまけられないたたかいがそこにはあるのだ！", allow_random=False)
                            else:
                                if p1_w is not None and p2_w is not None:
                                    p1_wins, p2_wins = p1_w, p2_w
                                    speak_zundamon(f"スコア修正、{p1_wins}ー{p2_wins}！", allow_random=False)
                                if name_changed:
                                    speak_zundamon(f"名前変更、{P1_NAME}対{P2_NAME}！", allow_random=False)
                                if rule_changed:
                                    speak_zundamon(f"形式変更、{mode_str}！", allow_random=False)

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

                    set_finished = False
                    highlight_done = False

                    if winner_detected:
                        print("[情報] 勝利検知！リプレイ保存を最優先で実行します...")
                        set_finished = (GAME_MODE == "FIRST_TO" and (p1_wins >= TARGET_NUM or p2_wins >= TARGET_NUM))

                        # ★ 1. 最優先：リプレイバッファを保存（保存のタイミングを毎回そろえる） ★
                        save_info = start_replay_save()
                        if save_info:
                            print(f"[情報] 検知→保存要求 {save_info['client_t0'] - current_time:.2f}秒")

                        # ★ 2. 読み上げをキューに入れる（音声合成は裏スレッドで行うので待たない） ★
                        announce_reach_or_win(winner_name_tmp, winner_wins_tmp, loser_wins_tmp, p1_wins, p2_wins, TARGET_NUM, GAME_MODE)

                        # ★ 3. 新しいファイルを確認（古いリプレイは絶対に流さない） ★
                        replay_path = secure_replay_file(save_info)
                        duration = get_clip_duration_seconds(replay_path) if replay_path else None

                        # ★ 4. 先取りモードなら、ハイライト候補として記録 ★
                        if GAME_MODE == "FIRST_TO" and replay_path:
                            record_set_clip(replay_path, duration, winner_name_tmp,
                                            winner_wins_tmp, loser_wins_tmp, p1_wins, p2_wins)

                        if set_finished:
                            # ★ 5a. セット決着：即リプレイは省き、そのままハイライトへ ★
                            t0 = save_info["client_t0"] if save_info else time.time()
                            highlight_done = run_highlights(t0)
                        elif replay_path:
                            # ★ 5b. 通常の試合：今までどおり即リプレイ ★
                            play_instant_replay(save_info["client_t0"], replay_path, duration)

                        # 古いリプレイ動画のお掃除（ハイライト候補は守られる）
                        clean_old_replays()

                    if winner_detected and set_finished:
                        delay = HIGHLIGHT_RESET_DELAY if highlight_done else NO_HIGHLIGHT_RESET_DELAY
                        print(f"[情報] 試合決着を検知しました。{delay:g}秒後にスコアを自動リセットします...")
                        time.sleep(delay)
                        
                        p1_wins, p2_wins = 0, 0
                        p1_active, p2_active = False, False
                        clear_set_clips()
                        clean_old_replays()
                        update_score_display(p1_wins, p2_wins, TARGET_NUM, GAME_MODE, P1_NAME, P2_NAME)
                        
                        speak_zundamon("セットカウント、リセットするのだ！", allow_random=False)
                        print("[情報] スコアを 0-0 に自動リセットしました。")
                
                # 高速監視（0.1秒間隔）
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nシステムを安全に停止しました。")

if __name__ == "__main__":
    main()
