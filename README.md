# codex-token · Codex 用量看板

实时查看 Codex 会话的 **token 消耗** 与 **提示缓存命中率**。

Codex 自身在 `/status` 只给 `input=… (+ … cached)` 这类绝对数，桌面端 "Usage in this chat" 也只给
cached / uncached 的条数——都没有百分比，也没有一个能边跑边看的窗口。这套工具补的就是这块。

三种形态，共用同一份解析逻辑：

| 形态 | 入口 | 特点 |
| --- | --- | --- |
| 伴随窗口 | Codex 启动时自动出现 | 原生 GUI，不用终端，不用切窗口 |
| 浏览器看板 | http://127.0.0.1:8788/ | 图表更丰富，可在应用内浏览器或 Chrome 打开 |
| 终端面板 | `codex-token` | 适合塞进 tmux、写脚本 |

## 依赖

- **Node.js 18+**：解析会话文件、提供看板服务
- **Python 3 + tkinter**（`python3-tk`）：伴随窗口
- **X11 工具**：`xdotool`、`xprop`、`xrandr` —— 用来定位窗口、判断当前焦点窗口
- 可选 **`python3-gi`**：GTK 版窗口，用 `CODEX_USAGE_IMPL=gtk` 启用

只读本地文件、只监听 `127.0.0.1`，不联网、不需要登录态。

## 安装

> 命令是 `codex-token` / `codex-token-window`；安装目录与内部脚本名沿用 `codex-usage` 前缀（历史原因）。

```bash
git clone https://github.com/SqyLt/codex-token.git
cd codex-token
./install.sh              # 安装命令 + 应用菜单入口
./install.sh --autostart  # 顺带装开机自启（可选）
```

安装脚本会把源码放到 `~/.local/share/codex-usage`，在 `~/.local/bin` 建两个命令
（`codex-token`、`codex-token-window`，同时保留 `codex-usage`、`codex-usage-window` 兼容链接），并写入应用菜单入口。
以后 `git pull` 之后重跑一次 `./install.sh` 即可更新。

## 形态一：伴随窗口（推荐）

Codex 打开时窗口自动出现，Codex 退出时自动关闭。

- 手动打开：应用菜单里搜 **Codex 用量窗口**，或运行 `codex-token-window`
- 开机自启：`~/.config/autostart/codex-usage-companion.desktop`（登录后由
  `codex-usage-companion.sh` 守着，等 Codex 出现再开窗）
- 多显示器时默认放在**第二块屏幕右侧**；不勾「置顶」时不会常压在其他软件之上
- 高度按屏幕自适应（上限 820px），也可以自己拖大拖小
- 同一时刻只会有一个用量窗口，重复启动会自动退出
- 自己点关闭后本轮不会自动重开，下次 Codex 启动会恢复
- 日志：`~/.local/share/codex-usage/companion.log`

窗口内容：会话命中率（大字）、上下文占用条、本次/本轮/会话累计输入、未缓存输入、
cache_write、调用次数、最近 40 次调用的命中率柱状图、最近调用明细、右上角切换会话。

**最近调用明细是可滚动的**：右侧有滑块，支持鼠标滚轮，最多展示最近 60 次调用，
最新的一次排在最上面，所以调用再多也不会顶出窗口。

**窗口是无边框的**：拖动请按住标题栏，关闭点右上角 ✕。之所以不用系统边框，是因为这台机器上
GNOME Shell 会把 Tk 的普通窗口卡在映射阶段（窗口被创建出来但一直不显示，Tk 自己 `deiconify()`
也救不回来），无边框窗口不受窗口管理器影响，能可靠显示。

**窗口可以随意缩放**：拖任意一条边或右下角的 ◢ 抓手都行，鼠标移上去会变成对应的缩放光标。
尺寸和位置记在 `window.state`，下次启动沿用；如果记录的位置已经不在任何显示器上（比如拔了副屏），
就自动重新摆回 Codex 旁边。想恢复默认大小，删掉 `window.state` 再重开窗口即可。

**默认不遮挡别人**。这里有个绕不开的坑：Mutter 会把 override-redirect（无边框）窗口**合成在普通窗口之上**，
X 层的 `lower()` 实测没有视觉效果（日志显示已让位，截图里它仍然盖在上面）。所以"降低层级"这条路是死的，
只能整体隐藏才真的不挡人。

判定触发方式：

- **焦点变化走事件**：后台用 `xprop -spy -root _NET_ACTIVE_WINDOW` 监听，切换窗口时立刻判定。
  实测点击到隐藏约 100ms（其中程序内部处理只要 3ms，剩下是 X 事件传递耗时）。
- **每 2 秒兜底轮询**：覆盖"焦点没变、但对方向窗口被移动导致重叠情况变化"这种场景。

判定规则：

| 情况 | 行为 |
| --- | --- |
| 刚启动的前 2 秒 | 先显示一下，避免你以为没打开 |
| 勾了标题栏的「置顶」 | 永远在最前 |
| 别的软件在前台，且和面板重叠 | **隐藏自己**，完全让开 |
| 其它情况（Codex 在前台 / 没有重叠 / 取不到活动窗口） | 保持显示 |

所以想让它**一直看得见**，两个办法：一是把面板拖到不和常用软件重叠的位置
（比如第二块屏幕，规则只在重叠时才隐藏）；二是直接勾「置顶」。

多显示器时，**首次启动默认就放在第二块屏幕的右侧**（Codex 所在屏幕之外），这样常见情况下
根本不重叠，面板会一直显示。位置改动会记进 `window.state`；删掉该文件即可恢复默认位置。

因为是无边框窗口，它不会出现在任务栏和 Alt-Tab 里。万一被完全盖住，
用应用菜单的 **Codex 用量窗口** 唤醒它，或者把鼠标移到它所在的区域（会自动冒出来）。

**面板不见了怎么办**：从应用菜单点一次 **Codex 用量窗口**。已经有窗口（包括被最小化的）
会直接唤醒它，没有就重新开一个。

改完 `usage-window.py` 后需要重开窗口才生效：从应用菜单点一次 **Codex 用量窗口**，
或者等下次 Codex 启动时自动换新。

伴随脚本判断"Codex 是否在运行"时只看主进程（排除 `--type=zygote` 这类辅助进程），
主进程 PID 变化就视为重启，会自动解除"用户已关闭"的休眠状态并重新开窗。

## 形态二：浏览器看板

```bash
# 服务已在伴随脚本里自动拉起；也可单独启动：
node ~/.local/share/codex-usage/dashboard-server.mjs
```

打开 `http://127.0.0.1:8788/` 。页面每秒刷新，含命中率趋势柱状图、最近调用明细表和
所有会话的命中率排行；表格里点一行即可切换会话。

服务只监听 `127.0.0.1`，不对外暴露。可用 `CODEX_USAGE_PORT` 换端口、
`CODEX_USAGE_DAYS` 换会话列表统计天数。

## 形态三：终端

```bash
codex-token              # 实时面板
codex-token --compact    # 单行输出，适合 tmux 状态栏
codex-token --all        # 最近一天所有会话的命中率总览
codex-token --json       # JSON，给脚本用
codex-token --once       # 打印一次快照
```

常用参数：

```
-i, --interval <ms>      刷新间隔（默认 500）
-f, --file <path>        指定某个 rollout 文件
-s, --session <前缀>     按会话 id 前缀选择
    --include-subagents  把 guardian / 子代理会话也算进来
    --alert-below <n>    单次调用命中率低于 n% 时响铃
    --log <path>         把每次调用追加写入 JSONL
```

## 命中率怎么算

（用词对齐 Codex 官方界面：`input − cached` 这个量官方称 **Uncached input tokens / 未缓存输入 Token**，
不要叫“未命中”——“未命中”还包含写缓存那部分。）


```
命中率 = cached_input_tokens / input_tokens
```

`input_tokens` 是**总量**，`cached_input_tokens` 是其中命中缓存的部分，未缓存部分是
`input - cached`。所以不要写成 `cached / (cached + uncached)`，会重复计算。

`cache_write_input_tokens` 通常为 0；一旦非 0，它属于“未命中但写入更贵”的第三类，
按计费口径算应是 `cached / (input + cache_write)`。

## 数据来源

读取 Codex 自己实时追加的会话文件：

```
$CODEX_HOME/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl
```

- `token_usage_record` —— 每次模型调用的 `usage` / `turn_token_usage` / `thread_token_usage`，实时性的来源
- `token_count` —— 上下文窗口大小与账号额度快照
- `session_meta` —— 会话 id、工作目录、来源、线程类型

全程只读本地文件、只监听本机回环地址，不联网、不需要登录态，也不会和 Codex 抢资源。

## 文件清单

```
~/.local/bin/codex-token                     终端命令（软链到安装目录）
~/.local/bin/codex-token-window              从应用菜单打开窗口
~/.local/share/codex-usage/
  codex-usage.mjs        解析 + 终端界面（可被 import 复用）
  dashboard-server.mjs   看板服务（127.0.0.1:8788）
  dashboard.html         看板页面
  usage-window.py        原生伴随窗口（tkinter）
  codex-usage-companion.sh  伴随脚本：守着 Codex 进程开关窗口
  companion.log          运行日志
~/.config/autostart/codex-usage-companion.desktop   开机自启
~/.local/share/applications/codex-usage-window.desktop  应用菜单入口
```

## 已知限制

- 默认只跟随 `thread_source == "user"` 的会话；guardian 等子代理会话是独立文件，
  用 `--include-subagents`、看板表格或窗口右上角的下拉框查看。
- “会话累计 input”是所有轮次重放上下文之和，会远大于你实际输入的字数，看命中率比值才有意义。
- 额度行依赖 provider 返回 `rate_limits`；DeepSeek / ZAI 这类第三方 provider 不返回时显示“未启用额度限制”。
- 解析适配 Codex CLI 0.153.4 的记录格式；Codex 升级若改动字段，需要同步更新 `codex-usage.mjs`。

## 顺带修掉的沙箱问题

这台机器上反复出现的 `bwrap: Can't mkdir .../.git: Read-only file system` 已定位：

每个任务会把 `~/.codex/visualizations/<日期>/<任务ID>` 注入成**可写根**，而 `~/.codex` 在沙箱里是
**只读根**。Codex 的沙箱要为每个可写根准备 `.git` / `.agents` / `.codex` 三个挂载点，
在只读树里建不出来就整个沙箱启动失败——失败发生在任何命令执行之前，所以**该任务里所有命令都会挂**。
不同任务的目录不同，表现就成了“时好时坏”。

复现与验证（把任意目录当可写根）：

```bash
codex sandbox -c sandbox_mode='"workspace-write"' \
  -c 'sandbox_workspace_write.writable_roots=["/some/dir"]' -- /bin/echo ok
```

结论：`/tmp/xxx`、`~/xxx` 这类可写树下的目录正常；`~/.codex/...` 下的目录必然报只读。
只要预先建好那三个目录，沙箱就能正常启动（已验证）。

所以伴随脚本每轮都在做一件事：给当天每个任务目录补上 `.git` / `.agents` / `.codex`，
即 `heal_visualization_roots()`。想撤掉的话，删掉这些空目录并移除该函数即可。

注意：`use_linux_sandbox_bwrap` 和 `use_legacy_landlock` 两个开关在当前版本里都是
`removed` / `deprecated` 状态（`codex features list` 可查），没有官方开关能绕开，
根治要等 Codex 应用更新。

## 许可证

MIT，见 [LICENSE](LICENSE)。
