# -*- coding: utf-8 -*-
"""
自动选波 —— 检测软件伴随守护进程（Win7 可用，仅依赖 Python 标准库）

作用：
    登录后常驻，每 poll_interval 秒检查一次"检测软件"是否在运行。
      - 检测软件启动 → 拉起 run.bat，开始监听目录并自动选波
      - 检测软件退出 → 通知 watcher 正常退出，停止监听
    空闲时只做一次进程快照（不导入 numpy、不扫描磁盘），CPU 与磁盘占用可忽略，
    避免老机器一开机就被 numpy 启动 + 目录轮询压满而卡顿/蓝屏。

用法：
    pythonw.exe guard.py         常驻守护（由任务计划程序在登录时调用）
    python  guard.py --once      只检查一次并打印判定结果
    python  guard.py --list      列出当前进程名与窗口标题，用于核对 app_process 配置
    python  guard.py --dry-run   不真正启停 watcher，只记录将要执行的动作
    python  guard.py --stop      停止守护进程与 watcher
"""

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_DIR, 'guard_config.ini')
LOG_DIR = os.path.join(PROJECT_DIR, 'logs')
GUARD_LOCK_PATH = os.path.join(PROJECT_DIR, 'guard.lock')
GUARD_PID_PATH = os.path.join(PROJECT_DIR, 'guard.pid')

CREATE_NO_WINDOW = 0x08000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
STILL_ACTIVE = 259
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_TERMINATE = 0x0001
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260

DEFAULT_CONFIG = [
    ('app_process', 'hello软件操作版'),
    ('app_title', 'hello软件操作版'),
    ('poll_interval', '3'),
    ('start_consecutive', '2'),
    ('stop_delay', '5'),
    ('startup_delay', '20'),
    ('start_timeout', '25'),
    ('stop_timeout', '8'),
    ('retry_cooldown', '120'),
    ('watch_dir', 'd:/data test'),
    ('start_command', 'run.bat'),
    ('pid_file', 'watcher.pid'),
    ('stop_flag', 'watcher.stop'),
    ('status_file', 'd:/data/watcher_status.txt'),
]

DEFAULT_CONFIG_TEXT = """\
; ============================================================
; DTW 自动选波 —— 检测软件伴随守护配置
; 修改后执行 run_guard.bat stop，再执行 run_guard.bat 生效
; ============================================================
[guard]
; 检测软件进程名，多个用逗号分隔；.exe 后缀可写可不写
; 不确定时：打开检测软件，双击 run_guard.bat list 查看候选进程名
app_process = hello软件操作版

; 窗口标题关键字（进程名对不上时的兜底匹配）；留空则只按进程名判断
app_title = hello软件操作版

; 检查间隔（秒）。越大越省资源，默认 3 秒足够
poll_interval = 3

; 连续命中几次才算"软件已打开"，避免闪一下就拉起 watcher（默认 2 次 ≈ 6 秒）
start_consecutive = 2

; 软件关闭后等待多少秒再停止监测，避免软件重启导致反复启停（默认 5 秒）
stop_delay = 5

; 登录后延迟多少秒才开始检测，避开开机磁盘高峰（默认 20 秒）
startup_delay = 20

; 启动 watcher 后等待出现进程的最长秒数
start_timeout = 25

; 通知 watcher 退出后等待的最长秒数，超时才强制结束
stop_timeout = 8

; 启动失败后锁定多少秒再重试，避免反复失败刷日志
retry_cooldown = 120

; 监听目录（watcher --dir），保持与 watcher 默认一致
watch_dir = d:/data test

; 启动命令（相对本项目目录，或写绝对路径）
start_command = run.bat

; watcher 的 pid 文件与停止信号文件（相对本项目目录）
pid_file = watcher.pid
stop_flag = watcher.stop

; 对外状态文件（供上位机/人工查看）
status_file = d:/data/watcher_status.txt
"""


# ============================================================
# Windows API 封装
# ============================================================
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_void_p),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_wchar * MAX_PATH),
    ]


_kernel32 = None
_user32_lib = None
_wndenumproc = None


def _user32():
    global _user32_lib, _wndenumproc
    if _user32_lib is None:
        lib = ctypes.WinDLL('user32', use_last_error=True)
        _wndenumproc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        lib.IsWindowVisible.argtypes = [wintypes.HWND]
        lib.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        lib.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        lib.EnumWindows.argtypes = [_wndenumproc, wintypes.LPARAM]
        _user32_lib = lib
    return _user32_lib


def _k32():
    global _kernel32
    if _kernel32 is None:
        lib = ctypes.WinDLL('kernel32', use_last_error=True)
        lib.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        lib.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        lib.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        lib.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        lib.OpenProcess.restype = wintypes.HANDLE
        lib.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        lib.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        lib.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        lib.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32 = lib
    return _kernel32


def iter_processes():
    """枚举当前所有进程，返回 [(pid, exe 文件名), ...]"""
    if os.name != 'nt':
        return []
    k32 = _k32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    result = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            result.append((int(entry.th32ProcessID), entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return result


def iter_window_titles():
    """枚举可见顶层窗口的标题（用于进程名对不上时的兜底匹配）"""
    if os.name != 'nt':
        return []
    user32 = _user32()
    enum_proc = _wndenumproc

    titles = []

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            text = buf.value.strip()
            if text:
                titles.append(text)
        except Exception:
            pass
        return True

    user32.EnumWindows(enum_proc(_callback), 0)
    return titles


def _pid_alive(pid):
    if not pid:
        return False
    k32 = _k32()
    handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return True
    finally:
        k32.CloseHandle(handle)


def _terminate_pid(pid):
    k32 = _k32()
    handle = k32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return False
    try:
        return bool(k32.TerminateProcess(handle, 0))
    finally:
        k32.CloseHandle(handle)


# ============================================================
# 输出 / 日志
# ============================================================
def safe_print(text):
    """pythonw 下 stdout 为 None；非中文代码页下也不会因编码异常中断守护"""
    if sys.stdout is None:
        return
    try:
        print(text)
        sys.stdout.flush()
    except Exception:
        try:
            print(text.encode('ascii', 'replace').decode('ascii'))
        except Exception:
            pass


def log(msg):
    line = '{} [guard] {}'.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, 'guard_{}.log'.format(time.strftime('%Y%m%d')))
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass
    safe_print(line)


def write_status(cfg, status, detail):
    if not cfg.status_path:
        return
    try:
        folder = os.path.dirname(cfg.status_path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(cfg.status_path, 'w', encoding='utf-8') as f:
            f.write('status={}\ntime={}\ndetail={}'.format(
                status, time.strftime('%Y-%m-%d %H:%M:%S'), detail))
    except Exception as e:
        log('写入状态文件失败: {}'.format(e))


# ============================================================
# 配置
# ============================================================
def _read_text_any(path):
    """兼容 UTF-8 / UTF-8 BOM / GBK（记事本另存为 ANSI 的情况）"""
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8-sig', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


def _split_list(value):
    out = []
    for chunk in (value or '').replace('，', ',').replace('；', ';').replace(';', ',').split(','):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


def _num(value, default, low, high):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return float(default)
    if num < low:
        num = float(low)
    if num > high:
        num = float(high)
    return num


def _abs(path):
    path = (path or '').strip()
    if not path:
        return ''
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(PROJECT_DIR, path))


class Config(object):
    def __init__(self, raw):
        self.app_process = _split_list(raw.get('app_process'))
        self.app_title = _split_list(raw.get('app_title'))
        self.poll_interval = _num(raw.get('poll_interval'), 3, 0.5, 600)
        self.start_consecutive = int(_num(raw.get('start_consecutive'), 2, 1, 100))
        self.stop_delay = _num(raw.get('stop_delay'), 5, 0, 3600)
        self.startup_delay = _num(raw.get('startup_delay'), 20, 0, 3600)
        self.start_timeout = _num(raw.get('start_timeout'), 25, 5, 600)
        self.stop_timeout = _num(raw.get('stop_timeout'), 8, 1, 600)
        self.retry_cooldown = _num(raw.get('retry_cooldown'), 120, 10, 86400)
        self.watch_dir = (raw.get('watch_dir') or 'd:/data test').strip()
        self.start_command_path = _abs(raw.get('start_command') or 'run.bat')
        self.pid_path = _abs(raw.get('pid_file') or 'watcher.pid')
        self.stop_flag_path = _abs(raw.get('stop_flag') or 'watcher.stop')
        self.status_path = _abs(raw.get('status_file') or '')


def load_config(config_path=None):
    path = config_path or CONFIG_PATH
    raw = dict(DEFAULT_CONFIG)
    if not os.path.isfile(path):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_CONFIG_TEXT)
            log('未找到配置文件，已生成默认配置: {}'.format(path))
        except Exception as e:
            log('生成默认配置失败，使用内置默认值: {}'.format(e))
    else:
        try:
            import configparser
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(_read_text_any(path))
            if parser.has_section('guard'):
                for key, val in parser.items('guard'):
                    raw[key.strip()] = val.strip()
        except Exception as e:
            log('读取配置失败，使用内置默认值: {}'.format(e))
    return Config(raw)


# ============================================================
# 检测软件判断
# ============================================================
def _norm_name(name):
    text = (name or '').strip().lower()
    if text.endswith('.exe'):
        text = text[:-4]
    return text


def is_app_running(cfg):
    """返回 (是否在运行, 匹配原因描述)"""
    proc_keys = set(k for k in (_norm_name(x) for x in cfg.app_process) if k)
    title_keys = [x.strip().lower() for x in cfg.app_title if x.strip()]

    for pid, exe in iter_processes():
        if _norm_name(exe) in proc_keys:
            return True, 'process {} pid={}'.format(exe, pid)

    if title_keys:
        for title in iter_window_titles():
            lower = title.lower()
            for key in title_keys:
                if key in lower:
                    return True, 'window {}'.format(title)
    return False, ''


# ============================================================
# watcher 启停
# ============================================================
def _read_pid(path):
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return None


def watcher_pid(cfg):
    if not cfg.pid_path:
        return None
    return _read_pid(cfg.pid_path)


def watcher_alive(cfg):
    return _pid_alive(watcher_pid(cfg))


def _remove_file(path):
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def start_watcher(cfg, dry_run=False):
    if watcher_alive(cfg):
        log('watcher 已在运行 pid={}，无需重复启动'.format(watcher_pid(cfg)))
        return True

    # 清理上次残留的 pid / 停止信号，避免误判
    _remove_file(cfg.pid_path)
    _remove_file(cfg.stop_flag_path)

    if dry_run:
        log('[dry-run] 将执行: {}'.format(cfg.start_command_path))
        return True

    if not os.path.isfile(cfg.start_command_path):
        log('启动失败：找不到启动命令 {}'.format(cfg.start_command_path))
        write_status(cfg, 'error', 'start command missing: {}'.format(cfg.start_command_path))
        return False

    os.makedirs(LOG_DIR, exist_ok=True)
    launch_log = os.path.join(LOG_DIR, 'guard_launch_{}.log'.format(time.strftime('%Y%m%d')))
    comspec = os.environ.get('COMSPEC') or 'cmd.exe'
    try:
        fh = open(launch_log, 'a')
    except Exception as e:
        log('启动失败：无法写入启动日志: {}'.format(e))
        return False
    try:
        fh.write('\n--- {} guard start: {} ---\n'.format(
            time.strftime('%Y-%m-%d %H:%M:%S'), cfg.start_command_path))
        fh.flush()
        env = dict(os.environ)
        # 传给 launcher.py，使 guard_config.ini 的 watch_dir 生效
        env['DTW_WATCH_DIR'] = cfg.watch_dir
        subprocess.Popen(
            [comspec, '/c', cfg.start_command_path],
            cwd=PROJECT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        log('启动失败：{}'.format(e))
        write_status(cfg, 'error', 'launch failed: {}'.format(e))
        return False
    finally:
        fh.close()

    log('已执行 {}，等待 watcher 就绪...'.format(os.path.basename(cfg.start_command_path)))
    deadline = time.time() + cfg.start_timeout
    while time.time() < deadline:
        if watcher_alive(cfg):
            pid = watcher_pid(cfg)
            log('watcher 已启动 pid={}，开始监测目录 {}'.format(pid, cfg.watch_dir))
            write_status(cfg, 'ready', 'guard: app running, watcher pid={}'.format(pid))
            return True
        time.sleep(0.5)

    log('启动超时：{} 秒内未检测到 watcher 进程，详见 logs\\{}.log'.format(
        int(cfg.start_timeout), time.strftime('%Y-%m-%d')))
    write_status(cfg, 'error', 'watcher start timeout')
    return False


def stop_watcher(cfg, reason='app exited', dry_run=False):
    pid = watcher_pid(cfg)
    if not pid or not _pid_alive(pid):
        _remove_file(cfg.pid_path)
        _remove_file(cfg.stop_flag_path)
        write_status(cfg, 'stopped', reason)
        return True

    if dry_run:
        log('[dry-run] 将停止 watcher pid={} ({})'.format(pid, reason))
        return True

    log('通知 watcher 退出 pid={}（{}）'.format(pid, reason))
    try:
        with open(cfg.stop_flag_path, 'w') as f:
            f.write(reason)
    except Exception as e:
        log('写入停止信号失败，将直接结束进程: {}'.format(e))

    deadline = time.time() + cfg.stop_timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.3)

    if _pid_alive(pid):
        log('watcher 未在 {} 秒内退出，强制结束 pid={}'.format(int(cfg.stop_timeout), pid))
        _terminate_pid(pid)
        time.sleep(0.3)

    _remove_file(cfg.pid_path)
    _remove_file(cfg.stop_flag_path)
    write_status(cfg, 'stopped', reason)
    log('监测已停止')
    return True


# ============================================================
# 单实例锁
# ============================================================
def acquire_lock(path):
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        handle = open(path, 'w')
    except OSError:
        return None
    try:
        handle.write(str(os.getpid()))
        handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        try:
            handle.close()
        except Exception:
            pass
        return None


# ============================================================
# 主循环
# ============================================================
def run_loop(cfg, dry_run=False):
    if not cfg.app_process and not cfg.app_title:
        log('配置错误：app_process 与 app_title 都为空，无法判断检测软件是否运行')
        return 2

    lock = acquire_lock(GUARD_LOCK_PATH)
    if lock is None:
        log('已有守护进程在运行（guard.lock 被占用），本次退出')
        return 0

    try:
        with open(GUARD_PID_PATH, 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    log('守护进程启动 pid={}'.format(os.getpid()))
    log('目标进程: {}'.format(', '.join(cfg.app_process) or '(未配置)'))
    log('目标窗口标题: {}'.format(', '.join(cfg.app_title) or '(未配置)'))
    log('轮询间隔 {}s | 连续命中 {} 次启动 | 关闭后延迟 {}s 停止'.format(
        cfg.poll_interval, cfg.start_consecutive, cfg.stop_delay))

    if cfg.startup_delay > 0:
        log('等待 {} 秒后开始检测（避开登录时的磁盘高峰）'.format(int(cfg.startup_delay)))
        time.sleep(cfg.startup_delay)

    state = {'mode': 'idle', 'hits': 0, 'absent_since': None, 'blocked_until': 0.0}
    try:
        while True:
            try:
                _tick(cfg, dry_run, state)
            except Exception as e:
                log('本轮检查异常（已忽略，稍后重试）: {}'.format(e))
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        log('收到中断信号，退出前停止监测')
        if state['mode'] == 'running':
            try:
                stop_watcher(cfg, 'guard interrupted', dry_run)
            except Exception:
                pass
    finally:
        _remove_file(GUARD_PID_PATH)
        try:
            lock.close()
        except Exception:
            pass
    return 0


def _tick(cfg, dry_run, state):
    """一轮检查：检测软件存在与否 → 启动 / 停止 / 重启 watcher"""
    running, why = is_app_running(cfg)
    now = time.time()

    if running:
        state['absent_since'] = None

        if state['mode'] == 'running':
            if watcher_alive(cfg) or now < state['blocked_until']:
                return
            log('watcher 已退出但检测软件仍在运行，重新拉起')
            if start_watcher(cfg, dry_run):
                state['mode'] = 'running'
            else:
                state['mode'] = 'idle'
                state['blocked_until'] = now + cfg.retry_cooldown
                log('{} 秒后才允许重试'.format(int(cfg.retry_cooldown)))
            return

        state['hits'] += 1
        if state['hits'] < cfg.start_consecutive or now < state['blocked_until']:
            return
        log('检测软件已启动（{}），开始监测'.format(why))
        if start_watcher(cfg, dry_run):
            state['mode'] = 'running'
        else:
            state['hits'] = 0
            state['blocked_until'] = now + cfg.retry_cooldown
            log('{} 秒后才允许重试'.format(int(cfg.retry_cooldown)))
        return

    state['hits'] = 0
    if state['mode'] != 'running':
        return

    if state['absent_since'] is None:
        state['absent_since'] = now
        log('检测软件已退出，{} 秒后停止监测'.format(int(cfg.stop_delay)))
        return

    if now - state['absent_since'] >= cfg.stop_delay:
        stop_watcher(cfg, 'detection app exited', dry_run)
        state['mode'] = 'idle'
        state['absent_since'] = None


def stop_all(cfg):
    guard_pid = _read_pid(GUARD_PID_PATH)
    if guard_pid and guard_pid != os.getpid() and _pid_alive(guard_pid):
        log('停止守护进程 pid={}'.format(guard_pid))
        _terminate_pid(guard_pid)
        time.sleep(0.8)
    _remove_file(GUARD_PID_PATH)
    stop_watcher(cfg, 'guard stopped manually')
    log('守护进程与 watcher 均已停止')
    write_status(cfg, 'stopped', 'guard stopped manually')
    return 0


def print_list():
    processes = iter_processes()
    safe_print('--- 当前进程（共 {} 个）---'.format(len(processes)))
    for pid, exe in sorted(processes, key=lambda x: x[1].lower()):
        safe_print('  {:<8} {}'.format(pid, exe))
    safe_print('')
    safe_print('--- 可见窗口标题 ---')
    for title in iter_window_titles():
        safe_print('  {}'.format(title))
    safe_print('')
    safe_print('把检测软件的 exe 名称（可去掉 .exe）填入 guard_config.ini 的 app_process。')


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(description='DTW 自动选波 - 检测软件伴随守护')
    parser.add_argument('--once', action='store_true', help='只检查一次并打印结果')
    parser.add_argument('--list', action='store_true', help='列出当前进程名与窗口标题')
    parser.add_argument('--dry-run', action='store_true', help='只记录将要执行的动作')
    parser.add_argument('--stop', action='store_true', help='停止守护进程与 watcher')
    parser.add_argument('--no-delay', action='store_true',
                        help='忽略 startup_delay，立即开始检测（装机后手工启动时用）')
    parser.add_argument('--config', default=None, help='指定配置文件路径')
    args = parser.parse_args(argv)

    if args.list:
        print_list()
        return 0

    cfg = load_config(args.config)
    if args.no_delay:
        cfg.startup_delay = 0

    if args.once:
        running, why = is_app_running(cfg)
        safe_print('检测软件: {}'.format(('运行中 -> ' + why) if running else '未运行'))
        safe_print('watcher : {}'.format(
            '运行中 pid={}'.format(watcher_pid(cfg)) if watcher_alive(cfg) else '未运行'))
        return 0

    if args.stop:
        return stop_all(cfg)

    return run_loop(cfg, dry_run=args.dry_run)


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        import traceback
        log('守护进程异常退出: {}'.format(traceback.format_exc().replace('\n', ' | ')))
        sys.exit(1)
