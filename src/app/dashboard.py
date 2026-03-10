from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from flask import Flask, jsonify, redirect, render_template_string, request, url_for

from app.ai.openai_client import OpenAIChatClient
from app.config import OpenAIModelConfig

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_CANDIDATES = [
    ROOT_DIR / "config.realrun.local.yaml",
    ROOT_DIR / "config.realrun.force.yaml",
    ROOT_DIR / "config.example.yaml",
]
DEFAULT_CONFIG = next((path for path in DEFAULT_CONFIG_CANDIDATES if path.exists()), DEFAULT_CONFIG_CANDIDATES[0])

app = Flask(__name__)
_STATE: Dict[str, Any] = {"proc": None}


HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>KS Auto Commenter 控制台</title>
  <style>
    :root {
      --bg: #0b1220;
      --panel: #111b30;
      --panel-soft: #1a2742;
      --line: #2c3f62;
      --text: #e9eefc;
      --muted: #9caed0;
      --ok: #22c55e;
      --warn: #f59e0b;
      --danger: #ef4444;
      --accent: #60a5fa;
      --info: #38bdf8;
    }

    * { box-sizing: border-box; }

    html, body {
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
    }

    body {
      margin: 0;
      padding: clamp(10px, 1.6vw, 20px);
      color: var(--text);
      font-family: Inter, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: radial-gradient(1200px 700px at 10% -10%, #1e3a8a 0%, transparent 42%),
                  radial-gradient(1200px 700px at 100% -15%, #3b0764 0%, transparent 38%),
                  var(--bg);
    }

    .wrap {
      width: min(100%, 1280px);
      margin: 0 auto;
      display: grid;
      gap: 12px;
      min-width: 0;
      overflow-x: visible;
    }

    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      overflow: hidden;
      min-width: 0;
    }

    .topbar {
      padding: 14px 16px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 10px;
    }

    .topbar-left {
      min-width: 0;
    }

    .topbar-right {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-start;
      min-width: 0;
    }

    .title-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      min-width: 0;
    }

    #configPath {
      display: inline;
      word-break: break-all;
      overflow-wrap: anywhere;
    }

    h1 { margin: 0; font-size: clamp(18px, 2vw, 22px); }
    .muted { color: var(--muted); font-size: 12px; }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      background: rgba(255,255,255,0.03);
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--warn);
    }
    .dot.ok { background: var(--ok); }

    .alert-banner {
      border-radius: 12px;
      border: 1px solid var(--line);
      padding: 12px 14px;
      display: grid;
      gap: 4px;
    }

    .alert-info { background: rgba(56,189,248,0.09); border-color: rgba(56,189,248,0.4); }
    .alert-warn { background: rgba(245,158,11,0.10); border-color: rgba(245,158,11,0.45); }
    .alert-error { background: rgba(239,68,68,0.11); border-color: rgba(239,68,68,0.5); }

    .alert-title {
      font-size: clamp(16px, 2.1vw, 22px);
      font-weight: 800;
      letter-spacing: .2px;
    }

    .stats {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }

    .stat {
      min-height: 90px;
      min-width: 0;
      padding: 12px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }

    .stat .k { color: var(--muted); font-size: 12px; }
    .stat .v {
      font-size: clamp(18px, 2.2vw, 28px);
      font-weight: 800;
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.15;
    }

    .layout {
      display: block;
      min-width: 0;
    }

    .layout > * {
      min-width: 0;
    }

    .layout > * + * {
      margin-top: 12px;
    }

    .section-title {
      margin: 0;
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
      font-size: 14px;
      font-weight: 700;
    }

    .panel-body {
      padding: 12px 14px;
      min-width: 0;
    }

    label {
      display: block;
      margin: 10px 0 4px;
      font-size: 13px;
      font-weight: 600;
    }

    input[type=text], input[type=number], textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 10px;
      color: var(--text);
      background: var(--panel-soft);
      outline: none;
      max-width: 100%;
    }

    input[type=text]:focus, input[type=number]:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(96,165,250,0.2);
    }

    textarea { min-height: 84px; resize: vertical; }

    .check {
      display: flex;
      gap: 8px;
      align-items: center;
      margin: 8px 0 2px;
      font-size: 13px;
    }

    .buttons { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

    button, .btn {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px 12px;
      background: linear-gradient(180deg, #1f3b75, #1a3263);
      color: var(--text);
      cursor: pointer;
      text-decoration: none;
      font-size: 13px;
      white-space: nowrap;
      max-width: 100%;
    }

    .btn.secondary { background: rgba(255,255,255,0.03); }
    button:hover, .btn:hover { filter: brightness(1.08); }

    .hidden { display: none !important; }

    .settings-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .settings-close {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.03);
      color: var(--text);
      padding: 5px 10px;
      cursor: pointer;
      font-size: 12px;
    }

    .test-log {
      margin: 8px 0 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      min-height: 54px;
      max-height: 220px;
      overflow: auto;
      background: #0a1224;
      color: #d6e4ff;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .switch-row {
      margin: 10px 0 4px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: start;
      gap: 12px;
      font-size: 13px;
      font-weight: 600;
    }

    .switch-row span {
      min-width: 0;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }

    .switch {
      position: relative;
      display: inline-block;
      width: 48px;
      height: 28px;
      flex: 0 0 auto;
    }

    .switch-input {
      opacity: 0;
      width: 0;
      height: 0;
      position: absolute;
    }

    .switch-slider {
      position: absolute;
      inset: 0;
      border-radius: 999px;
      background: #36455f;
      transition: .2s;
      cursor: pointer;
      border: 1px solid rgba(255,255,255,0.18);
    }

    .switch-slider:before {
      content: '';
      position: absolute;
      width: 22px;
      height: 22px;
      left: 2px;
      top: 2px;
      border-radius: 50%;
      background: #fff;
      transition: .2s;
    }

    .switch-input:checked + .switch-slider {
      background: #2563eb;
      border-color: rgba(96,165,250,0.8);
    }

    .switch-input:checked + .switch-slider:before {
      transform: translateX(20px);
    }

    .control-panel .panel-body {
      display: grid;
      gap: 10px;
    }

    .control-buttons {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }

    .control-inline-form { margin: 0; }

    .control-btn {
      border-radius: 10px;
      padding: 10px 18px;
      font-size: 15px;
      font-weight: 700;
      border: 1px solid var(--line);
      color: var(--text);
      background: linear-gradient(180deg, #1e40af, #1d4ed8);
    }

    .control-btn.stop {
      background: linear-gradient(180deg, #b91c1c, #dc2626);
      border-color: rgba(239,68,68,0.7);
    }

    .tables {
      display: grid;
      gap: 12px;
      grid-template-columns: 1fr;
      width: 100%;
      min-width: 0;
    }

    .tables > .card { min-width: 0; }

    .table-wrap {
      max-height: 360px;
      overflow-x: auto;
      overflow-y: auto;
      width: 100%;
      min-width: 0;
    }

    table {
      width: 100%;
      min-width: 0;
      border-collapse: collapse;
      table-layout: fixed;
    }

    thead th {
      text-align: left;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      border-bottom: 1px solid var(--line);
      padding: 8px 6px;
      position: sticky;
      top: 0;
      background: #131f37;
      z-index: 2;
    }

    tbody td {
      padding: 8px 6px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      font-size: 12px;
      vertical-align: top;
      word-break: break-word;
      overflow-wrap: anywhere;
    }

    .log-card { margin-top: 12px; }
    pre {
      margin: 0;
      padding: 12px;
      max-height: 420px;
      overflow: auto;
      background: #0a1224;
      color: #d6e4ff;
      font-size: 12px;
      line-height: 1.45;
    }

    .row-inline {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      padding: 10px 12px;
      border-top: 1px solid var(--line);
    }

    .txt-ok { color: var(--ok); }
    .txt-warn { color: var(--warn); }
    .txt-danger { color: var(--danger); }

    .modal {
      position: fixed;
      inset: 0;
      display: none;
      justify-content: center;
      align-items: center;
      background: rgba(6, 11, 20, 0.58);
      z-index: 9999;
      padding: 16px;
    }

    .modal.open { display: flex; }

    .modal-card {
      width: min(720px, 100%);
      border: 1px solid rgba(239,68,68,0.55);
      border-radius: 14px;
      background: #1a1220;
      box-shadow: 0 20px 60px rgba(0,0,0,.45);
      overflow: hidden;
    }

    .modal-head {
      padding: 14px 16px;
      background: rgba(239,68,68,0.16);
      border-bottom: 1px solid rgba(239,68,68,0.45);
      font-size: clamp(18px, 2.2vw, 28px);
      font-weight: 900;
      color: #ffd7dc;
    }

    .modal-body { padding: 14px 16px; font-size: 14px; line-height: 1.55; color: #ffe8ea; }
    .modal-actions { padding: 10px 16px 14px; display: flex; justify-content: flex-end; }

    @media (max-width: 1360px) {
      .stats { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
      .tables { grid-template-columns: 1fr; }
    }

    @media (max-width: 980px) {
      .topbar { grid-template-columns: 1fr; }
      .topbar-right { justify-content: flex-start; }
    }

    @media (max-width: 720px) {
      .stats { grid-template-columns: 1fr; }
      body { padding: 10px; }
      .tables { grid-template-columns: 1fr; }
      .control-buttons, .buttons { flex-direction: column; align-items: stretch; }
      .control-inline-form { width: 100%; }
      .control-btn, .btn { width: 100%; }
      button, .btn { white-space: normal; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card topbar">
      <div class="topbar-left">
        <div class="title-row">
          <h1>KS Auto Commenter 控制台</h1>
          <button type="button" class="btn secondary" id="settingsToggle">设置</button>
        </div>
        <div class="muted">配置文件：<span id="configPath">{{ config_path }}</span></div>
      </div>
      <div class="topbar-right">
        <span class="pill" id="runBadge"><span class="dot"></span><span id="runText">{{ run_status }}</span></span>
        <span class="pill">最近刷新：<span id="lastUpdated">--</span></span>
      </div>
    </div>

    <div class="card control-panel">
      <div class="panel-body">
        <div class="control-buttons">
          <form class="control-inline-form" method="post" action="{{ url_for('run_once') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn" type="submit" name="run_mode" value="once">开始任务（单轮）</button>
          </form>

          <form class="control-inline-form" method="post" action="{{ url_for('run_once') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn" type="submit" name="run_mode" value="loop">开始任务（持续）</button>
          </form>

          <form class="control-inline-form" method="post" action="{{ url_for('stop_task') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn stop" type="submit">停止任务</button>
          </form>

          <a class="btn secondary" href="{{ url_for('index', config_path=config_path) }}">手动刷新</a>
        </div>
        {% if message %}<div class="txt-ok" style="font-size:13px;">{{ message }}</div>{% endif %}
        <div class="muted">这是核心控制区：请优先使用上方“开始任务 / 停止任务”按钮。</div>
      </div>
    </div>

    <div id="alertBanner" class="alert-banner {{ initial_alert_class }}">
      <div id="alertTitle" class="alert-title">{{ initial_alert_title }}</div>
      <div id="alertMessage" class="muted">{{ initial_alert_message }}</div>
      <div id="alertHint" class="muted">{{ initial_alert_hint }}</div>
    </div>

    <div class="stats">
      <div class="card stat"><div class="k">今日评论数</div><div class="v" id="statToday">{{ initial_stats.today_comments }}</div></div>
      <div class="card stat"><div class="k">累计评论数</div><div class="v" id="statTotal">{{ initial_stats.total_comments }}</div></div>
      <div class="card stat"><div class="k">关键词历史数</div><div class="v" id="statKeyword">{{ initial_stats.keyword_history_total }}</div></div>
      <div class="card stat"><div class="k">当前阶段</div><div class="v" id="statPhase" style="font-size:15px; font-weight:700;">{{ initial_phase }}</div></div>
    </div>

    <div class="layout">
      <div class="card hidden" id="settingsPanel">
        <h3 class="section-title settings-title"><span>运行配置</span><button type="button" class="settings-close" id="settingsClose">收起</button></h3>
        <div class="panel-body">
          <form id="settingsForm" method="post" action="{{ url_for('save') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />

            <label>模型 Base URL</label>
            <input type="text" name="openai_base_url" value="{{ cfg.openai.base_url if cfg.openai else '' }}" placeholder="https://gmn.chuangzuoli.com" />

            <label>模型 ID</label>
            <input type="text" name="openai_model_id" value="{{ cfg.openai.model_id if cfg.openai else '' }}" placeholder="gpt-5.3-codex" />

            <label>API Key（保存到配置）</label>
            <input type="text" name="openai_api_key" value="{{ cfg.openai.api_key if cfg.openai else '' }}" placeholder="sk-..." />

            <div class="buttons">
              <button type="button" class="btn secondary" id="testConnectionBtn">测试连接</button>
            </div>
            <pre id="testConnectionLog" class="test-log">点击“测试连接”可查看连通结果与详细错误日志。</pre>

            <div class="switch-row">
              <span>每条帖子都重新搜索（老模式）</span>
              <label class="switch" for="search_each_post">
                <input id="search_each_post" class="switch-input" type="checkbox" name="search_each_post" {% if cfg.browser.search_each_post %}checked{% endif %} />
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="muted">关闭=同词搜索一次后连续处理帖子</div>

            <div class="switch-row">
              <span>每轮每个方向只搜1个关键词</span>
              <label class="switch" for="single_keyword_search">
                <input id="single_keyword_search" class="switch-input" type="checkbox" name="single_keyword_search" {% if cfg.runtime.single_keyword_search %}checked{% endif %} />
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="muted">开启=每轮只取 AI 扩展的第1个新词</div>

            <div class="switch-row">
              <span>关闭关键词联想（按输入词直搜）</span>
              <label class="switch" for="disable_keyword_expansion">
                <input id="disable_keyword_expansion" class="switch-input" type="checkbox" name="disable_keyword_expansion" {% if cfg.runtime.disable_keyword_expansion %}checked{% endif %} />
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="muted">开启=输入什么关键词就搜索什么关键词，不走 AI 扩词</div>

            <div class="switch-row">
              <span>严格评论判定</span>
              <label class="switch" for="strict_comment_gate">
                <input id="strict_comment_gate" class="switch-input" type="checkbox" name="strict_comment_gate" {% if cfg.ai.strict_comment_gate %}checked{% endif %} />
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="muted">关闭=AI建议跳过也继续尝试评论</div>

            <div class="switch-row">
              <span>尽量每条都评论</span>
              <label class="switch" for="comment_every_post">
                <input id="comment_every_post" class="switch-input" type="checkbox" name="comment_every_post" {% if cfg.runtime.comment_every_post %}checked{% endif %} />
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="muted">开启=候选被过滤时仍尝试兜底评论</div>

            <label>关键词扩展数量</label>
            <input type="number" min="1" max="20" name="keyword_max_count" value="{{ cfg.ai.keyword_max_count }}" />

            <label>每轮评论上限</label>
            <input type="number" min="1" max="200" name="max_comments_per_round" value="{{ cfg.runtime.max_comments_per_round }}" />

            <label>每词抓取帖子数</label>
            <input type="number" min="1" max="200" name="search_limit_per_keyword" value="{{ cfg.runtime.search_limit_per_keyword }}" />

            <label>方向词（逗号分隔）</label>
            <input type="text" name="direction_keywords" value="{{ direction_keywords }}" />

            <label>评论要求（每行一条）</label>
            <textarea name="requirements">{{ requirements_text }}</textarea>

            <div class="buttons">
              <button type="submit">保存配置</button>
            </div>
          </form>


        </div>
      </div>

      <div>
        <div class="tables">
          <div class="card">
            <h3 class="section-title">评论日志（每秒动态）</h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th style="width:108px">时间</th><th style="width:72px">关键词</th><th style="width:150px">帖子ID</th><th>评论内容</th></tr></thead>
                <tbody id="commentRows"></tbody>
              </table>
            </div>
          </div>

          <div class="card">
            <h3 class="section-title">关键词历史（防重复）</h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th style="width:108px">时间</th><th style="width:84px">方向词</th><th>已用关键词</th></tr></thead>
                <tbody id="keywordRows"></tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="card log-card">
          <h3 class="section-title">运行日志（每秒轮询）</h3>
          <pre id="runtimeLog"></pre>
          <div class="row-inline">
            <span class="muted">每 1 秒刷新；可用于观察运行状态与报错</span>
            <label class="check" style="margin:0;"><input type="checkbox" id="autoScroll" checked /> 自动滚动到底部</label>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="errorModal" class="modal">
    <div class="modal-card">
      <div class="modal-head" id="modalTitle">系统告警</div>
      <div class="modal-body" id="modalBody">--</div>
      <div class="modal-actions">
        <button onclick="closeModal()">我知道了</button>
      </div>
    </div>
  </div>

  <script>
    const configPath = {{ config_path | tojson }};
    const initialData = {{ initial_payload | tojson }};

    const runBadge = document.getElementById('runBadge');
    const runText = document.getElementById('runText');
    const lastUpdated = document.getElementById('lastUpdated');

    const alertBanner = document.getElementById('alertBanner');
    const alertTitle = document.getElementById('alertTitle');
    const alertMessage = document.getElementById('alertMessage');
    const alertHint = document.getElementById('alertHint');

    const statToday = document.getElementById('statToday');
    const statTotal = document.getElementById('statTotal');
    const statKeyword = document.getElementById('statKeyword');
    const statPhase = document.getElementById('statPhase');

    const commentRows = document.getElementById('commentRows');
    const keywordRows = document.getElementById('keywordRows');
    const runtimeLog = document.getElementById('runtimeLog');
    const autoScroll = document.getElementById('autoScroll');

    const errorModal = document.getElementById('errorModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');

    const settingsToggle = document.getElementById('settingsToggle');
    const settingsPanel = document.getElementById('settingsPanel');
    const settingsClose = document.getElementById('settingsClose');
    const settingsForm = document.getElementById('settingsForm');
    const testConnectionBtn = document.getElementById('testConnectionBtn');
    const testConnectionLog = document.getElementById('testConnectionLog');

    let lastAlertKey = '';

    function esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function closeModal() {
      errorModal.classList.remove('open');
    }
    window.closeModal = closeModal;

    function setSettingsVisible(visible) {
      if (!settingsPanel) return;
      if (visible) settingsPanel.classList.remove('hidden');
      else settingsPanel.classList.add('hidden');
    }

    async function testConnection() {
      if (!settingsForm || !testConnectionLog || !testConnectionBtn) return;

      testConnectionBtn.disabled = true;
      testConnectionLog.textContent = '正在测试连接，请稍候...';

      try {
        const resp = await fetch('/api/test_connection', {
          method: 'POST',
          body: new FormData(settingsForm),
        });

        let payload = {};
        try {
          payload = await resp.json();
        } catch (_) {
          payload = { ok: false, message: `HTTP ${resp.status}`, detail: '返回内容不是 JSON' };
        }

        const ok = !!payload.ok;
        const status = ok ? '✅ 连接成功' : '❌ 连接失败';
        const detail = payload.detail || '';
        const summary = payload.message || '';
        testConnectionLog.textContent = `${status}\n${summary}${detail ? `\n\n${detail}` : ''}`;
      } catch (err) {
        const msg = String((err && err.message) || err);
        testConnectionLog.textContent = `❌ 请求异常\n${msg}`;
      } finally {
        testConnectionBtn.disabled = false;
      }
    }

    if (settingsToggle) settingsToggle.addEventListener('click', () => setSettingsVisible(true));
    if (settingsClose) settingsClose.addEventListener('click', () => setSettingsVisible(false));
    if (testConnectionBtn) testConnectionBtn.addEventListener('click', testConnection);

    function renderComments(rows) {
      if (!rows || rows.length === 0) {
        commentRows.innerHTML = '<tr><td colspan="4" class="muted">暂无评论记录</td></tr>';
        return;
      }
      commentRows.innerHTML = rows.map((row) => `\n<tr>\n<td>${esc(row.created_at)}</td>\n<td>${esc(row.keyword)}</td>\n<td>${esc(row.post_id)}</td>\n<td>${esc(row.comment_text)}</td>\n</tr>`).join('');
    }

    function renderKeywords(rows) {
      if (!rows || rows.length === 0) {
        keywordRows.innerHTML = '<tr><td colspan="3" class="muted">暂无关键词历史</td></tr>';
        return;
      }
      keywordRows.innerHTML = rows.map((row) => `\n<tr>\n<td>${esc(row.created_at)}</td>\n<td>${esc(row.topic)}</td>\n<td>${esc(row.keyword)}</td>\n</tr>`).join('');
    }

    function applyAlert(alert) {
      const safeAlert = alert || {};
      const level = safeAlert.level || 'info';
      const title = safeAlert.title || '系统状态';
      const message = safeAlert.message || '--';
      const hint = safeAlert.hint || '';

      alertBanner.classList.remove('alert-info', 'alert-warn', 'alert-error');
      if (level === 'error') alertBanner.classList.add('alert-error');
      else if (level === 'warn') alertBanner.classList.add('alert-warn');
      else alertBanner.classList.add('alert-info');

      alertTitle.textContent = title;
      alertMessage.textContent = message;
      alertHint.textContent = hint;

      if (level === 'error') {
        const key = safeAlert.key || title + message;
        if (key && key !== lastAlertKey) {
          lastAlertKey = key;
          modalTitle.textContent = title;
          modalBody.textContent = `${message}${hint ? '\\n\\n建议：' + hint : ''}`;
          errorModal.classList.add('open');
        }
      }
    }

    function renderStatus(payload) {
      const running = !!payload.running;
      runText.textContent = running ? '运行中' : '空闲';
      runBadge.innerHTML = `<span class="dot ${running ? 'ok' : ''}"></span><span>${running ? '运行中' : '空闲'}</span>`;

      const stats = payload.stats || {};
      statToday.textContent = (stats.today_comments === undefined || stats.today_comments === null) ? 0 : stats.today_comments;
      statTotal.textContent = (stats.total_comments === undefined || stats.total_comments === null) ? 0 : stats.total_comments;
      statKeyword.textContent = (stats.keyword_history_total === undefined || stats.keyword_history_total === null) ? 0 : stats.keyword_history_total;

      const summary = payload.summary || {};
      statPhase.textContent = summary.phase || '待机';

      runtimeLog.textContent = payload.logs || '(no log file yet)';
      if (autoScroll.checked) runtimeLog.scrollTop = runtimeLog.scrollHeight;

      renderComments(payload.comments || []);
      renderKeywords(payload.keyword_history || []);
      applyAlert(payload.alert || {});

      lastUpdated.textContent = new Date().toLocaleTimeString();
    }

    let inFlight = false;

    async function pollLive() {
      if (inFlight) return;
      inFlight = true;
      try {
        const resp = await fetch(`/api/live?config_path=${encodeURIComponent(configPath)}`, { cache: 'no-store' });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const payload = await resp.json();
        renderStatus(payload);
      } catch (err) {
        applyAlert({
          level: 'error',
          title: '轮询失败',
          message: String((err && err.message) || err),
          hint: '请检查本地控制台服务是否仍在运行',
          key: 'poll-failed-' + String((err && err.message) || err),
        });
      } finally {
        inFlight = false;
      }
    }

    renderStatus(initialData);
    setInterval(pollLive, 1000);
  </script>
</body>
</html>
"""


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _save_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _tail_log(path: Path, lines: int = 260) -> str:
    if not path.exists():
        return "(no log file yet)"
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(text[-lines:])


def _recent_comments(db_path: Path, limit: int = 40) -> List[Dict[str, str]]:
    if not db_path.exists():
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {
            str(row[1]).strip().lower()
            for row in conn.execute("PRAGMA table_info(comments)").fetchall()
            if len(row) > 1
        }

        if "comment_text" in cols:
            sql = (
                "SELECT created_at, "
                "COALESCE(keyword, '') AS keyword, "
                "COALESCE(post_id, '') AS post_id, "
                "comment_text AS comment_text "
                "FROM comments ORDER BY id DESC LIMIT ?"
            )
        elif "content" in cols:
            sql = (
                "SELECT created_at, '' AS keyword, "
                "COALESCE(post_id, '') AS post_id, "
                "content AS comment_text "
                "FROM comments ORDER BY id DESC LIMIT ?"
            )
        else:
            return []

        rows = conn.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _recent_keyword_history(db_path: Path, limit: int = 40) -> List[Dict[str, str]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT created_at, topic, keyword FROM keyword_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _db_stats(db_path: Path) -> Dict[str, int]:
    if not db_path.exists():
        return {"today_comments": 0, "total_comments": 0, "keyword_history_total": 0}

    conn = sqlite3.connect(db_path)
    try:
        today = datetime.now().date().isoformat()
        total_comments = conn.execute("SELECT COUNT(1) FROM comments").fetchone()[0]

        comment_cols = {
            str(row[1]).strip().lower()
            for row in conn.execute("PRAGMA table_info(comments)").fetchall()
            if len(row) > 1
        }
        if "date" in comment_cols:
            today_comments = conn.execute("SELECT COUNT(1) FROM comments WHERE date = ?", (today,)).fetchone()[0]
        else:
            today_comments = conn.execute(
                "SELECT COUNT(1) FROM comments WHERE substr(created_at, 1, 10) = ?",
                (today,),
            ).fetchone()[0]

        has_keyword_table = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='keyword_history' LIMIT 1"
            ).fetchone()
        )
        keyword_total = (
            conn.execute("SELECT COUNT(1) FROM keyword_history").fetchone()[0]
            if has_keyword_table
            else 0
        )

        return {
            "today_comments": int(today_comments or 0),
            "total_comments": int(total_comments or 0),
            "keyword_history_total": int(keyword_total or 0),
        }
    except Exception:
        return {"today_comments": 0, "total_comments": 0, "keyword_history_total": 0}
    finally:
        conn.close()


def _iter_process_entries() -> List[Tuple[int, str]]:
    if os.name == "nt":
        scripts = [
            (
                "$ErrorActionPreference='SilentlyContinue'; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine } | "
                "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
            ),
            (
                "$ErrorActionPreference='SilentlyContinue'; "
                "Get-WmiObject Win32_Process | "
                "Where-Object { $_.CommandLine } | "
                "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
            ),
        ]

        for script in scripts:
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                continue

            if result.returncode != 0:
                continue

            entries: List[Tuple[int, str]] = []
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                if "\t" not in line:
                    continue
                pid_text, args_text = line.split("\t", 1)
                pid_text = pid_text.strip()
                if not pid_text.isdigit():
                    continue
                entries.append((int(pid_text), args_text.strip()))

            if entries:
                return entries

        return []

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    entries = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_text, args_text = parts
        if not pid_text.isdigit():
            continue
        entries.append((int(pid_text), args_text))
    return entries


def _find_runner_pid(config_path: Optional[Path] = None) -> Optional[int]:
    current_pid = os.getpid()
    normalized_config = str(config_path.resolve()) if config_path else None

    for pid, args_text in _iter_process_entries():
        if pid == current_pid:
            continue

        if "main.py" not in args_text or "--config" not in args_text:
            continue

        try:
            argv = shlex.split(args_text)
        except Exception:
            continue

        if not any(Path(arg).name == "main.py" for arg in argv):
            continue

        config_arg = None
        if "--config" in argv:
            idx = argv.index("--config")
            if idx + 1 < len(argv):
                config_arg = argv[idx + 1]

        if normalized_config and config_arg:
            cfg_path = Path(config_arg)
            if not cfg_path.is_absolute():
                cfg_path = (ROOT_DIR / cfg_path).resolve()
            else:
                cfg_path = cfg_path.resolve()
            if str(cfg_path) != normalized_config:
                continue

        return pid

    return None


def _is_running(config_path: Optional[Path] = None) -> bool:
    proc = _STATE.get("proc")
    if proc and proc.poll() is None:
        return True

    return _find_runner_pid(config_path) is not None


def _stop_running(config_path: Optional[Path] = None) -> bool:
    stopped = False

    proc = _STATE.get("proc")
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=6)
            stopped = True
        except Exception:
            try:
                proc.kill()
                stopped = True
            except Exception:
                pass

    pid = _find_runner_pid(config_path)
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            else:
                os.kill(pid, signal.SIGTERM)
            stopped = True
        except ProcessLookupError:
            pass
        except Exception:
            pass

    _STATE["proc"] = None
    return stopped


def _apply_defaults(cfg: Dict[str, Any]) -> Dict[str, Any]:
    browser = cfg.setdefault("browser", {})
    ai = cfg.setdefault("ai", {})
    runtime = cfg.setdefault("runtime", {})
    topics = cfg.setdefault("topics", {})
    comment_rules = cfg.setdefault("comment_rules", {})
    logging_cfg = cfg.setdefault("logging", {})
    dedup_cfg = cfg.setdefault("dedup", {})
    openai_cfg = cfg.setdefault("openai", {})

    openai_cfg.setdefault("base_url", "https://gmn.chuangzuoli.com")
    openai_cfg.setdefault("model_id", "gpt-5.3-codex")
    openai_cfg.setdefault("api_key", "${OPENAI_API_KEY}")

    browser.setdefault("search_each_post", False)
    ai.setdefault("strict_comment_gate", False)
    ai.setdefault("keyword_max_count", 10)

    runtime.setdefault("max_comments_per_round", 5)
    runtime.setdefault("search_limit_per_keyword", 5)
    runtime.setdefault("single_keyword_search", True)
    runtime.setdefault("disable_keyword_expansion", False)
    runtime.setdefault("comment_every_post", True)

    topics.setdefault("direction_keywords", ["美女"])
    comment_rules.setdefault("requirements", ["先认可观点，再补一句虚心求教，语气自然"])

    logging_cfg.setdefault("file_path", "./logs/app.log")
    dedup_cfg.setdefault("sqlite_path", "./data/dedup.sqlite3")

    return cfg


def _resolve_paths(config_path: Path) -> Tuple[Dict[str, Any], Path, Path]:
    cfg = _apply_defaults(_load_yaml(config_path))
    log_path = (ROOT_DIR / cfg["logging"]["file_path"]).resolve()
    db_path = (ROOT_DIR / cfg["dedup"]["sqlite_path"]).resolve()
    return cfg, log_path, db_path


def _detect_phase(log_text: str, running: bool) -> str:
    if not log_text.strip():
        return "待机"

    lines = [line for line in log_text.splitlines() if line.strip()]
    last_line = lines[-1] if lines else ""

    patterns = [
        (r"startup health check", "AI健康检查"),
        (r"expand direction keyword", "AI关键词扩展"),
        (r"search keyword", "搜索帖子"),
        (r"fetch post context", "提取帖子信息"),
        (r"commentability check", "AI评论判定"),
        (r"generate comment candidates", "AI生成评论"),
        (r"submit comment", "提交评论"),
        (r"write sqlite record", "写入日志库"),
        (r"round=.*failed", "运行失败"),
    ]

    text = last_line.lower()
    for regex, phase in patterns:
        if re.search(regex, text):
            return phase

    return "运行中" if running else "空闲"


def _classify_alert(log_text: str, running: bool) -> Dict[str, str]:
    lines = [line for line in log_text.splitlines() if line.strip()]

    def last_match_with_index(regex: str) -> Tuple[int, str]:
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if re.search(regex, line, re.IGNORECASE):
                return idx, line
        return -1, ""

    ai_bad_idx, ai_bad_line = last_match_with_index(r"AI 未启用|配置缺失|startup health check failed|OpenAI disabled")
    ai_ok_idx, _ = last_match_with_index(r"startup health check passed")

    if ai_bad_line and ai_bad_idx > ai_ok_idx:
        key_src = f"AI 配置异常|{ai_bad_line}"
        key = hashlib.md5(key_src.encode("utf-8")).hexdigest()[:12]
        return {
            "level": "error",
            "title": "AI 配置异常",
            "message": ai_bad_line,
            "hint": "请检查 API Key / Base URL / Model ID 是否正确，并保存配置后重试。",
            "key": key,
        }

    rules = [
        {
            "regex": r"browser client start failed|Playwright 启动被系统拒绝访问|WinError 5|persistent browser launch failed|CDP relay 未连接",
            "level": "error",
            "title": "浏览器启动失败",
            "hint": "请确认浏览器权限、CDP relay 可用，以及安全软件未拦截 Playwright 子进程。",
        },
        {
            "regex": r"api error 401|api error 403",
            "level": "error",
            "title": "模型鉴权失败",
            "hint": "API Key 可能无效或权限不足，请更换可用密钥。",
        },
        {
            "regex": r"api error 5\d\d|502|503|504",
            "level": "warn",
            "title": "模型网关异常",
            "hint": "上游服务波动，建议稍后重试。",
        },
        {
            "regex": r"comment input not found",
            "level": "warn",
            "title": "评论输入框未命中",
            "hint": "页面 DOM 变化导致定位失败，脚本会自动重试。",
        },
        {
            "regex": r"comment submit button not found|submit not confirmed",
            "level": "warn",
            "title": "评论发送未确认",
            "hint": "已尝试重试发送，请观察后续日志是否成功落库。",
        },
        {
            "regex": r"not logged in|login required",
            "level": "warn",
            "title": "快手登录态失效",
            "hint": "请在浏览器先手动登录快手，再运行任务。",
        },
    ]

    for rule in rules:
        _, matched = last_match_with_index(rule["regex"])
        if matched:
            key_src = f"{rule['title']}|{matched}"
            key = hashlib.md5(key_src.encode("utf-8")).hexdigest()[:12]
            return {
                "level": rule["level"],
                "title": rule["title"],
                "message": matched,
                "hint": rule["hint"],
                "key": key,
            }

    if running:
        return {
            "level": "info",
            "title": "任务运行中",
            "message": "任务正在执行，日志每秒自动刷新。",
            "hint": "如需停止请点击顶部“停止任务”按钮。",
            "key": "running",
        }

    return {
        "level": "info",
        "title": "当前空闲",
        "message": "系统待机中，可点击“开始任务（单轮）”或“开始任务（持续）”。",
        "hint": "建议先确认模型配置和方向词是否正确。",
        "key": "idle",
    }


def _runtime_summary(log_text: str, running: bool) -> Dict[str, str]:
    lines = [line for line in log_text.splitlines() if line.strip()]

    def pick_last(level: str) -> str:
        token = f"| {level.upper()} |"
        for line in reversed(lines):
            if token in line:
                return line
        return ""

    return {
        "phase": _detect_phase(log_text, running),
        "last_error": pick_last("ERROR"),
        "last_warning": pick_last("WARNING"),
        "last_info": pick_last("INFO"),
    }


def _live_payload(config_path: Path) -> Dict[str, Any]:
    cfg, log_path, db_path = _resolve_paths(config_path)
    running = _is_running(config_path)
    logs = _tail_log(log_path, 260)
    return {
        "running": running,
        "run_status": "运行中" if running else "空闲",
        "stats": _db_stats(db_path),
        "comments": _recent_comments(db_path),
        "keyword_history": _recent_keyword_history(db_path),
        "logs": logs,
        "summary": _runtime_summary(logs, running),
        "alert": _classify_alert(logs, running),
        "config_file": str(config_path),
    }


@app.get("/")
def index():
    config_path = Path(request.args.get("config_path") or str(DEFAULT_CONFIG))
    cfg, _, _ = _resolve_paths(config_path)
    payload = _live_payload(config_path)
    alert = payload.get("alert") or {}
    initial_level = str(alert.get("level") or "info")
    initial_alert_class = {
        "error": "alert-error",
        "warn": "alert-warn",
    }.get(initial_level, "alert-info")

    return render_template_string(
        HTML,
        cfg=cfg,
        config_path=str(config_path),
        direction_keywords=", ".join(cfg["topics"].get("direction_keywords", [])),
        requirements_text="\n".join(cfg["comment_rules"].get("requirements", [])),
        run_status="运行中" if payload.get("running") else "空闲",
        message=request.args.get("message", ""),
        initial_payload=payload,
        initial_stats=payload.get("stats") or {"today_comments": 0, "total_comments": 0, "keyword_history_total": 0},
        initial_phase=(payload.get("summary") or {}).get("phase") or "待机",
        initial_alert_class=initial_alert_class,
        initial_alert_title=alert.get("title") or "系统状态",
        initial_alert_message=alert.get("message") or "等待轮询数据",
        initial_alert_hint=alert.get("hint") or "",
    )


@app.get("/api/live")
def api_live():
    config_path = Path(request.args.get("config_path") or str(DEFAULT_CONFIG))
    return jsonify(_live_payload(config_path))


@app.post("/api/test_connection")
def api_test_connection():
    config_path = Path(request.form.get("config_path") or str(DEFAULT_CONFIG))
    cfg = _apply_defaults(_load_yaml(config_path))
    openai_cfg = cfg.get("openai", {})

    base_url = (request.form.get("openai_base_url") or str(openai_cfg.get("base_url") or "")).strip()
    model_id = (request.form.get("openai_model_id") or str(openai_cfg.get("model_id") or "")).strip()
    api_key = (request.form.get("openai_api_key") or str(openai_cfg.get("api_key") or "")).strip()
    timeout_seconds = int(openai_cfg.get("timeout_seconds") or 30)

    if not base_url:
        return jsonify({"ok": False, "message": "Base URL 不能为空", "detail": "请在设置中填写 openai_base_url"})
    if not model_id:
        return jsonify({"ok": False, "message": "模型 ID 不能为空", "detail": "请在设置中填写 openai_model_id"})
    if not api_key or api_key.startswith("${"):
        return jsonify({"ok": False, "message": "API Key 为空或未解析", "detail": "请填写可用的 API Key（不要留环境变量占位符）"})

    client: Optional[OpenAIChatClient] = None
    started = time.perf_counter()
    try:
        model_cfg = OpenAIModelConfig(
            base_url=base_url,
            api_key=api_key,
            model_id=model_id,
            timeout_seconds=timeout_seconds,
            temperature=0,
            max_tokens=32,
        )
        client = OpenAIChatClient(model_cfg, enabled=True)
        reply = client.chat(
            system_prompt="你是连通性检测助手。",
            user_prompt="只回复 CONNECT_OK",
            temperature=0,
            max_tokens=16,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return jsonify(
            {
                "ok": True,
                "message": f"连接成功，耗时 {elapsed_ms}ms，模型返回：{reply[:120]}",
                "detail": f"base_url={base_url}\nmodel_id={model_id}",
            }
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        detail = traceback.format_exc()
        return jsonify(
            {
                "ok": False,
                "message": f"{type(exc).__name__}: {exc}（耗时 {elapsed_ms}ms）",
                "detail": detail,
            }
        )
    finally:
        if client is not None:
            client.close()


@app.post("/save")
def save():
    config_path = Path(request.form.get("config_path") or str(DEFAULT_CONFIG))
    cfg = _apply_defaults(_load_yaml(config_path))

    openai_cfg = cfg.setdefault("openai", {})
    base_url = (request.form.get("openai_base_url") or "").strip()
    model_id = (request.form.get("openai_model_id") or "").strip()
    api_key = (request.form.get("openai_api_key") or "").strip()

    if base_url:
        openai_cfg["base_url"] = base_url
    if model_id:
        openai_cfg["model_id"] = model_id
    if api_key:
        openai_cfg["api_key"] = api_key

    cfg.setdefault("browser", {})["search_each_post"] = bool(request.form.get("search_each_post"))

    ai = cfg.setdefault("ai", {})
    ai["strict_comment_gate"] = bool(request.form.get("strict_comment_gate"))
    ai["keyword_max_count"] = int(request.form.get("keyword_max_count") or 10)

    runtime = cfg.setdefault("runtime", {})
    runtime["max_comments_per_round"] = int(request.form.get("max_comments_per_round") or 5)
    runtime["search_limit_per_keyword"] = int(request.form.get("search_limit_per_keyword") or 5)
    runtime["single_keyword_search"] = bool(request.form.get("single_keyword_search"))
    runtime["disable_keyword_expansion"] = bool(request.form.get("disable_keyword_expansion"))
    runtime["comment_every_post"] = bool(request.form.get("comment_every_post"))

    direction_keywords = [v.strip() for v in (request.form.get("direction_keywords") or "").split(",") if v.strip()]
    cfg.setdefault("topics", {})["direction_keywords"] = direction_keywords or ["美女"]

    requirements = [v.strip() for v in (request.form.get("requirements") or "").splitlines() if v.strip()]
    cfg.setdefault("comment_rules", {})["requirements"] = requirements or ["先认可对方观点，再补一句虚心求教，语气自然"]

    _save_yaml(config_path, cfg)
    return redirect(url_for("index", config_path=str(config_path), message="配置已保存"))


@app.post("/run_once")
def run_once():
    config_path = Path(request.form.get("config_path") or str(DEFAULT_CONFIG))
    if _is_running(config_path):
        return redirect(url_for("index", config_path=str(config_path), message="已有任务在运行"))

    run_mode = (request.form.get("run_mode") or "once").strip().lower()
    loop_mode = run_mode == "loop"

    env = os.environ.copy()
    cmd = [sys.executable, "main.py", "--config", str(config_path)]
    if not loop_mode:
        cmd.append("--once")

    _STATE["proc"] = subprocess.Popen(cmd, cwd=str(ROOT_DIR), env=env)

    message = "已启动持续运行任务" if loop_mode else "已启动一轮任务"
    return redirect(url_for("index", config_path=str(config_path), message=message))


@app.post("/stop")
def stop_task():
    config_path = Path(request.form.get("config_path") or str(DEFAULT_CONFIG))
    stopped = _stop_running(config_path)
    message = "已停止运行任务" if stopped else "当前无运行任务"
    return redirect(url_for("index", config_path=str(config_path), message=message))


def main() -> int:
    parser = argparse.ArgumentParser(description="KS Auto Commenter 本地可视化控制台")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="默认配置文件路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    globals()["DEFAULT_CONFIG"] = Path(args.config)

    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
