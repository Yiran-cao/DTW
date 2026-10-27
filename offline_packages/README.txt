离线依赖包目录 (offline_packages)
===================================

此目录用于存放 DTW 选波脚本的 Python 依赖离线安装包。
目标机器（产线环境）通常无网络，install.bat 会优先从此目录离线安装依赖。

## 准备离线包（在有网络的机器上执行）

```batch
:: 1. 打开命令行，进入本脚本仓库根目录
cd /d D:\dtw

:: 2. 下载依赖到 offline_packages 目录
::    目标机是 32 位 Windows + Python 3.6，必须显式指定平台和版本
pip download -r requirements.txt -d offline_packages --platform win32 --python-version 36 --only-binary=:all:

:: 3. 确认 numpy 的 .whl 文件已下载（文件名应包含 cp36 和 win32）
dir offline_packages
```

## 支持的平台

目标机器为 32 位 Windows + Python 3.6，因此 .whl 必须是 cp36 + win32 版本，
例如本目录中的 numpy-1.19.5-cp36-cp36m-win32.whl。

- 如果不加 --platform win32，在 64 位机器上会下载成 win_amd64 包，装到目标机会失败
- 如果有多种平台需求，可多次运行，pip 不会覆盖已有文件

## 目录内容示例

```
offline_packages/
├── numpy-1.19.5-cp36-cp36m-win32.whl
├── VC_redist.x86.exe          (VC++ 运行库，numpy 依赖它)
└── ... (其他传递依赖)
```

## 注意事项

- 此目录连同 U 盘一起带到目标机器，install.bat 会自动从此目录安装
- 如果没有此目录或目录为空，install.bat 会提示并尝试联网安装（通常失败）
- numpy 导入失败大多是缺少 VC++ 运行库：先手动运行 VC_redist.x86.exe，再跑 install.bat
- 每次更新 requirements.txt 后，请重新运行 pip download 更新此目录