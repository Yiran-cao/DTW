离线依赖包目录 (offline_packages)
===================================

此目录用于存放 DTW 选波脚本的 Python 依赖离线安装包。
目标机器（产线环境）通常无网络，install.bat 会优先从此目录离线安装依赖。

## 准备离线包（在有网络的机器上执行）

```batch
:: 1. 打开命令行，进入本脚本仓库根目录
cd /d D:\dtw

:: 2. 下载依赖到 offline_packages 目录
pip download -r requirements.txt -d offline_packages

:: 3. 确认 numpy 的 .whl 文件已下载
dir offline_packages
```

## 支持的平台

目标机器为 32 位 Windows，请确保下载的 .whl 包支持目标平台：
- 如果准备包的机器是 64 位，加 --platform 参数指定 32 位：
  ```
  pip download -r requirements.txt -d offline_packages --platform win32 --python-version 3.9
  ```
- 如果有多种平台需求，可多次运行，pip 不会覆盖已有文件

## 目录内容示例

```
offline_packages/
├── numpy-1.24.4-cp39-cp39-win32.whl
└── ... (其他传递依赖)
```

## 注意事项

- 此目录连同 U 盘一起带到目标机器，install.bat 会自动从此目录安装
- 如果没有此目录或目录为空，install.bat 会尝试联网安装（通常会失败）
- 每次更新 requirements.txt 后，请重新运行 pip download 更新此目录
