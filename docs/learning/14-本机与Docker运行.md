# 14 本机与 Docker 运行

## 业务目标与选择原因

学习、单人开发直接运行 `python run.py` 即可，Docker 不是前置条件。Docker 适合让团队在相近的依赖环境中启动演示服务。简单说，镜像像“已装好软件的盒子”；专业地说，镜像是容器运行所需的分层文件系统及启动元数据。容器不自动提供企业级账号、备份或公网安全。

## 核心技术、模块关系与数据流

`Dockerfile` 安装 `requirements.txt` 的依赖并复制 `app/`、`static/`、`templates/`；用非 root 用户启动单个 Uvicorn worker。`compose.yaml` 把本机 `127.0.0.1:8000` 映射到容器端口，并将 `data/` 放入命名卷。请求仍走原有 FastAPI、SQLite、导出器和模型接口；容器只改变运行环境，不改变报价规则。**必须掌握：** 环境变量、端口映射、卷。**可暂缓：** 多副本编排、Kubernetes、镜像仓库。

## 核心代码逐段解释

1. `Dockerfile` 的 `FROM python:3.13-slim` 指定基础环境；安装中文字体供文档处理；`COPY requirements.txt` 后安装依赖，以便后续代码变化复用依赖层。
2. `COPY --chown=quote:quote` 让普通用户读取代码；`USER quote` 避免默认 root 运行；只开一个 worker，因为当前 SQLite 和本地文件适合单实例演示。
3. `compose.yaml` 的 `volumes` 保存 SQLite、上传文件和导出结果，容器重建后数据仍在。`healthcheck` 请求服务根路径。环境变量向服务传模型与审批配置，密钥本身不写进 Git。
4. `.dockerignore` 排除 `.venv`、`data`、`.env` 和 Git 资料，减少构建上下文并保护本地数据。

## 必须掌握的 Python 基础

`app/config.py` 通过 `Path` 推导项目目录和数据目录；`os.getenv()` 从进程环境读取配置；`app/main.py:lifespan()` 在启动时创建目录并初始化 SQLite。理解“进程环境”与磁盘上的 `.env` 文件不同：本地运行不会自动读取 `.env`；Compose 会从当前目录读取它用于变量替换。

## 实际运行与测试

本机：创建虚拟环境、安装 `requirements.txt`、设置需要的环境变量、`python run.py`。容器：安装 Docker Desktop 后，在项目目录执行 `docker compose up --build`，访问 `http://127.0.0.1:8000/static/index.html`；`docker compose down` 停止服务，卷保留。自动测试使用 `.venv/bin/python -m pytest -q`。本次环境没有 Docker CLI，尚未实测镜像构建和容器启动；首次运行需核对字体、卷权限和健康检查。

## 常见错误与排查

- 端口被占：检查 8000 端口上的旧服务并停止，或修改 Compose 的本机端口。
- AI 问答 503：容器环境没有 `OPENAI_API_KEY` 或 `OPENAI_MODEL`，而非 Docker 故障。
- 启动时报数据目录权限：检查命名卷挂载权限与容器用户；不要删除业务卷来掩盖问题。
- 改代码后页面仍旧：重新构建镜像并刷新浏览器。

## 面试可能问

**为什么现在不强制 Docker？** 功能开发和学习在本机更直接，容器只是部署一致性的工具。**为什么数据用卷？** 容器可重建，数据库和上传文件不能随容器删除。**为何单 worker？** 当前本地 SQLite 与文件导出采用单实例设计；多实例需重构存储、并发和鉴权。

## 小练习

1. 指出 `compose.yaml` 中哪些是本机端口、哪些是容器端口；解释为什么只绑定 `127.0.0.1`。
2. 在不写入真实密钥的前提下，用本机环境变量启动服务，访问 `/`；然后找到 `lifespan()` 为什么先初始化数据库。
