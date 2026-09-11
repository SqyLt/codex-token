#!/usr/bin/env node
// codex-usage 看板服务
// 只监听 127.0.0.1，把本机会话数据渲染成可实时刷新的网页（默认 1 秒一次）
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  pickSession,
  stateFromFile,
  listSessionFiles,
  sessionMeta,
  toJSON,
  newState,
  rate,
  sessionsRoot,
} from './codex-usage.mjs';

const PORT = Number(process.env.CODEX_USAGE_PORT || 8788);
const DAYS = Number(process.env.CODEX_USAGE_DAYS || 1);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PAGE = fs.readFileSync(path.join(HERE, 'dashboard.html'), 'utf8');

// 按 (文件, 大小, mtime) 缓存解析结果，避免每次轮询都重新解析
const stateCache = new Map();
function getState(file, maxBytes = 64 << 20) {
  try {
    const s = fs.statSync(file);
    const key = maxBytes + ':' + s.size + ':' + s.mtimeMs;
    const hit = stateCache.get(file);
    if (hit && hit.key === key) return hit.st;
    const st = stateFromFile(file, maxBytes);
    stateCache.set(file, { key, st, at: Date.now() });
    if (stateCache.size > 200) {
      for (const [k, v] of stateCache) {
        if (Date.now() - v.at > 600000) stateCache.delete(k);
      }
    }
    return st;
  } catch {
    return newState(file);
  }
}

function sessionRows(days) {
  const cutoff = Date.now() - days * 86400000;
  return listSessionFiles()
    .filter((f) => f.mtime >= cutoff)
    .map((f) => {
      const meta = sessionMeta(f.path);
      const st = getState(f.path, 1 << 20);
      return {
        file: f.path,
        id: meta.id || st.sessionId || path.basename(f.path),
        thread: meta.threadLabel || 'user',
        model: st.model || null,
        cwd: st.cwd || meta.cwd || null,
        originator: meta.originator || null,
        mtime: f.mtime,
        active: Date.now() - f.mtime < 120000,
        input: st.thread ? st.thread.input : 0,
        cached: st.thread ? st.thread.cached : 0,
        output: st.thread ? st.thread.output : 0,
        hit: st.thread ? rate(st.thread) : null,
      };
    });
}

function resolveSelection(wanted) {
  if (wanted) {
    if (wanted.startsWith('/') || wanted.endsWith('.jsonl')) return path.resolve(wanted);
    const hit = pickSession({ file: null, session: wanted, includeSubagents: true });
    if (hit) return hit;
  }
  return pickSession({ file: null, session: null, includeSubagents: false });
}

function buildSnapshot(query) {
  const days = Number(query.get('days') || DAYS);
  const file = resolveSelection(query.get('session'));
  if (!file) return { error: 'no-session', data_dir: sessionsRoot() };
  const st = getState(file, 64 << 20);
  const usage = toJSON(st);
  return {
    now: Date.now(),
    days,
    data_dir: sessionsRoot(),
    usage: usage,
    hit_history: st.hitHistory,
    recent_calls: st.recentCalls,
    sessions: sessionRows(days),
  };
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  if (url.pathname === '/' || url.pathname === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(PAGE);
    return;
  }
  if (url.pathname === '/api/snapshot') {
    let body;
    try {
      body = JSON.stringify(buildSnapshot(url.searchParams));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: String((e && e.message) || e) }));
      return;
    }
    res.writeHead(200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    res.end(body);
    return;
  }
  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    res.end('ok\n');
    return;
  }
  res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('not found\n');
});

server.on('error', (e) => {
  if (e.code === 'EADDRINUSE') {
    process.stderr.write(
      '端口 ' + PORT + ' 已被占用，可能看板已经在运行。' +
        '用 CODEX_USAGE_PORT 换个端口即可。\n',
    );
  } else {
    process.stderr.write('看板服务启动失败：' + e.message + '\n');
  }
  process.exit(1);
});

server.listen(PORT, '127.0.0.1', () => {
  process.stdout.write('codex-usage 看板已启动： http://127.0.0.1:' + PORT + '/\n');
  process.stdout.write('数据目录： ' + sessionsRoot() + '\n');
});
