# Docker Desktop 本机启动故障记录

当前状态：尚未恢复，不能据此宣称容器、数据库迁移或邮件队列联调通过。

## 已检查与处理

- Docker Desktop 4.74.0 在启动 Inference Manager 时无法移除 `%LOCALAPPDATA%\Docker\run\dockerInference`。
- 使用官方 `docker desktop stop --force` 停止故障进程，确认 Desktop 和 backend 进程退出后，将仅含该临时端点的 `run` 目录改名为同级 `run.quarantine-<随机标识>`。未删除原目录。
- 再启动后通过 Inference 初始化，但在 `%LOCALAPPDATA%\docker-secrets-engine\engine.sock` 遇到相同问题。确认该目录仅含临时套接字后，同样停止进程、改名保留，再重新启动。
- 最新启动已进入 WSL 数据盘阶段，报错位置为 `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx`，Windows 提示路径中的装入点与设备对象不匹配。
- `Docker\wsl` 目录带重解析点属性，但查询其重解析信息也报装入点错误；具体重解析类型和成因尚未确认。
- 数据盘文件可被目录枚举，大小为 15,893,266,432 字节；尝试只读打开也失败，因此尚未完成备份，不能宣称数据盘内容完整或损坏。

## 数据保护与下一步

未执行 Factory Reset、prune、卷删除、WSL 注销、数据盘移动/修复或重解析点删除；未修改 Docker 设置、项目密钥或真实 `.env`。隔离目录保留用于排查，不应当作持久数据备份。

建议保存其他应用工作后执行 Windows“重启”，再验证 Docker。此步骤尚未执行，也不能保证修复。若仍报数据盘路径错误，先确认路径映射及可读取性、取得可靠备份，再评估后续处理；不要直接重建或覆盖现有 VHDX。

引擎恢复后，先执行 `docker info` 和只读容器/卷清单，核实已有资源，再继续本项目 Compose 与迁移联调。不要使用 `down -v`。

参考：[同类运行时端点故障报告](https://github.com/docker/desktop-feedback/issues/625)、[Docker 官方备份与恢复说明](https://docs.docker.com/desktop/settings-and-maintenance/backup-and-restore/)。这些资料不能替代本机数据盘与路径检查。
