# cmd-patrol

轻量级 CMD 脚本管理工具。统一管理你的 watchdog 脚本，查看实时日志，一键启动/停止/重启。

## 功能

- **注册脚本**：点击"注册脚本"按钮，输入 `.cmd/.bat/.ps1/.sh` 脚本的完整路径
- **服务列表**：左侧显示所有已注册的服务及其状态
- **实时日志**：右侧显示选中服务的实时 stdout/stderr 输出
- **操作按钮**：启动、停止、重启、打开目录、删除

## 快速开始

1. 双击 `start.cmd` 启动（首次会自动创建 venv 并安装依赖）
2. 浏览器自动打开 http://127.0.0.1:5050
3. 点击"注册脚本"，输入你的 watchdog 脚本路径
4. 点击服务名查看详情和日志

## 技术栈

- **后端**: Python + Flask + Flask-SocketIO
- **前端**: HTML + Vanilla JS + TailwindCSS (CDN)
- **持久化**: `backend/services.json`

## 文件结构

```
cmd-patrol/
├── backend/
│   ├── app.py              # Flask 主入口
│   ├── process_manager.py  # 进程管理
│   ├── services.json       # 服务配置持久化
│   └── requirements.txt
├── frontend/
│   └── index.html          # 单页前端
├── start.cmd               # 一键启动
└── README.md
```
