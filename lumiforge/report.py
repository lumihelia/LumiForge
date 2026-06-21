"""Self-contained, local HTML project review."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .storage import EventStore, ProjectStore, utc_now
from .episodes import build_episodes


def _event_summary(event: Dict[str, Any]) -> str:
    payload = event.get("payload", {})
    event_type = event.get("type")
    if event_type == "message":
        role = "用户" if payload.get("role") == "user" else "Agent"
        text = " ".join(str(payload.get("text", "")).split())
        return f"{role}: {text[:220]}" + ("…" if len(text) > 220 else "")
    if event_type == "file_change":
        return f"{payload.get('change_type', 'change')}: {payload.get('path', 'unknown')}"
    if event_type == "tool_call":
        files = ", ".join(payload.get("files", [])[:3])
        return f"工具 {payload.get('name', 'unknown')}" + (f" · {files}" if files else "")
    if event_type == "command":
        command = payload.get("command", "")
        if not isinstance(command, str):
            command = json.dumps(command, ensure_ascii=False)
        return f"命令 exit={payload.get('exit_code')}: {command[:180]}"
    if event_type == "lifecycle":
        return f"Project Run {payload.get('action', '')}"
    return f"{event_type}: {str(payload)[:180]}"


def _system_map(episodes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, set[str]] = defaultdict(set)
    for episode in episodes:
        for file_path in episode.get("files", []):
            path = Path(file_path)
            lowered = str(path).lower()
            suffix = path.suffix.lower()
            if suffix == ".md" or "docs" in path.parts:
                group = "文档与产品说明"
            elif "test" in lowered or "spec" in lowered:
                group = "验证与质量"
            elif any(word in lowered for word in ("adapter", "claude", "codex", "capture", "ai_")):
                group = "Agent 对话接入"
            elif any(word in lowered for word in ("storage", "event", "memory", "database", "snapshot")):
                group = "项目记忆与证据"
            elif any(word in lowered for word in ("cli", "watcher", "recorder", "runtime", "server", "main")):
                group = "运行与采集"
            elif suffix in {".html", ".css", ".js", ".jsx", ".tsx", ".vue", ".svelte"}:
                group = "界面与呈现"
            else:
                group = "核心实现"
            groups[group].add(str(file_path))

    preferred = [
        "运行与采集",
        "Agent 对话接入",
        "项目记忆与证据",
        "核心实现",
        "界面与呈现",
        "验证与质量",
        "文档与产品说明",
    ]
    return [
        {"name": name, "files": sorted(groups[name]), "count": len(groups[name])}
        for name in preferred
        if groups.get(name)
    ]


def _report_data(
    project: Dict[str, Any],
    runs: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    episodes: Sequence[Dict[str, Any]],
    build_record: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_counts = Counter(event.get("source", "unknown") for event in events)
    status_counts = Counter(episode.get("status", "unknown") for episode in episodes)
    conversations = sorted({event.get("conversation_id") for event in events if event.get("conversation_id")})
    latest_run = runs[-1] if runs else None
    evidence = []
    for event in events:
        payload = event.get("payload", {})
        files = payload.get("files", [])
        if event.get("type") == "file_change" and payload.get("path"):
            files = [payload["path"]]
        evidence.append(
            {
                "event_id": event.get("event_id"),
                "timestamp": event.get("timestamp"),
                "source": event.get("source"),
                "type": event.get("type"),
                "conversation_id": event.get("conversation_id"),
                "summary": _event_summary(event),
                "files": files,
                "source_ref": event.get("source_ref"),
            }
        )

    safe_episodes = []
    for episode in episodes:
        value = dict(episode)
        value["changes"] = [
            {**change, "diff": str(change.get("diff", ""))[:24_000]} for change in episode.get("changes", [])[:30]
        ]
        safe_episodes.append(value)

    return {
        "generated_at": utc_now(),
        "project": project,
        "runs": list(runs),
        "latest_run": latest_run,
        "episodes": safe_episodes,
        "evidence": evidence,
        "system_map": _system_map(episodes),
        "metrics": {
            "runs": len(runs),
            "conversations": len(conversations),
            "episodes": len(episodes),
            "verified": status_counts.get("verified", 0),
            "failed": status_counts.get("failed", 0),
            "unverified": status_counts.get("unverified", 0),
            "events": len(events),
        },
        "source_counts": dict(source_counts),
        "build_record": build_record,
    }


def generate_report(
    project_path: str | Path,
    *,
    output: str | Path | None = None,
    events: Optional[Sequence[Dict[str, Any]]] = None,
    build_record: Optional[Dict[str, Any]] = None,
) -> Path:
    project_store = ProjectStore(project_path)
    project = project_store.ensure_project()
    selected_events = list(events) if events is not None else EventStore(project_path).read_all()
    runs = project_store.list_runs()
    episodes = build_episodes(selected_events)
    data = _report_data(project, runs, selected_events, episodes, build_record)
    serialized = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    title = escape(project.get("name", "LumiForge Project"))
    html = _template(title, serialized)
    output_path = Path(output).expanduser().resolve() if output else project_store.paths.report_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _template(title: str, serialized: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{title} · LumiForge Project Review</title>
  <style>
    :root {{
      --ink: #292633;
      --primary: #4c436f;
      --primary-deep: #342f4f;
      --paper: #f4f3f7;
      --paper-warm: #fbfaf7;
      --surface: rgba(255, 255, 255, 0.86);
      --line: #d6d1df;
      --muted: #756f88;
      --accent: #6b5b8a;
      --teal: #2f756d;
      --amber: #a9672e;
      --red: #a54949;
      --shadow: rgba(76, 67, 111, 0.12);
      --radius: 16px;
      --display: "Playfair Display", "Iowan Old Style", "Palatino Linotype", Georgia, serif;
      --body: "Source Serif Pro", "Iowan Old Style", Charter, Georgia, serif;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(circle at 12% 4%, rgba(107,91,138,.13), transparent 26rem),
        radial-gradient(circle at 90% 22%, rgba(47,117,109,.09), transparent 24rem),
        linear-gradient(135deg, #f8f7f4 0%, var(--paper) 48%, #eeebf3 100%);
      font-family: var(--body);
      min-height: 100vh;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: .32;
      background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 140 140' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.82' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.035'/%3E%3C/svg%3E");
    }}
    button, input, select {{ font: inherit; }}
    button {{ cursor: pointer; }}
    :focus-visible {{ outline: 3px solid #b67936; outline-offset: 3px; }}
    .skip-link {{ position: fixed; top: -5rem; left: 1rem; z-index: 100; padding: .75rem 1rem; background: var(--primary-deep); color: white; border-radius: 8px; }}
    .skip-link:focus {{ top: 1rem; }}
    .shell {{ width: min(1480px, calc(100% - 36px)); margin: 0 auto; padding: 28px 0 64px; position: relative; }}
    .masthead {{
      display: grid;
      grid-template-columns: 84px minmax(0, 1fr) auto;
      gap: 28px;
      align-items: end;
      padding: 30px 34px;
      border: 1px solid rgba(76,67,111,.18);
      border-radius: 24px 24px 8px 8px;
      background: linear-gradient(120deg, rgba(255,255,255,.92), rgba(248,246,251,.75));
      box-shadow: 0 24px 60px var(--shadow);
      overflow: hidden;
      position: relative;
    }}
    .masthead::after {{ content:""; position:absolute; right:-5rem; top:-7rem; width:20rem; height:20rem; border:1px solid rgba(76,67,111,.14); border-radius:50%; box-shadow: 0 0 0 3rem rgba(76,67,111,.025), 0 0 0 7rem rgba(76,67,111,.018); }}
    .wordmark {{ writing-mode: vertical-rl; transform: rotate(180deg); letter-spacing: .3em; text-transform: uppercase; font-size: .72rem; color: var(--primary); font-weight: 600; }}
    .eyebrow {{ margin: 0 0 .55rem; color: var(--accent); letter-spacing: .18em; text-transform: uppercase; font-size: .76rem; font-weight: 600; }}
    h1, h2, h3, h4 {{ font-family: var(--display); font-weight: 600; margin-top: 0; }}
    h1 {{ margin-bottom: .55rem; font-size: clamp(2.45rem, 6vw, 5.7rem); line-height: .92; letter-spacing: -.045em; max-width: 13ch; }}
    .dek {{ margin: 0; color: var(--muted); font-size: 1.08rem; max-width: 62ch; line-height: 1.65; }}
    .run-state {{ position:relative; z-index:1; min-width: 190px; text-align: right; }}
    .state-pill {{ display:inline-flex; align-items:center; gap:.55rem; padding:.55rem .8rem; border:1px solid var(--line); border-radius:999px; background:white; font-size:.82rem; }}
    .state-dot {{ width:.55rem; height:.55rem; border-radius:50%; background:var(--teal); box-shadow:0 0 0 4px rgba(47,117,109,.12); }}
    .goal {{ margin:.8rem 0 0; color:var(--primary-deep); font-size:.95rem; max-width:28ch; }}
    .tabs {{ position: sticky; top: 10px; z-index: 20; display:flex; gap:8px; margin:14px 0 24px; padding:8px; border:1px solid rgba(76,67,111,.16); border-radius:12px; background:rgba(250,249,252,.88); backdrop-filter:blur(18px); box-shadow:0 10px 28px rgba(52,47,79,.08); overflow-x:auto; }}
    .tab {{ border:0; background:transparent; color:var(--muted); padding:.72rem 1rem; border-radius:8px; white-space:nowrap; font-weight:600; }}
    .tab[aria-selected="true"] {{ color:white; background:var(--primary); box-shadow:0 8px 18px rgba(76,67,111,.22); }}
    .view[hidden] {{ display:none; }}
    .view {{ animation: rise .45s ease both; }}
    @keyframes rise {{ from {{ opacity:0; transform:translateY(10px); }} to {{ opacity:1; transform:none; }} }}
    .section-head {{ display:flex; justify-content:space-between; gap:24px; align-items:end; margin:36px 2px 16px; }}
    .section-head h2 {{ margin:0; font-size:clamp(1.7rem, 3vw, 2.8rem); letter-spacing:-.025em; }}
    .section-head p {{ margin:0; color:var(--muted); max-width:52ch; line-height:1.55; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6, minmax(0,1fr)); gap:12px; }}
    .metric {{ padding:18px; border:1px solid rgba(76,67,111,.14); border-radius:var(--radius); background:var(--surface); min-height:126px; box-shadow:0 10px 30px rgba(76,67,111,.055); }}
    .metric span {{ color:var(--muted); font-size:.82rem; }}
    .metric strong {{ display:block; margin-top:.42rem; font-family:var(--display); font-size:2.45rem; line-height:1; color:var(--primary-deep); }}
    .metric small {{ display:block; margin-top:.7rem; color:var(--muted); }}
    .truth-panel {{ display:grid; grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr); gap:16px; margin-top:16px; }}
    .record-panel {{ margin-top:16px; }}
    .record-lead {{ margin:8px 0 0; max-width:72ch; font-size:1.08rem; line-height:1.7; }}
    .record-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; margin-top:24px; }}
    .record-section {{ border-top:1px solid var(--line); padding-top:16px; }}
    .record-section h4 {{ margin:0 0 8px; }}
    .record-section ul {{ margin:0; padding-left:20px; color:var(--muted); line-height:1.7; }}
    .checkpoint-links {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }}
    .checkpoint-link {{ color:var(--primary); text-decoration:none; border-bottom:1px solid var(--line); padding:8px 0; }}
    .checkpoint-link:hover {{ color:var(--primary-deep); border-color:var(--primary); }}
    .panel {{ border:1px solid rgba(76,67,111,.15); border-radius:var(--radius); background:var(--surface); padding:24px; box-shadow:0 14px 34px rgba(76,67,111,.06); }}
    .panel h3 {{ font-size:1.4rem; margin-bottom:.4rem; }}
    .panel-copy {{ color:var(--muted); line-height:1.65; }}
    .truth-bar {{ display:flex; overflow:hidden; height:14px; border-radius:99px; background:#e7e3eb; margin:1.2rem 0 .8rem; }}
    .truth-bar span {{ min-width:0; transition:width .6s ease; }}
    .truth-verified {{ background:var(--teal); }} .truth-failed {{ background:var(--red); }} .truth-unverified {{ background:var(--amber); }}
    .legend {{ display:flex; flex-wrap:wrap; gap:12px 18px; color:var(--muted); font-size:.82rem; }}
    .legend i {{ display:inline-block; width:.65rem; height:.65rem; border-radius:50%; margin-right:.36rem; }}
    .recent-list {{ display:grid; gap:9px; margin-top:1rem; }}
    .recent-item {{ display:grid; grid-template-columns:8px 1fr auto; gap:12px; align-items:center; padding:.7rem 0; border-top:1px solid #e5e1e9; }}
    .recent-item:first-child {{ border-top:0; }}
    .recent-item b {{ font-family:var(--display); font-size:1rem; }}
    .recent-item small {{ color:var(--muted); }}
    .filters {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:18px; }}
    .filter {{ border:1px solid var(--line); background:rgba(255,255,255,.72); color:var(--muted); border-radius:999px; padding:.55rem .85rem; }}
    .filter.active {{ background:var(--primary-deep); border-color:var(--primary-deep); color:white; }}
    .timeline {{ position:relative; display:grid; gap:18px; padding-left:36px; }}
    .timeline::before {{ content:""; position:absolute; left:12px; top:10px; bottom:10px; width:1px; background:linear-gradient(var(--accent), rgba(107,91,138,.12)); }}
    .episode {{ position:relative; border:1px solid rgba(76,67,111,.15); border-radius:18px; background:rgba(255,255,255,.86); box-shadow:0 14px 36px rgba(76,67,111,.07); overflow:hidden; }}
    .episode::before {{ content:""; position:absolute; left:-30px; top:26px; width:11px; height:11px; border-radius:50%; background:var(--primary); box-shadow:0 0 0 6px var(--paper); }}
    .episode summary {{ list-style:none; padding:22px 24px; cursor:pointer; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:18px; align-items:start; }}
    .episode summary::-webkit-details-marker {{ display:none; }}
    .episode summary:hover {{ background:rgba(244,243,247,.72); }}
    .episode h3 {{ margin:.4rem 0 .35rem; font-size:1.38rem; }}
    .meta-row {{ display:flex; flex-wrap:wrap; gap:7px; align-items:center; }}
    .badge {{ display:inline-flex; align-items:center; border:1px solid var(--line); border-radius:999px; padding:.26rem .52rem; color:var(--muted); background:white; font-size:.73rem; }}
    .status {{ color:white; border-color:transparent; }}
    .status-verified {{ background:var(--teal); }} .status-failed {{ background:var(--red); }} .status-unverified {{ background:var(--amber); }} .status-discussed {{ background:var(--muted); }}
    .episode-time {{ color:var(--muted); font-size:.8rem; white-space:nowrap; }}
    .episode-body {{ padding:0 24px 24px; border-top:1px solid #e7e3eb; }}
    .chain {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:9px; padding:22px 0 16px; }}
    .chain-step {{ position:relative; padding:13px 14px; border:1px solid var(--line); border-radius:12px; background:var(--paper-warm); min-height:112px; }}
    .chain-step:not(:last-child)::after {{ content:"→"; position:absolute; right:-10px; top:40%; z-index:2; color:var(--accent); background:var(--paper-warm); border-radius:99px; }}
    .chain-step span {{ display:block; color:var(--accent); text-transform:uppercase; letter-spacing:.12em; font-size:.66rem; font-weight:700; margin-bottom:.45rem; }}
    .chain-step p {{ margin:0; font-size:.88rem; line-height:1.5; color:#494454; }}
    .episode-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
    .evidence-block {{ border:1px solid #ded9e4; border-radius:12px; background:#faf9fc; padding:15px; }}
    .evidence-block h4 {{ margin:0 0 .55rem; font-size:1rem; }}
    .file-list {{ display:flex; flex-wrap:wrap; gap:6px; }}
    code, pre {{ font-family:"SFMono-Regular", Consolas, "Liberation Mono", monospace; }}
    .file-chip {{ background:#eeeaf3; color:var(--primary-deep); border-radius:6px; padding:.28rem .45rem; font-size:.72rem; overflow-wrap:anywhere; }}
    .change {{ margin-top:10px; }}
    .change summary {{ display:block; padding:10px 0; color:var(--primary); font-weight:600; }}
    pre {{ margin:0; padding:14px; border-radius:10px; background:#24222b; color:#e9e5ef; max-height:420px; overflow:auto; font-size:.73rem; line-height:1.55; white-space:pre; }}
    .system-flow {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin-bottom:16px; }}
    .flow-node {{ position:relative; min-height:125px; padding:18px; color:white; background:var(--primary-deep); border-radius:14px; box-shadow:0 12px 25px rgba(52,47,79,.18); }}
    .flow-node:nth-child(2) {{ background:#554a72; }} .flow-node:nth-child(3) {{ background:#4f6074; }} .flow-node:nth-child(4) {{ background:#3e706b; }} .flow-node:nth-child(5) {{ background:#8b603a; }}
    .flow-node:not(:last-child)::after {{ content:""; position:absolute; right:-12px; top:50%; width:12px; height:1px; background:var(--accent); }}
    .flow-node small {{ opacity:.72; letter-spacing:.12em; text-transform:uppercase; }}
    .flow-node b {{ display:block; margin:.7rem 0 .35rem; font-family:var(--display); font-size:1.18rem; }}
    .component-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .component {{ border:1px solid rgba(76,67,111,.15); border-radius:14px; padding:18px; background:var(--surface); }}
    .component h3 {{ display:flex; justify-content:space-between; gap:12px; margin:0 0 .8rem; font-size:1.14rem; }}
    .component ul {{ margin:0; padding:0; list-style:none; display:grid; gap:7px; }}
    .component li {{ color:var(--muted); font-size:.8rem; overflow-wrap:anywhere; border-top:1px solid #e7e3eb; padding-top:7px; }}
    .evidence-tools {{ display:grid; grid-template-columns:minmax(240px,1fr) 180px; gap:10px; margin-bottom:12px; }}
    .field {{ width:100%; border:1px solid var(--line); background:white; border-radius:10px; padding:.75rem .85rem; color:var(--ink); }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:14px; background:var(--surface); }}
    table {{ width:100%; border-collapse:collapse; min-width:780px; }}
    th, td {{ text-align:left; padding:12px 14px; border-bottom:1px solid #e5e1e9; vertical-align:top; }}
    th {{ position:sticky; top:0; background:#f0edf4; color:var(--primary-deep); font-size:.76rem; letter-spacing:.08em; text-transform:uppercase; }}
    td {{ font-size:.82rem; line-height:1.45; }}
    .empty {{ padding:42px; text-align:center; border:1px dashed var(--line); border-radius:14px; color:var(--muted); background:rgba(255,255,255,.48); }}
    footer {{ display:flex; justify-content:space-between; gap:18px; color:var(--muted); font-size:.78rem; border-top:1px solid rgba(76,67,111,.16); margin-top:48px; padding:20px 2px; }}
    @media (max-width:1050px) {{ .metrics {{ grid-template-columns:repeat(3,1fr); }} .truth-panel {{ grid-template-columns:1fr; }} .chain {{ grid-template-columns:1fr; }} .chain-step:not(:last-child)::after {{ content:"↓"; right:50%; top:auto; bottom:-13px; }} .system-flow {{ grid-template-columns:1fr 1fr; }} .component-grid {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width:700px) {{ .shell {{ width:min(100% - 20px, 1480px); padding-top:10px; }} .masthead {{ grid-template-columns:1fr; padding:24px 20px; }} .wordmark {{ writing-mode:horizontal-tb; transform:none; }} .run-state {{ text-align:left; }} h1 {{ font-size:2.8rem; }} .tabs {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:3px; overflow:visible; }} .tab {{ padding:.7rem .22rem; font-size:.78rem; }} .metrics {{ grid-template-columns:1fr 1fr; }} .metric {{ min-height:105px; }} .episode summary {{ grid-template-columns:1fr; padding:18px; }} .episode-body {{ padding:0 18px 18px; }} .episode-grid, .component-grid, .system-flow, .evidence-tools, .record-grid {{ grid-template-columns:1fr; }} .flow-node:not(:last-child)::after {{ display:none; }} .section-head {{ display:block; }} .section-head p {{ margin-top:.5rem; }} footer {{ display:block; }} }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior:auto !important; animation:none !important; transition:none !important; }} }}
  </style>
</head>
<body>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="shell">
    <header class="masthead">
      <div class="wordmark">LumiForge · Project Observatory</div>
      <div>
        <p class="eyebrow" id="report-kind">Evidence-backed build review</p>
        <h1 id="project-title">{title}</h1>
        <p class="dek">把跨对话的目标、决策、代码变化与验证结果重新连成一条可以检查的工程路径。</p>
      </div>
      <div class="run-state">
        <div class="state-pill"><i class="state-dot" aria-hidden="true"></i><span id="run-status">项目已载入</span></div>
        <p class="goal" id="project-goal">等待 Project Run 目标</p>
      </div>
    </header>

    <nav class="tabs" aria-label="项目报告视图" role="tablist">
      <button class="tab" role="tab" aria-selected="true" data-view="overview">Overview</button>
      <button class="tab" role="tab" aria-selected="false" data-view="journey">Journey</button>
      <button class="tab" role="tab" aria-selected="false" data-view="system">System Map</button>
      <button class="tab" role="tab" aria-selected="false" data-view="evidence">Evidence</button>
    </nav>

    <main id="main-content">
      <section class="view" id="overview" role="tabpanel">
        <div class="section-head"><div><p class="eyebrow">Project truth</p><h2>这次构建，现在成立吗？</h2></div><p>这里不把“修改过”当成“完成了”。只有被命令、测试或明确结果支撑的结论，才算验证。</p></div>
        <article class="panel record-panel" id="build-record" hidden>
          <p class="eyebrow" id="record-identity"></p>
          <h3 id="record-title"></h3>
          <p class="record-lead" id="record-summary"></p>
          <div class="record-grid" id="record-grid"></div>
          <div class="checkpoint-links" id="checkpoint-links"></div>
        </article>
        <div class="metrics" id="metrics"></div>
        <div class="truth-panel">
          <article class="panel"><h3>验证覆盖</h3><p class="panel-copy">Episode 按“验证通过、验证失败、已修改但未验证”分开呈现。</p><div class="truth-bar" id="truth-bar" aria-label="验证状态分布"></div><div class="legend" id="truth-legend"></div></article>
          <article class="panel"><h3>最近的重要变化</h3><div class="recent-list" id="recent-list"></div></article>
        </div>
      </section>

      <section class="view" id="journey" role="tabpanel" hidden>
        <div class="section-head"><div><p class="eyebrow">Cross-conversation journey</p><h2>项目演化路径</h2></div><p>对话只是来源。这里以一次功能实现、Bug 修复或决策变化为单位，跨对话串联证据。</p></div>
        <div class="filters" id="episode-filters" aria-label="Episode 状态筛选"></div>
        <div class="timeline" id="timeline"></div>
      </section>

      <section class="view" id="system" role="tabpanel" hidden>
        <div class="section-head"><div><p class="eyebrow">System map</p><h2>工程是怎样运转的</h2></div><p>先展示证据流，再按实际被修改的文件归纳系统组成。它是观察结果，不冒充完整架构真相。</p></div>
        <div class="system-flow" aria-label="LumiForge 证据流">
          <div class="flow-node"><small>01 Intent</small><b>用户目标</b><span>需求、Bug、反馈</span></div>
          <div class="flow-node"><small>02 Evidence</small><b>Agent 行动</b><span>解释、工具、命令</span></div>
          <div class="flow-node"><small>03 Change</small><b>代码差异</b><span>真实 patch 与文件</span></div>
          <div class="flow-node"><small>04 Proof</small><b>验证结果</b><span>测试、构建、运行</span></div>
          <div class="flow-node"><small>05 Review</small><b>项目理解</b><span>Episode 与关系图</span></div>
        </div>
        <div class="component-grid" id="component-grid"></div>
      </section>

      <section class="view" id="evidence" role="tabpanel" hidden>
        <div class="section-head"><div><p class="eyebrow">Evidence ledger</p><h2>所有结论从哪里来</h2></div><p>可以按来源、类型或关键词追到原始事件。LumiForge 不保存或展示隐藏推理。</p></div>
        <div class="evidence-tools"><label><span class="eyebrow">搜索证据</span><input class="field" id="evidence-search" type="search" placeholder="例如：测试、登录、storage.py"></label><label><span class="eyebrow">来源</span><select class="field" id="source-filter"><option value="all">全部来源</option></select></label></div>
        <div class="table-wrap"><table><thead><tr><th>时间</th><th>来源</th><th>类型</th><th>内容</th><th>对话</th></tr></thead><tbody id="evidence-body"></tbody></table></div>
      </section>
    </main>
    <footer><span>LumiForge · 本地优先的工程观察台</span><span id="generated-at"></span></footer>
  </div>
  <script id="report-data" type="application/json">{serialized}</script>
  <script>
    const DATA = JSON.parse(document.getElementById('report-data').textContent);
    const $ = (selector) => document.querySelector(selector);
    const make = (tag, className, text) => {{ const node=document.createElement(tag); if(className) node.className=className; if(text!==undefined) node.textContent=text; return node; }};
    const date = (value) => value ? new Intl.DateTimeFormat('zh-CN', {{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}}).format(new Date(value)) : '未知时间';
    const statusLabel = {{verified:'验证通过',failed:'验证失败',unverified:'尚未验证',discussed:'仅讨论'}};

    function initHeader() {{
      const run=DATA.latest_run;
      const record=DATA.build_record;
      $('#run-status').textContent=record ? (record.kind==='release' ? '版本已封存' : '阶段已记录') : (run ? ({{running:'正在记录',paused:'已暂停',closed:'已封存'}}[run.status] || run.status) : '尚无 Project Run');
      $('#project-goal').textContent=record?.goal || run?.goal || '还没有记录项目目标';
      if(record) $('#report-kind').textContent=record.kind==='release' ? 'Version report · '+record.version : 'Checkpoint build record · '+record.record_id;
      $('#generated-at').textContent='报告生成于 ' + date(DATA.generated_at);
    }}
    function itemText(item) {{ return item.text||item.decision||item.problem||item.command||item.result||item.resolution||Object.values(item).filter(Boolean).join(' · '); }}
    function renderBuildRecord() {{
      const record=DATA.build_record; if(!record) return;
      const panel=$('#build-record'); panel.hidden=false;
      const recordStatus={{verified:'已验证',failed:'验证失败',unverified:'尚未验证'}}[record.record_status]||record.record_status;
      $('#record-identity').textContent=(record.kind==='release' ? '版本报告 · '+record.version : '阶段记录 · '+record.record_id)+' · '+recordStatus;
      $('#record-title').textContent=record.title;
      $('#record-summary').textContent=record.summary;
      const grid=$('#record-grid');
      [['decisions','关键决策'],['problems','问题与处理'],['verification','验证'],['next_steps','下一步']].forEach(([key,label])=>{{
        if(!record[key]?.length) return;
        const section=make('section','record-section'); section.append(make('h4','',label)); const list=make('ul');
        record[key].forEach(item=>list.append(make('li','',itemText(item)))); section.append(list); grid.append(section);
      }});
      const links=$('#checkpoint-links'); (record.included_checkpoints||[]).forEach(item=>{{ const link=make('a','checkpoint-link',item.title); link.href=item.report_href; links.append(link); }});
    }}
    function renderMetrics() {{
      const items=[['Project Runs',DATA.metrics.runs,'跨时间构建'],['Conversations',DATA.metrics.conversations,'跨对话来源'],['Episodes',DATA.metrics.episodes,'有意义的变化'],['Verified',DATA.metrics.verified,'有验证证据'],['Unverified',DATA.metrics.unverified,'需要继续确认'],['Evidence',DATA.metrics.events,'原始事件']];
      const root=$('#metrics'); root.replaceChildren();
      items.forEach(([label,value,help])=>{{ const card=make('article','metric'); card.append(make('span','',label),make('strong','',String(value)),make('small','',help)); root.append(card); }});
    }}
    function renderTruth() {{
      const total=Math.max(1,DATA.metrics.verified+DATA.metrics.failed+DATA.metrics.unverified);
      const values=[['verified','验证通过',DATA.metrics.verified],['failed','验证失败',DATA.metrics.failed],['unverified','尚未验证',DATA.metrics.unverified]];
      const bar=$('#truth-bar'), legend=$('#truth-legend'); bar.replaceChildren(); legend.replaceChildren();
      values.forEach(([key,label,count])=>{{ const segment=make('span','truth-'+key); segment.style.width=(count/total*100)+'%'; segment.title=label+': '+count; bar.append(segment); const item=make('span'); const dot=make('i','truth-'+key); item.append(dot,document.createTextNode(label+' '+count)); legend.append(item); }});
      const recent=$('#recent-list'); recent.replaceChildren();
      DATA.episodes.slice(-5).reverse().forEach(ep=>{{ const row=make('div','recent-item'); row.append(make('i','state-dot'),make('b','',ep.title),make('small','',statusLabel[ep.status]||ep.status)); recent.append(row); }});
      if(!DATA.episodes.length) recent.append(make('div','empty','还没有可呈现的 Episode。先同步对话或开始一次 Project Run。'));
    }}
    function chainStep(label,text) {{ const step=make('div','chain-step'); step.append(make('span','',label),make('p','',text)); return step; }}
    function renderEpisode(ep) {{
      const card=make('details','episode'); card.dataset.status=ep.status;
      const summary=make('summary'); const heading=make('div'); const meta=make('div','meta-row');
      meta.append(make('span','badge status status-'+ep.status,statusLabel[ep.status]||ep.status),make('span','badge',ep.kind),make('span','badge',ep.sources.join(' + ')),make('span','badge',ep.conversation_ids.length+' 个对话'));
      heading.append(meta,make('h3','',ep.title),make('div','panel-copy',ep.trigger)); summary.append(heading,make('time','episode-time',date(ep.started_at))); card.append(summary);
      const body=make('div','episode-body'); const chain=make('div','chain');
      chain.append(chainStep('Trigger',ep.trigger||'未捕获'),chainStep('Approach',ep.approach||'未捕获'),chainStep('Change',ep.files.length ? ep.files.slice(0,4).join('、') : '未发现文件变化'),chainStep('Verify',ep.verifications.length ? ep.verifications[ep.verifications.length-1].command : '未发现验证命令'),chainStep('Outcome',ep.outcome)); body.append(chain);
      const grid=make('div','episode-grid');
      const files=make('section','evidence-block'); files.append(make('h4','','涉及文件')); const fileList=make('div','file-list'); ep.files.forEach(path=>fileList.append(make('code','file-chip',path))); if(!ep.files.length) fileList.append(make('span','panel-copy','无文件证据')); files.append(fileList);
      const proof=make('section','evidence-block'); proof.append(make('h4','','验证结果')); if(ep.verifications.length) ep.verifications.forEach(v=>proof.append(make('p','panel-copy',(v.passed?'通过 · ':'失败 · ')+v.command+(v.output?' · '+v.output:'')))); else proof.append(make('p','panel-copy','没有发现测试、构建或运行验证。'));
      grid.append(files,proof); body.append(grid);
      ep.changes.filter(change=>change.diff).forEach(change=>{{ const detail=make('details','change'); const label=make('summary','',`${{change.action}} · ${{change.files.join(', ')||'代码变化'}}`); const pre=make('pre','',change.diff); detail.append(label,pre); body.append(detail); }});
      if(ep.related_episode_ids.length) body.append(make('p','panel-copy','跨对话相关 Episode：'+ep.related_episode_ids.length+' 个 · 关联置信度 '+Math.round((ep.relation_confidence||0)*100)+'%'));
      card.append(body); return card;
    }}
    function renderJourney(filter='all') {{ const root=$('#timeline'); root.replaceChildren(); const values=DATA.episodes.filter(ep=>filter==='all'||ep.status===filter); values.slice().reverse().forEach(ep=>root.append(renderEpisode(ep))); if(!values.length) root.append(make('div','empty','当前筛选下没有 Episode。')); }}
    function initFilters() {{ const root=$('#episode-filters'); [['all','全部'],['verified','验证通过'],['failed','验证失败'],['unverified','尚未验证'],['discussed','仅讨论']].forEach(([key,label],index)=>{{ const button=make('button','filter'+(index===0?' active':''),label); button.type='button'; button.addEventListener('click',()=>{{ root.querySelectorAll('button').forEach(x=>x.classList.remove('active')); button.classList.add('active'); renderJourney(key); }}); root.append(button); }}); }}
    function renderSystem() {{ const root=$('#component-grid'); root.replaceChildren(); DATA.system_map.forEach(group=>{{ const card=make('article','component'); const heading=make('h3'); heading.append(document.createTextNode(group.name),make('span','badge',String(group.count))); card.append(heading); const list=make('ul'); group.files.slice(0,12).forEach(file=>list.append(make('li','',file))); if(group.files.length>12) list.append(make('li','','另有 '+(group.files.length-12)+' 个文件')); card.append(list); root.append(card); }}); if(!DATA.system_map.length) root.append(make('div','empty','捕获代码变化后，这里会按证据归纳系统组成。')); }}
    function renderEvidence() {{ const search=$('#evidence-search').value.toLowerCase(); const source=$('#source-filter').value; const root=$('#evidence-body'); root.replaceChildren(); DATA.evidence.filter(item=>(source==='all'||item.source===source)&&(!search||(item.summary+' '+item.files.join(' ')).toLowerCase().includes(search))).slice().reverse().forEach(item=>{{ const row=make('tr'); row.append(make('td','',date(item.timestamp)),make('td','',item.source||''),make('td','',item.type||''),make('td','',item.summary||''),make('td','',item.conversation_id ? item.conversation_id.slice(0,10)+'…' : '—')); root.append(row); }}); }}
    function initEvidence() {{ const select=$('#source-filter'); Object.keys(DATA.source_counts).sort().forEach(source=>{{ const option=make('option','',source); option.value=source; select.append(option); }}); $('#evidence-search').addEventListener('input',renderEvidence); select.addEventListener('change',renderEvidence); renderEvidence(); }}
    function initTabs() {{ document.querySelectorAll('.tab').forEach(tab=>tab.addEventListener('click',()=>{{ const view=tab.dataset.view; document.querySelectorAll('.tab').forEach(x=>x.setAttribute('aria-selected',String(x===tab))); document.querySelectorAll('.view').forEach(section=>section.hidden=section.id!==view); history.replaceState(null,'','#'+view); }})); const requested=location.hash.slice(1); const tab=requested&&document.querySelector(`.tab[data-view="${{requested}}"]`); if(tab) tab.click(); }}
    initHeader(); renderBuildRecord(); renderMetrics(); renderTruth(); initFilters(); renderJourney(); renderSystem(); initEvidence(); initTabs();
  </script>
</body>
</html>"""
