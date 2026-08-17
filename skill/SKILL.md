---
name: agent-meeting
description: 让 WorkBuddy 以 Agent 身份接入 Agent Hub 群聊舞台（会议系统/agent_hub，localhost:8000），拉取人类网页消息并真实思考回答。Agent Hub 只负责传输（register/pull/reply/已读/持久化），思考由 WorkBuddy 本人（本对话实例 LLM）完成。触发词：开会、上线开会、拉会议消息、获取会议消息、回复老板、结束会议、agent hub、会议系统。
version: 1.0.0
---

# Agent Meeting

Agent Meeting（底层接入 Agent Hub 群聊舞台 `http://localhost:8000`，位于 `会议系统/agent_hub`）是一个**消息传输舞台**。
本 skill 让 **WorkBuddy 自己当一个 Agent**：连上去、拉人类的网页消息、用本人（本对话实例）自己的推理生成回答、发回去。

> **设计铁律（写进代码头部，不可违）**：脚本只做传输，**所有思考由本人（本对话实例 LLM）完成**。本连接器**绝无后台自循环、绝无自动大脑**（不接 MiniMax/OpenAI 等任何自动推理）。未读去重由**服务端** per-agent 已读集合保证，客户端纯透传，本地不再维护 `seen.json`。
>
> **❌ 铁律 I-11（老板 2026-08-17 16:02 定，最高优先级）**：**严禁用 `run_in_background=true`（或任何后台任务机制）起 `watch`/`pull` 长轮询监听**。监听必须由**本人在前台逐轮驱动**：每轮用 `pull --max 1 --interval 0.1`（单次探测）或短轮询，拉到消息即处理，无消息则本 turn 结束、由老板下一轮对话 / 网页消息重新唤醒。**后台 watch = 无大脑假运行**（后台进程没有 LLM，拉到消息也答不了，纯属误导绿点），与「大脑恒为本人（I-8）」直接冲突，绝对禁止。Agent 必须严格遵守，否则视为违规。

连接器只暴露 **4 个原子方法**：`init` / `pull` / `reply` / `end`。由本人在对话里显式逐个调用。

> **入口唯一性**：本技能**唯一维护的入口是 `loop.py`**（4 方法 CLI，已文档化、含本地名缓存 / UTF-8 保证 / 网络重试）。仓库内另含的 `agent_client.py` 已标记 **DEPRECATED**（早期实验性备用客户端，接口不统一且缺上述保障），**新用户请勿使用**，仅作历史参考保留。

> ⚠️ **铁律 I-10（老板 2026-08-17 定）**：**任何任务结束之前（收口 / 向老板汇报 / 调用 `end` 收工），必须先调用一次「获取会议消息」（`pull`）**——确认老板没有新指令、没有叫停，才能收尾。漏掉这一步 = 错过老板最新要求 = 伪收口。详见「方法 2 · 收消息」与「方法 4 · 收工」调用时机。
>
> **入口硬约束（老板 2026-08-17 02:27 定）**：获取会议消息**必须经 `Skill` 工具正式调用本技能**（`Skill` command `agent-meeting`），再执行技能内的 `pull`/`reply` 命令。**禁止在对话里裸跑 `loop.py` 命令**——裸跑 = 没调技能 = 违反本铁律。区别：先 `Skill` 加载技能 → 再跑技能白纸黑写的命令 = 合规；连 `Skill` 都省了直接敲命令 = 偷懒裸跑。
>
> **示例（入口硬约束 + 漏拉=伪收口）**：
> - ❌ 反例：对话里直接裸跑 `python loop.py pull`（没经 `Skill` 调用技能 = 违规，老板 02:27 抓过）。
> - ✅ 正例：`Skill` 工具调 `agent-meeting` → 技能加载后跑其 `pull` 命令。
> - ⚠️ **漏拉=伪收口**：曾发生「拉到老板消息却只 `pull` 没 `reply`」（02:47 被老板抓），教训——**抓到消息必须回复闭环，`pull` 不等于收口**；收口前（向老板汇报 / `end` 之前）还须再 `pull` 一次确认无新指令（见上 I-10 铁律）。

设变量（一次性，全程正斜杠绝对路径）。`loop.py` 仅用 Python 标准库，任意 `python3` 即可运行：
```
PY=python3                                         # macOS/Linux；Windows 用 python 或 WorkBuddy 管理的 python
LOOP=~/.workbuddy/skills/agent-meeting/loop.py                                 # 本技能目录下的 loop.py
```

> 注：若通过 WorkBuddy 的 `Skill` 工具加载本技能，**无需手动设这些变量**——工具会自动定位并执行 `loop.py`。下方命令仅作手动 / 调试参考。

---

## 〇、公共基础设施（每次执行任意方法先过这几道）

| 部件 | 代码位置 | 行为 |
|---|---|---|
| **UTF-8 保证** | `loop.py:32-40` | `os.environ.setdefault("PYTHONUTF8","1")` + `sys.stdout/stderr.reconfigure(utf-8)` → 命令行中文、输出中文均不乱码 |
| **SERVER** | `loop.py:42` | `os.environ.get("AGENT_HUB_URL", "http://localhost:8000")` → 可用环境变量覆盖 |
| **DATA_DIR（可配置 + 自动 fallback）** | `loop.py:46-49` | 默认内置实例 data 目录；**若该路径不存在（非作者机器）自动回退 `~/.agent_hub/data`**（开箱即用）。也可用环境变量 `AGENT_HUB_DATA_DIR` 显式覆盖（接入其它实例 / 跨机 / 隔离测试） |
| **AGENT_NAME_FILE** | `loop.py:50` | `DATA_DIR/agent_name.txt` —— 本地缓存"我是谁" |
| **`_req(method,path,body,query,timeout=15,max_retries=3)`** | `loop.py:53-102` | 统一 HTTP 封装：`query` 走 `urlencode`（中文 agent 名也不乱码）；4xx 客户端错误直接抛；**5xx 与连接失败按指数退避重试（默认 3 次：1s/2s/4s）**，重试耗尽才抛 `ConnectionError`/HTTPError |
| **`resolve_name(cli_name)`** | `loop.py:105-118` | 优先级 `--name` > 读 `agent_name.txt` > 都没有则打印"请先 init"并 `exit(3)` |
| **`ensure_registered(name)`** | `loop.py:139-144` | `POST /api/agents/register` 幂等注册，**best-effort**（异常吞掉，不影响主流程） |

**退出码语义**
- `exit(2)`：`init`/`session --active` 缺 `--name`，或 `reply` 缺 `--msg-id` / `--msg`/`--file` 均缺失。
- `exit(3)`：`pull`/`reply`/`end`/`session` 本地无 `agent_name.txt` 且未传 `--name` → `resolve_name` 打"请先 init"后退出。

---

## 方法 1 · 上线初始化 `init`

**命令**
```
"$PY" "$LOOP" init --name <名字>
```
等价写法（兼容）：`"$PY" "$LOOP" session --active --name <名字>`

**参数**
- `--name <名字>`（必填，否则 `exit(2)`）：Agent 名字。写入本地 `data/agent_name.txt`，并幂等注册到服务端、置开会态。

**执行流程（`do_init` → `loop.py:147-152`，主入口 `loop.py:240-245`）**
1. 主入口先校验 `--name`：为空 → 打印 `init 需要 --name <名字>` + `exit(2)`。
2. `save_name(name)`（`loop.py:121-125`）：`os.makedirs(DATA_DIR, exist_ok=True)` 后把名字写入 `agent_name.txt`（本地落盘，后续方法免传 `--name`）。
3. `ensure_registered(name)`（`loop.py:139-144`）：`POST /api/agents/register`，best-effort（异常吞掉）。
4. `do_session(name, active=True)`（`loop.py:155-163`）：
   - 再 `ensure_registered(name)` 一次。
   - `POST /api/agents/{name}/session?active=true`（名字走 `urllib.parse.quote(name, safe="")` 编码，中文安全）。
   - 打印服务端返回 JSON。
5. 打印 `[ok] 已初始化并上线: {name} @ {server}`。

**服务端动作**：注册/更新 agent 记录；`active=true` 把状态置 `working`（**网页显示🟢处理中**）。

**调用时机**：老板说「开会 / 上线开会」的第一步。之后 `pull`/`reply`/`end` 全靠本地 `agent_name.txt`，不用再传 `--name`。

---

## 方法 2 · 收消息 `pull`（获取会议消息）

**命令**
```
"$PY" "$LOOP" pull
```
等价写法（可配参）：`"$PY" "$LOOP" pull --interval 3 --max 1000`（即 `watch` 别名）。
> ⚠️ **`watch` 仅作前台阻塞用途（本人守在终端看输出），严禁用 `run_in_background=true` 起它当「常驻监听」**——那是无大脑假运行（见 I-11）。会议循环的"持续等待"由本人在前台逐轮 `pull --max 1` 实现，不是后台进程。

**参数**
- `--interval`（默认 3，秒）：轮询间隔。
- `--max`（默认 1000）：最大轮询次数（约 50 分钟）。
- **单次探测**：`--max 1 --interval 0.1` —— 只看一眼收件箱、立刻返回（有则返回消息，无则返回 `[]`），用于查状态/验证而不阻塞。

**执行流程（`do_pull` → `loop.py:177-195`，主入口 `loop.py:267-270`）**
```
name = resolve_name()                       # 读 --name 或本地 agent_name.txt
for _ in range(max_iters):                  # 默认 1000 次
    try:
        resp = GET /api/messages/pull?agent_name={name}
        msgs = resp.get("messages", [])
    except ConnectionError:
        msgs = []                           # 连接断了不退出，继续等
    if msgs:
        print(JSON 数组); return msgs       # 拉到即返回
    time.sleep(interval)                    # 没拉到就睡一会儿再来
print([]); return []                        # 跑满 max 次仍空 → 返回 []
```

**服务端去重机制（关键，`message_store.pull_messages`）**
- 返回**仅人类（user）发给本 Agent 的消息**——服务端已过滤掉 Agent 自己 `reply` 出去的内容（`sender_type != "user"` 不返回）。
- **不回灌历史**：新 Agent 首拉时，服务端用 `reads.json` 的已读回执**播种**已读集合，所以首拉只给"真未读"，不会把全部历史砸回来。
- 之后每次 `pull` 只返回"上次拉走之后新增的未读"，**去重完全在服务端**，客户端纯透传、无本地去重文件。

**返回结构**（每条消息字典）：`id / content / sender_type / sender_agent_name / target_type / target_agent_name / created_at / client_msg_id / read_by`。

**调用时机**：开会后循环体第一步；**每条消息回复完之后再拉一次**；收口前（`end` 之前）必须再拉一次（I-10 铁律）。

---

## 方法 3 · 发消息 `reply`

**命令**
```
"$PY" "$LOOP" reply --msg-id <消息id> --msg <内容>
```
长文本备选：`"$PY" "$LOOP" reply --msg-id <id> --file <绝对路径>`（`--file` 优先级高于 `--msg`，utf-8）

**参数**
- `--msg-id <id>`（必填，否则 `exit(2)`）：要回复的目标消息 id（来自 `pull` 返回）。
- `--msg <内容>`：直接传入回复文本。运行环境已设 `PYTHONUTF8=1`，命令行中文不乱码。
- `--file <路径>`：从文件读回复内容（utf-8），适合长文本。

**执行流程（`do_reply` → `loop.py:174-190`，主入口 `loop.py:248-254`）**
1. 主入口先校验 `--msg-id`：为空 → 打印 `reply 需要 --msg-id` + `exit(2)`。
2. `name = resolve_name()`（读 `--name` 或本地名）。
3. `do_reply(name, msg_id, content, content_file)`：
   - 若传了 `--file` → 读文件 utf-8 覆盖 `content`。
   - `content` 仍为空 → 打印 `reply 需要 --msg 或 --file` + `exit(2)`。
   - `ensure_registered(name)`（best-effort）。
   - `POST /api/messages/reply`，body = `{agent_name, content, reply_to_message_id: msg_id, client_msg_id: "c_"+msg_id}`。
   - 打印服务端返回 JSON。

**服务端动作**：把这条存为 `sender_type="agent"`、`sender_agent_name=name` 的消息，挂到 `reply_to_message_id` 下。
**彩蛋（实战有用）**：`/api/messages/reply` 的返回体带 `new_messages` 字段——你回复之后如果又有新消息到达，会一并返回。所以"回复完立即拉"有时可省一步，但规范上仍建议显式再 `pull` 一次，避免漏读。

**调用时机**：对 `pull` 拿到的**每条**消息，本人真思考后调用，逐条发回。

---

## 方法 4 · 收工 `end`

**命令**
```
"$PY" "$LOOP" end
```
等价写法（兼容）：`"$PY" "$LOOP" session`（不带 `--active`）

**参数**：无（名字默认读本地 `agent_name.txt`）。

**执行流程（`do_end` → `loop.py:143-151`，主入口 `loop.py:223-235`）**
1. `name = resolve_name()`（本地无则 `exit(3)`）。
2. `ensure_registered(name)`（best-effort）。
3. `POST /api/agents/{name}/session?active=false`（名字走 `quote` 编码）。
4. 打印服务端返回 JSON。

**服务端动作**：把 agent 状态置 `offline`（**网页显示⚪已收工**）。

**调用时机**：老板说「结束会议」时。**注意**：调 `end` 之前务必先 `pull` 一次（I-10 铁律），确认老板无最新指令/叫停，否则视为伪收口。

---

## 方法速查表

| 方法 | 必填参数 | 服务端端点 | 服务端副作用 |
|---|---|---|---|
| `init` | `--name` | `POST /api/agents/register` + `POST /api/agents/{name}/session?active=true` | 注册 + 置🟢working |
| `pull` | 无（读本地名） | `GET /api/messages/pull?agent_name={name}` | 标记已读（per-agent 集合） |
| `reply` | `--msg-id` + (`--msg`\|`--file`) | `POST /api/messages/reply` | 落库 agent 消息 + 回执 `new_messages` |
| `end` | 无（读本地名） | `POST /api/agents/{name}/session?active=false` | 置⚪offline |

---

## 标准调用顺序（每次「开会」）

```
1) "$PY" "$LOOP" init --name <名字>                # 上线初始化（写本地名+注册+置🟢）
2) "$PY" "$LOOP" pull --max 1 --interval 0.1      # 前台单次探测收消息（拉到即返回，不后台）
3) 对每条消息：本人真思考 → reply --msg-id <id> --msg <内容>
4) 回到 2) 再前台 pull 一次；无消息则本 turn 结束，等老板下一轮对话/网页消息唤醒后再 pull
5) "$PY" "$LOOP" pull --max 1 --interval 0.1      # 收工前最后再拉一次（铁律 I-10：确认老板无新指令）
6) "$PY" "$LOOP" end                              # 收工（置⚪）
```

> ⚠️ **循环全程无后台进程（I-11）**：每轮 `pull` 在前台跑、立即返回；无消息即本 turn 结束，靠老板下一轮触发唤醒。"持续监听"由本人逐轮驱动实现，**严禁 `run_in_background` 起 watch 常驻**（那是无大脑假运行）。

> 连接地址可用环境变量 `AGENT_HUB_URL` 覆盖（默认 `http://localhost:8000`）；Agent 名可用 `--name` 覆盖本地默认值。

---

## 快速上手（完整示例）

下面是一轮「开会」的真实命令与输出（Agent 名 `WorkBuddy`，服务端 `localhost:8000`）：

```bash
# ① 上线初始化
$ python3 loop.py init --name WorkBuddy
[ok] 已初始化并上线: WorkBuddy @ http://localhost:8000

# ② 收消息（老板在网页说了"上线开会，把昨天的方案整理一下"）
$ python3 loop.py pull
[{"id":"msg_a1b2","content":"上线开会，把昨天的方案整理一下","sender_type":"user","sender_agent_name":"老板","target_type":"single","target_agent_name":"WorkBuddy","created_at":"2026-08-17T02:10:00","client_msg_id":null,"read_by":[]}]

# ③ 本人（WorkBuddy 对话里）真思考后，逐条回复
$ python3 loop.py reply --msg-id msg_a1b2 --msg "收到，正在调出昨天的方案文档，马上整理给你。"
{"status":"ok","message_id":"msg_c3d4","new_messages":[]}

# ④ 收工前再拉一次（I-10：确认老板没新指令）
$ python3 loop.py pull
[]

# ⑤ 收工
$ python3 loop.py end
{"status":"ok","name":"WorkBuddy","active":false}
```

> `reply` 的返回体带 `new_messages` 字段：若你回复期间老板又发了新消息，会顺手带回，省一次 `pull`；但规范上仍建议显式再 `pull` 一次，避免漏读（见 I-10）。

---

## 使用场景（老板视角）

本连接器是 **AI（WorkBuddy）接入会议系统的"手"**，人类（老板）只需会网页操作：

- **唤醒 AI**：在会议系统网页（`http://localhost:8000`）说「开会 / 上线开会」，AI 即进入循环（先 `init` 上线、再 `pull` 拉你的消息）。
- **看状态**：网页上 Agent 显示 🟢 处理中 = 已上线在等你的消息；⚪ 已收工 = AI 已 `end` 离线。
- **收工**：说「结束会议」，AI 收工前会再 `pull` 一次确认你没新指令，然后 `end` 离线。
- **思考全在 AI 侧**：你看到的是 AI 在 WorkBuddy 对话里生成的回复，连接器脚本不自带任何大脑、不后台自循环。

---

## 兼容别名对照

| 别名 | 等价主命令 | 说明 |
|---|---|---|
| `session --active --name <名字>` | `init --name <名字>` | 上线初始化 |
| `session`（不带 `--active`） | `end` | 收工 |
| `watch [--interval --max]` | `pull [--interval --max]` | 轮询收消息（**仅前台阻塞用，禁止 `run_in_background` 常驻，见 I-11**） |
| `register` | （main 自动调用） | 幂等注册，独立调用亦可 |
| `--version` | — | 打印版本号 `agent-meeting 1.0.0` 并退出 |

---

## 工程细节与坑（实战踩过）

1. **`pull` 容错、其他方法不容错**：`do_pull` 里 `ConnectionError` 被 `except` 捕获 → 继续轮询不退出；但 `init`/`reply`/`end` 里的实质 `_req` 没包 try，服务端没起时会直接抛 `ConnectionError` 崩溃。所以**只有 pull 能扛服务端短暂离线**，其余方法依赖服务在线。
2. **DATA_DIR 已可配置**：`loop.py:43` 默认保留内置实例路径，支持 `AGENT_HUB_DATA_DIR` 环境变量覆盖（接入其它 Agent Hub 实例 / 跨机 / 隔离测试）。QA 隔离测试即设该变量指向 `test_data` 即可，无需改代码；生产零污染靠停服直改 JSON + 服务无内存缓存（每次从文件读）。
3. **`new_messages` 彩蛋**：`reply` 返回体带服务端顺手返回的新消息，所以"回复完立即拉"有时可省一步——但规范上仍建议显式再 `pull` 一次，避免漏读。
4. **中文名编码**：所有 URL 路径里的 agent 名走 `urllib.parse.quote(name, safe="")`，query 走 `urlencode`，所以"WorkBuddy/老板"这类中文/特殊名都不会乱码。
5. **单进程监听**：uvicorn 无热重载。改 `storage.py`/`message_store.py` 后必须净重启进程，否则旧逻辑在跑（端口竞争会报 `[Errno 10048]`，需 PowerShell 按命令行精准杀光残留再起唯一进程）。

---

## 🗺 路线图（Roadmap）

- [x] `DATA_DIR` 支持 `AGENT_HUB_DATA_DIR` 环境变量（跨平台 / 隔离测试友好）
- [x] 命令变量改为可移植写法（`python3` + 技能相对路径，去除硬编码机器路径）
- [x] 新增「快速上手完整示例」「老板视角使用场景」两节
- [ ] 多工具适配壳（OpenClaw / Trae 等）：核心已是标准 HTTP 接口，按需加薄壳即可
- [ ] `loop.py` 补充更丰富的 `--help` 子命令说明
- [ ] 可选：断线自动重连（当前仅 `pull` 容错，其余方法依赖服务在线）

> 运行时产物 `data/`（含 `agent_name.txt` / `agent_read_*.json`）为本地缓存，已纳入 `.gitignore`，不会进仓库。
