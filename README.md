# ruyi-captcha

独立于 ff-ruyijs 内核的验证码应用层，提供 **Python 包**和 **HTTP API** 两种模式。
首版支持 Cloudflare Turnstile 的 `site_url + sitekey` 求解，包含 action/cData、
代理、超时、重试和交互 checkbox 处理。其他验证码类型可在 `providers/` 扩展，
当前版本不会把尚未实现的类型标记为可用。

## 安装

Python 3.10+。本机开发已配置 `/root/ff-ruyijs` 的兼容内核；其提交和已验证的
Linux runtime SHA256 记录在 [runtime-lock.json](src/ruyi_captcha/runtime-lock.json)。

```sh
cd /root/ruyi-captcha
python3 -m venv .venv
.venv/bin/python -m pip install -e /root/ff-ruyijs
.venv/bin/python -m pip install -e '.[api,test]'
.venv/bin/ruyi-captcha check-runtime
```

应用包本身不捆绑或自动下载浏览器内核。部署到其他机器时，先安装由所记录内核提交
构建的匹配平台 wheel，再安装本包。上游原版 `ruyijs==0.3.5` 不包含必需的
Trusted Types 补丁；启动检查会实际验证能力，不能只按版本号判断兼容性。

## 模式一：接入已有 Python 流程

```python
from ruyi_captcha import solve_turnstile, solve_turnstile_details

token = solve_turnstile(
    site_url="https://capskip.com/captcha-demo/cloudflare-turnstile/",
    sitekey="0x4AAAAAADogn3t3_JKwKkgS",
    timeout=30,
    attempts=2,
)
# 随后把 token 提交给你的业务流程。

details = solve_turnstile_details(
    surl="https://capskip.com/captcha-demo/cloudflare-turnstile/",
    sitekey="0x4AAAAAADogn3t3_JKwKkgS",
    timeout=30,
    attempts=1,
)
print(details.elapsed, details.attempts)
```

返回的 `token` 是字符串，详情包含 `token`、`elapsed`、`attempts`。
`site_url` 可写为别名 `surl`，也支持旧风格的两个位置参数。
`action`、`cdata`、`proxy`、`profile` 和 `shell` 可作为可选关键字参数。
默认使用 Linux Firefox 159 profile；其他宿主应传入匹配的环境字典或 `profile=None`。
可配合 `ruyijs.use_engine(engine)` 使用已有共享 Engine。

`timeout` 是**每次尝试**的预算，`attempts` 是最大尝试次数。取得 token 后应立即提交；
取得 token 与业务后台接受是两种结果，本库不会在没有业务端回执时声称验证成功。

命令行同样可用：

```sh
.venv/bin/ruyi-captcha solve \
  'https://capskip.com/captcha-demo/cloudflare-turnstile/' \
  '0x4AAAAAADogn3t3_JKwKkgS' --timeout 30 --attempts 1
```

## 模式二：HTTP API

本机调用：

```sh
.venv/bin/ruyi-captcha serve --host 127.0.0.1 --port 8000 --concurrency 2
```

其他语言或服务器调用时，设置自己的长随机 API key 并监听远程接口：

```sh
export RUYI_CAPTCHA_API_KEY='replace-with-your-long-random-key'
.venv/bin/ruyi-captcha serve --host 0.0.0.0 --port 8000 --concurrency 2
```

交互文档位于 `/docs`，OpenAPI 位于 `/openapi.json`。开启 API key 后，任务接口使用
`Authorization: Bearer ...`。远程生产部署可由反向代理终止 HTTPS。

同步求解，默认最多等待 30 秒：

```sh
curl -X POST 'http://127.0.0.1:8000/v1/solve?wait_timeout=30' \
  -H "Authorization: Bearer $RUYI_CAPTCHA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"type":"turnstile","site_url":"https://capskip.com/captcha-demo/cloudflare-turnstile/","sitekey":"0x4AAAAAADogn3t3_JKwKkgS","timeout":30,"attempts":1}'
```

完成时返回 HTTP 200 和任务对象，token 位于 `result.token`。
超过等待时间仍在执行时返回 HTTP 202；使用返回的 `id` 继续查询。
任务失败时检查 `status=failed` 和 `error.code`，不能仅按 HTTP 200 判断验证码成功。

异步提交和轮询：

```sh
curl -X POST http://127.0.0.1:8000/v1/tasks \
  -H "Authorization: Bearer $RUYI_CAPTCHA_API_KEY" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: your-unique-request-id' \
  -d '{"type":"turnstile","surl":"https://capskip.com/captcha-demo/cloudflare-turnstile/","sitekey":"0x4AAAAAADogn3t3_JKwKkgS","timeout":30,"attempts":1}'

curl http://127.0.0.1:8000/v1/tasks/TASK_ID \
  -H "Authorization: Bearer $RUYI_CAPTCHA_API_KEY"

curl -X DELETE http://127.0.0.1:8000/v1/tasks/TASK_ID \
  -H "Authorization: Bearer $RUYI_CAPTCHA_API_KEY"
```

状态为 `queued`、`running`、`cancelling`、`cancelled`、`succeeded`、`failed`。
相同幂等键与相同参数返回同一任务；参数不同返回 409。容量用尽返回 429。
运行中的取消采用协作方式，当前 native RPC 返回或超时后生效。

Python 也可以只调用远程 API，无需在调用方安装内核：

```python
from ruyi_captcha import ApiClient

client = ApiClient("http://your-server:8000", api_key="your-api-key")
token = client.solve_turnstile(
    "https://capskip.com/captcha-demo/cloudflare-turnstile/",
    "0x4AAAAAADogn3t3_JKwKkgS",
    timeout=30, attempts=1, wait_timeout=90,
)
```

## 服务运行方式

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `RUYI_CAPTCHA_API_KEY` | 未设置 | Bearer API key；监听远程接口时必需 |
| `RUYI_CAPTCHA_CONCURRENCY` | 2 | 同时执行的任务数 |
| `RUYI_CAPTCHA_QUEUE_SIZE` | 16 | 等待任务上限 |
| `RUYI_CAPTCHA_QUEUE_TIMEOUT` | 30 | 最长排队秒数 |
| `RUYI_CAPTCHA_RESULT_TTL` | 120 | 完成后结果在内存中保留的秒数 |

每次尝试创建独立 Context；一次 native 崩溃不会结束其他任务的进程。
此版本的队列和结果存储位于**单个 API 进程的内存**，重启后任务和结果消失。
CLI 固定一个 HTTP worker；并发由上述任务池控制。多实例部署需要后续接入共享任务存储。
任务记录总数上限为运行/排队容量加 256，容量用尽时旧完成记录可提前淘汰。
`result_expires_at` 是服务端最长保留结果的截止时间，不是验证码厂商对 token 有效性的保证。

## 开发与测试

```sh
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m build
```

离线测试覆盖队列、取消、幂等、期限、SDK、HTTP 鉴权和错误返回；native 用例在内核
缺失时会跳过。实时 CapSkip 验收脚本放在 `tests/live_capskip.py`，结果只记录长度与
后台验收状态。架构与迁移说明见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

提交使用 Starktomy 的 GitHub noreply 身份，Codex 作为共同作者。
本仓库采用 AGPL-3.0-only；内核依赖保留自身许可。
