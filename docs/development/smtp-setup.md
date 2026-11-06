# 实际邮箱接入

SMTP 发送器已支持 STARTTLS 和 SSL，现有本地邮件、注册与邀请链路已通过真实依赖联调。实际邮箱服务商与授权码待项目负责人配置。

## 配置

将以下配置手动合并到被 Git 忽略的根目录 `.env`。API、邮件 dispatcher 和 Linux Worker 应使用相同配置。

```dotenv
AUTH_ENABLED=true
MAIL_ENABLED=true
AUTH_SECRET=<已有的私有认证密钥>
MAIL_PAYLOAD_KEY=<独立Fernet密钥>
MAIL_MODE=<starttls或ssl，按服务商要求选择>
MAIL_HOST=<SMTP服务器>
MAIL_PORT=<SMTP端口>
MAIL_USERNAME=<SMTP登录账号>
MAIL_PASSWORD=<SMTP授权码或服务商提供的密码>
MAIL_FROM=<允许使用的发件邮箱>
INVITATION_PUBLIC_ORIGIN=http://127.0.0.1:5173
```

数据库、Redis 和 Broker 连接也须配置完成。邀请地址应与访问前端的地址一致，并包含在 `AUTH_ALLOWED_ORIGINS` 中。已有认证与邮件加密密钥可复用，保持各进程一致。

`MAIL_MODE=local` 用于本地无认证收件器。实际服务使用其要求的 TLS 模式，发送器验证系统信任的服务端证书。授权码只填写在本地配置中。

## 运行

先按[本地开发](local-development.md)启动 API、dispatcher 和 Worker。站内通知另由 notifications 消费者处理。

配置检查在应用启动时进行，不会自动发送测试邮件。发起验证码申请后，页面显示受理状态；邮件被 SMTP 接受后仍可能由服务商延迟处理或进入垃圾箱。收到验证码并完成注册才算完成实际邮箱链路验证。

当前只验证了本地回环收件器。实际 SMTP 登录、TLS 和外部收件箱送达须在选定服务商并填写配置后确认。
