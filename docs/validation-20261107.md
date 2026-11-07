# 2026-11-07 首版验证

验证环境为 Linux x86_64、Python 3.14.4。内核源代码固定为 ff-ruyijs
`b9877c412c779bb783da1db523806b14732a881d`，基于上游 0.3.5。
运行时 SHA256 为
`66d41abd93a1cddfd4e4d1bed62321cef9058030a4cf26a0716d027804443bca`。

40 项回归全部通过，无跳过，覆盖：

- SDK 参数、action/cData、超时重试、过期 token 和 checkbox 刷新。
- 取消前置检查、运行中取消和 Context 清理。
- 在已有 Engine 中调用，包括冷启动内核能力探针。
- HTTP 鉴权、同步/异步任务、输入错误、幂等和容量限制。
- 并发上限、排队期限、结果过期、存储上限和服务关闭。
- 远程客户端的错误分类和等待期限。

真实 CapSkip 验收使用直连、Linux profile、真实 sitekey，每次新建 Context：

| 调用路径 | 取得 token 耗时 | Token 长度 | 原站后台 |
| --- | --- | --- | --- |
| 本地 Python SDK | 3.970 秒 | 624 | 接受 |
| 真实 HTTP 同步接口 | 4.246 秒 | 624 | 接受 |
| 真实 HTTP 异步接口及 ApiClient | 4.008 秒 | 624 | 接受 |
| 从 wheel 安装后，在源码目录外运行 SDK | 4.046 秒 | 624 | 接受 |

上述时间不包含后台提交。四次均为非 dummy token，后台 HTTP 200，
verified=true、server=true、success=true、hostname=capskip.com。
报告只保存长度和验收状态，不保存响应 token、Cookie 值或验证请求正文。
这是一次小样本功能验收，不代表所有站点的成功率或固定延迟。

发行构建生成 `ruyi_captcha-0.1.0-py3-none-any.whl` 和源码包。
wheel 包含 Linux profile 和内核锁定信息；在 `/tmp` 中确认导入来自
site-packages，并通过内核能力探针。独立的无内核、无 FastAPI 环境也能导入
ApiClient；在该环境调用本地求解会给出明确的 runtime_unavailable 错误。

当前 API 为单实例内存队列，服务重启后不保留任务和结果。CI 在缺少 native runtime
时会跳过 native 用例；本次本机验证实际运行了全部 40 项。

复现命令：

```sh
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/ruyi-captcha check-runtime
.venv/bin/python -m build

# 先启动 API，再在另一个进程进行三种调用路径的验收。
.venv/bin/ruyi-captcha serve --host 127.0.0.1 --port 8000
.venv/bin/python -B tests/live_capskip.py --api-url http://127.0.0.1:8000
```

启用 API key 时，两端使用同一个 `RUYI_CAPTCHA_API_KEY`。
原始无 token 值的证据在本地忽略目录 `artifacts/` 中，包括
`live-capskip.json`、`wheel-sdk-live.json` 及 HTTP 服务日志。
