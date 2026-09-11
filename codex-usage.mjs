#!/usr/bin/env node
// codex-usage — 实时查看 Codex 会话的 token 消耗与提示缓存命中率
//
// 数据来源：$CODEX_HOME/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl
//   token_usage_record  每次模型调用的用量（实时追加，粒度最细）
//   token_count         上下文窗口大小 + 账号额度快照
//   session_meta        会话 id / 工作目录 / 来源 / CLI 版本
//
// 完整用法见 --help

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

const ESC = '\x1b[';
let useColor = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR;

const c = {
  dim: (s) => (useColor ? ESC + '2m' + s + ESC + '0m' : String(s)),
  bold: (s) => (useColor ? ESC + '1m' + s + ESC + '0m' : String(s)),
  cyan: (s) => (useColor ? ESC + '36m' + s + ESC + '0m' : String(s)),
  green: (s) => (useColor ? ESC + '32m' + s + ESC + '0m' : String(s)),
  yellow: (s) => (useColor ? ESC + '33m' + s + ESC + '0m' : String(s)),
  red: (s) => (useColor ? ESC + '31m' + s + ESC + '0m' : String(s)),
};

const HELP = `codex-usage — 实时查看 Codex 的 token 消耗与缓存命中率

用法：
  codex-usage                    实时面板，跟随最近活动的会话
  codex-usage --once             打印一次快照后退出
  codex-usage --json             输出 JSON（便于脚本消费）
  codex-usage --compact          单行输出（适合 tmux 状态栏 / watch）
  codex-usage --all              列出最近几天的所有会话及命中率
  codex-usage -f <rollout.jsonl> 跟随指定会话文件

常用参数：
  -i, --interval <ms>      刷新间隔，默认 500
      --once               只输出一次
      --json               以 JSON 输出（隐含 --once）
      --compact            单行输出
      --all                多会话总览
  -d, --days <n>           --all 统计最近 n 天，默认 1
  -f, --file <path>        指定会话文件
  -s, --session <前缀>     按会话 id 前缀选择会话
      --include-subagents  连同 guardian / 子代理会话一并纳入
      --alert-below <n>    单次调用命中率低于 n% 时响铃提醒
      --log <path>         把每次调用的用量追加写入 JSONL
      --no-color           关闭颜色
  -h, --help               显示本帮助

命中率算法：命中率 = cached_input_tokens / input_tokens
（input_tokens 是总量，cached 是其中命中缓存的部分；未缓存输入 = input - cached）
`;

function parseArgs(argv) {
  const o = {
    mode: 'live',
    interval: 500,
    days: 1,
    file: null,
    json: false,
    compact: false,
    alertBelow: null,
    log: null,
    session: null,
    includeSubagents: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '-h':
      case '--help':
        o.mode = 'help';
        break;
      case '--all':
        o.mode = 'all';
        break;
      case '--once':
      case '--snapshot':
        if (o.mode !== 'all') o.mode = 'once';
        break;
      case '--json':
        o.json = true;
        if (o.mode !== 'all') o.mode = 'once';
        break;
      case '--compact':
        o.compact = true;
        break;
      case '--no-color':
        useColor = false;
        break;
      case '--include-subagents':
        o.includeSubagents = true;
        break;
      case '-i':
      case '--interval':
        o.interval = Math.max(100, Number(next()) || 500);
        break;
      case '-d':
      case '--days':
        o.days = Math.max(1, Number(next()) || 1);
        break;
      case '-f':
      case '--file':
        o.file = next();
        break;
      case '-s':
      case '--session':
        o.session = next();
        break;
      case '--alert-below':
        o.alertBelow = Number(next());
        break;
      case '--log':
        o.log = next();
        break;
      default:
        if (a.startsWith('-')) {
          process.stderr.write('未知参数：' + a + '\n');
          process.exit(2);
        }
    }
  }
  return o;
}

// ---------------------------------------------------------------- 基础工具

const fmt = (n) => (n == null ? '-' : Math.round(n).toLocaleString('en-US'));

function short(n) {
  if (n == null) return '-';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

const rate = (u) => (u && u.input > 0 ? (100 * u.cached) / u.input : null);

function colorRate(r, text) {
  if (r == null || !useColor) return text;
  return r >= 90 ? c.green(text) : r >= 60 ? c.yellow(text) : c.red(text);
}

function rateText(r) {
  return colorRate(r, r == null ? '-' : r.toFixed(1) + '%');
}

function truncate(s, n) {
  if (!s) return '';
  return s.length <= n ? s : '…' + s.slice(s.length - n + 1);
}

function bar(pct, width) {
  const filled = Math.max(0, Math.min(width, Math.round((pct / 100) * width)));
  const head = '█'.repeat(filled);
  const tail = '░'.repeat(width - filled);
  if (!useColor) return head + tail;
  return (pct >= 85 ? c.red(head) : pct >= 60 ? c.yellow(head) : c.cyan(head)) + c.dim(tail);
}

const BARS = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];

function sparkline(values) {
  if (!values.length) return c.dim('-');
  return values.map((v) => BARS[Math.max(0, Math.min(7, Math.floor(v / 12.5)))]).join('');
}

function hhmmss(ts) {
  const d = new Date(ts);
  const p = (x) => String(x).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
    p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}

function ago(ts) {
  if (!ts) return '-';
  const s = (Date.now() - ts) / 1000;
  if (s < 1.5) return '刚刚';
  if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + ' 秒前';
  if (s < 3600) return Math.round(s / 60) + ' 分钟前';
  return (s / 3600).toFixed(1) + ' 小时前';
}

// ---------------------------------------------------------------- 会话发现

function codexHome() {
  return process.env.CODEX_HOME || path.join(os.homedir(), '.codex');
}

function sessionsRoot() {
  return path.join(codexHome(), 'sessions');
}

/** 递归收集 rollout-*.jsonl（限制深度，避免异常目录拖慢扫描） */
function listSessionFiles(root = sessionsRoot(), maxDepth = 4) {
  const out = [];
  const walk = (dir, depth) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (depth < maxDepth) walk(full, depth + 1);
      } else if (e.isFile() && e.name.startsWith('rollout-') && e.name.endsWith('.jsonl')) {
        try {
          const st = fs.statSync(full);
          out.push({ path: full, mtime: st.mtimeMs, size: st.size });
        } catch {
          /* 文件刚被轮转，忽略 */
        }
      }
    }
  };
  walk(root, 0);
  out.sort((a, b) => b.mtime - a.mtime);
  return out;
}

/** 只读文件头部解析会话元数据（避开巨大的 base_instructions 字段） */
function sessionMeta(file) {
  let head = '';
  try {
    const fd = fs.openSync(file, 'r');
    const b = Buffer.alloc(16384);
    const n = fs.readSync(fd, b, 0, b.length, 0);
    fs.closeSync(fd);
    head = b.toString('utf8', 0, n);
  } catch {
    return {};
  }
  const pick = (re) => {
    const m = head.match(re);
    return m ? m[1] : null;
  };
  const meta = {
    id: pick(/"id":"([^"]+)"/),
    cwd: pick(/"cwd":"((?:[^"\\]|\\.)*)"/),
    source: pick(/"source":"([^"]+)"/),
    subagent: pick(/"subagent":\{"other":"([^"]+)"\}/),
    threadSource: pick(/"thread_source":"([^"]+)"/),
    originator: pick(/"originator":"([^"]+)"/),
    provider: pick(/"model_provider":"([^"]+)"/),
  };
  meta.isUserThread = !meta.threadSource || meta.threadSource === 'user';
  meta.threadLabel = meta.threadSource || (meta.subagent ? meta.subagent : 'user');
  return meta;
}

/** 选择要跟随的会话：默认只挑用户自己的会话，可用 --include-subagents 放开 */
function pickSession(opts) {
  if (opts.file) return path.resolve(opts.file);
  const files = listSessionFiles();
  if (!files.length) return null;
  const cache = new Map();
  const metaOf = (p) => {
    if (!cache.has(p)) cache.set(p, sessionMeta(p));
    return cache.get(p);
  };
  if (opts.session) {
    const hit = files.find((f) => {
      const m = metaOf(f.path);
      return (m.id && m.id.startsWith(opts.session)) || f.path.includes(opts.session);
    });
    return hit ? hit.path : null;
  }
  const pool = opts.includeSubagents ? files : files.filter((f) => metaOf(f.path).isUserThread);
  return (pool[0] || files[0]).path;
}

/** 读取文件尾部若干字节并按行切分（避免整文件读入） */
function readTailLines(file, maxBytes = 1 << 21) {
  const st = fs.statSync(file);
  const start = Math.max(0, st.size - maxBytes);
  const len = st.size - start;
  if (len <= 0) return [];
  const buf = Buffer.alloc(len);
  const fd = fs.openSync(file, 'r');
  try {
    fs.readSync(fd, buf, 0, len, start);
  } finally {
    fs.closeSync(fd);
  }
  let text = buf.toString('utf8');
  if (start > 0) {
    const nl = text.indexOf('\n');
    text = nl >= 0 ? text.slice(nl + 1) : '';
  }
  return text.split('\n').filter((l) => l.trim());
}

// ---------------------------------------------------------------- 状态模型

function newState(file) {
  return {
    file: file || null,
    sessionId: null,
    cwd: null,
    cliVersion: null,
    provider: null,
    originator: null,
    source: null,
    threadSource: null,
    model: null,
    contextWindow: null,
    rateLimits: null,
    lastCall: null, // 最近一次模型调用
    lastTurn: null, // 本轮累计
    thread: null, // 会话累计
    calls: 0,
    hitHistory: [],
    recentCalls: [],
    lastEventAt: null,
    alerts: 0,
    startedAt: Date.now(),
  };
}

function normUsage(u) {
  if (!u) return null;
  return {
    input: u.input_tokens ?? 0,
    cached: u.cached_input_tokens ?? 0,
    cacheWrite: u.cache_write_input_tokens ?? 0,
    output: u.output_tokens ?? 0,
    reasoning: u.reasoning_output_tokens ?? 0,
    total: u.total_tokens ?? 0,
  };
}

/** 把一条 rollout 记录并入状态；返回状态是否发生变化 */
function ingest(st, rec) {
  const p = rec && rec.payload;
  if (!p || typeof p !== 'object') return false;
  const type = p.type || rec.type;
  const ts = rec.timestamp ? Date.parse(rec.timestamp) || Date.now() : Date.now();

  switch (type) {
    case 'session_meta':
      st.sessionId = p.id ?? st.sessionId;
      st.cwd = p.cwd ?? st.cwd;
      st.cliVersion = p.cli_version ?? st.cliVersion;
      st.provider = p.model_provider ?? st.provider;
      st.originator = p.originator ?? st.originator;
      st.source = typeof p.source === 'string' ? p.source : st.source;
      st.threadSource = p.thread_source ?? st.threadSource;
      return true;

    case 'turn_context':
      st.model = p.model ?? (p.collaboration_mode && p.collaboration_mode.settings
        ? p.collaboration_mode.settings.model
        : null) ?? st.model;
      st.cwd = p.cwd ?? st.cwd;
      return true;

    case 'token_usage_record': {
      const call = normUsage(p.usage);
      if (!call) return false;
      st.calls += 1;
      st.lastCall = call;
      st.lastTurn = normUsage(p.turn_token_usage) || st.lastTurn;
      st.thread = normUsage(p.thread_token_usage) || st.thread;
      const r = rate(call);
      if (r != null) {
        st.hitHistory.push(r);
        if (st.hitHistory.length > 240) st.hitHistory.shift();
      }
      st.recentCalls.push(Object.assign({ ts, hit_rate: r }, call));
      if (st.recentCalls.length > 60) st.recentCalls.shift();
      st.lastEventAt = ts;
      return true;
    }

    case 'token_count':
      if (p.info && p.info.model_context_window) st.contextWindow = p.info.model_context_window;
      if (p.info && p.info.total_token_usage && !st.thread) {
        st.thread = normUsage(p.info.total_token_usage);
      }
      if (p.rate_limits) st.rateLimits = p.rate_limits;
      return true;

    default:
      return false;
  }
}

/** 从会话文件重建完整状态（文件通常几 MB，直接全量读；超大文件退化为读尾部） */
function stateFromFile(file, maxBytes = 64 << 20) {
  const st = newState(file);
  let size = 0;
  try {
    size = fs.statSync(file).size;
  } catch {
    return st;
  }
  const lines = size <= maxBytes ? fs.readFileSync(file, 'utf8').split('\n') : readTailLines(file);
  for (const line of lines) {
    if (!line.trim()) continue;
    let rec;
    try {
      rec = JSON.parse(line);
    } catch {
      continue;
    }
    ingest(st, rec);
  }
  return st;
}

// ---------------------------------------------------------------- 渲染

function rateLimitLines(rl) {
  if (!rl) return [c.dim('额度      ') + c.dim('当前 provider 未返回额度信息')];
  const parts = [];
  const show = (label, w) => {
    if (!w) return;
    const used = w.used_percent;
    const txt = used == null ? '未知' : (100 - used).toFixed(0) + '% 剩余（已用 ' + used.toFixed(0) + '%）';
    let when = '';
    if (w.resets_at) {
      const d = typeof w.resets_at === 'number' ? new Date(w.resets_at * 1000) : new Date(w.resets_at);
      if (!Number.isNaN(d.getTime())) when = ' · 重置 ' + hhmmss(d.getTime()).slice(5, 16);
    }
    parts.push(label + ' ' + txt + when);
  };
  show('5 小时', rl.primary);
  show('每周', rl.secondary);
  if (!parts.length) {
    parts.push('未启用额度限制');
    if (rl.plan_type) parts.push('方案 ' + rl.plan_type);
  }
  return [c.dim('额度      ') + parts.join(c.dim('  |  '))];
}

function usageLine(label, u) {
  if (!u) return c.dim(label.padEnd(10) + '暂无数据');
  const body =
    'in ' + fmt(u.input).padStart(11) +
    '  cached ' + fmt(u.cached).padStart(11) + ' (' + rateText(rate(u)) + ')  ' +
    'out ' + fmt(u.output).padStart(8);
  return c.dim(label.padEnd(10)) + body;
}

function renderLive(st, opts) {
  const lines = [];
  const W = Math.max(60, Math.min(process.stdout.columns || 100, 120));

  lines.push(c.bold(c.cyan('Codex 用量实时监控')) + '  ' + c.dim(hhmmss(Date.now())));
  lines.push(c.dim('─'.repeat(W)));

  const sid = st.sessionId ? st.sessionId.slice(0, 8) + '…' : path.basename(st.file || '?').slice(0, 12);
  lines.push(
    c.dim('会话 ') + sid + '  ' + c.dim('模型 ') + (st.model || '-') +
      (st.provider ? c.dim(' (' + st.provider + ')') : '') +
      '  ' + c.dim('目录 ') + truncate(st.cwd, 26),
  );
  lines.push(
    c.dim(
      '来源 ' + ((st.originator || st.source || '-') + (st.threadSource ? ' · ' + st.threadSource : '')) +
        ' · CLI ' + (st.cliVersion || '-') + ' · ' + path.basename(st.file || ''),
    ),
  );
  lines.push(c.dim('─'.repeat(W)));

  if (st.lastCall && st.contextWindow) {
    const pct = (100 * st.lastCall.input) / st.contextWindow;
    lines.push(
      c.dim('上下文 ') + bar(pct, 24) + ' ' + pct.toFixed(1) + '%  ' +
        c.dim(short(st.lastCall.input) + ' / ' + short(st.contextWindow)),
    );
  } else {
    lines.push(c.dim('上下文  等待第一次调用…'));
  }
  lines.push(c.dim('─'.repeat(W)));

  lines.push(usageLine('本次调用', st.lastCall));
  lines.push(usageLine('本轮累计', st.lastTurn));
  lines.push(usageLine('会话累计', st.thread));

  if (st.thread) {
    const miss = Math.max(0, st.thread.input - st.thread.cached);
    lines.push(
      c.dim('          total ' + fmt(st.thread.total) + '   未缓存 ' + fmt(miss) +
        '   cache_write ' + fmt(st.thread.cacheWrite) + '   调用 ' + st.calls + ' 次'),
    );
  }
  lines.push(c.dim('─'.repeat(W)));

  const recent = st.hitHistory.slice(-24);
  const avg = recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null;
  lines.push(
    c.dim('命中趋势 ') + sparkline(recent) + '  ' +
      c.dim('近 ' + recent.length + ' 次调用   平均 ' + (avg == null ? '-' : avg.toFixed(1) + '%')) +
      (opts.alertBelow != null ? c.dim('   阈值 ' + opts.alertBelow + '%') : ''),
  );
  for (const l of rateLimitLines(st.rateLimits)) lines.push(l);
  lines.push(c.dim('─'.repeat(W)));

  const active = Date.now() - (st.lastEventAt || 0) < 3000;
  lines.push(
    (active ? c.green('● 进行中') : c.dim('○ 空闲')) +
      c.dim('   最后一次用量 ' + ago(st.lastEventAt) + '   ' + opts.interval + 'ms 刷新   Ctrl-C 退出'),
  );
  return lines.join('\n');
}

function renderSnapshot(st) {
  const L = [];
  L.push('Codex 用量快照 · ' + hhmmss(Date.now()));
  L.push('会话    ' + (st.sessionId || '-'));
  L.push('模型    ' + (st.model || '-') + (st.provider ? ' (' + st.provider + ')' : '') +
    '    目录 ' + (st.cwd || '-'));
  L.push('来源    ' + (st.originator || st.source || '-') +
    (st.threadSource ? ' · ' + st.threadSource : '') + '    CLI ' + (st.cliVersion || '-'));
  if (st.lastCall && st.contextWindow) {
    L.push('上下文  ' + fmt(st.lastCall.input) + ' / ' + fmt(st.contextWindow) +
      ' (' + ((100 * st.lastCall.input) / st.contextWindow).toFixed(1) + '%)');
  }
  const line = (label, u) => {
    if (!u) return L.push(label + '  暂无数据');
    L.push(label + '  in ' + fmt(u.input) + '  cached ' + fmt(u.cached) + ' (' + rateText(rate(u)) + ')  ' +
      'out ' + fmt(u.output) + (u.reasoning ? ' (reasoning ' + fmt(u.reasoning) + ')' : ''));
  };
  line('本次调用', st.lastCall);
  line('本轮累计', st.lastTurn);
  line('会话累计', st.thread);
  if (st.thread) {
    L.push('合计      total ' + fmt(st.thread.total) +
      '  未缓存 ' + fmt(Math.max(0, st.thread.input - st.thread.cached)) +
      '  cache_write ' + fmt(st.thread.cacheWrite) + '  调用 ' + st.calls + ' 次');
  }
  const recent = st.hitHistory.slice(-24);
  const avg = recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null;
  L.push('命中趋势  ' + sparkline(recent) + '  平均 ' + (avg == null ? '-' : avg.toFixed(1) + '%'));
  for (const l of rateLimitLines(st.rateLimits)) L.push(l);
  L.push('数据文件  ' + (st.file || '-'));
  return L.join('\n');
}

function renderCompact(st) {
  const r = st.thread ? rate(st.thread) : null;
  const pct = st.lastCall && st.contextWindow
    ? ' ctx=' + ((100 * st.lastCall.input) / st.contextWindow).toFixed(1) + '%'
    : '';
  return 'in=' + fmt(st.thread && st.thread.input) +
    ' cached=' + fmt(st.thread && st.thread.cached) +
    ' hit=' + (r == null ? '-' : r.toFixed(1) + '%') +
    ' out=' + fmt(st.thread && st.thread.output) + pct + ' calls=' + st.calls;
}

function toJSON(st) {
  const pack = (u) => (u ? Object.assign({}, u, { hit_rate: rate(u) }) : null);
  return {
    file: st.file,
    session_id: st.sessionId,
    cwd: st.cwd,
    model: st.model,
    model_provider: st.provider,
    originator: st.originator,
    source: st.source,
    thread_source: st.threadSource,
    cli_version: st.cliVersion,
    context_window: st.contextWindow,
    context_used: st.lastCall ? st.lastCall.input : null,
    context_percent: st.lastCall && st.contextWindow
      ? (100 * st.lastCall.input) / st.contextWindow
      : null,
    calls: st.calls,
    hit_history: st.hitHistory,
    recent_calls: st.recentCalls,
    last_call: pack(st.lastCall),
    last_turn: pack(st.lastTurn),
    thread: pack(st.thread),
    rate_limits: st.rateLimits || null,
    last_event_at: st.lastEventAt ? new Date(st.lastEventAt).toISOString() : null,
    updated_at: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------- 多会话总览

function renderAll(opts) {
  const cutoff = Date.now() - opts.days * 86400000;
  const files = listSessionFiles().filter((f) => f.mtime >= cutoff);
  if (!files.length) return '没有找到会话记录。';

  const rows = files.map((f) => {
    let st;
    let meta = {};
    try {
      st = stateFromFile(f.path);
      meta = sessionMeta(f.path);
    } catch {
      st = newState(f.path);
    }
    return { f, st, meta };
  });

  const L = [];
  L.push(c.bold('Codex 会话总览 · 最近 ' + opts.days + ' 天 · ' + rows.length + ' 个会话'));
  L.push('');
  L.push(c.dim(
    '时间           ' + '会话      ' + '线程            ' + '模型            ' +
    '目录               ' + '累计 input'.padStart(13) + '  cached'.padStart(13) +
    '   命中率' + '  output'.padStart(10),
  ));
  L.push(c.dim('─'.repeat(126)));
  for (const { f, st, meta } of rows) {
    const active = Date.now() - f.mtime < 120000;
    const r = st.thread ? rate(st.thread) : null;
    const hitPlain = (r == null ? '-' : r.toFixed(1) + '%').padStart(9);
    L.push(
      (active ? c.green('● ') : '  ') +
      hhmmss(f.mtime).slice(5, 16) + '  ' +
      (st.sessionId ? st.sessionId.slice(0, 8) : path.basename(f.path).slice(9, 17)) + '  ' +
      (meta.threadLabel || 'user').padEnd(15).slice(0, 15) + ' ' +
      (st.model || '-').padEnd(15).slice(0, 15) + ' ' +
      truncate(st.cwd || '-', 18).padEnd(18) + ' ' +
      fmt(st.thread && st.thread.input).padStart(13) + '  ' +
      fmt(st.thread && st.thread.cached).padStart(11) + '  ' +
      colorRate(r, hitPlain) + '  ' +
      fmt(st.thread && st.thread.output).padStart(8),
    );
  }
  L.push('');
  const tot = rows.reduce((a, row) => {
    if (row.st.thread) {
      a.input += row.st.thread.input;
      a.cached += row.st.thread.cached;
      a.output += row.st.thread.output;
    }
    return a;
  }, { input: 0, cached: 0, output: 0 });
  L.push(c.bold(
    '合计：input ' + fmt(tot.input) + '   cached ' + fmt(tot.cached) +
    '   整体命中率 ' + (tot.input ? ((100 * tot.cached) / tot.input).toFixed(1) + '%' : '-') +
    '   output ' + fmt(tot.output),
  ));
  L.push('');
  L.push(c.dim('说明：● 表示最近 2 分钟内有活动；“线程”列 user 是你的会话，guardian_review 等是子代理会话。'));
  L.push(c.dim('      每个会话的“累计 input”是该会话所有轮次重放上下文之和，不等于当前上下文大小。'));
  return L.join('\n');
}

// ---------------------------------------------------------------- 实时跟随

function follow(opts) {
  let st = null;
  let fd = null;
  let offset = 0;
  let buf = '';
  let dirty = true;
  let lastDraw = 0;
  let lastScan = 0;
  let scanning = false;
  let primed = false; // 首次接入时的历史回填不记账

  const info = (m) => {
    process.stdout.write('\r\n' + c.dim(m) + '\r\n');
  };

  function closeFd() {
    if (fd != null) {
      try {
        fs.closeSync(fd);
      } catch {
        /* ignore */
      }
      fd = null;
    }
  }

  function attach(file) {
    closeFd();
    st = newState(file);
    buf = '';
    offset = 0;
    primed = false;
    try {
      fd = fs.openSync(file, 'r');
    } catch (e) {
      info('无法打开 ' + file + '：' + e.message);
      return;
    }
    // 初次接入先读最近 2MB 建立初始状态
    const info2 = fs.fstatSync(fd);
    offset = Math.max(0, info2.size - (1 << 21));
    dirty = true;
  }

  function pump() {
    if (fd == null) return;
    const isInitial = !primed;
    let size;
    try {
      size = fs.fstatSync(fd).size;
    } catch {
      return;
    }
    if (size <= offset) return;
    const len = size - offset;
    const b = Buffer.alloc(len);
    try {
      fs.readSync(fd, b, 0, len, offset);
    } catch {
      return;
    }
    offset = size;
    buf += b.toString('utf8');
    const parts = buf.split('\n');
    buf = parts.pop() ?? '';
    for (const line of parts) {
      if (!line.trim()) continue;
      let rec;
      try {
        rec = JSON.parse(line);
      } catch {
        continue;
      }
      const before = st.calls;
      if (!ingest(st, rec)) continue;
      dirty = true;
      if (st.calls > before && !isInitial) {
        const r = rate(st.lastCall);
        if (opts.alertBelow != null && r != null && r < opts.alertBelow) {
          st.alerts += 1;
          process.stdout.write('\x07');
        }
        if (opts.log) {
          try {
            fs.appendFileSync(
              opts.log,
              JSON.stringify({
                ts: new Date().toISOString(),
                session: st.sessionId,
                model: st.model,
                call: st.lastCall,
                hit_rate: r,
                thread: st.thread,
              }) + '\n',
            );
          } catch {
            /* 日志写失败不影响主流程 */
          }
        }
      }
    }
    primed = true;
  }

  /** 自动跟随最近活动的用户会话（每 5 秒重新扫描一次） */
  function rescan(force) {
    if (opts.file) return;
    const now = Date.now();
    if (!force && now - lastScan < 5000) return;
    lastScan = now;
    if (scanning) return;
    scanning = true;
    try {
      const newest = pickSession(opts);
      if (newest && (!st || newest !== st.file)) {
        attach(newest);
        info('跟随会话 ' + path.basename(newest));
      }
    } catch {
      /* ignore */
    } finally {
      scanning = false;
    }
  }

  function draw() {
    if (!dirty) return;
    dirty = false;
    lastDraw = Date.now();
    process.stdout.write(ESC + 'H' + ESC + '2J' + renderLive(st, opts));
  }

  if (opts.file) {
    const target = path.resolve(opts.file);
    attach(target);
    if (fd == null) {
      process.stderr.write('无法打开会话文件：' + target + '\n');
      process.exit(1);
    }
  }
  else {
    rescan(true);
    if (!st) info('没有找到任何会话记录，等待会话创建…');
  }
  if (fd != null) pump();
  process.stdout.write(ESC + '?25l');
  draw();

  const timer = setInterval(() => {
    pump();
    rescan(false);
    if (Date.now() - lastDraw > 1000) dirty = true;
    draw();
  }, opts.interval);

  const cleanup = () => {
    clearInterval(timer);
    closeFd();
    process.stdout.write(ESC + '?25h' + '\n');
    process.exit(0);
  };
  process.on('SIGINT', cleanup);
  process.on('SIGTERM', cleanup);
}

// ---------------------------------------------------------------- 入口

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.mode === 'help') {
    process.stdout.write(HELP);
    return;
  }
  if (opts.mode === 'all') {
    process.stdout.write(renderAll(opts) + '\n');
    return;
  }

  const file = pickSession(opts);
  if (!file) {
    process.stderr.write(
      '没有找到会话记录（查找路径：' + sessionsRoot() + '）' +
        (opts.session ? '，也没有匹配 --session ' + opts.session + ' 的会话' : '') + '\n',
    );
    process.exit(1);
  }

  if (!fs.existsSync(file)) {
    process.stderr.write('无法打开会话文件：' + file + '\n');
    process.exit(1);
  }

  if (opts.mode === 'once' || !process.stdout.isTTY) {
    const st = stateFromFile(file);
    if (opts.json) process.stdout.write(JSON.stringify(toJSON(st), null, 2) + '\n');
    else if (opts.compact) process.stdout.write(renderCompact(st) + '\n');
    else process.stdout.write(renderSnapshot(st) + '\n');
    return;
  }

  follow(opts);
}

// 直接执行时跑命令行界面；被 import 时只导出函数，供看板服务复用
const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : '';
if (import.meta.url === invokedPath) {
  try {
    main();
  } catch (e) {
    process.stderr.write('codex-usage 出错：' + (e && e.message ? e.message : e) + '\n');
    process.exit(1);
  }
}

export {
  pickSession,
  stateFromFile,
  listSessionFiles,
  sessionMeta,
  toJSON,
  newState,
  rate,
  sessionsRoot,
};
