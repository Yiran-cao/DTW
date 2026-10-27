# watcher.py 修复需求说明 —— 文件读取时机竞态问题

> 本文档用于交给 Claude Code（或其他 agent），在不改动 DTW / 失真过滤 / 选波核心算法逻辑的前提下，修复 `watcher.py` 中的文件读取时机问题。请先通读 `watcher.py` 全文，再按下述需求编码。

## 一、问题背景

`watcher.py` 监听固定 4 个波形文件（`LP.txt` / `RP.txt` / `LV.txt` / `RV.txt`），检测软件每次保存数据时会覆盖写入这些文件。目前观察到的现象：

- 同一份数据反复保存（文件内容不变，仅 mtime 更新），`selectwave.txt` 的输出**有时正常、有时是 `0`**（`0` 代表"过滤后有效波形不足 3 个"）。
- 已确认当前机器上只有一个 `python.exe`/`pythonw.exe` 进程在跑 watcher，**排除了多实例同时写文件互相覆盖的可能**。
- 定位到根因：`watch_folder()` 中检测到文件 mtime 变化后，**立即**调用 `process_wave_file()` 读取文件，中间没有任何"等待文件写入完成"的校验逻辑（尽管文件顶部注释写了"等待文件写入完成（大小稳定）"，但代码里从未真正实现）。检测软件保存文件是"打开截断 → 写入 → 关闭"的过程，watcher 的 0.8 秒轮询有概率恰好撞在这个写入过程中间，读到不完整/被截断的数据，导致波形数量不足或末尾出现大量重复值被误判为失真，最终过滤后不足 3 个波形，输出 `0`。

## 二、需要实现的修复（不改动 DTW 计算、失真判定、三元组选择等核心算法逻辑）

### 1. 新增"等待文件稳定"函数

在 `watcher.py` 中新增一个函数，等待目标文件大小连续多次不变后，才认为写入已完成：

```python
def wait_file_stable(fpath, interval=0.15, stable_checks=3, timeout=5.0):
    """
    等待文件大小连续 stable_checks 次检测保持不变，视为写入完成。
    超时后放弃等待，返回 False（调用方仍继续处理，仅打印警告，不阻塞整体流程）。
    """
```

- `interval`：每次检测间隔（默认 0.15 秒）
- `stable_checks`：连续稳定次数阈值（默认 3 次）
- `timeout`：最长等待时间，超时后放弃、打印警告、返回 `False`，但**仍继续往下处理**（不能因为等待超时导致整个流程卡死或跳过处理）

### 2. 在 `watch_folder()` 检测到文件出现/更新后，先等待稳定，再处理

将现有的：

```python
if last_mtime == 0:
    print(f"\n>>> 检测到新文件: {fname}")
    tracked[fpath] = cur_mtime
    process_wave_file(fpath)
elif cur_mtime > last_mtime:
    print(f"\n>>> 检测到文件更新: {fname}")
    tracked[fpath] = cur_mtime
    process_wave_file(fpath)
```

改为：检测到变化后先调用 `wait_file_stable(fpath)`，**等待结束后重新读取一次真实的 mtime 再记录**（避免用等待前的旧 mtime 导致下一轮误判为"又有更新"），然后再调用 `process_wave_file(fpath)`。

### 3. 波形数量二次校验 + 一次重试

在 `process_wave_file()` 中，`parse_txt_waveforms()` 解析完成后，如果解析出的波形段数**明显少于预期数量**（预期段数以实际业务为准，目前文档头部注释是"5 段波形"，请在代码里做成可调常量，例如 `EXPECTED_WAVEFORM_COUNT = 5`），则：

- 打印警告日志，说明"波形数量异常，可能读取不完整"
- `sleep` 一小段时间（如 0.5 秒）后**重新解析一次**该文件
- 重试后仍然不足，则按现有逻辑正常往下走（不要无限重试，避免卡死监听循环）

### 4. 调试日志：记录每次处理的关键信息，便于事后排查

不要求做成复杂的日志系统，但需要满足：

- 每次 `process_wave_file()` 被调用时，记录：**进程 PID、触发时间（精确到秒）、被处理文件的 size 和 mtime、解析到的原始波形段数**。
- 如果触发了"波形数量异常重试"（见第 3 点），需要记录：重试前后的解析段数分别是多少。
- 如果最终因为"过滤后有效波形不足 3 个"输出了 `0`，需要在日志里明确记录这次的过滤前波形数、过滤后有效波形数，方便区分"数据真的失真"和"读取不完整导致误判"两种情况。
- 日志建议输出到独立文件，按日期命名，例如 `logs/watcher_debug_YYYYMMDD.log`（`logs/` 目录已在项目结构中存在，供 `run.bat` 重定向标准输出使用，调试日志可以是同目录下的另一个文件，也可以复用现有的日志重定向机制，由 agent 根据现有 `run.bat` / `launcher.py` 的日志目录约定自行判断，保持风格统一即可）。
- 现有的中文 `print` 提示信息（面向非技术人员看进度）不要删除或大改，调试信息作为**补充**，不要打乱原有输出的可读性；调试细节可以用统一前缀（如 `[DEBUG]`）区分，方便后续如果需要也可以过滤掉。

### 5. 输出结果原子写入（顺手加固，非本次问题根因，但属于同类风险）

`process_wave_file()` 最后写 `selectwave.txt` 的地方，改为"临时文件 + `os.replace()` 原子重命名"的方式写入，避免任何情况下（包括未来可能出现的多实例场景）读到写了一半的结果文件：

```python
import tempfile

def write_result_atomic(out_path, content):
    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=out_dir)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp_path, out_path)
```

`process_wave_file()` 中所有写 `selectwave.txt`（包括正常结果和 `0` 的分支）都统一改用这个函数。

### 6. 单实例保护（防御性加固）

虽然本次问题已确认与多实例无关，但为避免未来手动调试（cmd 直接跑 `watcher.py`）与后台任务计划同时运行造成竞态，请在 `watch_folder()` 入口处加单实例锁：

```python
import msvcrt

def acquire_single_instance_lock(lock_path):
    """尝试独占打开锁文件，拿不到说明已有实例在跑，返回 None"""
    try:
        lock_file = open(lock_path, 'w')
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_file
    except OSError:
        return None
```

- 锁文件放在监听目录下，如 `.watcher.lock`
- 拿不到锁时，打印清晰的中文提示（例如"检测到已有 watcher 实例正在运行，本次启动退出，如需调试请先停止后台服务"），然后直接退出，不进入监听循环
- `--once` 单文件处理模式（`process_wave_file(args.once)` 分支）不需要加锁，只对 `watch_folder()` 常驻监听模式生效

## 三、明确不要改动的部分

- `_dtw_distance`、`is_distorted_waveform`、`filter_distorted_waveforms`、`compute_dtw_distance_matrix`、`select_best_triplet` 等核心算法函数内部逻辑，一律不改
- `parse_filename` 的命名规则解析逻辑不改
- 输出文件路径（`d:\data test\selectwave.txt`）、命名规则、内容格式（逗号分隔的 1-based 序号）不改
- 4 个固定监听文件名（`LP.txt` / `RP.txt` / `LV.txt` / `RV.txt`）不改

## 四、验收标准

- [ ] 检测软件反复保存同一份数据（内容不变、mtime 更新），watcher 输出的 `selectwave.txt` 结果应保持一致，不再出现"这次 0、下次正常"的情况
- [ ] 人为制造一次"写入过程中读取"的场景（例如手动用极慢速度分多次追加写入测试文件），确认 `wait_file_stable` 能等到写入完成后再处理，不会读到半截数据
- [ ] 调试日志中能清晰看到每次处理的 PID、时间、文件 size/mtime、解析到的波形段数，出现 `0` 结果时能通过日志判断是"数据真的失真"还是"读取不完整"
- [ ] 用 cmd 手动再起一个 watcher.py 实例（后台任务计划已有一个在跑），确认新实例会提示"已有实例运行"并退出，不会和后台实例同时处理同一批文件
- [ ] `--once` 单文件处理模式功能不受影响，行为与之前一致
- [ ] `selectwave.txt` 写入方式改为原子写入后，正常结果和 `0` 结果两种分支都验证过写入行为正常
