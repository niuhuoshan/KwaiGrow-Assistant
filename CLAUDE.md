# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KwaiGrow Assistant (ks-ai-auto-commenter) is a Python automation tool for Kuaishou content interaction. It combines AI-powered keyword expansion and comment generation with browser automation to perform controlled content engagement workflows.

## Development Setup

### Environment Setup
```bash
# Create and activate virtual environment
uv venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
uv pip install -r requirements.txt

# Install Playwright browsers
python -m playwright install chromium
```

### Configuration
```bash
# Copy example config and customize
cp config.example.yaml config.realrun.local.yaml
```

Required configuration:
- `openai.base_url` and `openai.api_key` for AI services
- `topics.direction_keywords` for search directions
- `comment_rules.requirements` for comment generation rules

## Running the Application

### CLI Mode
```bash
# Single run
python main.py --config ./config.realrun.local.yaml --once

# Continuous mode
python main.py --config ./config.realrun.local.yaml
```

### Web Dashboard
```bash
python dashboard.py --config ./config.realrun.local.yaml --host 127.0.0.1 --port 8091
```
Access at: http://127.0.0.1:8091

## Architecture

### Core Components

**Entry Points:**
- `main.py` - CLI interface, delegates to `src/app/main.py`
- `dashboard.py` - Flask web dashboard with real-time control

**Core Orchestration:**
- `src/app/orchestrator.py` - Main automation workflow coordinator
- `src/app/config.py` - Pydantic-based configuration management

**AI Services (`src/app/ai/`):**
- `openai_client.py` - OpenAI-compatible API client wrapper
- `keyword_expander.py` - AI-powered keyword expansion from direction words
- `comment_engine.py` - Comment generation, filtering, and validation

**Browser Automation (`src/app/browser/`):**
- `kuaishou_client.py` - Playwright-based Kuaishou platform automation

**Data Management (`src/app/storage/`):**
- `dedup_store.py` - SQLite-based deduplication and comment history

### Configuration System

The application uses a hierarchical YAML configuration system with Pydantic models:

- **OpenAI Config**: API credentials, model settings, timeouts
- **Browser Config**: Playwright settings, selectors, timeouts
- **Runtime Config**: Rate limits, search parameters, wait intervals
- **Comment Rules**: Generation requirements, banned words, length limits
- **Dedup Config**: SQLite path, deduplication strategies

CSS selectors are externalized in `config/selectors/kuaishou.yaml` for maintainability.

### Automation Workflow

1. **Keyword Expansion**: AI expands direction keywords into search terms
2. **Post Discovery**: Search Kuaishou for posts using expanded keywords
3. **Context Analysis**: Extract post content and hot comments for context
4. **Comment Generation**: AI generates candidate comments based on rules
5. **Validation & Filtering**: Apply length, banned words, and dedup filters
6. **Submission**: Submit approved comments via browser automation
7. **Record Keeping**: Store results in SQLite for deduplication

### Rate Limiting & Safety

- Daily comment limits (`runtime.daily_comment_limit`)
- Per-round limits (`runtime.max_comments_per_round`)
- Random wait intervals between actions
- Deduplication by post ID, URL, and title hash
- Login state verification before operations

## Development Guidelines

### Configuration Files
- Never commit real API keys or `config.realrun*.yaml` files
- Use `config.example.yaml` as template
- Environment variables supported via `${VAR_NAME}` syntax

### Data Directories
- `logs/` - Application logs (gitignored)
- `data/` - SQLite database and browser data (gitignored)

### Testing AI Integration
The dashboard provides an AI connection test endpoint to verify OpenAI API configuration before running automation.

### Browser Automation
- Uses Playwright with Chromium
- Supports both headless and headed modes
- CSS selectors configurable in `config/selectors/kuaishou.yaml`
- Handles login flow detection and user intervention

## Key Files to Understand

- `src/app/orchestrator.py` - Core automation logic and workflow
- `src/app/config.py` - Complete configuration schema
- `src/app/ai/comment_engine.py` - Comment generation and validation rules
- `config/selectors/kuaishou.yaml` - Platform-specific CSS selectors