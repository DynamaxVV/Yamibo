# React + Vite WebUI Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task.

**Goal:** Migrate the YamiboMCP web console from server-rendered Python HTML to a React + Vite SPA with 4 switchable theme impressions.

**Architecture:** Python backend becomes a JSON API (FastAPI-style routes on the existing http.server). React SPA in `frontend/` built with Vite, served as static files by the Python server in production. Theme system via CSS custom properties + data-theme attribute.

**Tech Stack:** React 18, TypeScript, Vite, CSS custom properties, Python http.server (existing)

---

## File Structure

```
frontend/
├── package.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── public/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts          # fetch wrapper for /api/*
│   ├── themes/
│   │   ├── index.ts            # theme registry + CSS vars
│   │   ├── minimalist.ts       # 极简专业
│   │   ├── rational.ts         # 理性数据驱动
│   │   ├── brutalist.ts        # 粗野
│   │   └── retro.ts            # 芥末黄复古
│   ├── context/
│   │   └── ThemeContext.tsx     # theme provider + localStorage
│   ├── components/
│   │   ├── Layout.tsx          # topbar, nav, theme switcher
│   │   ├── DataTable.tsx       # reusable sortable table
│   │   ├── Badge.tsx           # status badge
│   │   └── StatRow.tsx         # dashboard stat cards
│   └── pages/
│       ├── Dashboard.tsx
│       ├── Jobs.tsx
│       ├── JobDetail.tsx
│       ├── Threads.tsx
│       ├── ThreadDetail.tsx
│       ├── Series.tsx
│       ├── SeriesDetail.tsx
│       ├── Review.tsx
│       ├── Exports.tsx
│       └── Forums.tsx
src/yamibo_mcp/web/
├── app.py                      # modified: add /api/* routes, serve static
└── api.py                      # new: JSON API handlers
```

## Theme Definitions

### Theme 1: 极简专业 (Minimalist)
- Font: Outfit + Space Grotesk
- Colors: white bg, #1a1a2e text, #0066ff accent
- Feel: clean, professional, minimal chrome

### Theme 2: 理性 (Rational / Data-Dense)
- Font: Instrument Serif + Space Grotesk + JetBrains Mono
- Colors: #f8f9fa bg, #212529 text, #495057 secondary, #0d6efd accent
- Feel: Bloomberg-terminal inspired, high density, authoritative

### Theme 3: 粗野 (Brutalist)
- Font: system monospace stack
- Colors: #fff bg, #000 text, #ff0000 accent, no border-radius
- Feel: raw, honest, anti-design, newspaper

### Theme 4: 复古 (Retro 1957)
- Font: Georgia + system serif
- Colors: #f5f0e8 bg, #2d2d2d text, #d4a574 accent, #c75b39 secondary
- Feel: mid-century optimism, warm tones, geometric

## Tasks

### Task 1: Scaffold React + Vite project
- Create `frontend/` with package.json, vite.config.ts, tsconfig.json
- Install react, react-dom, react-router-dom, typescript
- Basic index.html + main.tsx + App.tsx

### Task 2: API client layer
- Create `frontend/src/api/client.ts` with typed fetch wrappers
- Create `src/yamibo_mcp/web/api.py` with JSON API routes
- Modify `app.py` to serve `/api/*` and static files

### Task 3: Theme system
- Create 4 theme definition files with CSS custom properties
- Create ThemeContext with localStorage persistence
- Create theme switcher component

### Task 4: Shared components
- Layout (topbar, nav, theme switcher)
- DataTable (reusable, sortable)
- Badge (status indicators)
- StatRow (dashboard stats)

### Task 5: Core pages
- Dashboard, Jobs, JobDetail, Threads, ThreadDetail
- Series, SeriesDetail, Review, Exports, Forums

### Task 6: Integration & build
- Configure Vite build output to `src/yamibo_mcp/web/static/`
- Python server serves built files in production
- Update pyproject.toml scripts
