# Prism 独立镜像部署

镜像：`ghcr.io/power12317/prism-adapter:v2026.10.10-1`（同时发布 `latest`），支持 Linux amd64 / arm64。CPA 配套版本为 `ghcr.io/power12317/cliproxyapi:v2026.10.10-3`。本镜像包含适配器、Python 3.12、固定版本的 Playwright 1.63.0 及匹配的 Chromium；服务器不需要安装 Python、编译源码或运行 `docker build`。

[发布与部署附件](https://github.com/power12317/prism-adapter/releases/tag/v2026.10.10-1) · [构建与验收记录](https://github.com/power12317/prism-adapter/actions) · [来源与 LGPL 许可证](https://github.com/power12317/prism-adapter/blob/main/UPSTREAM.md)

每个 Release 的 `IMAGE.txt` 给出确切版本和不可变 manifest digest，`manifest.json`、`digest-amd64.txt`、`digest-arm64.txt` 给出架构信息。先检查对应 Actions 全部成功；`anonymous-pull` 验证的是无登录状态下实际下载完整镜像。

## 1. 只下载部署文件

需要 Docker Engine 和 Docker Compose v2.20+。在现有 CPA 的 Compose 目录执行（不要更改已有项目名）；下载包只包含部署附件，不包含也不会覆盖 `config.yaml`、`auths/` 或凭据：

```sh
VERSION=v2026.10.10-1
curl -fL "https://github.com/power12317/prism-adapter/releases/download/$VERSION/prism-adapter-deploy-$VERSION.tar.gz" -o prism-deploy.tar.gz
tar -xzf prism-deploy.tar.gz
```

必须把 `chromium-seccomp.json` 和 Compose 文件放在一起；只下载一个 Compose 文件会缺少 Chromium sandbox 的必要附件。包内也包含 seccomp 的许可证。`IMAGE.txt` 可用于固定 digest：

```sh
cat IMAGE.txt
# 可选：在当前 shell 固定到 Release 的不可变 digest。
export PRISM_ADAPTER_IMAGE="$(tail -n 1 IMAGE.txt)"
docker pull "$PRISM_ADAPTER_IMAGE"
```

## 2. 选择与你当前部署匹配的一种启动方式

### 已经运行 CPA（推荐使用 overlay）

沿用现有 CPA 的目录、项目名以及同一组 `-f` 文件。下面假设现有文件是 `docker-compose.yml`，服务名为 `cli-proxy-api`：

```sh
docker compose -f docker-compose.yml -f docker-compose.prism-overlay.yml pull prism-adapter
docker compose -f docker-compose.yml -f docker-compose.prism-overlay.yml up -d --no-build --wait prism-adapter
```

若原来使用 `-p my-cpa`，上面和后续所有命令都继续加上 `-p my-cpa`。可用 `docker compose ls` 核对。原有 CPA 必须升级到已经移除适配器桥接 Key 的 `v2026.10.10-3` 或兼容版本；overlay 不替换你的 CPA 配置、镜像或端口。

若现有文件就是 `docker-compose.codex.yml`，把上面的 `docker-compose.yml` 换成它。若你原来通过多份 Compose 文件叠加部署，则保留原有全部 `-f` 参数，最后追加 `-f docker-compose.prism-overlay.yml`。两种 sidecar 可以同时共享 `cli-proxy-api` 的网络命名空间。`--no-build` 避免原有 Codex 示例文件中的 `build` 设置触发本地编译。

不要在已有 CPA 旁再启动下面的完整文件，否则会建立第二套 CPA。若原 CPA 是 `docker run` 启动，应先将它迁入同一 Compose 项目、沿用原有配置与绑定目录，再加入 overlay；`network_mode: service:cli-proxy-api` 不能跨两个 Compose 项目指向另一个服务。

### 尚未部署 CPA（完整文件）

把已有有效的 CPA `config.yaml`、`auths/`、`logs/` 放在当前目录，或用原有路径变量指定它们：

```sh
export CLI_PROXY_CONFIG_PATH=/你的现有路径/config.yaml
export CLI_PROXY_AUTH_PATH=/你的现有路径/auths
export CLI_PROXY_LOG_PATH=/你的现有路径/logs

docker compose -f docker-compose.prism.yml pull
docker compose -f docker-compose.prism.yml up -d --wait
```

`config.yaml` 必须已存在；文件缺失会明确报错，不会自动生成配置或凭据。完整文件保留 CPA 8317 和原有 OAuth 回调端口；适配器不暴露宿主机端口。

## 3. 健康检查与日志

以下以完整文件为例。使用 overlay 时，替换为你启动时同一组 `-f` / `-p` 参数：

```sh
docker compose -f docker-compose.prism.yml ps
docker compose -f docker-compose.prism.yml exec -T prism-adapter python container/healthcheck.py --verbose
docker compose -f docker-compose.prism.yml logs --tail=100 prism-adapter
```

健康响应为 `{"status":"ok"}`。启动日志的 `prism_adapter_listening` 会显示 `127.0.0.1:8319`、`uid:10001`、`mode:browser` 和 `browser:on_demand`，不输出 token、Cookie 或请求正文。`/health` 只代表 HTTP 进程健康，**不代表浏览器已启动、账号已获 Prism 权限或真实上游可用**。

`./prism-data:/var/lib/cliproxy-prism` 保存原有会话/工具状态与回执。新建目录由入口自动初始化为 UID/GID 10001、权限 0700；随后适配器及浏览器以非 root 用户运行。不要给服务添加 `user: 0:0`，不要挂载 CPA 全量 `auths`。OAuth 由 CPA 每次通过原有身份头转发；没有独立桥接 Key，也不需要配置适配器 Bearer 验证。

Compose 使用 `init: true` 回收浏览器子进程，并提供 1 GiB `/dev/shm` 容量以支持 Chromium 的进程间共享内存；这是容量上限，不是预留 1 GiB 内存，也不改变适配器原有并发或内存策略。默认浏览器模式、缓存和所有业务协议均保持 CPA 基线版本的行为。

Chromium 的 user-namespace 和 seccomp sandbox 保持开启。附件在 Moby 默认 seccomp 规则上只添加 `clone/setns/unshare/chroot`，不使用 `privileged`、`SYS_ADMIN` 或 `--no-sandbox`。宿主机需要允许非特权用户命名空间；如果宿主机的 AppArmor/SELinux 额外禁止它，应根据主机审计日志处理该限制，不要直接关闭 sandbox。入口的有限权限仅用于目录初始化；适配器进程的有效 capabilities 为 0。

## 4. 开启原有 Prism 开关并用真实账号测试

在 CPA 原有管理界面开启 Prism，或在已有配置中合并以下字段（默认关闭）：

```yaml
codex:
  prism:
    enabled: true
```

沿用你原有的 Codex OAuth 登录与 CPA API Key。下面 `CPA_API_KEY` 是客户端访问 **CPA** 的现有密钥，不是适配器桥接 Key。适配器不读取服务器上的 OAuth 文件。

```sh
export CPA_URL=http://127.0.0.1:8317
read -r -s -p 'CPA API Key: ' CPA_API_KEY; echo
export CPA_API_KEY

# JSON
curl --fail-with-body "$CPA_URL/v1/responses" \
  -H "Authorization: Bearer $CPA_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-6.1-sol","reasoning":{"effort":"xhigh"},"input":[{"role":"user","content":"只回复 Prism OK"}],"stream":false}'

# SSE
curl --fail-with-body -N "$CPA_URL/v1/responses" \
  -H "Authorization: Bearer $CPA_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-6.1-sol","reasoning":{"effort":"xhigh"},"input":[{"role":"user","content":"只回复 Prism SSE OK"}],"stream":true}'
```

Prism 的 SSE 返回完成后的事件和 `response.completed`，不是逐 token delta；`usage` 为 null 是当前协议行为。首次调用会按需启动 Chromium。

客户端工具请求与文本请求共用现有四模型目录：`gpt-6.1-sol`、`gpt-5.6-sol`、`gpt-5.6-terra`、`gpt-6-luna`。适配器不再把带工具的请求限定为 Sol；请求的模型和推理强度原样保留，实际可用性仍取决于账号在 Prism 中的选项。此次兼容调整只需更新适配器。

超长请求会自动按完整文本（包含工具目录、指令、历史）经 JSON 转义后的字节数分段，在同一 Prism 会话中连续发送。中间段仅确认接收，最后一段才返回正式回答或客户端工具调用。上游明确拒绝大小时，自动减小分段并继续发送剩余内容；已接收的前缀不会重发。工具描述、指令和历史原文均保留，所有段使用请求指定的模型和推理强度。

同一账号、调用者和客户端会话的后续请求，历史前缀匹配时只发送新增内容，继续使用上游返回的会话、响应 ID 和快照。历史或工具目录改变时新建聊天；上游明确表示旧会话无法继续且本次尚未接收任何新分段时，自动新建聊天并分段重放。结果未知时仍保留 pending，不自动重放。普通浏览器与 multiplex 模式都支持此流程，无需新增配置或更新 CPA。

已经处理完成的工具结果可以继续出现在客户端完整历史中，重复提交同一份已完成历史也不会因“已消费”被拒绝。旧版 `prism-data` 中的已完成记录直接兼容，无需清库；仍未完成或结果未知的请求保留原有 pending 状态。

镜像内提供可选的真实账号工具示例，无需在服务器安装 Python：

```sh
docker compose -f docker-compose.prism.yml exec --user 10001:10001 prism-adapter \
  python container/verify_account.py
```

它会隐藏输入 CPA API Key，通过同一网络内 `http://127.0.0.1:8317/v1/responses` 发起 gpt-6.1-sol/xhigh SSE 请求，保留完整工具目录和 call/result 历史，验证 `function lookup → custom echo → final`。工具只返回固定测试字符串，不执行模型生成的代码。若 CPA 使用其他端口/TLS，可通过 `exec -e CPA_URL=你的完整responses地址` 指定；也可用 `exec -e CPA_API_KEY` 传入已导出的现有 CPA Key。

这些真实账号请求由你在服务器主动执行。CI 只使用本地模拟 Prism 页面和合成身份，成功证明 Linux 镜像、真实 Chromium、HTTP、模型/推理选择与工具协议链路；不证明你的账号权益、网络出口或真实上游当前可用性。

## 5. 升级与回滚

记录当前使用的版本或 `IMAGE.txt` 中的 digest。下载目标版本的部署包后，在同一项目中操作；例如只升级适配器：

```sh
export PRISM_ADAPTER_IMAGE=ghcr.io/power12317/prism-adapter:latest
docker compose -f docker-compose.prism.yml pull prism-adapter
docker compose -f docker-compose.prism.yml up -d --wait prism-adapter
```

需要同时升级 CPA 时，更新 `CLI_PROXY_IMAGE` 后对完整栈执行 `pull` 和 `up -d --wait`，保留所有 overlay 参数。适配器单向依赖 CPA `service_started`，并设置 `restart: true` 处理 Compose 主动更新联动；不要给 CPA 添加反向依赖。共享网络命名空间下，CPA 被重建时应通过整套 Compose 更新相关 sidecar，不要绕开 Compose 单独删除 CPA 容器。

回滚到本次版本（也可以把值换成之前记录的 digest）：

```sh
export PRISM_ADAPTER_IMAGE=ghcr.io/power12317/prism-adapter:v2026.10.10-1
docker compose -f docker-compose.prism.yml pull prism-adapter
docker compose -f docker-compose.prism.yml up -d --wait prism-adapter
```

保留 `prism-data`，不要删除它来“重试”结果未知的请求；原适配器的 pending/防重放行为仍然有效。Compose 的 `restart: unless-stopped` 处理进程退出；健康检查失败本身不会自动重启容器。遇到 502 先查看适配器日志和 CPA 日志，区分“没有监听进程”“浏览器/sandbox 启动失败”和“真实 Prism 请求失败”。
