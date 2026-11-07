# ruyi-captcha

独立于 ff-ruyijs 内核的验证码应用层，提供 **Python 包**和 **HTTP API** 两种模式。
首版支持 Cloudflare Turnstile 的 `site_url + sitekey` 求解，包含 action/cData、
代理、超时、重试和交互 checkbox 处理。其他验证码类型可在 `providers/` 扩展，
当前版本不会把尚未实现的类型标记为可用。

## 安装

GitHub 发版流水线会同时提供两种 wheel：

- `ruyi_captcha-版本-py3-none-any.whl`：轻量包，用于已有内核的环境或远程客户端。
- 平台 wheel：内置锁定版本的 ff-ruyijs，安装后可直接本地求解，无需再安装 `ruyijs`。
  支持 Windows x64、Linux x86_64、macOS arm64。

从 [Releases](https://github.com/Starktomy/ruyi-captcha/releases) 下载匹配平台的文件后：

```sh
python -m pip install '下载的wheel文件.whl[api]'
ruyi-captcha check-runtime
ruyi-captcha serve --host 127.0.0.1 --port 8000
```

包模式只需安装 wheel；`[api]` 额外安装 HTTP 服务依赖。
Linux 发版在 Ubuntu 24.04 构建，要求 glibc 2.39+；macOS arm64 要求 macOS 26+。
本机生成的 Linux wheel 标为 `manylinux_2_43_x86_64`，它对应本机 glibc 2.43，
应按 wheel 文件自己的平台标记选择安装环境。

Python 3.10+。本机开发已配置 `/root/ff-ruyijs` 的兼容内核；其提交和已验证的
Linux runtime SHA256 记录在 [runtime-lock.json](src/ruyi_captcha/runtime-lock.json)。

```sh
cd /root/ruyi-captcha
python3 -m venv .venv
.venv/bin/python -m pip install -e /root/ff-ruyijs
.venv/bin/python -m pip install -e '.[api,test]'
.venv/bin/ruyi-captcha check-runtime
```

从源码构建的默认包是轻量版。可先安装由所记录内核提交构建的匹配平台 kernel wheel，
再安装本包；也可以直接安装上面的内置内核平台 wheel。
上游原版 `ruyijs==0.3.5` 不包含必需的
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
可从 `ruyi_captcha` 导入 `Engine` 和 `use_engine`，使用所选择内核的共享引擎。
若已有代码直接使用外部 `ruyijs.use_engine`，设置 `RUYI_CAPTCHA_KERNEL=external` 保持相同绑定实例。

`timeout` 是**每次尝试**的预算，`attempts` 是最大尝试次数。取得 token 后应立即提交；
取得 token 与业务后台接受是两种结果，本库不会在没有业务端回执时声称验证成功。

命令行同样可用：

```sh
.venv/bin/ruyi-captcha solve \
  'https://capskip.com/captcha-demo/cloudflare-turnstile/' \
  '0x4AAAAAADogn3t3_JKwKkgS' --timeout 30 --attempts 1
```

## 多环境生成器

旧 fork 的 `b014478` 生成器已迁入本仓库，保持内核只负责应用 profile。
支持 Linux / Windows Firefox 159，固定 seed 可复现，默认语言 `en-US`、时区
`America/New_York`；可显式指定语言、时区、屏幕、DPR 和浏览器报告的逻辑 CPU 数。

```python
from ruyi_captcha import generate_profile, solve_turnstile

profile = generate_profile("linux", seed=42, locale="en-US",
                           timezone="America/New_York", hardware_concurrency=8)
token = solve_turnstile(site_url="https://your-site.example/", sitekey="your-sitekey",
                        profile=profile)
```

```sh
ruyi-captcha profile --os linux --seed 42 --output profile.json
ruyi-captcha profile --os windows --seed 100 --count 10 --output-dir profiles
ruyi-captcha solve 'https://your-site.example/' 'your-sitekey' --profile-file profile.json
# 等价的生成入口，无需安装原生内核：
python -m ruyi_captcha.profiles --os linux --seed 42
```

HTTP 调用把生成的 JSON 对象放进现有请求的 `profile` 字段即可。
`hardware_concurrency` 只改变浏览器属性；服务并发使用 `--concurrency` 控制。
生成器不会安装另一个操作系统、字体或 GPU，跨宿主配置不保证对应的渲染结果。
实现范围、内核继承修复与旧性能补丁清单见
[fork 审计与迁移建议](docs/fork-performance-and-profiles.zh-CN.md)。

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
| `RUYI_CAPTCHA_KERNEL` | `auto` | 优先内置内核；`bundled` 强制内置，`external` 使用外部 `ruyijs` |

每次尝试创建独立 Context；一次 native 崩溃不会结束其他任务的进程。
此版本的队列和结果存储位于**单个 API 进程的内存**，重启后任务和结果消失。
CLI 固定一个 HTTP worker；并发由上述任务池控制。多实例部署需要后续接入共享任务存储。
任务记录总数上限为运行/排队容量加 256，容量用尽时旧完成记录可提前淘汰。
`result_expires_at` 是服务端最长保留结果的截止时间，不是验证码厂商对 token 有效性的保证。

## 开发与测试

```sh
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m build

# 内置内核 wheel，从锁定的内核源码及已构建的原生运行时打包。
.venv/bin/python tools/build_bundled.py --kernel /root/ff-ruyijs
```

离线测试覆盖队列、取消、幂等、期限、SDK、HTTP 鉴权和错误返回；native 用例在内核
缺失时会跳过。实时 CapSkip 验收脚本放在 `tests/live_capskip.py`，结果只记录长度与
后台验收状态。架构与迁移说明见 [docs/architecture.zh-CN.md](docs/architecture.zh-CN.md)。

已恢复旧 fork 的通用性能优化；同底座 CPU、PSS、延迟与原始页面验收记录见
[性能恢复报告](docs/runtime-restoration-20261107.md)。

提交使用 Starktomy 的 GitHub noreply 身份，Codex 作为共同作者。
本仓库采用 AGPL-3.0-only；内核依赖保留自身许可。

发版规则、产物与内核锁定方式见 [release.md](docs/release.md)。
