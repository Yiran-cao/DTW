"""
自动选波监听器
- 监听文件夹，检测新增的波形 txt 文件
- 等待文件写入完成（大小稳定）
- 解析 txt 提取 5 段波形，DTW 计算两两相似度
- 选出最相似三元组，输出 _result.txt
- 命名规则 {患者编号}{侧别}{波形类型}.txt
  波形类型 P=压力波 → 失真过滤 + 相似度阈值(<200)；V=流速波 → 长度筛选(50~120)
"""

import os
import re
import time
import tempfile
import msvcrt
import numpy as np
from itertools import combinations
from pathlib import Path


# ============================================================
# 可调常量
# ============================================================
EXPECTED_WAVEFORM_COUNT = 5  # 预期波形段数（文档头部注释：5 段波形）
MAX_ALL_DISTORTED_RETRIES = 2  # "全部波形失真"时的保护性重试次数

# 流速波 V 波峰校验参数
V_PEAK_PROMINENCE_RATIO = 0.3  # 波峰显著性阈值：局部极大值的 prominence 低于全局最大值*该比例时视为噪声/肩峰，不计入波峰
V_PEAK_POSITION_RATIO = 0.19    # 波峰位置阈值：主峰必须出现在前 20% 的采样点内

# 调试日志目录（与 run.bat / launcher.py 的 logs 目录保持一致）
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')


# ============================================================
# 调试日志
# ============================================================
def debug_log(msg):
    """以 [DEBUG] 前缀追加写入按日期命名的调试日志，失败时静默忽略"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_file = os.path.join(LOG_DIR, 'watcher_debug_{}.log'.format(time.strftime('%Y%m%d')))
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write('{} [DEBUG] {}\n'.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except Exception:
        pass


# ============================================================
# 文件写入稳定等待 / 原子写入 / 单实例锁
# ============================================================
def wait_file_stable(fpath, interval=0.15, stable_checks=3, timeout=5.0):
    """
    等待文件大小连续 stable_checks 次检测保持不变，视为写入完成。
    超时后放弃等待，返回 False（调用方仍继续处理，仅打印警告，不阻塞整体流程）。
    """
    start = time.time()
    stable_count = 0
    last_size = None
    while True:
        try:
            cur_size = os.path.getsize(fpath)
        except OSError:
            cur_size = None
        if cur_size is not None and cur_size == last_size:
            stable_count += 1
        else:
            stable_count = 0
        last_size = cur_size
        if stable_count >= stable_checks:
            return True
        if time.time() - start > timeout:
            print(f"  [警告] 等待文件写入稳定超时（{timeout}秒），继续处理: {os.path.basename(fpath)}")
            return False
        time.sleep(interval)


def write_result_atomic(out_path, content):
    """临时文件 + os.replace() 原子写入，避免读到写了一半的结果文件"""
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=out_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(content)
        os.replace(tmp_path, out_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_select_result(indices):
    """
    将选波结果写入 d:/data test/selectwave.txt。
    indices 为 1-based 波形序号列表；为空时写入 0。
    """
    out_dir = r'd:\data test'
    out_path = os.path.join(out_dir, 'selectwave.txt')
    content = '0' if not indices else ','.join(str(x) for x in indices)
    write_result_atomic(out_path, content)
    print(f"  结果已写入: {out_path}")
    return out_path


def acquire_single_instance_lock(lock_path):
    """尝试独占打开锁文件，拿不到说明已有实例在跑，返回 None"""
    try:
        lock_file = open(lock_path, 'w')
        lock_file.write('L')
        lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_file
    except OSError:
        return None


# ============================================================
# 自包含 DTW 实现（仅依赖 numpy，无需额外包）
# ============================================================
def _dtw_distance(x, y):
    """
    计算两个一维序列之间的 DTW 距离（纯 numpy 实现）。
    使用欧氏距离作为局部代价。
    """
    x = np.asarray(x, dtype=np.float64).flatten()
    y = np.asarray(y, dtype=np.float64).flatten()
    n, m = len(x), len(y)

    # 距离矩阵
    d = np.zeros((n + 1, m + 1))
    d[0, 0] = 0
    d[1:, 0] = np.inf
    d[0, 1:] = np.inf

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(x[i - 1] - y[j - 1])  # 曼哈顿距离，速度快
            d[i, j] = cost + min(d[i - 1, j], d[i, j - 1], d[i - 1, j - 1])

    return d[n, m]


# ============================================================
# 失真判断与过滤（复用自 demo_simple.py 逻辑）
# ============================================================
def is_distorted_waveform(waveform, max_repeats=15):
    """连续 max_repeats 个相同采样点 → 失真"""
    if waveform is None or len(waveform) == 0:
        return True
    count = 1
    for i in range(1, len(waveform)):
        if waveform[i] == waveform[i - 1]:
            count += 1
            if count >= max_repeats:
                return True
        else:
            count = 1
    return False


def filter_distorted_waveforms(waveforms, max_repeats=15):
    """过滤失真波形，返回有效波形列表和对应的原始序号"""
    valid = []
    valid_indices = []  # 0-based 原始序号
    for idx, w in enumerate(waveforms):
        try:
            if is_distorted_waveform(w, max_repeats=max_repeats):
                print(f"    [失真] 波形 {idx + 1} 被过滤")
            else:
                valid.append(w)
                valid_indices.append(idx)
        except Exception:
            print(f"    [异常] 波形 {idx + 1} 格式异常，已过滤")
    return valid, valid_indices

# ============================================================
# V 波峰检测（单波峰 + 波峰位置前 30%）
# ============================================================
def _peak_prominences(w, peak_indices):
    """
    计算每个局部极大值的 prominence（纯 numpy 实现，等价于 scipy.signal.find_peaks 的 prominence）。
    prominence = 从该峰下降到两侧更低点、且中途不越过更高峰所需的最小下降高度。
    """
    n = len(w)
    proms = []
    for p in peak_indices:
        h = w[p]
        # 左侧最近的不低于当前峰的峰（无则取数组起点）
        left = 0
        for q in reversed(peak_indices):
            if q < p and w[q] >= h:
                left = q
                break
        left_base = float(np.min(w[left:p + 1]))
        # 右侧最近的严格高于当前峰的峰（无则取数组末尾）
        right = n - 1
        for q in peak_indices:
            if q > p and w[q] > h:
                right = q
                break
        right_base = float(np.min(w[p:right + 1]))
        proms.append(h - max(left_base, right_base))
    return proms


def find_v_wave_peaks(waveform, prominence_ratio=V_PEAK_PROMINENCE_RATIO):
    """
    检测显著波峰（局部极大值），返回波峰索引列表。
    仅保留 prominence >= 全局最大值 * prominence_ratio 的局部极大值，
    用于忽略主峰上的小切迹、升支阶梯和降支的小幅振荡。
    """
    w = np.asarray(waveform, dtype=np.float64).flatten()
    n = len(w)
    if n < 3:
        return []
    global_max = float(np.max(w))
    if global_max <= 0:
        return []
    local_peaks = [i for i in range(1, n - 1) if w[i] > w[i - 1] and w[i] > w[i + 1]]
    if not local_peaks:
        return []
    proms = _peak_prominences(w, local_peaks)
    threshold = prominence_ratio * global_max
    return [p for p, prom in zip(local_peaks, proms) if prom >= threshold]


def check_v_wave_peak(waveform, position_ratio=V_PEAK_POSITION_RATIO):
    """
    流速波 V 波的波峰校验，返回 (是否合格, 波峰个数, 主峰位置比例或 None)。
    1. 波峰个数必须为 1（单波峰），否则不合格
    2. 主峰（全局最大值）位置必须出现在前 position_ratio（默认 30%）采样点内，否则不合格
    """
    w = np.asarray(waveform, dtype=np.float64).flatten()
    n = len(w)
    peaks = find_v_wave_peaks(w)

    # 规则1：波峰个数必须为 1
    if len(peaks) != 1:
        return False, len(peaks), None

    # 规则2：主峰位置必须在前 30% 采样点内
    main_idx = int(np.argmax(w))
    peak_ratio = main_idx / float(n)
    if peak_ratio > position_ratio:
        return False, 1, peak_ratio

    return True, 1, peak_ratio


# ============================================================
# TXT 解析
# ============================================================
def parse_txt_waveforms(file_path):
    """按 ===== 波形 N ===== 分割，提取所有波形数据"""
    text = Path(file_path).read_text(encoding='gbk', errors='replace')
    lines = [line.strip() for line in text.splitlines()]

    waveforms = []
    current = []
    for line in lines:
        if not line:
            continue
        # 检测分隔符：===== 波形 N =====
        if '=====' in line and '波形' in line:
            if current:
                waveforms.append(np.array(current, dtype=np.float64))
                current = []
            continue
        try:
            current.append(float(line))
        except ValueError:
            continue

    if current:
        waveforms.append(np.array(current, dtype=np.float64))

    return waveforms


# ============================================================
# 文件命名解析
# ============================================================
def parse_filename(file_name):
    """
    解析文件名，支持两种格式：
      {患者编号}{侧别}{波形类型}.txt → ('260507001', 'L', 'P')
      {侧别}{波形类型}.txt           → (None, 'L', 'P')
    例如 260507001LP.txt → ('260507001', 'L', 'P')
         LP.txt          → (None, 'L', 'P')
    """
    stem = Path(file_name).stem  # 去掉 .txt
    # 格式1：数字串 + L/R + P/V
    match = re.match(r'^(\d+)([LR])([PV])$', stem)
    if match:
        return match.group(1), match.group(2), match.group(3)
    # 格式2：仅 L/R + P/V（无患者编号）
    match = re.match(r'^([LR])([PV])$', stem)
    if match:
        return None, match.group(1), match.group(2)
    return None


# ============================================================
# DTW 最相似三元组
# ============================================================
def compute_dtw_distance_matrix(waveforms):
    """计算 N 个波形的两两 DTW 距离矩阵（对称）"""
    n = len(waveforms)
    dist_mat = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            w1 = np.asarray(waveforms[i], dtype=np.float64).flatten()
            w2 = np.asarray(waveforms[j], dtype=np.float64).flatten()
            d = _dtw_distance(w1, w2)
            dist_mat[i, j] = d
            dist_mat[j, i] = d
    return dist_mat


def select_best_triplet(distance_matrix):
    """枚举三元组，返回总距离最小的 (i,j,k) 和总距离（1-based 序号）"""
    n = distance_matrix.shape[0]
    if n < 3:
        return None, float('inf')

    best_sum = float('inf')
    best_triplet = None
    for i, j, k in combinations(range(n), 3):
        total = distance_matrix[i, j] + distance_matrix[i, k] + distance_matrix[j, k]
        if total < best_sum:
            best_sum = total
            best_triplet = (i, j, k)
    return best_triplet, best_sum

# ============================================================
# 选波主函数：输入单个 txt，输出 _result.txt
# ============================================================
def process_wave_file(txt_path):
    """处理单个波形 txt 文件，生成 _result.txt"""
    print(f"\n{'=' * 50}")
    print(f"[处理] {txt_path}")
    print(f"{'=' * 50}")

    trigger_time = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        fsize = os.path.getsize(txt_path)
        fmtime = os.path.getmtime(txt_path)
    except OSError:
        fsize, fmtime = -1, -1

    file_name = os.path.basename(txt_path)
    info = parse_filename(file_name)
    if info is None:
        print(f"  [跳过] 文件名不符合命名规则: {file_name}")
        return
    patient_id, side, wave_type = info
    wave_label = '压力波(P)' if wave_type == 'P' else '流速波(V)'
    if patient_id:
        print(f"  患者: {patient_id}  侧别: {side}  类型: {wave_label}")
    else:
        print(f"  侧别: {side}  类型: {wave_label}")

    # 1. 解析波形
    waveforms = parse_txt_waveforms(txt_path)
    first_count = len(waveforms)
    debug_log('处理文件: pid={} 触发时间={} 文件={} size={} mtime={} 原始波形段数={}'.format(
        os.getpid(), trigger_time, txt_path, fsize, fmtime, first_count))

    # 波形数量二次校验：明显少于预期说明可能读取不完整，等待后重试一次
    if first_count < EXPECTED_WAVEFORM_COUNT:
        print(f"  [警告] 波形数量异常（{first_count} < 预期 {EXPECTED_WAVEFORM_COUNT}），可能读取不完整，等待后重新解析...")
        time.sleep(0.5)
        waveforms = parse_txt_waveforms(txt_path)
        second_count = len(waveforms)
        debug_log('波形数量异常重试: 文件={} 重试前段数={} 重试后段数={}'.format(
            txt_path, first_count, second_count))
        if second_count < EXPECTED_WAVEFORM_COUNT:
            print(f"  [警告] 重试后波形数量仍不足（{second_count} 个），按现有逻辑继续处理")
        else:
            print(f"  重新解析后波形数量: {second_count} 个")

    if len(waveforms) == 0:
        print(f"  [错误] 未解析到任何波形数据")
        return
    print(f"  解析到 {len(waveforms)} 个波形")

    # 2. 失真过滤（仅 P 波）
    if wave_type == 'P':
        valid_waveforms, valid_indices = filter_distorted_waveforms(waveforms)
        if len(valid_waveforms) < 3:
            print(f"  [警告] 过滤后有效波形不足 3 个（{len(valid_waveforms)}），无法选三元组")
            debug_log('输出0: 文件={} 过滤前波形数={} 过滤后有效波形数={}'.format(
                txt_path, len(waveforms), len(valid_waveforms)))
            write_select_result([])
            return
        print(f"  过滤后有效波形: {len(valid_waveforms)} 个")

        # 3. DTW 距离矩阵 (P 波：两两比较)
        dist_mat = compute_dtw_distance_matrix(valid_waveforms)
        print(f"  DTW 距离矩阵:\n{np.array2string(dist_mat, precision=2)}")

        # 3.5 相似度阈值：只保留与其他波形最短 DTW 距离 < 200 的波，>= 200 的删掉
        n = len(valid_waveforms)
        kept_positions = []
        for i in range(n):
            min_dist = min(dist_mat[i, j] for j in range(n) if j != i)
            if min_dist < 200:
                kept_positions.append(i)
            else:
                print(f"    [相似度阈值] 波形 {valid_indices[i] + 1} 与其他波形最短 DTW 距离 {min_dist:.2f} >= 200，已删除")

        if len(kept_positions) < 3:
            print(f"  [警告] 相似度阈值过滤后有效波形不足 3 个（{len(kept_positions)}），无法选三元组")
            debug_log('输出0: 文件={} 相似度阈值过滤后有效波形数={}'.format(txt_path, len(kept_positions)))
            write_select_result([])
            return

        # 3.6 用保留波形重新计算距离矩阵并选最相似三元组
        active_waveforms = [valid_waveforms[i] for i in kept_positions]
        mapping = [valid_indices[i] for i in kept_positions]  # 映射到原始 1-based 序号
        dist_mat = compute_dtw_distance_matrix(active_waveforms)
        print(f"  阈值过滤后 DTW 距离矩阵:\n{np.array2string(dist_mat, precision=2)}")

        # 4. 选最相似三元组
        triplet, total_dist = select_best_triplet(dist_mat)
        if triplet is None:
            print(f"  [错误] 无法选出三元组")
            return
        result_indices = sorted([mapping[t] + 1 for t in triplet])
        print(f"  最相似三元组（1-based 序号）: {result_indices}  总距离: {total_dist:.4f}")

    else:
        # === V 波：先按长度筛选（60~135 个采样点），再做相似度比较 ===
        # 长度筛选放在相似度计算之前
        length_kept = []
        for i, w in enumerate(waveforms):
            wlen = len(w)
            if 60 <= wlen <= 135:
                length_kept.append(i)
            else:
                print(f"    [长度过滤] 波形 {i + 1} 长度 {wlen} 不在 60~135 范围内，已删除")

        if not length_kept:
            print(f"  [警告] 长度筛选后无有效波形，5 个波均不符合要求")
            debug_log('输出0: 文件={} 长度筛选后有效波形数=0'.format(txt_path))
            write_select_result([])
            return
        print(f"  长度筛选后有效波形: {len(length_kept)} 个")

        # 波峰校验（V 波）：单波峰 + 波峰位置在前 30%（仅对通过长度筛选的波形）
        v_valid = []
        for i in length_kept:
            w = waveforms[i]
            ok, peak_count, peak_ratio = check_v_wave_peak(w)
            if ok:
                v_valid.append((i, w))
            elif peak_count == 0:
                print(f"    [波峰过滤] 波形 {i + 1} 被过滤（未检测到波峰）")
            elif peak_count > 1:
                print(f"    [波峰过滤] 波形 {i + 1} 被过滤（波峰个数={peak_count}，非单波峰）")
            else:
                print(f"    [波峰过滤] 波形 {i + 1} 被过滤（波峰位置在 {peak_ratio:.1%}，超出前 20%）")
        debug_log('V波峰过滤: 文件={} 过滤前波形数={} 过滤后有效波形数={}'.format(
            txt_path, len(length_kept), len(v_valid)))

        if len(v_valid) < 3:
            print(f"  [警告] 波峰过滤后有效波形不足 3 个（{len(v_valid)}），无法选 3 个波形")
            debug_log('输出0(V波峰过滤): 文件={} 过滤前波形数={} 过滤后有效波形数={}'.format(
                txt_path, len(length_kept), len(v_valid)))
            write_select_result([])
            return

        print(f"  波峰过滤后有效波形: {len(v_valid)} 个")

        # L 侧用 ruleLV.txt，R 侧用 ruleRV.txt
        rule_name = f'rule{side}V.txt'
        rule_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), rule_name)
        if not os.path.exists(rule_path):
            print(f"  [错误] 标准波形文件不存在: {rule_path}")
            return
        rule_waveforms = parse_txt_waveforms(rule_path)
        if len(rule_waveforms) == 0:
            print(f"  [错误] 标准波形文件未包含有效波形: {rule_path}")
            return
        reference = rule_waveforms[0]  # 取第一个波形作为标准
        print(f"  已加载标准波形: {rule_path} (长度={len(reference)})")

        # 计算每个有效 V 波形与标准波形的 DTW 距离
        distances = []
        for i, w in v_valid:
            d = _dtw_distance(w, reference)
            distances.append((i, d))
            print(f"    波形 {i + 1} 与标准波形 DTW 距离: {d:.2f}")

        # 选距离最小的 3 个
        distances.sort(key=lambda x: x[1])
        top3 = distances[:3]
        result_indices = sorted([idx + 1 for idx, _ in top3])
        print(f"  最相似 3 个波形（1-based 序号）: {result_indices}")

    # 5. 输出结果到 d:\data test\selectwave.txt
    write_select_result(result_indices)


# ============================================================
# 文件夹监听器（固定 4 文件 + 初次逐个触发模式）
# ============================================================
def watch_folder(watch_dir='d:/data test', check_interval=0.8):
    """
    监听固定 4 个文件（LP.txt / RP.txt / LV.txt / RV.txt）：
    - 初次运行：文件逐个出现，每出现一个立即触发选波
    - 稳态运行：4 个文件均已就绪后，通过 mtime 变化检测覆写更新
    """
    watch_dir = os.path.abspath(watch_dir)
    os.makedirs(watch_dir, exist_ok=True)

    # 单实例保护：锁文件放在监听目录下
    lock_path = os.path.join(watch_dir, '.watcher.lock')
    lock_file = acquire_single_instance_lock(lock_path)
    if lock_file is None:
        print("检测到已有 watcher 实例正在运行，本次启动退出，如需调试请先停止后台服务")
        return

    FILE_NAMES = ['LP.txt', 'RP.txt', 'LV.txt', 'RV.txt']

    print(f"监听目录: {watch_dir}")
    print(f"输出文件: d:\\data test\\selectwave.txt")
    print(f"模式: 初次逐个触发 / 稳态 mtime 变化触发")

    tracked = {}       # {full_path: last_mtime}, 0 表示从未出现过
    all_seen = False   # 是否已进入稳态

    try:
        while True:
            time.sleep(check_interval)

            present_count = 0
            for fname in FILE_NAMES:
                fpath = os.path.join(watch_dir, fname)
                if not os.path.isfile(fpath):
                    continue
                present_count += 1

                try:
                    cur_mtime = os.path.getmtime(fpath)
                except OSError:
                    continue

                last_mtime = tracked.get(fpath, 0)

                if last_mtime == 0:
                    # 文件首次出现 → 先等待写入稳定，再处理
                    print(f"\n>>> 检测到新文件: {fname}")
                    wait_file_stable(fpath)
                    try:
                        tracked[fpath] = os.path.getmtime(fpath)
                    except OSError:
                        tracked[fpath] = cur_mtime
                    process_wave_file(fpath)
                elif cur_mtime > last_mtime:
                    # 文件内容更新 → 先等待写入稳定，再处理
                    print(f"\n>>> 检测到文件更新: {fname}")
                    wait_file_stable(fpath)
                    try:
                        tracked[fpath] = os.path.getmtime(fpath)
                    except OSError:
                        tracked[fpath] = cur_mtime
                    process_wave_file(fpath)

            if present_count == 4 and not all_seen:
                all_seen = True
                print("\n全部 4 个文件已就绪，进入稳态监听模式...\n")

    except KeyboardInterrupt:
        print("\n监听已停止。")


# ============================================================
# 入口
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='DTW 自动选波监听器')
    parser.add_argument('--dir', default='d:/data test', help='监听目录 (默认 d:/data test)')
    parser.add_argument('--interval', type=float, default=0.8, help='扫描间隔/秒 (默认 0.8)')
    parser.add_argument('--once', type=str, default=None, help='单次处理指定文件后退出')
    args = parser.parse_args()

    if args.once:
        process_wave_file(args.once)
    else:
        watch_folder(watch_dir=args.dir, check_interval=args.interval)
