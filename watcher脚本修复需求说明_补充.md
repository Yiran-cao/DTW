# watcher.py 修复需求补充说明 —— "全部失真"误判问题

> 承接上一份《watcher脚本修复需求说明.md》。已应用的 `wait_file_stable`（基于文件大小连续稳定）和"波形数量不足重试"逻辑，未能解决问题。新发现现象：LP.txt/RP.txt 被 watcher 处理时，**全部波形（4-5个）都被判定为失真**，导致输出 0；但用 cmd 稍晚手动重新处理**同一份文件**，结果正常（如 [1,2,3]）。经核对上传的 LP.TXT/RP.txt 样本数据，波形本身并不存在连续 15 点以上相同值的特征，说明大概率不是数据真的失真，而是 watcher 读取时机仍然过早。

## 一、根因怀疑：`wait_file_stable` 基于"文件大小"判断稳定性，在"预分配文件+原地覆写"的保存方式下会失效

如果检测软件保存文件时是**固定行数预分配、原地覆写数值**（而不是先清空再逐行追加），那么文件大小从覆写开始那一刻起就已经是最终大小，`wait_file_stable`（只看 size 是否连续不变）会**几乎瞬间**判定"文件已稳定"，但此时内容可能仍在被覆写，读到的可能是"上一次保存的旧数据残留 + 本次新数据"混杂的中间态。这种新旧数据拼接的情况，比单纯的"半截空白"更容易制造出长串连续相同值，从而让 `is_distorted_waveform` 大量误判。

## 二、需要修复的点

### 1. 稳定性判断改为同时监控 `mtime` 连续不变，而不只是 `size`

文件每次被写入（即使是原地覆写、大小不变），操作系统也会更新其 mtime。因此应该把"稳定"的判定标准改为：**mtime 连续 N 次检测保持不变**（而不仅是 size 不变）。

```python
def wait_file_stable(fpath, interval=0.15, stable_checks=4, timeout=5.0):
    """
    等待文件 mtime 和 size 都连续 stable_checks 次检测保持不变，视为写入完成。
    使用 mtime 而非仅 size，因为部分保存方式是"预分配固定大小 + 原地覆写"，
    此时文件大小全程不变，仅靠 size 无法判断是否仍在写入。
    超时后放弃等待，打印警告并返回 False，但调用方仍继续处理（不阻塞整体流程）。
    """
    last_size = -1
    last_mtime = -1
    stable_count = 0
    start = time.time()
    while time.time() - start < timeout:
        try:
            size = os.path.getsize(fpath)
            mtime = os.path.getmtime(fpath)
        except OSError:
            time.sleep(interval)
            continue
        if size == last_size and mtime == last_mtime and size > 0:
            stable_count += 1
            if stable_count >= stable_checks:
                return True
        else:
            stable_count = 0
        last_size, last_mtime = size, mtime
        time.sleep(interval)
    print(f"    [警告] 等待文件稳定超时: {fpath}")
    return False
```

### 2. 新增"全部失真"保护性重试（区别于已有的"波形数量不足"重试）

已有的重试逻辑只处理"解析出的波形段数少于预期"的情况。现在需要新增一层保护：**如果过滤后有效波形数为 0（即全部被判定失真）**，这是一个强烈的异常信号（真实数据里 5 段全部失真的概率很低），应该：

- 打印警告日志，说明"全部波形被判定失真，疑似读取时机过早，等待后重试"
- 等待一小段时间（如 0.5～0.8 秒）后，**重新完整读取并重新解析该文件**（不是复用内存里的旧数据）
- 重新执行一次失真过滤
- 最多重试 2 次（避免无限循环），仍然全部失真才最终判定为真的失真、写入 0
- 每次重试都要记录日志（第几次重试、重试前后有效波形数变化）

请在 `process_wave_file` 中，P 波分支的 `filter_distorted_waveforms` 之后加入该逻辑，示例结构：

```python
MAX_ALL_DISTORTED_RETRIES = 2

def process_wave_file(txt_path):
    ...
    if wave_type == 'P':
        retry_count = 0
        while True:
            waveforms = parse_txt_waveforms(txt_path)  # 重试时需要重新读文件，不能复用旧数据
            valid_waveforms, valid_indices = filter_distorted_waveforms(waveforms)

            if len(valid_waveforms) > 0 or retry_count >= MAX_ALL_DISTORTED_RETRIES:
                break

            retry_count += 1
            print(f"  [警告] 全部波形被判定失真（第{retry_count}次），疑似读取时机过早，等待后重试")
            time.sleep(0.6)

        if len(valid_waveforms) < 3:
            print(f"  [警告] 过滤后有效波形不足 3 个（{len(valid_waveforms)}），无法选三元组")
            ...
```

### 3. 诊断日志：记录每个波形被判失真时的"最长连续相同点长度"

目前 `is_distorted_waveform` 只返回 True/False，看不出"差多少"。为了后续排查是"真失真"还是"读取问题"，需要让失真判断同时返回最长连续相同点的长度，并打印出来：

```python
def is_distorted_waveform(waveform, max_repeats=15):
    """连续 max_repeats 个相同采样点 → 失真。返回 (是否失真, 最长连续相同点长度)"""
    if waveform is None or len(waveform) == 0:
        return True, 0
    count = 1
    max_count = 1
    for i in range(1, len(waveform)):
        if waveform[i] == waveform[i - 1]:
            count += 1
            max_count = max(max_count, count)
        else:
            count = 1
    return max_count >= max_repeats, max_count
```

对应 `filter_distorted_waveforms` 里打印时改为带上具体数值，例如：

```python
is_bad, run_len = is_distorted_waveform(w, max_repeats=max_repeats)
if is_bad:
    print(f"    [失真] 波形 {idx + 1} 被过滤（最长连续相同点={run_len}）")
```

这样以后再出现"全部失真"的情况，日志里能直接看出是刚好压线（比如 15-16，接近读取中间态的特征）还是远超阈值（比如 40+，更像真的传感器卡死），帮助你判断阈值 `max_repeats=15` 本身是否需要调整，还是纯粹是时机问题。

### 4. 顺便确认：`run.bat` / `launcher.py` 本身不是本次问题的根因

看了你贴的 `run.bat`，激活虚拟环境、调用 `launcher.py`、重定向日志这几步逻辑正常，`launcher.py` 只负责拉起 `watcher.py` 一次性检查+启动，不涉及后续的文件读取时机问题，这部分不需要改动。**本次问题定位应集中在 `watcher.py` 的 `wait_file_stable` 判断标准和失真判断的重试保护上**，不需要动 `run.bat`。

## 三、验收标准（补充）

- [ ] 让检测软件按其真实的"预分配+原地覆写"方式反复保存同一份不失真数据，watcher 输出结果应保持稳定为正常三元组，不再出现全部失真判 0 的情况
- [ ] 日志中能看到每个波形被过滤时的"最长连续相同点长度"数值，用于人工复核判断是否真失真
- [ ] 人为制造一次"全部失真"的边界场景（如手动构造一个覆写到一半的测试文件），确认能触发重试逻辑并在日志中看到重试记录
- [ ] 确认 `wait_file_stable` 修改为基于 mtime+size 双重判断后，处理耗时没有异常增长（正常保存场景下不应频繁触发 timeout 警告）
