# 后端工作区

计划使用FastAPI，以模块化单体组织账号、剧组、任务、剧本与分镜、档期、资源、计划、通告单和通知等业务。

Diane在基础业务完成后使用LangGraph和LangChain接入，复用业务服务、权限和事务校验。异步任务使用Celery及RabbitMQ；正式任务状态保存在PostgreSQL。

当前仅建立工作区，尚未实现业务代码。
