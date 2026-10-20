# 本地运行配置工作区

本阶段以本地运行和Docker Compose为目标，不租服务器、不引入云对象存储。计划编排PostgreSQL、Redis、RabbitMQ、API及后台Worker，文件保存在本地私有目录。

缓存、消息队列和业务数据承担不同职责。正式配置将在版本与接口设计确定后实现，当前没有可执行的Compose文件。
