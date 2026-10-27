# DTW 选波脚本 —— 自动化部署实现需求说明

> 本文档用于交给 Claude Code，结合现有 DTW 自动选波脚本，实现"U盘一键装机 + 开机自动运行"的完整方案。请先通读现有脚本目录结构和依赖，再按下述需求编码。

## 一、背景与目标

现有 DTW 自动选波功能脚本已开发完成，当前装机方式是通过 U 盘把脚本文件夹拷贝到目标机器。目标是新增两个自动化脚本，替代"手动进 PowerShell → 激活环境 → 运行"的操作：

1. **`install.bat`**（装机时手动跑一次）：创建虚拟环境、离线安装依赖、注册开机自动运行任务
2. **`run.bat`**（由系统自动触发，日常不需人工干预）：激活环境、运行 DTW 脚本

两者职责严格分离：`install.bat` 是一次性初始化操作，`run.bat` 是每次开机都会执行的常驻操作，**不要把创建环境/装依赖的逻辑放进开机自动运行的部分**，避免每次开机重复建环境、重复装依赖、甚至因目录已存在而报错。

## 二、目标环境约束

- 操作系统：Windows，目标机器为 **32 位**
- 现有脚本用 Python 实现，需与已有 C++ 主程序集成运行
- 目标机器**可能没有网络连接**（产线机器常见情况），因此依赖安装必须支持**离线安装**
- 部署方式：U 盘拷贝，插上 U 盘后运行一次安装脚本即可完成全部配置，不依赖联网

## 三、需要实现的文件结构

请在现有脚本仓库根目录下新增以下内容（具体路径可根据实际仓库结构调整，但请保持职责划分不变）：

```
项目根目录/
├── dtw_script/                  # 现有的 DTW 选波脚本（已存在，不要改动内部逻辑）
├── requirements.txt             # 现有或需要生成的依赖清单
├── offline_packages/            # 新增：离线依赖包缓存目录（pip download 产物）
├── install.bat                  # 新增：一次性安装脚本
├── run.bat                      # 新增：日常自动运行脚本
├── task_template.xml            # 新增：任务计划程序的任务模板（已废弃删除，见第九节）
└── logs/                        # 新增：运行日志输出目录
```

## 四、`install.bat` 的具体行为要求

按顺序执行以下逻辑，每一步都要有清晰的中文提示信息，方便非技术人员在装机时看懂进度和报错：

1. **检查虚拟环境是否已存在**
   - 如果目标虚拟环境目录已存在，跳过创建步骤，提示"环境已存在，跳过安装"
   - 如果不存在，用 `python -m venv <路径>` 创建

2. **离线安装依赖**
   - 优先从本地 `offline_packages/` 目录安装：`pip install -r requirements.txt --no-index --find-links=offline_packages`
   - 如果本地离线包缺失导致安装失败，给出清晰提示（比如"离线依赖包缺失，请检查 U 盘中 offline_packages 目录是否完整"），不要静默失败
   - 不要在这一步尝试联网安装作为默认行为（目标机器大概率无网），联网安装可作为可选的备用分支，但需要明确的提示告知当前正在尝试联网

3. **注册开机自动运行任务**
   - 使用 `schtasks /create /xml "task_template.xml" /tn "DTW_AutoRun"` 导入任务
   - 导入前检查同名任务是否已存在，存在则询问是否覆盖（或直接覆盖并提示），避免重复导入报错
   - 任务模板中路径需要是**部署到目标机器后的实际绝对路径**，请在 `install.bat` 里动态生成 `task_template.xml`（用当前脚本所在目录变量替换模板中的占位路径），不要要求用户手动改 XML

4. **权限处理**
   - `schtasks /create` 和某些系统目录操作可能需要管理员权限，请在 `install.bat` 开头加入自检 + 自动提权逻辑（检测当前是否管理员权限运行，不是的话用 `powershell Start-Process ... -Verb RunAs` 重新以管理员身份拉起自己），避免用户还要手动右键"以管理员身份运行"

5. **执行完成后给出总结**
   - 打印本次安装结果摘要：环境路径、依赖安装状态、任务注册状态，方便装机人员核对

## 五、`run.bat` 的具体行为要求

这个脚本只做"运行"，不做任何安装/检查依赖的操作，保持尽量简单和快：

1. 激活虚拟环境
2. 运行 DTW 脚本主入口
3. 把标准输出和报错都重定向到 `logs/` 目录下按日期命名的日志文件（例如 `logs/2026-08-06.log`），方便事后排查问题，而不是只显示在一闪而过的黑框里
4. 脚本运行失败时，日志里要能看出是"环境激活失败"还是"脚本本身报错"，两者报错信息不要混在一起

## 六、`task_template.xml` 要求

> **注意（2026-10 变更）**：本节的"开机触发器"模板已废弃并从仓库删除，
> 现行方案使用 `task_guard_template.xml`（登录触发器 + guard.py），详见第九、十节。
> 需要查看旧模板内容时：`git show v1.0-legacy:task_template.xml`。

- 触发器：开机启动时执行（如需求变化为"用户登录时"也需支持，请做成 install.bat 里可选参数或注释说明如何切换）
- 操作：指向 `run.bat`（不是直接指向 python.exe，保持入口统一）
- 运行权限：使用最高权限运行
- 请确认路径不要写死成开发机上的绝对路径，必须由 `install.bat` 在目标机器上动态填入实际路径

## 七、验收标准（请对照逐条自测）

- [ ] 全新机器插入 U 盘，双击 `install.bat`，全程无需再打开 PowerShell 或手动改任何配置文件
- [ ] 在已经安装过一次的机器上重复运行 `install.bat`，不会重复创建环境/重复报错，能正常跳过或安全覆盖
- [ ] 断网环境下运行 `install.bat`，依赖能从 `offline_packages/` 正常装上
- [ ] 安装完成后重启机器，DTW 脚本能自动运行，且日志文件里能看到本次运行记录
- [ ] 人为制造一次运行失败（比如临时改错依赖路径），确认日志里能定位到具体是哪一步失败
- [ ] `install.bat` 双击运行时能自动弹出管理员权限确认，不需要用户提前手动选择"以管理员身份运行"

## 八、暂不需要实现的部分（明确排除范围）

- 不需要做成图形界面（GUI），继续用 bat 脚本即可
- 不需要打包成 Windows 服务（NSSM），当前采用任务计划程序方案
- 不需要处理多用户/多账户并发运行的场景

## 九、追加变更（2026-10）：改为"跟随检测软件启停"

### 9.1 背景

原方案用任务计划 `DTW_AutoRun`（**开机触发器 BootTrigger**，最高权限）在开机时直接执行 `run.bat`，
开机阶段就完成虚拟环境激活、numpy 启动和 `d:\data test` 目录轮询。目标机器为 Win7 + 老机械硬盘，
开机时磁盘/内存本就紧张，再叠加这一进程导致**卡顿甚至蓝屏**。

### 9.2 新运行模型

```
登录（LogonTrigger） → 任务 DTW_Guard → guard.py（pythonw，无窗口，常驻）
                                          │ 每 3 秒检查一次"检测软件"进程
                                          ├─ 软件已启动 → run.bat → launcher.py → watcher.py（开始监测）
                                          └─ 软件已退出 → 写 watcher.stop → watcher 正常退出（停止监测）
```

要点：

- 开机阶段不再执行任何选波相关程序；登录后只有 `guard.py` 这一个轻量进程
- `guard.py` 空闲时只做一次进程快照（**不导入 numpy、不扫描磁盘**），CPU/磁盘占用可忽略
- 关闭软件后默认等 5 秒再停止，避免软件自动重启造成反复启停
- watcher 若异常退出而软件仍在运行，guard 会自动重新拉起
- 启动失败会锁定 120 秒后再重试，避免反复失败刷日志
- 登录后默认延迟 20 秒才开始检测（`startup_delay`），避开开机磁盘高峰

### 9.3 文件变更

| 文件 | 变更说明 |
| --- | --- |
| `guard.py` | 新增：检测软件伴随守护进程（仅标准库，兼容 Python 3.6 / Win7 32 位） |
| `guard_config.ini` | 新增：守护配置（目标进程名、轮询间隔、启停延迟等） |
| `task_guard_template.xml` | 新增：登录触发器任务模板，由 `install.bat` 填入实际绝对路径后导入 |
| `run_guard.bat` | 新增：手工启动 / 停止 / 查看状态 / 自检守护进程 |
| `install.bat` | 删除旧 `DTW_AutoRun`（开机任务），改为注册 `DTW_Guard`（登录任务） |
| `watcher.py` | `watch_folder` 主循环新增停止信号检查（`watcher.stop`），实现"关闭即停止" |
| `launcher.py` | 接收 `DTW_WATCH_DIR` 环境变量并转成 `--dir`，使 `guard_config.ini` 的 `watch_dir` 真正生效；`--stop` 注明会与守护进程冲突 |
| `start_watcher.bat` | **已删除**（原手工启动脚本，写死 `d:\dtw` 且绕过 guard）；需要时从 `v1.0-legacy` 取回 |
| `task_template.xml` | **已删除**（原开机触发器模板，无任何脚本引用）；需要时从 `v1.0-legacy` 取回 |

### 9.4 关键配置（`guard_config.ini`）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `app_process` | `hello软件操作版` | 检测软件进程名，多个用逗号分隔，`.exe` 可省略 |
| `app_title` | `hello软件操作版` | 窗口标题关键字（进程名对不上时的兜底匹配） |
| `poll_interval` | `3` | 检查间隔（秒） |
| `start_consecutive` | `2` | 连续命中几次才启动监测（避免闪一下就拉起） |
| `stop_delay` | `5` | 软件关闭后等待多少秒停止监测 |
| `startup_delay` | `20` | 登录后延迟多少秒开始检测 |
| `retry_cooldown` | `120` | 启动失败后锁定多少秒再重试 |

改完配置后执行 `run_guard.bat stop`，再执行 `run_guard.bat` 生效（或重新登录）。

> 若出现误判（例如资源管理器窗口标题里含同样字样），把 `app_title` 留空，即可只按进程名判断。

### 9.5 现场排查命令

| 命令 | 用途 |
| --- | --- |
| `run_guard.bat list` | 列出当前所有进程名和窗口标题（**先打开检测软件再运行**，用来确认 `app_process` 该填什么） |
| `run_guard.bat test` | 只检查一次：检测软件是否被识别到、watcher 是否在运行 |
| `run_guard.bat status` | 查看计划任务状态、`guard.pid` / `watcher.pid` 和 `d:\data\watcher_status.txt` |
| `run_guard.bat stop` | 停止守护进程和 watcher（临时停用选波，不卸载） |
| `run_guard.bat` | 立即在后台启动守护进程（不必等下次登录） |
| `run_guard.bat console` | 前台运行守护进程，便于现场观察日志输出 |

日志位置：

- `logs\guard_YYYYMMDD.log`：守护进程的启停记录（只在状态变化时写一行）
- `logs\YYYY-MM-DD.log`：`run.bat` 的运行日志（每次启动 watcher 一份）
- `logs\guard_launch_YYYYMMDD.log`：guard 拉起 `run.bat` 时的控制台输出
- `d:\data\watcher_status.txt`：`status=ready/stopped/error` + 时间 + 原因

### 9.6 追加验收标准

- [ ] 重装后 `taskschd.msc` 中只剩 `DTW_Guard`（登录触发），`DTW_AutoRun` 已被删除
- [ ] 重启机器：开机阶段任务管理器里看不到 python 进程；登录约 20 秒后 `guard.pid` 出现、`watcher.pid` 不出现
- [ ] 打开检测软件：约 3~6 秒内 `watcher.pid` 出现，`d:\data\watcher_status.txt` 变为 `status=ready`，`logs\YYYY-MM-DD.log` 有新记录
- [ ] 关闭检测软件：约 5~10 秒内 watcher 退出、`watcher.pid` 消失，状态变为 `status=stopped`，日志中有"监测已停止"
- [ ] 再次打开检测软件：监测自动恢复，无需任何人工操作
- [ ] 检测软件开着时手动结束 watcher 进程：guard 会自动重新拉起
- [ ] `run_guard.bat stop` 后守护进程与 watcher 均停止，且不会自行恢复
- [ ] 首次部署若识别不到检测软件，用 `run_guard.bat list` 找到真实 exe 名并填入 `guard_config.ini`

## 十、装机与日常操作手册

### 10.1 首次装机（约 5 分钟，只需一次）

| 步骤 | 操作 | 预期结果 |
| --- | --- | --- |
| 1 | 把整个 `dtw` 目录拷到目标机 `D:\dtw`（U 盘拷贝） | 目录里有 `install.bat`、`guard.py`、`offline_packages`、`deji` 或需新建 |
| 2 | 如首次安装，先手动运行 `offline_packages\VC_redist.x86.exe`（32 位 VC++ 运行库） | 提示安装完成；否则 numpy 会因为缺 DLL 导入失败 |
| 3 | 双击 `install.bat`（会弹管理员确认，点"是"） | 依次打印 Step 1/4 ~ 4/4 |
| 4 | 看最后的 ***** DTW Setup Complete ***** 摘要 | 关键三行：`deji` 环境路径、`numpy verified`、`Task DTW_Guard registered` |
| 5 | 打开检测软件，等 5~10 秒后查看 `D:\data\watcher_status.txt` | 内容为 `status=ready` |
| 6 | 关闭检测软件，等 10 秒 | `status=stopped`，`D:\dtw\watcher.pid` 消失 |
| 7 | 重启机器，登录后不做任何操作 | 只出现一个 `pythonw.exe`（守护）；打开检测软件才开始选波 |

安装脚本会做的事（重复运行也安全）：

1. 检查/创建 `deji` 虚拟环境（已存在则跳过）
2. 从 `offline_packages` 离线安装 numpy，并验证 `import numpy`
3. 删除旧的开机任务 `DTW_AutoRun`，导入登录任务 `DTW_Guard`（XML 导入失败会自动降级为命令行注册）
4. 创建 `logs` 目录

### 10.2 日常操作（操作员视角）

日常**不需要任何操作**：

- 上班开机 → 登录 → 大约 20 秒后守护进程就绪，但**此时不选波**
- 打开检测软件 → 约 3~6 秒后自动开始监测，每保存一个 `LP/RP/LV/RV.txt` 就自动出结果
- 关闭检测软件 → 约 5~10 秒后自动停止监测
- 结果始终写到 `D:\data test\selectwave.txt`（`0` 表示本次没有合格波形）

唯一需要说明的是：**不要用任务管理器结束 `pythonw.exe`**，结束了就不会自动重启（要等下次登录）。

### 10.3 现场常用命令

在 `D:\dtw` 目录打开 PowerShell 或 cmd，执行：

| 目的 | 命令 |
| --- | --- |
| 看现在是否在监测 | `run_guard.bat status` |
| 只检查一次识别结果 | `run_guard.bat test` |
| 核对检测软件进程名 | 先打开检测软件，再 `run_guard.bat list` |
| 临时停用选波 | `run_guard.bat stop` |
| 临时停用后恢复 | `run_guard.bat`（后台）或 `run_guard.bat console`（前台看日志） |
| 前台调试 | `run_guard.bat console`，Ctrl+C 退出 |

PowerShell 里注意两点：bat 要写成 `.\run_guard.bat stop`；想避免窗口一闪而过可以直接调用
`.\deji\Scripts\python.exe .\guard.py --stop`。

### 10.4 日常故障排查

按"现象 → 先看哪里 → 处理"的顺序：

| 现象 | 先确认 | 处理 |
| --- | --- | --- |
| 打开软件后一直不出结果 | `run_guard.bat status`；`D:\data\watcher_status.txt` 是否 `ready` | 若 `stopped`，看 `logs\guard_*.log` 最后几行；多半是进程名没匹配上，用 `run_guard.bat list` 核对后改 `app_process` |
| 状态 `error`，`detail=watcher start timeout` | `logs\guard_launch_*.log` 和 `logs\watcher_error.log` | 通常是 `deji` 环境坏了或 numpy 导入失败，重跑 `install.bat` |
| 状态 `error`，`detail=numpy import failed` | 直接在 cmd 跑 `deji\Scripts\python.exe -c "import numpy"` | 缺 VC++ 运行库，运行 `offline_packages\VC_redist.x86.exe` 后重跑 `install.bat` |
| 关掉软件后状态还是 `ready` | `stop_delay` 是否被改大；是否有同名的第二个进程 | 用 `run_guard.bat list` 检查是否还有残留进程；必要时 `run_guard.bat stop` |
| 结果经常是 `0` | `logs\watcher_debug_*.log` 里的"最长连续相同点"数值 | 贴近 15~16 说明还在读写入中间态；远超 40 说明传感器真的异常，需查采集端 |
| 选出的波形明显不对 | `logs\YYYY-MM-DD.log` 里的过滤记录 | 按 P/V 与 L/R 规则确认文件是否写对侧别、类型 |
| 重启后完全没反应 | `taskschd.msc` 里 `DTW_Guard` 是否存在且为"登录时"触发 | 不存在则重跑 `install.bat`；存在但没跑，检查该任务"上次运行结果" |
| 登录任务存在但状态是 `0x41301`（运行中）却没监测 | `D:\dtw\guard.pid` 是否存在 | 数字是几就代表守护在跑；再用 `run_guard.bat test` 看识别结果 |
| 机器仍然卡顿 | 任务管理器看是否有多个 `python.exe`/`pythonw.exe` | 多余的手工启动进程用 `run_guard.bat stop` 清掉，再 `run_guard.bat` 只起一个 |
| 想彻底卸载 | — | `run_guard.bat stop` → `schtasks /delete /tn DTW_Guard /f` → 删除 `D:\dtw` 目录 |

日志文件一览：

| 文件 | 内容 |
| --- | --- |
| `logs\guard_YYYYMMDD.log` | 守护进程状态变化（启动/停止/重启 watcher、错误原因） |
| `logs\guard_launch_YYYYMMDD.log` | 守护拉起 `run.bat` 的控制台输出 |
| `logs\YYYY-MM-DD.log` | `run.bat` 激活环境 + 启动 watcher 的过程 |
| `logs\watcher_error.log` | `launcher.py` 捕获的 watcher 启动/崩溃输出 |
| `logs\watcher_debug_YYYYMMDD.log` | 每个文件的解析结果、过滤原因、输出 0 的原因 |
| `D:\data\watcher_status.txt` | 当前状态 `ready` / `stopped` / `error` + 时间 + 原因 |


