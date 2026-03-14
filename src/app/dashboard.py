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
from datetime import datetime, timedelta
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
      --bg: #06111f;
      --bg-deep: #030712;
      --panel: rgba(10, 21, 39, 0.78);
      --panel-soft: rgba(20, 36, 63, 0.82);
      --panel-strong: rgba(7, 18, 34, 0.92);
      --line: rgba(132, 166, 218, 0.18);
      --line-strong: rgba(132, 166, 218, 0.34);
      --text: #e8f0ff;
      --muted: #90a6cb;
      --ok: #33d69f;
      --warn: #f7b84b;
      --danger: #ff6b7a;
      --accent: #69c8ff;
      --accent-strong: #40a5ff;
      --accent-lime: #9df76f;
      --accent-blue: #8ea5ff;
      --info: #38bdf8;
      --shadow: 0 28px 70px rgba(0, 0, 0, 0.28);
      color-scheme: dark;
    }

    * { box-sizing: border-box; }

    html, body {
      width: 100%;
      max-width: 100%;
      overflow-x: hidden;
    }

    body {
      position: relative;
      min-height: 100vh;
      margin: 0;
      padding: clamp(12px, 1.8vw, 24px);
      color: var(--text);
      font-family: "IBM Plex Sans", "Segoe UI Variable", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(1100px 780px at 0% 0%, rgba(56, 189, 248, 0.18), transparent 48%),
        radial-gradient(1200px 840px at 100% 0%, rgba(64, 165, 255, 0.18), transparent 52%),
        radial-gradient(900px 720px at 50% 120%, rgba(157, 247, 111, 0.10), transparent 55%),
        linear-gradient(180deg, #07111f 0%, #02060d 100%);
    }

    body::before {
      content: '';
      position: fixed;
      inset: -20%;
      background:
        radial-gradient(circle at 18% 18%, rgba(105, 200, 255, 0.14), transparent 26%),
        radial-gradient(circle at 80% 12%, rgba(142, 165, 255, 0.12), transparent 24%),
        radial-gradient(circle at 60% 82%, rgba(157, 247, 111, 0.08), transparent 20%);
      filter: blur(44px);
      pointer-events: none;
      animation: auroraDrift 18s ease-in-out infinite alternate;
      z-index: 0;
    }

    body::after {
      content: '';
      position: fixed;
      inset: 0;
      background-image:
        linear-gradient(rgba(105, 200, 255, 0.07) 1px, transparent 1px),
        linear-gradient(90deg, rgba(105, 200, 255, 0.06) 1px, transparent 1px);
      background-size: 120px 120px;
      mask-image: linear-gradient(180deg, rgba(255,255,255,0.35), transparent 92%);
      opacity: 0.25;
      pointer-events: none;
      animation: gridShift 26s linear infinite;
      z-index: 0;
    }

    .sprite-defs {
      position: absolute;
      width: 0;
      height: 0;
      overflow: hidden;
      pointer-events: none;
    }

    .wrap {
      position: relative;
      z-index: 1;
      width: min(100%, 1380px);
      margin: 0 auto;
      display: grid;
      gap: 14px;
      min-width: 0;
      overflow-x: visible;
      animation: sceneIn .75s ease both;
    }

    .card {
      position: relative;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: linear-gradient(180deg, rgba(11, 23, 41, 0.92), rgba(6, 13, 24, 0.94));
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
      overflow: hidden;
      min-width: 0;
      transition: transform .28s ease, border-color .28s ease, box-shadow .28s ease;
    }

    .card::before {
      content: '';
      position: absolute;
      inset: 0 0 auto 0;
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(141, 224, 255, 0.55), transparent);
      opacity: 0.72;
      pointer-events: none;
    }

    .card:hover {
      transform: translateY(-2px);
      border-color: rgba(132, 166, 218, 0.28);
    }

    .topbar {
      padding: 22px;
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(300px, .95fr);
      align-items: start;
      gap: 18px;
      border-color: rgba(56, 189, 248, 0.24);
      background: linear-gradient(135deg, rgba(12, 27, 48, 0.96), rgba(5, 13, 24, 0.92));
    }

    .topbar::after {
      content: '';
      position: absolute;
      width: 300px;
      height: 300px;
      right: -90px;
      top: -140px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(105, 200, 255, 0.17), transparent 62%);
      filter: blur(10px);
      animation: floatOrb 15s ease-in-out infinite;
      pointer-events: none;
    }

    .topbar-left {
      min-width: 0;
      display: grid;
      gap: 14px;
    }

    .topbar-right {
      display: grid;
      gap: 10px;
      align-content: start;
      min-width: 0;
    }

    .title-row {
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      min-width: 0;
    }

    .title-copy {
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    #configPath {
      display: inline;
      word-break: break-all;
      overflow-wrap: anywhere;
    }

    h1 {
      margin: 0;
      font-size: clamp(28px, 3vw, 40px);
      line-height: 1;
      letter-spacing: .01em;
    }

    .muted {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      background: rgba(255,255,255,0.03);
      min-height: 40px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--warn);
      box-shadow: 0 0 18px rgba(247, 184, 75, 0.5);
    }
    .dot.ok {
      background: var(--ok);
      box-shadow: 0 0 18px rgba(51, 214, 159, 0.6);
    }

    .alert-banner {
      border-radius: 20px;
      border: 1px solid var(--line);
      padding: 16px 18px;
      display: block;
      backdrop-filter: blur(18px);
    }

    .alert-info { background: rgba(56,189,248,0.09); border-color: rgba(56,189,248,0.4); }
    .alert-warn { background: rgba(245,158,11,0.12); border-color: rgba(245,158,11,0.42); }
    .alert-error { background: rgba(255,107,122,0.12); border-color: rgba(255,107,122,0.42); }

    .alert-title {
      font-size: clamp(16px, 2vw, 24px);
      font-weight: 800;
      letter-spacing: .02em;
    }

    .stats {
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(4, minmax(0, 1fr));
    }

    .stat {
      min-height: 164px;
      min-width: 0;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 16px;
      background: linear-gradient(180deg, rgba(16, 29, 49, 0.94), rgba(7, 15, 27, 0.94));
    }

    .stat .k {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .stat .v {
      font-size: clamp(24px, 2.8vw, 36px);
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.05;
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
      padding: 15px 18px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
      font-size: 14px;
      font-weight: 700;
    }

    .panel-body {
      padding: 16px 18px;
      min-width: 0;
    }

    label {
      display: block;
      margin: 12px 0 6px;
      font-size: 13px;
      font-weight: 600;
      color: #d6e6ff;
    }

    input[type=text], input[type=number], textarea {
      width: 100%;
      border: 1px solid rgba(132, 166, 218, 0.2);
      border-radius: 12px;
      padding: 10px 12px;
      color: var(--text);
      background: linear-gradient(180deg, rgba(17, 32, 57, 0.98), rgba(12, 25, 43, 0.96));
      outline: none;
      max-width: 100%;
      transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
    }

    input[type=text]:focus, input[type=number]:focus, textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 4px rgba(64,165,255,0.14);
      transform: translateY(-1px);
    }

    textarea { min-height: 96px; resize: vertical; }

    .check {
      display: flex;
      gap: 8px;
      align-items: center;
      margin: 8px 0 2px;
      font-size: 13px;
    }

    .buttons { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }

    button, .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 14px;
      background: linear-gradient(180deg, rgba(29, 64, 175, 0.95), rgba(29, 78, 216, 0.92));
      color: var(--text);
      cursor: pointer;
      text-decoration: none;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
      max-width: 100%;
      transition: transform .2s ease, filter .2s ease, border-color .2s ease, box-shadow .2s ease;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.08);
    }

    .btn.secondary {
      background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.03));
    }

    button:hover, .btn:hover {
      filter: brightness(1.05);
      transform: translateY(-1px);
      border-color: rgba(132, 166, 218, 0.32);
      box-shadow: 0 14px 30px rgba(0,0,0,0.18);
    }

    .hidden { display: none !important; }

    body.modal-open {
      overflow: hidden;
    }

    .settings-modal {
      position: fixed;
      inset: 0;
      min-height: 100dvh;
      z-index: 40;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      overflow-y: auto;
      overscroll-behavior: contain;
      background: rgba(2, 7, 14, 0.7);
      backdrop-filter: blur(18px);
    }

    .settings-dialog {
      width: min(1120px, 100%);
      max-height: min(92vh, 920px);
      margin: auto;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      border-radius: 24px;
      border: 1px solid rgba(132, 166, 218, 0.2);
      background: linear-gradient(180deg, rgba(8, 18, 33, 0.98), rgba(4, 10, 20, 0.98));
      box-shadow: 0 36px 90px rgba(0, 0, 0, 0.4);
      overflow: hidden;
    }

    .settings-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      padding: 22px 24px 18px;
      border-bottom: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(12, 27, 48, 0.9), rgba(8, 18, 33, 0.7));
    }

    .settings-heading {
      display: grid;
      gap: 8px;
      min-width: 0;
    }

    .settings-subtitle {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }

    .settings-heading small {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      letter-spacing: .04em;
    }

    .settings-close {
      border: 1px solid rgba(132, 166, 218, 0.2);
      border-radius: 14px;
      background: rgba(255,255,255,0.04);
      color: var(--text);
      padding: 10px 14px;
      cursor: pointer;
      font-size: 13px;
      flex: 0 0 auto;
    }

    .settings-form {
      min-height: 0;
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
    }

    .settings-form-shell {
      min-height: 0;
      display: grid;
      grid-template-columns: 220px minmax(0, 1fr);
    }

    .settings-tabs {
      display: grid;
      align-content: start;
      gap: 10px;
      padding: 18px;
      border-right: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(10, 21, 38, 0.92), rgba(6, 13, 24, 0.92));
    }

    .settings-tab {
      width: 100%;
      justify-content: flex-start;
      align-items: flex-start;
      padding: 12px;
      border-radius: 18px;
      border: 1px solid rgba(132, 166, 218, 0.12);
      background: rgba(255,255,255,0.03);
      text-align: left;
      box-shadow: none;
    }

    .settings-tab.is-active {
      border-color: rgba(105, 200, 255, 0.34);
      background: linear-gradient(180deg, rgba(56,189,248,0.16), rgba(15, 29, 50, 0.82));
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.06), 0 12px 28px rgba(0,0,0,0.18);
    }

    .settings-tab-copy {
      display: grid;
      gap: 4px;
      min-width: 0;
    }

    .settings-tab-title {
      font-size: 14px;
      font-weight: 700;
      color: #e8f2ff;
    }

    .settings-tab-note {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      white-space: normal;
    }

    .settings-panels {
      min-height: 0;
      overflow: auto;
      padding: 20px;
      display: grid;
    }

    .settings-panel {
      display: none;
      align-content: start;
      gap: 16px;
      min-width: 0;
    }

    .settings-panel.is-active {
      display: grid;
    }

    .settings-panel-head {
      display: grid;
      gap: 6px;
    }

    .settings-panel-head h4 {
      margin: 0;
      font-size: 18px;
    }

    .settings-panel-head p {
      margin: 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.6;
    }

    .settings-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      min-width: 0;
    }

    .setting-card {
      min-width: 0;
      padding: 16px;
      border-radius: 18px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(14, 28, 49, 0.88), rgba(7, 15, 27, 0.9));
      display: grid;
      gap: 10px;
    }

    .setting-card.full {
      grid-column: 1 / -1;
    }

    .setting-card label {
      margin: 0;
    }

    .setting-title {
      font-size: 14px;
      font-weight: 700;
      color: #e6f2ff;
    }

    .setting-hint {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }

    .setting-chip {
      display: inline-flex;
      width: fit-content;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: rgba(255,255,255,0.04);
      color: #d5e8ff;
      font-size: 11px;
      letter-spacing: .06em;
      text-transform: uppercase;
    }

    .setting-toggle-head {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
    }

    .setting-toggle-copy {
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    .test-log {
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px 14px;
      min-height: 72px;
      max-height: 220px;
      overflow: auto;
      background: linear-gradient(180deg, rgba(6, 16, 30, 0.98), rgba(8, 18, 36, 0.96));
      color: #d6e4ff;
      font-size: 12px;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "IBM Plex Mono", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
    }

    .switch {
      position: relative;
      display: inline-block;
      width: 54px;
      height: 32px;
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
      background: rgba(66, 86, 117, 0.86);
      transition: .2s;
      cursor: pointer;
      border: 1px solid rgba(255,255,255,0.18);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
    }

    .switch-slider:before {
      content: '';
      position: absolute;
      width: 24px;
      height: 24px;
      left: 3px;
      top: 3px;
      border-radius: 50%;
      background: #fff;
      transition: .2s;
      box-shadow: 0 4px 14px rgba(0,0,0,0.22);
    }

    .switch-input:focus + .switch-slider {
      box-shadow: 0 0 0 4px rgba(64,165,255,0.14);
    }

    .switch-input:checked + .switch-slider {
      background: linear-gradient(90deg, rgba(56,189,248,0.95), rgba(64,165,255,0.88));
      border-color: rgba(96,165,250,0.8);
    }

    .switch-input:checked + .switch-slider:before {
      transform: translateX(22px);
    }

    .settings-footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 16px 24px 20px;
      border-top: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(8, 18, 33, 0.82), rgba(5, 11, 22, 0.96));
    }

    .settings-footer-copy {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.55;
    }

    .settings-footer-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .control-panel .panel-body {
      display: grid;
      gap: 14px;
    }

    .control-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .control-buttons {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }

    .control-inline-form { margin: 0; }

    .control-btn {
      border-radius: 14px;
      padding: 12px 18px;
      font-size: 15px;
      font-weight: 700;
      border: 1px solid var(--line);
      color: var(--text);
      background: linear-gradient(180deg, rgba(29, 78, 216, 0.98), rgba(30, 64, 175, 0.92));
    }

    .control-btn.stop {
      background: linear-gradient(180deg, rgba(224, 71, 94, 0.95), rgba(185, 28, 28, 0.92));
      border-color: rgba(255,107,122,0.54);
    }

    .tables {
      display: grid;
      gap: 14px;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, .9fr);
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
      scrollbar-color: rgba(105, 200, 255, 0.4) transparent;
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
      font-variant-numeric: tabular-nums;
      border-bottom: 1px solid var(--line);
      padding: 8px 6px;
      position: sticky;
      top: 0;
      background: rgba(12, 25, 43, 0.96);
      z-index: 2;
    }

    tbody td {
      padding: 8px 6px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      vertical-align: top;
      word-break: break-word;
      overflow-wrap: anywhere;
    }

    tbody tr {
      transition: background-color .18s ease;
    }

    tbody tr:hover {
      background: rgba(105, 200, 255, 0.05);
    }

    .log-card { margin-top: 12px; }
    pre {
      margin: 0;
      padding: 14px 16px;
      max-height: 420px;
      overflow: auto;
      background: linear-gradient(180deg, rgba(6, 16, 30, 0.98), rgba(8, 18, 36, 0.96));
      color: #d6e4ff;
      font-size: 12px;
      line-height: 1.45;
      font-family: "IBM Plex Mono", "Cascadia Code", "JetBrains Mono", Consolas, monospace;
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

    .glyph {
      width: 18px;
      height: 18px;
      display: block;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
      flex: 0 0 auto;
    }

    .icon-shell {
      width: 42px;
      height: 42px;
      border-radius: 14px;
      display: inline-grid;
      place-items: center;
      background: linear-gradient(180deg, rgba(255,255,255,0.08), rgba(255,255,255,0.02));
      border: 1px solid rgba(255,255,255,0.08);
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.06);
      color: #d7ecff;
      flex: 0 0 auto;
    }

    .icon-shell.small {
      width: 36px;
      height: 36px;
      border-radius: 12px;
    }

    .accent-cyan {
      color: #9fe8ff;
      background: linear-gradient(180deg, rgba(56,189,248,0.22), rgba(8,37,60,0.42));
    }

    .accent-blue {
      color: #c6d2ff;
      background: linear-gradient(180deg, rgba(129,140,248,0.20), rgba(28,37,74,0.42));
    }

    .accent-lime {
      color: #d8ffc1;
      background: linear-gradient(180deg, rgba(163,230,53,0.18), rgba(35,58,20,0.42));
    }

    .accent-amber {
      color: #ffe1af;
      background: linear-gradient(180deg, rgba(245,158,11,0.22), rgba(66,41,12,0.42));
    }

    .eyebrow {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .eyebrow-chip,
    .meta-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(132, 166, 218, 0.16);
      background: rgba(255,255,255,0.03);
      font-size: 12px;
      color: #d7eaff;
    }

    .eyebrow-chip {
      text-transform: uppercase;
      letter-spacing: .12em;
      font-size: 11px;
    }

    .eyebrow-note {
      color: var(--muted);
      font-size: 12px;
    }

    .brand-mark {
      width: 64px;
      height: 64px;
      border-radius: 22px;
      display: grid;
      place-items: center;
      color: #b5efff;
      background:
        radial-gradient(circle at 30% 30%, rgba(56,189,248,0.28), transparent 42%),
        linear-gradient(180deg, rgba(18, 41, 71, 0.95), rgba(8, 18, 31, 0.92));
      border: 1px solid rgba(105, 200, 255, 0.18);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.06),
        0 18px 40px rgba(0,0,0,0.22);
    }

    .brand-mark .glyph {
      width: 28px;
      height: 28px;
    }

    .topbar-copy {
      color: #c9daf6;
      font-size: 14px;
      line-height: 1.6;
      max-width: 64ch;
    }

    .topbar-meta {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      min-width: 0;
    }

    .pill-live {
      width: fit-content;
      border-color: rgba(105, 200, 255, 0.24);
      background: linear-gradient(180deg, rgba(56,189,248,0.14), rgba(56,189,248,0.05));
    }

    .hero-telemetry {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    .mini-readout {
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
      display: grid;
      gap: 4px;
      min-width: 0;
    }

    .mini-readout span {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .12em;
      color: var(--muted);
    }

    .mini-readout strong {
      font-size: 22px;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }

    .alert-main {
      display: flex;
      gap: 14px;
      align-items: flex-start;
    }

    .alert-copy {
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    .alert-topline {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .alert-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(255,255,255,0.08);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .metric-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }

    .metric-sub,
    .metric-foot {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .phase-value {
      font-size: clamp(18px, 2vw, 24px) !important;
      line-height: 1.35 !important;
    }

    .stat-cyan {
      background:
        radial-gradient(circle at 100% 0%, rgba(56,189,248,0.16), transparent 36%),
        linear-gradient(180deg, rgba(16,29,49,0.94), rgba(7,15,27,0.94));
    }

    .stat-blue {
      background:
        radial-gradient(circle at 100% 0%, rgba(129,140,248,0.14), transparent 36%),
        linear-gradient(180deg, rgba(16,29,49,0.94), rgba(7,15,27,0.94));
    }

    .stat-lime {
      background:
        radial-gradient(circle at 100% 0%, rgba(163,230,53,0.14), transparent 36%),
        linear-gradient(180deg, rgba(16,29,49,0.94), rgba(7,15,27,0.94));
    }

    .stat-phase {
      background:
        radial-gradient(circle at 100% 0%, rgba(245,158,11,0.16), transparent 38%),
        linear-gradient(180deg, rgba(16,29,49,0.94), rgba(7,15,27,0.94));
    }

    .section-title-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }

    .section-title-main {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }

    .section-title-main > span:last-child {
      display: grid;
      gap: 2px;
      min-width: 0;
    }

    .section-title small {
      color: var(--muted);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: .04em;
      text-transform: uppercase;
    }

    .section-tag {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
      color: #d5e9ff;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
    }

    .flash-msg {
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(51, 214, 159, 0.12);
      border: 1px solid rgba(51, 214, 159, 0.18);
      color: #b8f8df;
      font-size: 12px;
      font-weight: 600;
    }

    .overview-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(320px, .92fr);
      gap: 14px;
      min-width: 0;
      align-items: start;
    }

    .chart-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(260px, .85fr);
      gap: 14px;
      align-items: start;
    }

    .chart-stage,
    .mini-panel,
    .signal-body {
      min-width: 0;
    }

    .chart-stage {
      display: grid;
      gap: 14px;
      align-content: start;
    }

    .chart-meta {
      display: flex;
      justify-content: space-between;
      gap: 14px;
      align-items: flex-start;
      flex-wrap: wrap;
    }

    .legend {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .legend-item {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 28px;
      padding: 4px 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }

    .legend-item::before {
      content: '';
      width: 8px;
      height: 8px;
      border-radius: 50%;
      box-shadow: 0 0 12px currentColor;
    }

    .legend-item.comments { color: #77e8ff; }
    .legend-item.comments::before { background: #77e8ff; }
    .legend-item.keywords { color: #c7ff8a; }
    .legend-item.keywords::before { background: #c7ff8a; }

    .chart-values {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      width: min(100%, 360px);
    }

    .chart-value {
      padding: 12px;
      border-radius: 14px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: rgba(255,255,255,0.03);
      display: grid;
      gap: 4px;
      min-width: 0;
    }

    .chart-value span {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }

    .chart-value strong {
      font-size: 22px;
      line-height: 1;
      font-variant-numeric: tabular-nums;
    }

    .chart-canvas {
      position: relative;
      min-height: 250px;
      border-radius: 20px;
      overflow: hidden;
      border: 1px solid rgba(132, 166, 218, 0.16);
      background:
        radial-gradient(circle at 20% 20%, rgba(56,189,248,0.08), transparent 32%),
        linear-gradient(180deg, rgba(8, 18, 33, 0.98), rgba(6, 13, 25, 0.96));
    }

    .chart-grid-lines {
      position: absolute;
      inset: 0;
      background-image:
        linear-gradient(rgba(255,255,255,0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px);
      background-size: 100% 25%, calc(100% / 12) 100%;
      opacity: 0.45;
      pointer-events: none;
    }

    .chart-svg {
      position: relative;
      z-index: 1;
      width: 100%;
      height: 250px;
      display: block;
    }

    .chart-area.comments { fill: url(#commentsFill); }
    .chart-area.keywords { fill: url(#keywordsFill); }
    .chart-line {
      fill: none;
      stroke-width: 3;
      stroke-linecap: round;
      stroke-linejoin: round;
      filter: drop-shadow(0 0 12px currentColor);
    }

    .chart-line.comments { stroke: #77e8ff; color: #77e8ff; }
    .chart-line.keywords { stroke: #c7ff8a; color: #c7ff8a; }

    .chart-point {
      stroke: rgba(9,17,31,0.95);
      stroke-width: 3;
    }

    .chart-point.comments { fill: #77e8ff; }
    .chart-point.keywords { fill: #c7ff8a; }

    .axis-row {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 8px;
    }

    .axis-pill {
      text-align: center;
      padding: 6px 0;
      border-radius: 999px;
      background: rgba(255,255,255,0.03);
      color: var(--muted);
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }

    .axis-pill.is-current {
      color: #e6f6ff;
      background: rgba(105, 200, 255, 0.14);
    }

    .chart-side {
      display: grid;
      gap: 14px;
      min-width: 0;
      align-content: start;
    }

    .mini-panel {
      padding: 14px;
      border-radius: 18px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: linear-gradient(180deg, rgba(15, 29, 50, 0.86), rgba(8, 16, 29, 0.92));
      display: grid;
      gap: 14px;
    }

    .mini-panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: #dff0ff;
      font-size: 13px;
      font-weight: 700;
    }

    .mini-bars {
      display: grid;
      grid-template-columns: repeat(12, minmax(0, 1fr));
      gap: 8px;
      align-items: end;
      min-height: 180px;
    }

    .mini-bar-col {
      display: grid;
      gap: 8px;
      justify-items: center;
      min-width: 0;
    }

    .mini-bar-track {
      position: relative;
      width: 100%;
      min-height: 132px;
      border-radius: 999px;
      background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01));
      overflow: hidden;
      border: 1px solid rgba(132, 166, 218, 0.12);
    }

    .mini-bar {
      position: absolute;
      inset: auto 0 0 0;
      height: var(--bar-height, 0%);
      border-radius: 999px;
      background: linear-gradient(180deg, rgba(201,255,138,0.95), rgba(125,211,82,0.34));
      box-shadow: 0 0 22px rgba(163, 230, 53, 0.22);
      transform-origin: bottom center;
    }

    .mini-bar-value {
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      color: #edf7ff;
    }

    .mini-bar-label {
      font-size: 10px;
      color: var(--muted);
      letter-spacing: .06em;
      text-transform: uppercase;
    }

    .telemetry-stack {
      display: grid;
      gap: 12px;
    }

    .telemetry-block {
      display: grid;
      gap: 6px;
    }

    .telemetry-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      font-size: 12px;
      color: #dcecff;
    }

    .telemetry-row strong {
      font-variant-numeric: tabular-nums;
    }

    .telemetry-meter {
      position: relative;
      height: 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.05);
      overflow: hidden;
      border: 1px solid rgba(132, 166, 218, 0.10);
    }

    .telemetry-fill {
      display: block;
      height: 100%;
      width: 0%;
      border-radius: inherit;
      transition: width .35s ease;
    }

    .telemetry-fill.error { background: linear-gradient(90deg, rgba(255,107,122,0.95), rgba(255,107,122,0.35)); }
    .telemetry-fill.warning { background: linear-gradient(90deg, rgba(247,184,75,0.95), rgba(247,184,75,0.35)); }
    .telemetry-fill.info { background: linear-gradient(90deg, rgba(119,232,255,0.95), rgba(119,232,255,0.35)); }

    .signal-body {
      display: grid;
      gap: 16px;
    }

    .signal-cluster {
      display: grid;
      gap: 16px;
      justify-items: center;
      text-align: center;
    }

    .signal-ring {
      position: relative;
      width: 172px;
      aspect-ratio: 1;
      display: grid;
      place-items: center;
      padding: 14px;
      border-radius: 50%;
      background: conic-gradient(from 180deg, rgba(56,189,248,0.12), rgba(56,189,248,0.9), rgba(56,189,248,0.12));
      box-shadow:
        inset 0 0 22px rgba(56,189,248,0.14),
        0 0 30px rgba(56,189,248,0.12);
      animation: ringPulse 4s ease-in-out infinite;
    }

    .signal-ring::before {
      content: '';
      position: absolute;
      inset: 14px;
      border-radius: 50%;
      background:
        radial-gradient(circle at 32% 28%, rgba(105, 200, 255, 0.18), transparent 34%),
        linear-gradient(180deg, rgba(6, 18, 33, 0.96), rgba(7, 14, 24, 0.98));
      border: 1px solid rgba(132, 166, 218, 0.16);
    }

    .signal-ring[data-level="warn"] {
      background: conic-gradient(from 180deg, rgba(245,158,11,0.10), rgba(245,158,11,0.88), rgba(245,158,11,0.10));
      box-shadow:
        inset 0 0 22px rgba(245,158,11,0.16),
        0 0 30px rgba(245,158,11,0.12);
    }

    .signal-ring[data-level="error"] {
      background: conic-gradient(from 180deg, rgba(255,107,122,0.10), rgba(255,107,122,0.90), rgba(255,107,122,0.10));
      box-shadow:
        inset 0 0 22px rgba(255,107,122,0.16),
        0 0 30px rgba(255,107,122,0.12);
    }

    .signal-ring-core {
      position: relative;
      z-index: 1;
      display: grid;
      gap: 6px;
      justify-items: center;
      text-align: center;
    }

    #signalTone {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .18em;
      text-transform: uppercase;
      color: #9fd8ff;
    }

    #signalPhase {
      max-width: 120px;
      font-size: 20px;
      line-height: 1.25;
    }

    .signal-summary {
      display: grid;
      gap: 8px;
      width: 100%;
    }

    .signal-label {
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .signal-message {
      font-size: 15px;
      line-height: 1.55;
      color: #e5f2ff;
    }

    .signal-feed {
      display: grid;
      gap: 10px;
    }

    .feed-item {
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: rgba(255,255,255,0.03);
      display: grid;
      gap: 6px;
      min-width: 0;
    }

    .feed-label {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .feed-value {
      font-size: 12px;
      line-height: 1.55;
      color: #dcecff;
      word-break: break-word;
    }

    .signal-footer {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .signal-pill {
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(132, 166, 218, 0.14);
      background: rgba(255,255,255,0.03);
      display: grid;
      gap: 4px;
      min-width: 0;
    }

    .signal-pill-label {
      font-size: 11px;
      letter-spacing: .12em;
      text-transform: uppercase;
      color: var(--muted);
    }

    .signal-pill strong {
      font-size: 18px;
      font-variant-numeric: tabular-nums;
    }

    body[data-state="running"] .dot.ok {
      animation: pulseDot 1.8s ease-in-out infinite;
    }

    .hero-card,
    .chart-card,
    .signal-card,
    .table-card,
    .log-card {
      overflow: hidden;
    }

    .signal-card {
      align-self: start;
    }

    @keyframes sceneIn {
      from {
        opacity: 0;
      }
      to {
        opacity: 1;
      }
    }

    @keyframes auroraDrift {
      from { transform: translate3d(-2%, -1%, 0) scale(1); }
      to { transform: translate3d(2%, 1%, 0) scale(1.05); }
    }

    @keyframes gridShift {
      from { transform: translate3d(0, 0, 0); }
      to { transform: translate3d(0, 120px, 0); }
    }

    @keyframes floatOrb {
      0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
      50% { transform: translate3d(-14px, 16px, 0) scale(1.06); }
    }

    @keyframes pulseDot {
      0%, 100% { transform: scale(1); opacity: 1; }
      50% { transform: scale(1.24); opacity: .78; }
    }

    @keyframes ringPulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.02); }
    }

    @media (max-width: 1360px) {
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .overview-grid { grid-template-columns: 1fr; }
      .chart-grid { grid-template-columns: 1fr; }
      .tables { grid-template-columns: 1fr; }
    }

    @media (max-width: 980px) {
      .topbar { grid-template-columns: 1fr; }
      .chart-values { width: 100%; }
      .hero-telemetry { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .topbar-right { justify-content: flex-start; }
      .settings-form-shell { grid-template-columns: 1fr; }
      .settings-tabs {
        grid-template-columns: repeat(4, minmax(160px, 1fr));
        grid-auto-flow: column;
        overflow-x: auto;
        border-right: 0;
        border-bottom: 1px solid rgba(132, 166, 218, 0.14);
      }
    }

    @media (max-width: 720px) {
      .stats { grid-template-columns: 1fr; }
      body { padding: 10px; }
      .tables { grid-template-columns: 1fr; }
      .hero-telemetry,
      .signal-footer,
      .chart-values { grid-template-columns: 1fr; }
      .mini-bars,
      .axis-row { gap: 6px; }
      .brand-mark { width: 56px; height: 56px; }
      .chart-canvas,
      .chart-svg { min-height: 220px; height: 220px; }
      .control-buttons, .buttons { flex-direction: column; align-items: stretch; }
      .control-inline-form { width: 100%; }
      .control-btn, .btn { width: 100%; }
      button, .btn { white-space: normal; }
      .settings-modal { padding: 10px; }
      .settings-dialog { max-height: calc(100vh - 20px); }
      .settings-header,
      .settings-footer { padding-left: 16px; padding-right: 16px; }
      .settings-header { flex-direction: column; }
      .settings-grid { grid-template-columns: 1fr; }
      .settings-panels { padding: 16px; }
      .settings-footer,
      .settings-footer-actions { flex-direction: column; align-items: stretch; }
      .settings-tab { min-width: 150px; }
    }

    @media (prefers-reduced-motion: reduce) {
      *,
      *::before,
      *::after {
        animation: none !important;
        transition: none !important;
        scroll-behavior: auto !important;
      }
    }
  </style>
</head>
<body data-state="{{ 'running' if run_status == '运行中' else 'idle' }}">
  <svg class="sprite-defs" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
    <symbol id="i-orbit" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="2.2"></circle>
      <ellipse cx="12" cy="12" rx="8.5" ry="4.25"></ellipse>
      <path d="M12 3.25c3.55 0 6.5 3.92 6.5 8.75S15.55 20.75 12 20.75 5.5 16.83 5.5 12 8.45 3.25 12 3.25Z"></path>
    </symbol>
    <symbol id="i-folder" viewBox="0 0 24 24">
      <path d="M3.5 7.5h6l1.8 2h9.2v7.8a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7.5Z"></path>
      <path d="M3.5 9.5h17"></path>
    </symbol>
    <symbol id="i-browser" viewBox="0 0 24 24">
      <rect x="3.5" y="5" width="17" height="14" rx="2.4"></rect>
      <path d="M3.5 9h17"></path>
      <path d="M7 7h.01"></path>
      <path d="M10 7h.01"></path>
      <path d="M13 7h.01"></path>
    </symbol>
    <symbol id="i-wave" viewBox="0 0 24 24">
      <path d="M3.5 12h2.8l2.1-4.5 3.2 9 2.8-6 2.1 3.5h4"></path>
    </symbol>
    <symbol id="i-refresh" viewBox="0 0 24 24">
      <path d="M20 11a8 8 0 0 0-14-4.8"></path>
      <path d="M4 5.5v4h4"></path>
      <path d="M4 13a8 8 0 0 0 14 4.8"></path>
      <path d="M20 18.5v-4h-4"></path>
    </symbol>
    <symbol id="i-settings" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3"></circle>
      <path d="M19 12a7 7 0 0 0-.08-1l2.03-1.58-2-3.46-2.44.83a7.1 7.1 0 0 0-1.72-1l-.42-2.53h-4l-.42 2.53a7.1 7.1 0 0 0-1.72 1l-2.44-.83-2 3.46L5.08 11a7 7 0 0 0 0 2l-2.03 1.58 2 3.46 2.44-.83a7.1 7.1 0 0 0 1.72 1l.42 2.53h4l.42-2.53a7.1 7.1 0 0 0 1.72-1l2.44.83 2-3.46L18.92 13c.05-.33.08-.66.08-1Z"></path>
    </symbol>
    <symbol id="i-play" viewBox="0 0 24 24">
      <path d="M8 6.5v11l9-5.5Z"></path>
    </symbol>
    <symbol id="i-repeat" viewBox="0 0 24 24">
      <path d="M17.5 7H7.8a3.8 3.8 0 0 0 0 7.6H10"></path>
      <path d="M14 4.5 17.5 7 14 9.5"></path>
      <path d="M6.5 17h9.7a3.8 3.8 0 0 0 0-7.6H14"></path>
      <path d="M10 14.5 6.5 17 10 19.5"></path>
    </symbol>
    <symbol id="i-stop" viewBox="0 0 24 24">
      <rect x="6.5" y="6.5" width="11" height="11" rx="2"></rect>
    </symbol>
    <symbol id="i-alert" viewBox="0 0 24 24">
      <path d="M12 4.25 20 19H4L12 4.25Z"></path>
      <path d="M12 9v4.8"></path>
      <circle cx="12" cy="16.8" r="0.8" fill="currentColor" stroke="none"></circle>
    </symbol>
    <symbol id="i-chart" viewBox="0 0 24 24">
      <path d="M4 18.5h16"></path>
      <path d="M7 16V11"></path>
      <path d="M12 16V7"></path>
      <path d="M17 16v-3.5"></path>
    </symbol>
    <symbol id="i-keyword" viewBox="0 0 24 24">
      <circle cx="8.5" cy="11.5" r="3.5"></circle>
      <path d="M11.2 14.2 20 23"></path>
      <path d="M15.5 18.5h2.5"></path>
      <path d="M17.5 16.5V21"></path>
    </symbol>
    <symbol id="i-log" viewBox="0 0 24 24">
      <path d="M6 4.5h9l3 3V19a1.8 1.8 0 0 1-1.8 1.8H7.8A1.8 1.8 0 0 1 6 19V4.5Z"></path>
      <path d="M15 4.5V8h3"></path>
      <path d="M8.5 12h7"></path>
      <path d="M8.5 15.5h7"></path>
    </symbol>
    <symbol id="i-activity" viewBox="0 0 24 24">
      <path d="M3.5 13h4l2.2-5 4.1 9 2.2-4H20.5"></path>
    </symbol>
    <symbol id="i-chat" viewBox="0 0 24 24">
      <path d="M5 6.5h14v9H9l-4 3v-12Z"></path>
      <path d="M8.5 10.5h7"></path>
      <path d="M8.5 13.5h5"></path>
    </symbol>
    <symbol id="i-archive" viewBox="0 0 24 24">
      <path d="M4.5 7.5h15v11a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2v-11Z"></path>
      <path d="M3.5 4.5h17v3h-17Z"></path>
      <path d="M9.5 12h5"></path>
    </symbol>
  </svg>

  <div class="wrap">
    <div class="card topbar hero-card">
      <div class="topbar-left">
        <div class="eyebrow">
          <span class="eyebrow-chip">
            <svg class="glyph"><use href="#i-orbit"></use></svg>
            快手养号 / 自动评论
          </span>
          <span class="eyebrow-note">运行监控与参数配置</span>
        </div>
        <div class="title-row">
          <div class="brand-mark">
            <svg class="glyph"><use href="#i-orbit"></use></svg>
          </div>
          <div class="title-copy">
            <h1>KS Auto Commenter 控制台</h1>
            <div class="topbar-copy">用于快手养号、自动评论、关键词管理和运行状态监控。</div>
          </div>
        </div>
        <div class="topbar-meta">
          <span class="meta-chip">
            <svg class="glyph"><use href="#i-folder"></use></svg>
            配置文件：<span id="configPath">{{ config_path }}</span>
          </span>
          <span class="meta-chip">
            <svg class="glyph"><use href="#i-wave"></use></svg>
            当前阶段：<strong id="heroPhase">{{ initial_phase }}</strong>
          </span>
        </div>
      </div>
      <div class="topbar-right">
        <span class="pill pill-live" id="runBadge"><span class="dot"></span><span id="runText">{{ run_status }}</span></span>
        <span class="pill">
          <svg class="glyph"><use href="#i-refresh"></use></svg>
          <span>最近刷新：<span id="lastUpdated">--</span></span>
        </span>
        <div class="hero-telemetry">
          <div class="mini-readout">
            <span>错误</span>
            <strong id="heroErrorCount">0</strong>
          </div>
          <div class="mini-readout">
            <span>警告</span>
            <strong id="heroWarnCount">0</strong>
          </div>
          <div class="mini-readout">
            <span>信息</span>
            <strong id="heroInfoCount">0</strong>
          </div>
        </div>
      </div>
    </div>

    <div class="card control-panel">
      <div class="panel-body">
        <div class="control-header">
          <div class="section-title-main">
            <span class="icon-shell small accent-cyan">
              <svg class="glyph"><use href="#i-activity"></use></svg>
            </span>
            <span>
              <span>控制面板</span>
              <small>启动任务、持续运行任务或手动停止任务</small>
            </span>
          </div>
          {% if message %}<div class="flash-msg">{{ message }}</div>{% endif %}
        </div>
        <div class="control-buttons">
          <button type="button" class="btn secondary" id="settingsToggle">
            <svg class="glyph"><use href="#i-settings"></use></svg>
            <span>任务设置</span>
          </button>

          <form class="control-inline-form" method="post" action="{{ url_for('run_once') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn" type="submit" name="run_mode" value="once">
              <svg class="glyph"><use href="#i-play"></use></svg>
              <span>开始任务（单轮）</span>
            </button>
          </form>

          <form class="control-inline-form" method="post" action="{{ url_for('run_once') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn" type="submit" name="run_mode" value="loop">
              <svg class="glyph"><use href="#i-repeat"></use></svg>
              <span>开始任务（持续）</span>
            </button>
          </form>

          <form class="control-inline-form" method="post" action="{{ url_for('stop_task') }}">
            <input type="hidden" name="config_path" value="{{ config_path }}" />
            <button class="control-btn stop" type="submit">
              <svg class="glyph"><use href="#i-stop"></use></svg>
              <span>停止任务</span>
            </button>
          </form>

          <a class="btn secondary" href="{{ url_for('index', config_path=config_path) }}">
            <svg class="glyph"><use href="#i-refresh"></use></svg>
            <span>手动刷新</span>
          </a>
        </div>
        <div class="muted">用于启动单轮任务、持续任务和停止当前任务。程序会自动检查登录态并尝试关闭连播。</div>
      </div>
    </div>

    <div class="stats">
      <div class="card stat stat-cyan">
        <div class="metric-head">
          <div>
            <div class="k">今日评论数</div>
            <div class="metric-sub">今日写入数据库</div>
          </div>
          <span class="icon-shell small accent-cyan"><svg class="glyph"><use href="#i-chat"></use></svg></span>
        </div>
        <div class="v" id="statToday">{{ initial_stats.today_comments }}</div>
        <div class="metric-foot" id="statTodayTrend">近 3 小时窗口 --</div>
      </div>
      <div class="card stat stat-blue">
        <div class="metric-head">
          <div>
            <div class="k">累计评论数</div>
            <div class="metric-sub">历史累计记录</div>
          </div>
          <span class="icon-shell small accent-blue"><svg class="glyph"><use href="#i-archive"></use></svg></span>
        </div>
        <div class="v" id="statTotal">{{ initial_stats.total_comments }}</div>
        <div class="metric-foot" id="statTotalTrend">图表窗口评论 --</div>
      </div>
      <div class="card stat stat-lime">
        <div class="metric-head">
          <div>
            <div class="k">关键词历史数</div>
            <div class="metric-sub">关键词历史记录</div>
          </div>
          <span class="icon-shell small accent-lime"><svg class="glyph"><use href="#i-keyword"></use></svg></span>
        </div>
        <div class="v" id="statKeyword">{{ initial_stats.keyword_history_total }}</div>
        <div class="metric-foot" id="statKeywordTrend">近 3 小时扩词 --</div>
      </div>
      <div class="card stat stat-phase">
        <div class="metric-head">
          <div>
            <div class="k">当前阶段</div>
            <div class="metric-sub">从运行日志推断</div>
          </div>
          <span class="icon-shell small accent-amber"><svg class="glyph"><use href="#i-wave"></use></svg></span>
        </div>
        <div class="v phase-value" id="statPhase">{{ initial_phase }}</div>
        <div class="metric-foot" id="statPhaseNote">等待轮询数据</div>
      </div>
    </div>

    <div class="overview-grid">
      <div class="card chart-card">
        <h3 class="section-title section-title-row">
          <span class="section-title-main">
            <span class="icon-shell small accent-cyan"><svg class="glyph"><use href="#i-chart"></use></svg></span>
            <span>
              <span>活动图表</span>
              <small>最近 12 小时评论与关键词记录</small>
            </span>
          </span>
          <span class="section-tag">12 小时</span>
        </h3>
        <div class="panel-body chart-grid">
          <div class="chart-stage">
            <div class="chart-meta">
              <div class="legend">
                <span class="legend-item comments">评论量</span>
                <span class="legend-item keywords">关键词数</span>
              </div>
              <div class="chart-values">
                <div class="chart-value"><span>评论峰值</span><strong id="metricCommentPeak">0</strong></div>
                <div class="chart-value"><span>关键词峰值</span><strong id="metricKeywordPeak">0</strong></div>
                <div class="chart-value"><span>窗口总量</span><strong id="metricWindowTotal">0</strong></div>
              </div>
            </div>
            <div class="chart-canvas">
              <div class="chart-grid-lines"></div>
              <svg id="activityChart" class="chart-svg" viewBox="0 0 720 220" preserveAspectRatio="none" aria-hidden="true"></svg>
            </div>
            <div class="axis-row" id="activityAxis"></div>
          </div>
          <div class="chart-side">
            <div class="mini-panel">
              <div class="mini-panel-head">
                <span>关键词活跃度</span>
                <span class="section-tag">12 小时</span>
              </div>
              <div class="mini-bars" id="keywordBars"></div>
            </div>
            <div class="mini-panel">
              <div class="mini-panel-head">
                <span>日志统计</span>
                <span class="section-tag" id="logLineBadge">0 行</span>
              </div>
              <div class="telemetry-stack">
                <div class="telemetry-block">
                  <div class="telemetry-row"><span>错误</span><strong id="telemetryErrorCount">0</strong></div>
                  <div class="telemetry-meter"><span class="telemetry-fill error" id="telemetryErrorFill"></span></div>
                </div>
                <div class="telemetry-block">
                  <div class="telemetry-row"><span>警告</span><strong id="telemetryWarnCount">0</strong></div>
                  <div class="telemetry-meter"><span class="telemetry-fill warning" id="telemetryWarnFill"></span></div>
                </div>
                <div class="telemetry-block">
                  <div class="telemetry-row"><span>信息</span><strong id="telemetryInfoCount">0</strong></div>
                  <div class="telemetry-meter"><span class="telemetry-fill info" id="telemetryInfoFill"></span></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="card signal-card">
        <h3 class="section-title section-title-row">
          <span class="section-title-main">
            <span class="icon-shell small accent-blue"><svg class="glyph"><use href="#i-wave"></use></svg></span>
            <span>
              <span>任务状态</span>
              <small>当前状态、最近告警和日志摘要</small>
            </span>
          </span>
        </h3>
        <div class="panel-body signal-body">
          <div class="signal-cluster">
            <div class="signal-ring" id="signalRing" data-level="info">
              <div class="signal-ring-core">
                <span id="signalTone">空闲</span>
                <strong id="signalPhase">{{ initial_phase }}</strong>
              </div>
            </div>
            <div class="signal-summary">
              <div class="signal-label">当前摘要</div>
              <div class="signal-message" id="signalMessage">{{ initial_alert_message }}</div>
              <div class="muted" id="signalHint">{{ initial_alert_hint }}</div>
            </div>
          </div>

          <div class="signal-feed">
            <div class="feed-item">
              <span class="feed-label">最近错误</span>
              <span class="feed-value" id="lastError">--</span>
            </div>
            <div class="feed-item">
              <span class="feed-label">最近警告</span>
              <span class="feed-value" id="lastWarning">--</span>
            </div>
            <div class="feed-item">
              <span class="feed-label">最近信息</span>
              <span class="feed-value" id="lastInfo">--</span>
            </div>
          </div>

          <div class="signal-footer">
            <div class="signal-pill">
              <span class="signal-pill-label">评论记录</span>
              <strong id="commentCountBadge">0 条</strong>
            </div>
            <div class="signal-pill">
              <span class="signal-pill-label">关键词记录</span>
              <strong id="keywordCountBadge">0 条</strong>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="layout">
      <div>
        <div class="tables">
          <div class="card table-card">
            <h3 class="section-title section-title-row">
              <span class="section-title-main">
                <span class="icon-shell small accent-cyan"><svg class="glyph"><use href="#i-chat"></use></svg></span>
                <span>
                  <span>评论日志</span>
                  <small>每秒动态刷新</small>
                </span>
              </span>
              <span class="section-tag" id="commentTableBadge">0 条</span>
            </h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th style="width:108px">时间</th><th style="width:72px">关键词</th><th style="width:150px">帖子ID</th><th>评论内容</th></tr></thead>
                <tbody id="commentRows"></tbody>
              </table>
            </div>
          </div>

          <div class="card table-card">
            <h3 class="section-title section-title-row">
              <span class="section-title-main">
                <span class="icon-shell small accent-lime"><svg class="glyph"><use href="#i-keyword"></use></svg></span>
                <span>
                  <span>关键词历史</span>
                  <small>防重复扩词轨迹</small>
                </span>
              </span>
              <span class="section-tag" id="keywordTableBadge">0 条</span>
            </h3>
            <div class="table-wrap">
              <table>
                <thead><tr><th style="width:108px">时间</th><th style="width:84px">方向词</th><th>已用关键词</th></tr></thead>
                <tbody id="keywordRows"></tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="card log-card">
          <h3 class="section-title section-title-row">
            <span class="section-title-main">
              <span class="icon-shell small accent-blue"><svg class="glyph"><use href="#i-log"></use></svg></span>
              <span>
                <span>运行日志</span>
                <small>每秒轮询 tail</small>
              </span>
            </span>
            <span class="section-tag" id="runtimeLogBadge">0 行</span>
          </h3>
          <pre id="runtimeLog"></pre>
          <div class="row-inline">
            <span class="muted">每 1 秒刷新；可用于观察运行状态与报错</span>
            <label class="check" style="margin:0;"><input type="checkbox" id="autoScroll" checked /> 自动滚动到底部</label>
          </div>
        </div>
      </div>
      </div>
    </div>

      <div class="settings-modal hidden" id="settingsPanel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
        <div class="settings-dialog">
          <div class="settings-header">
            <div class="settings-heading">
              <span class="section-title-main">
                <span class="icon-shell small accent-blue"><svg class="glyph"><use href="#i-settings"></use></svg></span>
                <span>
                  <span id="settingsTitle">任务设置</span>
                  <small>按评论、偏好、模型和浏览器四个模块调整运行参数。</small>
                </span>
              </span>
              <div class="settings-subtitle">所有改动保存到当前配置文件，用于快手养号、自动评论和浏览器运行控制。</div>
            </div>
            <button type="button" class="settings-close" id="settingsClose">关闭</button>
          </div>

          <form id="settingsForm" method="post" action="{{ url_for('save') }}" class="settings-form">
            <input type="hidden" name="config_path" value="{{ config_path }}" />

            <div class="settings-form-shell">
              <div class="settings-tabs" role="tablist" aria-label="设置分类">
                <button type="button" class="settings-tab is-active" id="tabComment" data-tab-target="comment" role="tab" aria-selected="true" aria-controls="panelComment">
                  <span class="icon-shell small accent-cyan"><svg class="glyph"><use href="#i-chat"></use></svg></span>
                  <span class="settings-tab-copy">
                    <span class="settings-tab-title">评论配置</span>
                    <span class="settings-tab-note">评论要求、评论上限和评论策略</span>
                  </span>
                </button>
                <button type="button" class="settings-tab" id="tabPreference" data-tab-target="preference" role="tab" aria-selected="false" aria-controls="panelPreference">
                  <span class="icon-shell small accent-lime"><svg class="glyph"><use href="#i-keyword"></use></svg></span>
                  <span class="settings-tab-copy">
                    <span class="settings-tab-title">偏好配置</span>
                    <span class="settings-tab-note">方向词、扩词方式和抓取范围</span>
                  </span>
                </button>
                <button type="button" class="settings-tab" id="tabModel" data-tab-target="model" role="tab" aria-selected="false" aria-controls="panelModel">
                  <span class="icon-shell small accent-blue"><svg class="glyph"><use href="#i-orbit"></use></svg></span>
                  <span class="settings-tab-copy">
                    <span class="settings-tab-title">模型配置</span>
                    <span class="settings-tab-note">Base URL、模型 ID、API Key 和连通测试</span>
                  </span>
                </button>
                <button type="button" class="settings-tab" id="tabBrowser" data-tab-target="browser" role="tab" aria-selected="false" aria-controls="panelBrowser">
                  <span class="icon-shell small accent-amber"><svg class="glyph"><use href="#i-browser"></use></svg></span>
                  <span class="settings-tab-copy">
                    <span class="settings-tab-title">浏览器配置</span>
                    <span class="settings-tab-note">浏览器显示、连接方式和执行路径</span>
                  </span>
                </button>
              </div>

              <div class="settings-panels">
                <section class="settings-panel is-active" id="panelComment" data-tab-panel="comment" role="tabpanel" aria-labelledby="tabComment">
                  <div class="settings-panel-head">
                    <h4>评论配置</h4>
                    <p>控制评论筛选方式、评论上限和评论生成要求。</p>
                  </div>
                  <div class="settings-grid">
                    <div class="setting-card full">
                      <span class="setting-chip">评论筛选</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">启用评论前判定</div>
                          <div class="setting-hint">开启后先判断帖子是否适合评论；关闭后跳过筛选，直接对抓取到的帖子生成评论。默认关闭。</div>
                        </div>
                        <label class="switch" for="enable_commentability_check">
                          <input id="enable_commentability_check" class="switch-input" type="checkbox" name="enable_commentability_check" {% if cfg.ai.enable_commentability_check %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">判定执行</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">严格执行判定结果</div>
                          <div class="setting-hint">仅在启用评论前判定时生效。开启后 AI 判定不适合评论的帖子会直接跳过。</div>
                        </div>
                        <label class="switch" for="strict_comment_gate">
                          <input id="strict_comment_gate" class="switch-input" type="checkbox" name="strict_comment_gate" {% if cfg.ai.strict_comment_gate %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">评论覆盖</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">尽量每条都评论</div>
                          <div class="setting-hint">开启后当候选评论被过滤时仍继续尝试兜底评论，适合提高交互频次。</div>
                        </div>
                        <label class="switch" for="comment_every_post">
                          <input id="comment_every_post" class="switch-input" type="checkbox" name="comment_every_post" {% if cfg.runtime.comment_every_post %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card">
                      <label for="max_comments_per_round">每轮评论上限</label>
                      <div class="setting-hint">控制单轮任务最多写入多少条评论。</div>
                      <input id="max_comments_per_round" type="number" min="1" max="2000000" name="max_comments_per_round" value="{{ cfg.runtime.max_comments_per_round }}" />
                    </div>

                    <div class="setting-card">
                      <label for="daily_comment_limit">每日评论上限</label>
                      <div class="setting-hint">达到上限后，当天剩余轮次不会继续评论。</div>
                      <input id="daily_comment_limit" type="number" min="1" max="5000000" name="daily_comment_limit" value="{{ cfg.runtime.daily_comment_limit }}" />
                    </div>

                    <div class="setting-card full">
                      <label for="requirements">基础评论要求（每行一条）</label>
                      <div class="setting-hint">写入通用约束，例如字数、结构和互动方式。</div>
                      <textarea id="requirements" name="requirements">{{ requirements_text }}</textarea>
                    </div>

                    <div class="setting-card full">
                      <label for="style_prompt">评论风格</label>
                      <div class="setting-hint">例如自然、真诚、轻松、像普通用户留言，不要写成模板话术。</div>
                      <textarea id="style_prompt" name="style_prompt">{{ style_prompt_text }}</textarea>
                    </div>

                    <div class="setting-card full">
                      <label for="content_prompt">评论内容侧重</label>
                      <div class="setting-hint">例如优先认可观点、围绕求职交流、补一句提问，或聚焦某个内容点。</div>
                      <textarea id="content_prompt" name="content_prompt">{{ content_prompt_text }}</textarea>
                    </div>
                  </div>
                </section>

                <section class="settings-panel" id="panelPreference" data-tab-panel="preference" role="tabpanel" aria-labelledby="tabPreference" hidden>
                  <div class="settings-panel-head">
                    <h4>偏好配置</h4>
                    <p>控制方向词、关键词扩展方式以及每轮搜索偏好。</p>
                  </div>
                  <div class="settings-grid">
                    <div class="setting-card full">
                      <label for="direction_keywords">方向词（使用 & 分隔）</label>
                      <div class="setting-hint">例如：找搭子 & 找工作 & 广东 & 求职。保存时也兼容中英文逗号。</div>
                      <input id="direction_keywords" type="text" name="direction_keywords" value="{{ direction_keywords }}" />
                    </div>

                    <div class="setting-card">
                      <label for="keyword_max_count">关键词扩展数量</label>
                      <div class="setting-hint">控制 AI 每轮最多扩展多少个新关键词。</div>
                      <input id="keyword_max_count" type="number" min="1" max="20" name="keyword_max_count" value="{{ cfg.ai.keyword_max_count }}" />
                    </div>

                    <div class="setting-card">
                      <label for="search_limit_per_keyword">每个关键词抓取帖子数</label>
                      <div class="setting-hint">控制每个关键词最多分析多少条帖子。</div>
                      <input id="search_limit_per_keyword" type="number" min="1" max="2000000" name="search_limit_per_keyword" value="{{ cfg.runtime.search_limit_per_keyword }}" />
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">关键词来源</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">每轮每个方向只搜索 1 个关键词</div>
                          <div class="setting-hint">开启后每轮只取 AI 扩展结果中的第一个关键词，适合更稳的养号节奏。</div>
                        </div>
                        <label class="switch" for="single_keyword_search">
                          <input id="single_keyword_search" class="switch-input" type="checkbox" name="single_keyword_search" {% if cfg.runtime.single_keyword_search %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">扩词开关</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">关闭关键词联想</div>
                          <div class="setting-hint">开启后只按输入关键词直搜，不再使用 AI 扩词。</div>
                        </div>
                        <label class="switch" for="disable_keyword_expansion">
                          <input id="disable_keyword_expansion" class="switch-input" type="checkbox" name="disable_keyword_expansion" {% if cfg.runtime.disable_keyword_expansion %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>
                  </div>
                </section>

                <section class="settings-panel" id="panelModel" data-tab-panel="model" role="tabpanel" aria-labelledby="tabModel" hidden>
                  <div class="settings-panel-head">
                    <h4>模型配置</h4>
                    <p>用于配置评论生成和关键词扩展所使用的模型连接。</p>
                  </div>
                  <div class="settings-grid">
                    <div class="setting-card full">
                      <label for="openai_base_url">模型 Base URL</label>
                      <div class="setting-hint">填写接口地址，例如代理地址或兼容 OpenAI 的服务地址。</div>
                      <input id="openai_base_url" type="text" name="openai_base_url" value="{{ cfg.openai.base_url if cfg.openai else '' }}" placeholder="https://gmn.chuangzuoli.com" />
                    </div>

                    <div class="setting-card">
                      <label for="openai_model_id">模型 ID</label>
                      <div class="setting-hint">填写实际调用的模型标识，例如 `gpt-5.3-codex`。</div>
                      <input id="openai_model_id" type="text" name="openai_model_id" value="{{ cfg.openai.model_id if cfg.openai else '' }}" placeholder="gpt-5.3-codex" />
                    </div>

                    <div class="setting-card">
                      <label for="openai_api_key">API Key</label>
                      <div class="setting-hint">保存到当前配置文件，用于发起模型请求。</div>
                      <input id="openai_api_key" type="text" name="openai_api_key" value="{{ cfg.openai.api_key if cfg.openai else '' }}" placeholder="sk-..." />
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">连通检查</span>
                      <div class="setting-hint">保存前可先测试当前模型配置是否能正常连接。</div>
                      <div class="buttons">
                        <button type="button" class="btn secondary" id="testConnectionBtn">测试模型连接</button>
                      </div>
                      <pre id="testConnectionLog" class="test-log">点击“测试模型连接”检查 Base URL、模型 ID 和 API Key 是否可用。</pre>
                    </div>
                  </div>
                </section>

                <section class="settings-panel" id="panelBrowser" data-tab-panel="browser" role="tabpanel" aria-labelledby="tabBrowser" hidden>
                  <div class="settings-panel-head">
                    <h4>浏览器配置</h4>
                    <p>用于控制 Playwright 启动方式、已有 Chrome 连接和搜索行为。</p>
                  </div>
                  <div class="settings-grid">
                    <div class="setting-card full">
                      <span class="setting-chip">运行方式</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">显示浏览器窗口</div>
                          <div class="setting-hint">开启后弹出真实浏览器窗口，便于扫码登录和观察运行过程；关闭后后台无头运行。</div>
                        </div>
                        <label class="switch" for="headless">
                          <input id="headless" class="switch-input" type="checkbox" name="headless" {% if not cfg.browser.headless %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card full">
                      <span class="setting-chip">搜索模式</span>
                      <div class="setting-toggle-head">
                        <div class="setting-toggle-copy">
                          <div class="setting-title">每条帖子重新搜索</div>
                          <div class="setting-hint">开启后每处理一条帖子都回到搜索流程；关闭后同一关键词只搜索一次并连续处理帖子。</div>
                        </div>
                        <label class="switch" for="search_each_post">
                          <input id="search_each_post" class="switch-input" type="checkbox" name="search_each_post" {% if cfg.browser.search_each_post %}checked{% endif %} />
                          <span class="switch-slider"></span>
                        </label>
                      </div>
                    </div>

                    <div class="setting-card full">
                      <label for="ws_url">CDP 远程调试地址（可选）</label>
                      <div class="setting-hint">填写后优先连接已启动的 Chrome 调试端口；留空则自动拉起浏览器。</div>
                      <input id="ws_url" type="text" name="ws_url" value="{{ cfg.browser.ws_url or '' }}" placeholder="如 http://127.0.0.1:9222，留空则自动启动" />
                    </div>

                    <div class="setting-card full">
                      <label for="executable_path">Chrome 可执行文件路径（可选）</label>
                      <div class="setting-hint">留空时自动检测；填写后优先使用这个 Chrome 路径。</div>
                      <input id="executable_path" type="text" name="executable_path" value="{{ cfg.browser.executable_path or '' }}" placeholder="如 C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" />
                    </div>
                  </div>
                </section>
              </div>
            </div>

            <div class="settings-footer">
              <div class="settings-footer-copy">修改完成后点击“保存配置”，任务将按新的参数执行。</div>
              <div class="settings-footer-actions">
                <button type="button" class="btn secondary" id="settingsCancel">关闭</button>
                <button type="submit">保存配置</button>
              </div>
            </div>
          </form>
        </div>
      </div>


  <script>
    const configPath = {{ config_path | tojson }};
    const initialData = {{ initial_payload | tojson }};

    const bodyEl = document.body;
    const runBadge = document.getElementById('runBadge');
    const runText = document.getElementById('runText');
    const lastUpdated = document.getElementById('lastUpdated');
    const heroPhase = document.getElementById('heroPhase');
    const heroErrorCount = document.getElementById('heroErrorCount');
    const heroWarnCount = document.getElementById('heroWarnCount');
    const heroInfoCount = document.getElementById('heroInfoCount');

    const statToday = document.getElementById('statToday');
    const statTotal = document.getElementById('statTotal');
    const statKeyword = document.getElementById('statKeyword');
    const statPhase = document.getElementById('statPhase');
    const statTodayTrend = document.getElementById('statTodayTrend');
    const statTotalTrend = document.getElementById('statTotalTrend');
    const statKeywordTrend = document.getElementById('statKeywordTrend');
    const statPhaseNote = document.getElementById('statPhaseNote');

    const metricCommentPeak = document.getElementById('metricCommentPeak');
    const metricKeywordPeak = document.getElementById('metricKeywordPeak');
    const metricWindowTotal = document.getElementById('metricWindowTotal');
    const activityChart = document.getElementById('activityChart');
    const activityAxis = document.getElementById('activityAxis');
    const keywordBars = document.getElementById('keywordBars');
    const logLineBadge = document.getElementById('logLineBadge');
    const runtimeLogBadge = document.getElementById('runtimeLogBadge');
    const telemetryErrorCount = document.getElementById('telemetryErrorCount');
    const telemetryWarnCount = document.getElementById('telemetryWarnCount');
    const telemetryInfoCount = document.getElementById('telemetryInfoCount');
    const telemetryErrorFill = document.getElementById('telemetryErrorFill');
    const telemetryWarnFill = document.getElementById('telemetryWarnFill');
    const telemetryInfoFill = document.getElementById('telemetryInfoFill');

    const signalRing = document.getElementById('signalRing');
    const signalTone = document.getElementById('signalTone');
    const signalPhase = document.getElementById('signalPhase');
    const signalMessage = document.getElementById('signalMessage');
    const signalHint = document.getElementById('signalHint');
    const lastError = document.getElementById('lastError');
    const lastWarning = document.getElementById('lastWarning');
    const lastInfo = document.getElementById('lastInfo');
    const commentCountBadge = document.getElementById('commentCountBadge');
    const keywordCountBadge = document.getElementById('keywordCountBadge');
    const commentTableBadge = document.getElementById('commentTableBadge');
    const keywordTableBadge = document.getElementById('keywordTableBadge');

    const commentRows = document.getElementById('commentRows');
    const keywordRows = document.getElementById('keywordRows');
    const runtimeLog = document.getElementById('runtimeLog');
    const autoScroll = document.getElementById('autoScroll');

    const settingsToggle = document.getElementById('settingsToggle');
    const settingsPanel = document.getElementById('settingsPanel');
    const settingsClose = document.getElementById('settingsClose');
    const settingsCancel = document.getElementById('settingsCancel');
    const settingsForm = document.getElementById('settingsForm');
    const testConnectionBtn = document.getElementById('testConnectionBtn');
    const testConnectionLog = document.getElementById('testConnectionLog');
    const settingsTabButtons = Array.from(document.querySelectorAll('[data-tab-target]'));
    const settingsTabPanels = Array.from(document.querySelectorAll('[data-tab-panel]'));
    let activeSettingsTab = 'comment';
    let lastActivitySignature = '';

    function esc(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function tryRender(label, fn) {
      try {
        fn();
      } catch (err) {
        console.error(`render ${label} failed`, err);
      }
    }

    function clip(value, maxLen = 96) {
      const text = String(value == null ? '' : value).trim();
      if (!text) return '--';
      return text.length > maxLen ? `${text.slice(0, Math.max(0, maxLen - 3))}...` : text;
    }

    function toNum(value) {
      const num = Number(value);
      return Number.isFinite(num) ? num : 0;
    }

    function sum(values) {
      return (Array.isArray(values) ? values : []).reduce((acc, value) => acc + toNum(value), 0);
    }

    function recentWindow(values, size = 3) {
      const safe = Array.isArray(values) ? values : [];
      return sum(safe.slice(Math.max(0, safe.length - size)));
    }

    function previousWindow(values, size = 3) {
      const safe = Array.isArray(values) ? values : [];
      return sum(safe.slice(Math.max(0, safe.length - size * 2), Math.max(0, safe.length - size)));
    }

    function describeWindow(prefix, values) {
      const recent = recentWindow(values, 3);
      const prev = previousWindow(values, 3);
      const delta = recent - prev;
      const signed = delta > 0 ? `+${delta}` : `${delta}`;
      return `${prefix}${recent} 条 / 对比上一窗口 ${signed}`;
    }

    function seriesPoints(values, maxValue, width, height, padX, padY) {
      const safe = Array.isArray(values) ? values : [];
      const spanX = Math.max(1, width - padX * 2);
      const spanY = Math.max(1, height - padY * 2);
      const denominator = Math.max(1, safe.length - 1);
      return safe.map((value, index) => {
        const x = padX + (spanX * index) / denominator;
        const y = height - padY - (spanY * toNum(value)) / Math.max(maxValue, 1);
        return { x, y };
      });
    }

    function linePath(points) {
      if (!points.length) return '';
      return points.map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(' ');
    }

    function areaPath(points, baseY) {
      if (!points.length) return '';
      const start = points[0];
      const end = points[points.length - 1];
      return `${linePath(points)} L${end.x.toFixed(2)},${baseY.toFixed(2)} L${start.x.toFixed(2)},${baseY.toFixed(2)} Z`;
    }

    function renderActivity(activity) {
      const labels = Array.isArray(activity && activity.labels) ? activity.labels : [];
      const comments = labels.map((_, index) => toNum(activity && activity.comments && activity.comments[index]));
      const keywords = labels.map((_, index) => toNum(activity && activity.keywords && activity.keywords[index]));
      const peakComment = Math.max(0, ...comments);
      const peakKeyword = Math.max(0, ...keywords);
      const maxValue = Math.max(1, peakComment, peakKeyword);
      const totalWindow = sum(comments) + sum(keywords);
      const activitySignature = JSON.stringify({ labels, comments, keywords });

      metricCommentPeak.textContent = peakComment;
      metricKeywordPeak.textContent = peakKeyword;
      metricWindowTotal.textContent = totalWindow;

      statTodayTrend.textContent = describeWindow('近 3 小时评论 ', comments);
      statTotalTrend.textContent = `图表窗口评论 ${sum(comments)} 条`;
      statKeywordTrend.textContent = describeWindow('近 3 小时关键词 ', keywords);

      if (!labels.length) {
        lastActivitySignature = '';
        if (activityChart) activityChart.innerHTML = '';
        if (activityAxis) activityAxis.innerHTML = '';
        if (keywordBars) keywordBars.innerHTML = '';
        return;
      }

      if (activitySignature === lastActivitySignature) {
        return;
      }
      lastActivitySignature = activitySignature;

      const width = 720;
      const height = 220;
      const padX = 18;
      const padY = 18;
      const baseY = height - padY;
      const commentPoints = seriesPoints(comments, maxValue, width, height, padX, padY);
      const keywordPoints = seriesPoints(keywords, maxValue, width, height, padX, padY);

      if (activityChart) {
        activityChart.innerHTML = `
          <defs>
            <linearGradient id="commentsFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stop-color="rgba(119, 232, 255, 0.38)"></stop>
              <stop offset="100%" stop-color="rgba(119, 232, 255, 0.02)"></stop>
            </linearGradient>
            <linearGradient id="keywordsFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stop-color="rgba(199, 255, 138, 0.28)"></stop>
              <stop offset="100%" stop-color="rgba(199, 255, 138, 0.02)"></stop>
            </linearGradient>
          </defs>
          <path class="chart-area keywords" d="${areaPath(keywordPoints, baseY)}"></path>
          <path class="chart-area comments" d="${areaPath(commentPoints, baseY)}"></path>
          <path class="chart-line keywords" d="${linePath(keywordPoints)}"></path>
          <path class="chart-line comments" d="${linePath(commentPoints)}"></path>
          ${keywordPoints.map((point) => `<circle class="chart-point keywords" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="3.3"></circle>`).join('')}
          ${commentPoints.map((point) => `<circle class="chart-point comments" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="3.3"></circle>`).join('')}
        `;
      }

      if (activityAxis) {
        activityAxis.innerHTML = labels.map((label, index) => {
          const extraClass = index === labels.length - 1 ? ' is-current' : '';
          return `<span class="axis-pill${extraClass}">${esc(label)}</span>`;
        }).join('');
      }

      if (keywordBars) {
        keywordBars.innerHTML = labels.map((label, index) => {
          const value = keywords[index];
          const heightPct = peakKeyword > 0 ? Math.max(6, (value / peakKeyword) * 100) : 0;
          return `
            <div class="mini-bar-col">
              <div class="mini-bar-value">${value}</div>
              <div class="mini-bar-track">
                <span class="mini-bar" style="--bar-height:${heightPct}%"></span>
              </div>
              <div class="mini-bar-label">${esc(label.slice(0, 2))}</div>
            </div>
          `;
        }).join('');
      }
    }

    function renderTelemetry(telemetry, logsText) {
      const safe = telemetry || {};
      const error = toNum(safe.error);
      const warning = toNum(safe.warning);
      const info = toNum(safe.info);
      const lines = toNum(safe.lines) || String(logsText || '').split('\\n').filter((line) => line.trim()).length;
      const total = Math.max(1, error + warning + info);

      heroErrorCount.textContent = error;
      heroWarnCount.textContent = warning;
      heroInfoCount.textContent = info;

      telemetryErrorCount.textContent = error;
      telemetryWarnCount.textContent = warning;
      telemetryInfoCount.textContent = info;
      telemetryErrorFill.style.width = `${(error / total) * 100}%`;
      telemetryWarnFill.style.width = `${(warning / total) * 100}%`;
      telemetryInfoFill.style.width = `${(info / total) * 100}%`;
      logLineBadge.textContent = `${lines} 行`;
      runtimeLogBadge.textContent = `${lines} 行`;
    }

    function renderSignalPanel(payload) {
      const alert = payload.alert || {};
      const summary = payload.summary || {};
      const level = alert.level || (payload.running ? 'info' : 'info');
      const tone = level === 'error' ? '错误' : level === 'warn' ? '警告' : (payload.running ? '运行中' : '空闲');
      const message = alert.message || summary.last_info || (payload.running ? '任务运行中' : '系统待机中');
      const hint = alert.hint || summary.last_warning || summary.last_error || '等待新的运行信号';

      signalRing.dataset.level = level === 'error' ? 'error' : (level === 'warn' ? 'warn' : 'info');
      signalTone.textContent = tone;
      signalPhase.textContent = summary.phase || '待机';
      signalMessage.textContent = message;
      signalHint.textContent = clip(hint, 120);
      lastError.textContent = clip(summary.last_error, 120);
      lastWarning.textContent = clip(summary.last_warning, 120);
      lastInfo.textContent = clip(summary.last_info, 120);
      statPhaseNote.textContent = clip(message, 80);
      heroPhase.textContent = summary.phase || '待机';
    }

    function setSettingsTab(name) {
      activeSettingsTab = name;
      settingsTabButtons.forEach((button) => {
        const active = button.dataset.tabTarget === name;
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
        button.tabIndex = active ? 0 : -1;
      });

      settingsTabPanels.forEach((panel) => {
        const active = panel.dataset.tabPanel === name;
        panel.classList.toggle('is-active', active);
        if (active) panel.removeAttribute('hidden');
        else panel.setAttribute('hidden', 'hidden');
      });
    }

    function setSettingsVisible(visible) {
      if (!settingsPanel) return;
      settingsPanel.classList.toggle('hidden', !visible);
      document.body.classList.toggle('modal-open', !!visible);
    }

    async function testConnection() {
      if (!settingsForm || !testConnectionLog || !testConnectionBtn) return;

      testConnectionBtn.disabled = true;
      testConnectionLog.textContent = '正在测试模型连接，请稍候...';

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
        const status = ok ? '连接成功' : '连接失败';
        const detail = payload.detail || '';
        const summary = payload.message || '';
        testConnectionLog.textContent = `${status}\n${summary}${detail ? `\n\n${detail}` : ''}`;
      } catch (err) {
        const msg = String((err && err.message) || err);
        testConnectionLog.textContent = `请求异常\n${msg}`;
      } finally {
        testConnectionBtn.disabled = false;
      }
    }

    if (settingsTabButtons.length) {
      settingsTabButtons.forEach((button) => {
        button.addEventListener('click', () => setSettingsTab(button.dataset.tabTarget || 'comment'));
      });
      setSettingsTab(activeSettingsTab);
    }

    if (settingsToggle) settingsToggle.addEventListener('click', () => setSettingsVisible(true));
    if (settingsClose) settingsClose.addEventListener('click', () => setSettingsVisible(false));
    if (settingsCancel) settingsCancel.addEventListener('click', () => setSettingsVisible(false));
    if (settingsPanel) {
      settingsPanel.addEventListener('click', (event) => {
        if (event.target === settingsPanel) setSettingsVisible(false);
      });
    }
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && settingsPanel && !settingsPanel.classList.contains('hidden')) {
        setSettingsVisible(false);
      }
    });
    if (testConnectionBtn) testConnectionBtn.addEventListener('click', testConnection);

    function renderComments(rows) {
      const safeRows = Array.isArray(rows) ? rows : [];
      commentCountBadge.textContent = `${safeRows.length} 条`;
      commentTableBadge.textContent = `${safeRows.length} 条`;

      if (safeRows.length === 0) {
        commentRows.innerHTML = '<tr><td colspan="4" class="muted">暂无评论记录</td></tr>';
        return;
      }
      commentRows.innerHTML = safeRows.map((row) => `\n<tr>\n<td>${esc(row.created_at)}</td>\n<td>${esc(row.keyword)}</td>\n<td>${esc(row.post_id)}</td>\n<td>${esc(row.comment_text)}</td>\n</tr>`).join('');
    }

    function renderKeywords(rows) {
      const safeRows = Array.isArray(rows) ? rows : [];
      keywordCountBadge.textContent = `${safeRows.length} 条`;
      keywordTableBadge.textContent = `${safeRows.length} 条`;

      if (safeRows.length === 0) {
        keywordRows.innerHTML = '<tr><td colspan="3" class="muted">暂无关键词历史</td></tr>';
        return;
      }
      keywordRows.innerHTML = safeRows.map((row) => `\n<tr>\n<td>${esc(row.created_at)}</td>\n<td>${esc(row.topic)}</td>\n<td>${esc(row.keyword)}</td>\n</tr>`).join('');
    }

    function applyAlert(alert) {
      const safeAlert = alert || {};
      const level = safeAlert.level || 'info';
      const title = safeAlert.title || '系统状态';
      const rawMessage = safeAlert.message || '';
      const message = rawMessage && rawMessage !== title ? `${title}：${rawMessage}` : (rawMessage || title || '--');
      const hint = safeAlert.hint || '';

      signalRing.dataset.level = level === 'error' ? 'error' : (level === 'warn' ? 'warn' : 'info');
      signalTone.textContent = level === 'error' ? '错误' : (level === 'warn' ? '警告' : '运行中');
      signalMessage.textContent = message;
      signalHint.textContent = hint;
      statPhaseNote.textContent = clip(message, 80);
    }

    function renderStatus(payload) {
      const running = !!payload.running;
      bodyEl.dataset.state = running ? 'running' : 'idle';
      if (runText) runText.textContent = running ? '运行中' : '空闲';
      const runDot = runBadge ? runBadge.querySelector('.dot') : null;
      if (runDot) runDot.classList.toggle('ok', running);

      const stats = payload.stats || {};
      statToday.textContent = (stats.today_comments === undefined || stats.today_comments === null) ? 0 : stats.today_comments;
      statTotal.textContent = (stats.total_comments === undefined || stats.total_comments === null) ? 0 : stats.total_comments;
      statKeyword.textContent = (stats.keyword_history_total === undefined || stats.keyword_history_total === null) ? 0 : stats.keyword_history_total;

      const summary = payload.summary || {};
      statPhase.textContent = summary.phase || '待机';

      const logsText = payload.logs || '暂无日志文件';
      runtimeLog.textContent = logsText;
      if (autoScroll.checked) runtimeLog.scrollTop = runtimeLog.scrollHeight;

      tryRender('activity', () => renderActivity(payload.activity || {}));
      tryRender('telemetry', () => renderTelemetry(payload.telemetry || {}, logsText));
      tryRender('signal', () => renderSignalPanel(payload));
      tryRender('comments', () => renderComments(payload.comments || []));
      tryRender('keywords', () => renderKeywords(payload.keyword_history || []));
      lastUpdated.textContent = new Date().toLocaleTimeString('zh-CN', { hour12: false });
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
        return "暂无日志文件"
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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    ).fetchone()
    return bool(row)


def _chart_window(hours: int = 12) -> Tuple[List[str], List[str]]:
    total_hours = max(6, min(24, int(hours or 12)))
    window_end = datetime.now().replace(minute=0, second=0, microsecond=0)
    buckets = [window_end - timedelta(hours=total_hours - idx - 1) for idx in range(total_hours)]
    return [bucket.strftime("%H:%M") for bucket in buckets], [bucket.strftime("%Y-%m-%dT%H") for bucket in buckets]


def _hourly_counts(conn: sqlite3.Connection, table_name: str, bucket_keys: List[str]) -> List[int]:
    if not bucket_keys or not _table_exists(conn, table_name):
        return [0] * len(bucket_keys)

    start_key = bucket_keys[0]
    rows = conn.execute(
        f"SELECT substr(created_at, 1, 13) AS bucket, COUNT(1) AS cnt "
        f"FROM {table_name} "
        "WHERE substr(created_at, 1, 13) >= ? "
        "GROUP BY bucket "
        "ORDER BY bucket ASC",
        (start_key,),
    ).fetchall()
    counts = {str(bucket): int(cnt or 0) for bucket, cnt in rows if bucket}
    return [counts.get(key, 0) for key in bucket_keys]


def _activity_chart(db_path: Path, hours: int = 12) -> Dict[str, Any]:
    labels, bucket_keys = _chart_window(hours)
    empty = {
        "labels": labels,
        "comments": [0] * len(labels),
        "keywords": [0] * len(labels),
    }
    if not db_path.exists():
        return empty

    conn = sqlite3.connect(db_path)
    try:
        return {
            "labels": labels,
            "comments": _hourly_counts(conn, "comments", bucket_keys),
            "keywords": _hourly_counts(conn, "keyword_history", bucket_keys),
        }
    except Exception:
        return empty
    finally:
        conn.close()


def _log_telemetry(log_text: str) -> Dict[str, int]:
    lines = [line for line in log_text.splitlines() if line.strip()]
    counts = {"error": 0, "warning": 0, "info": 0, "lines": len(lines)}

    for line in lines:
        upper = line.upper()
        if "| ERROR |" in upper:
            counts["error"] += 1
        elif "| WARNING |" in upper:
            counts["warning"] += 1
        elif "| INFO |" in upper:
            counts["info"] += 1

    return counts


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
    browser.setdefault("headless", False)
    browser.setdefault("ws_url", None)
    browser.setdefault("executable_path", None)
    ai.setdefault("enable_commentability_check", False)
    ai.setdefault("strict_comment_gate", False)
    ai.setdefault("keyword_max_count", 10)

    runtime.setdefault("max_comments_per_round", 5)
    runtime.setdefault("daily_comment_limit", 30)
    runtime.setdefault("search_limit_per_keyword", 5)
    runtime.setdefault("single_keyword_search", True)
    runtime.setdefault("disable_keyword_expansion", False)
    runtime.setdefault("comment_every_post", True)

    topics.setdefault("direction_keywords", ["美女"])
    comment_rules.setdefault("requirements", ["先认可观点，再补一句虚心求教，语气自然"])
    comment_rules.setdefault("style_prompt", "")
    comment_rules.setdefault("content_prompt", "")

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


def _current_run_log_text(log_text: str, running: bool) -> str:
    if not running:
        return ""

    lines = [line for line in log_text.splitlines() if line.strip()]
    if not lines:
        return ""

    start_patterns = [
        r"\[AI\]\s+startup health check(?! passed| failed)",
        r"\[AUTOMATION\]\s+start browser client\b",
        r"\[AUTOMATION\]\s+round=1 begin\b",
    ]

    start_idx = -1
    for idx, line in enumerate(lines):
        if any(re.search(pattern, line, re.IGNORECASE) for pattern in start_patterns):
            start_idx = idx

    if start_idx >= 0:
        backtrack_idx = start_idx
        for _ in range(8):
            if backtrack_idx <= 0:
                break
            prev_line = lines[backtrack_idx - 1]
            if re.search(r"once mode enabled, stop after round=\d+", prev_line, re.IGNORECASE):
                break
            backtrack_idx -= 1
            if re.search(start_patterns[0], prev_line, re.IGNORECASE):
                break
        return "\n".join(lines[backtrack_idx:])

    return "\n".join(lines)


def _classify_alert(log_text: str, running: bool) -> Dict[str, str]:
    session_log = _current_run_log_text(log_text, running)
    lines = [line for line in session_log.splitlines() if line.strip()]

    def last_match_with_index(regex: str) -> Tuple[int, str]:
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if re.search(regex, line, re.IGNORECASE):
                return idx, line
        return -1, ""

    if not lines:
        return {
            "level": "info",
            "title": "当前空闲",
            "message": "系统待机中，可点击“开始任务（单轮）”或“开始任务（持续）”。",
            "hint": "任务状态只显示当前运行中的告警信息。",
            "key": "idle",
        }

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
    session_log = _current_run_log_text(log_text, running)
    lines = [line for line in session_log.splitlines() if line.strip()]

    def pick_last(level: str) -> str:
        token = f"| {level.upper()} |"
        for line in reversed(lines):
            if token in line:
                return line
        return ""

    return {
        "phase": _detect_phase(session_log, running) if session_log.strip() else ("运行中" if running else "待机"),
        "last_error": pick_last("ERROR"),
        "last_warning": pick_last("WARNING"),
        "last_info": pick_last("INFO"),
    }


def _live_payload(config_path: Path) -> Dict[str, Any]:
    cfg, log_path, db_path = _resolve_paths(config_path)
    running = _is_running(config_path)
    logs = _tail_log(log_path, 260)
    summary = _runtime_summary(logs, running)
    return {
        "running": running,
        "run_status": "运行中" if running else "空闲",
        "stats": _db_stats(db_path),
        "comments": _recent_comments(db_path),
        "keyword_history": _recent_keyword_history(db_path),
        "activity": _activity_chart(db_path),
        "telemetry": _log_telemetry(logs),
        "logs": logs,
        "summary": summary,
        "alert": _classify_alert(logs, running),
        "config_file": str(config_path),
    }


@app.get("/")
def index():
    config_path = Path(request.args.get("config_path") or str(DEFAULT_CONFIG))
    cfg, _, _ = _resolve_paths(config_path)
    payload = _live_payload(config_path)
    alert = payload.get("alert") or {}

    return render_template_string(
        HTML,
        cfg=cfg,
        config_path=str(config_path),
        direction_keywords=" & ".join(cfg["topics"].get("direction_keywords", [])),
        requirements_text="\n".join(cfg["comment_rules"].get("requirements", [])),
        style_prompt_text=cfg["comment_rules"].get("style_prompt", ""),
        content_prompt_text=cfg["comment_rules"].get("content_prompt", ""),
        run_status="运行中" if payload.get("running") else "空闲",
        message=request.args.get("message", ""),
        initial_payload=payload,
        initial_stats=payload.get("stats") or {"today_comments": 0, "total_comments": 0, "keyword_history_total": 0},
        initial_phase=(payload.get("summary") or {}).get("phase") or "待机",
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

    browser_cfg = cfg.setdefault("browser", {})
    browser_cfg["search_each_post"] = bool(request.form.get("search_each_post"))
    browser_cfg["headless"] = not bool(request.form.get("headless"))
    ws_url = (request.form.get("ws_url") or "").strip()
    browser_cfg["ws_url"] = ws_url if ws_url else None
    executable_path = (request.form.get("executable_path") or "").strip()
    browser_cfg["executable_path"] = executable_path if executable_path else None

    ai = cfg.setdefault("ai", {})
    ai["enable_commentability_check"] = bool(request.form.get("enable_commentability_check"))
    ai["strict_comment_gate"] = bool(request.form.get("strict_comment_gate"))
    ai["keyword_max_count"] = int(request.form.get("keyword_max_count") or 10)

    runtime = cfg.setdefault("runtime", {})
    runtime["max_comments_per_round"] = int(request.form.get("max_comments_per_round") or 5)
    runtime["daily_comment_limit"] = int(request.form.get("daily_comment_limit") or 30)
    runtime["search_limit_per_keyword"] = int(request.form.get("search_limit_per_keyword") or 5)
    runtime["single_keyword_search"] = bool(request.form.get("single_keyword_search"))
    runtime["disable_keyword_expansion"] = bool(request.form.get("disable_keyword_expansion"))
    runtime["comment_every_post"] = bool(request.form.get("comment_every_post"))

    direction_keywords = [
        v.strip()
        for v in re.split(r"\s*(?:&|，|,)\s*", request.form.get("direction_keywords") or "")
        if v.strip()
    ]
    cfg.setdefault("topics", {})["direction_keywords"] = direction_keywords or ["美女"]

    requirements = [v.strip() for v in (request.form.get("requirements") or "").splitlines() if v.strip()]
    comment_rules = cfg.setdefault("comment_rules", {})
    comment_rules["requirements"] = requirements or ["先认可对方观点，再补一句虚心求教，语气自然"]
    comment_rules["style_prompt"] = (request.form.get("style_prompt") or "").strip()
    comment_rules["content_prompt"] = (request.form.get("content_prompt") or "").strip()

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
