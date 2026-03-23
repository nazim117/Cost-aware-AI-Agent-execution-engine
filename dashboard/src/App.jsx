import { useState, useEffect, useRef, useMemo } from "react";
import ReactMarkdown from "react-markdown";

// ─── Constants ────────────────────────────────────────────────────────────

const MODELS = [
  { id: "deepseek-chat",  label: "DeepSeek Chat",   tier: "free", badge: "FREE",  color: "#3b82f6" },
  { id: "deepseek-coder", label: "DeepSeek Coder",  tier: "premium",  badge: "PRO",  color: "#f97316" },
  { id: "deepseek-flash", label: "DeepSeek Flash",  tier: "cheap",    badge: "FAST", color: "#22c55e" },
];

const MODES = [
  {
    id: "research",
    label: "Research",
    icon: "⬡",
    description: "Deep multi-step research with search & synthesis",
    graph: {
      entry: "plan",
      nodes: {
        plan:        { name: "plan",        edges: [{ to: "search",     condition: { always: true } }] },
        search:      { name: "search",      step_type: "execute", edges: [{ to: "deep_search", condition: { always: true } }] },
        deep_search: { name: "deep_search", step_type: "execute", edges: [{ to: "synthesize",  condition: { always: true } }] },
        synthesize:  { name: "synthesize",  edges: [{ to: "summarize",  condition: { always: true } }] },
        summarize:   { name: "summarize",   edges: [] },
      },
    },
  },
  {
    id: "analysis",
    label: "Analysis",
    icon: "⬢",
    description: "Structured plan → execute → summarize",
    graph: {
      entry: "plan",
      nodes: {
        plan:      { name: "plan",      edges: [{ to: "execute",  condition: { always: true } }] },
        execute:   { name: "execute",   step_type: "execute", edges: [{ to: "summarize", condition: { always: true } }] },
        summarize: { name: "summarize", edges: [] },
      },
    },
  },
  {
    id: "deep_dive",
    label: "Deep Dive",
    icon: "⬟",
    description: "Exhaustive 6-step deep dive with critique",
    graph: {
      entry: "plan",
      nodes: {
        plan:        { name: "plan",        edges: [{ to: "search",     condition: { always: true } }] },
        search:      { name: "search",      step_type: "execute", edges: [{ to: "deep_search", condition: { always: true } }] },
        deep_search: { name: "deep_search", step_type: "execute", edges: [{ to: "cross_check",  condition: { always: true } }] },
        cross_check: { name: "cross_check", step_type: "execute", edges: [{ to: "synthesize",   condition: { always: true } }] },
        synthesize:  { name: "synthesize",  edges: [{ to: "summarize",  condition: { always: true } }] },
        summarize:   { name: "summarize",   edges: [] },
      },
    },
  },
  {
    id: "quick",
    label: "Quick",
    icon: "⬠",
    description: "Fast plan → summarize only",
    graph: {
      entry: "plan",
      nodes: {
        plan:      { name: "plan",      edges: [{ to: "summarize", condition: { always: true } }] },
        summarize: { name: "summarize", edges: [] },
      },
    },
  },
];

const TIER_META = {
  premium:  { color: "#f97316", bg: "rgba(249,115,22,0.12)",  label: "PRO",  glow: "rgba(249,115,22,0.3)" },
  standard: { color: "#3b82f6", bg: "rgba(59,130,246,0.12)",  label: "STD",  glow: "rgba(59,130,246,0.3)" },
  cheap:    { color: "#22c55e", bg: "rgba(34,197,94,0.12)",   label: "FAST", glow: "rgba(34,197,94,0.3)"  },
  stopped:  { color: "#ef4444", bg: "rgba(239,68,68,0.12)",   label: "STOP", glow: "rgba(239,68,68,0.3)"  },
};

// ─── Graph Layout & Rendering ─────────────────────────────────────────────

const NODE_W = 84, NODE_H = 40, H_GAP = 48, V_GAP = 20, GRAPH_PAD = 14;

function buildLayout(graph) {
  if (!graph?.nodes || !graph?.entry) return null;
  const { nodes, entry } = graph;

  // Topological sort (iterative DFS post-order)
  const topo = [], visited = new Set();
  function dfs(name) {
    if (visited.has(name) || !nodes[name]) return;
    visited.add(name);
    for (const e of (nodes[name].edges || [])) dfs(e.to);
    topo.push(name);
  }
  dfs(entry);
  topo.reverse(); // entry first

  // Longest-path rank from entry (handles skip edges correctly)
  const rank = Object.fromEntries(topo.map(n => [n, 0]));
  for (const n of topo) {
    for (const e of (nodes[n]?.edges ?? [])) {
      if (nodes[e.to] && rank[e.to] <= rank[n]) rank[e.to] = rank[n] + 1;
    }
  }

  // Group by rank, preserving topo order within each rank
  const byRank = {};
  for (const n of topo) {
    const r = rank[n];
    (byRank[r] = byRank[r] || []).push(n);
  }

  const pos = {};
  for (const [r, names] of Object.entries(byRank)) {
    const ri = +r;
    names.forEach((name, col) => {
      pos[name] = {
        x: GRAPH_PAD + ri * (NODE_W + H_GAP),
        y: GRAPH_PAD + col * (NODE_H + V_GAP),
      };
    });
  }

  const maxRank = Math.max(0, ...topo.map(n => rank[n]));
  const maxCols = Math.max(1, ...Object.values(byRank).map(a => a.length));
  return {
    pos,
    svgW: GRAPH_PAD * 2 + (maxRank + 1) * NODE_W + maxRank * H_GAP,
    svgH: GRAPH_PAD * 2 + maxCols * NODE_H + Math.max(0, maxCols - 1) * V_GAP,
  };
}

function edgeStyle(cond) {
  if (cond?.on_hard_stop)       return { stroke: "#ef4444", dash: "5,3", marker: "arr-red",    label: "hard stop" };
  if (cond?.budget_ratio_below) return { stroke: "#f97316", dash: "5,3", marker: "arr-orange", label: `<${Math.round(cond.budget_ratio_below * 100)}% budget` };
  return                               { stroke: "#3d444d", dash: null,   marker: "arr-gray",   label: null };
}

// ─── Expanded Step Panel ──────────────────────────────────────────────────

function ExpandedStep({ name, step }) {
  const meta = TIER_META[step.model_tier] || TIER_META.stopped;
  return (
    <div style={{
      marginTop: "8px", padding: "10px 14px",
      background: "#0d1117", border: "1px solid #21262d",
      borderLeft: `3px solid ${meta.color}`,
      borderRadius: "0 6px 6px 0",
    }}>
      <div style={{
        fontSize: "10px", color: "#6e7681", fontFamily: "'JetBrains Mono', monospace",
        marginBottom: "6px", letterSpacing: "0.06em",
        display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center",
      }}>
        <span style={{ color: meta.color, fontWeight: 600 }}>{name.toUpperCase()}</span>
        <span style={{ color: "#484f58" }}>·</span>
        <span>{step.model_tier}</span>
        <span style={{ color: "#484f58" }}>·</span>
        <span>{step.latency_ms}ms</span>
        <span style={{ color: "#484f58" }}>·</span>
        <span style={{ color: "#22c55e" }}>${step.cost?.toFixed(3)}</span>
      </div>
      {step.decision && (
        <div style={{
          fontSize: "10px", color: "#8b949e", fontFamily: "'JetBrains Mono', monospace",
          marginBottom: "8px", paddingBottom: "6px", borderBottom: "1px solid #21262d",
          fontStyle: "italic",
        }}>
          policy: {step.decision}
        </div>
      )}
      <div style={{
        fontSize: "12px", lineHeight: "1.7", color: "#c9d1d9",
        fontFamily: "'JetBrains Mono', monospace",
        whiteSpace: "pre-wrap", maxHeight: "240px", overflowY: "auto",
      }}>
        <ReactMarkdown>{step.content}</ReactMarkdown>
      </div>

      {/* Tool calls */}
      {step.tool_calls?.length > 0 && (
        <div style={{ marginTop: "10px" }}>
          <div style={{
            fontSize: "9px", color: "#6e7681", letterSpacing: "0.1em",
            fontFamily: "'JetBrains Mono', monospace", marginBottom: "6px",
          }}>
            TOOL CALLS ({step.tool_calls.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {step.tool_calls.map((tc, i) => (
              <ToolCallCard key={i} tc={tc} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ToolCallCard({ tc }) {
  const [open, setOpen] = useState(false);
  const color = tc.is_error ? "#ef4444" : "#a78bfa";
  const bg    = tc.is_error ? "rgba(239,68,68,0.08)" : "rgba(124,58,237,0.08)";
  const border = tc.is_error ? "#ef444430" : "#7c3aed40";

  return (
    <div style={{
      background: bg, border: `1px solid ${border}`,
      borderRadius: "5px", overflow: "hidden",
    }}>
      {/* Header row */}
      <div
        onClick={() => setOpen(v => !v)}
        style={{
          display: "flex", alignItems: "center", gap: "8px",
          padding: "6px 10px", cursor: "pointer",
        }}
      >
        <span style={{ fontSize: "10px", color, fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>
          ⚙ {tc.tool_name}
        </span>
        <span style={{ fontSize: "9px", color: "#484f58", fontFamily: "'JetBrains Mono', monospace", marginLeft: "auto" }}>
          {tc.latency_ms}ms
        </span>
        {tc.is_error && (
          <span style={{ fontSize: "9px", color: "#ef4444", fontFamily: "'JetBrains Mono', monospace" }}>ERR</span>
        )}
        <span style={{ fontSize: "8px", color: "#484f58", fontFamily: "'JetBrains Mono', monospace" }}>
          {open ? "▴" : "▾"}
        </span>
      </div>

      {/* Args + result */}
      {open && (
        <div style={{ padding: "0 10px 8px", display: "flex", flexDirection: "column", gap: "6px" }}>
          {tc.arguments && Object.keys(tc.arguments).length > 0 && (
            <div>
              <div style={{ fontSize: "9px", color: "#6e7681", fontFamily: "'JetBrains Mono', monospace", marginBottom: "3px" }}>
                ARGS
              </div>
              <pre style={{
                margin: 0, fontSize: "10px", color: "#8b949e",
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: "pre-wrap", wordBreak: "break-all",
                background: "#0d1117", padding: "6px 8px",
                borderRadius: "4px", maxHeight: "80px", overflowY: "auto",
              }}>
                {JSON.stringify(tc.arguments, null, 2)}
              </pre>
            </div>
          )}
          <div>
            <div style={{ fontSize: "9px", color: "#6e7681", fontFamily: "'JetBrains Mono', monospace", marginBottom: "3px" }}>
              RESULT
            </div>
            <pre style={{
              margin: 0, fontSize: "10px",
              color: tc.is_error ? "#ef4444" : "#c9d1d9",
              fontFamily: "'JetBrains Mono', monospace",
              whiteSpace: "pre-wrap", wordBreak: "break-all",
              background: "#0d1117", padding: "6px 8px",
              borderRadius: "4px", maxHeight: "120px", overflowY: "auto",
            }}>
              {tc.result || "(empty)"}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Dynamic Step Graph ───────────────────────────────────────────────────

function StepGraph({ graph, steps = [], running = false }) {
  const [expanded, setExpanded] = useState(null);
  const layout = useMemo(() => buildLayout(graph), [graph]);

  if (!layout) return null;
  const { pos, svgW, svgH } = layout;

  // nodeName → step result
  const executedMap = useMemo(() => {
    const m = {};
    steps.forEach(s => { m[s.step] = s; });
    return m;
  }, [steps]);

  // Nodes pulsing as "next to execute" while running
  const nextNodes = useMemo(() => {
    if (!running || !graph) return new Set();
    if (steps.length === 0) return new Set([graph.entry]);
    const last = steps[steps.length - 1]?.step;
    return new Set(
      (graph.nodes[last]?.edges || []).map(e => e.to).filter(n => graph.nodes[n])
    );
  }, [running, steps, graph]);

  // Collect all edges from graph topology
  const edges = useMemo(() => {
    if (!layout) return [];
    const result = [];
    for (const [from, node] of Object.entries(graph.nodes)) {
      for (const edge of (node.edges || [])) {
        if (pos[from] && pos[edge.to]) {
          result.push({ from, to: edge.to, condition: edge.condition });
        }
      }
    }
    return result;
  }, [layout]);

  const totalCost    = steps.reduce((s, r) => s + (r.cost || 0), 0);
  const totalLatency = steps.reduce((s, r) => s + (r.latency_ms || 0), 0);

  return (
    <div style={{ marginTop: "10px" }}>
      <div style={{ overflowX: "auto" }}>
        <svg width={svgW} height={svgH} style={{ display: "block", overflow: "visible" }}>
          <defs>
            {[["arr-gray", "#3d444d"], ["arr-red", "#ef4444"], ["arr-orange", "#f97316"]].map(([id, fill]) => (
              <marker key={id} id={id}
                markerWidth="6" markerHeight="6"
                refX="0" refY="3" orient="auto" markerUnits="userSpaceOnUse">
                <path d="M0,0 L6,3 L0,6 z" fill={fill} />
              </marker>
            ))}
          </defs>

          {/* ── Edges ── */}
          {edges.map(({ from, to, condition }, i) => {
            const sp = pos[from], tp = pos[to];
            const s  = edgeStyle(condition);
            const sx = sp.x + NODE_W, sy = sp.y + NODE_H / 2;
            const ex = tp.x - 6,     ey = tp.y + NODE_H / 2; // -6px gap for arrowhead
            const dx = ex - sx;
            const d  = `M${sx},${sy} C${sx + dx * 0.5},${sy} ${ex - dx * 0.5},${ey} ${ex},${ey}`;
            return (
              <g key={i}>
                <path d={d} stroke={s.stroke} strokeWidth="1.5"
                  strokeDasharray={s.dash || undefined}
                  fill="none" markerEnd={`url(#${s.marker})`} />
                {s.label && (
                  <text x={(sx + ex) / 2} y={Math.min(sy, ey) - 5}
                    fill={s.stroke} fontSize="8" textAnchor="middle"
                    fontFamily="'JetBrains Mono', monospace">
                    {s.label}
                  </text>
                )}
              </g>
            );
          })}

          {/* ── Nodes ── */}
          {Object.entries(pos).map(([name, p]) => {
            const result     = executedMap[name];
            const isNext     = nextNodes.has(name);
            const isExpanded = expanded === name;
            const tier       = result?.model_tier;
            const meta       = tier ? (TIER_META[tier] || TIER_META.stopped) : null;

            return (
              <g key={name}
                onClick={() => result?.content && setExpanded(isExpanded ? null : name)}
                style={{ cursor: result?.content ? "pointer" : "default" }}>

                {/* Pulse ring while this node is "up next" */}
                {isNext && (
                  <rect x={p.x - 3} y={p.y - 3} width={NODE_W + 6} height={NODE_H + 6}
                    rx="9" fill="none" stroke="#58a6ff" strokeWidth="1" opacity="0.5"
                    style={{ animation: "borderPulse 1.5s infinite" }} />
                )}

                {/* Node body */}
                <rect x={p.x} y={p.y} width={NODE_W} height={NODE_H} rx="6"
                  fill={meta ? meta.bg : isNext ? "rgba(88,166,255,0.07)" : "#0d1117"}
                  stroke={meta ? meta.color : isNext ? "#58a6ff80" : "#30363d"}
                  strokeWidth="1" />

                {/* Node name */}
                <text x={p.x + NODE_W / 2} y={p.y + (meta ? 14 : NODE_H / 2 + 4)}
                  fill={meta ? meta.color : isNext ? "#58a6ff" : "#6e7681"}
                  fontSize="11" fontWeight="600" textAnchor="middle"
                  fontFamily="'JetBrains Mono', monospace" letterSpacing="0.02em">
                  {name}
                </text>

                {/* Tier + cost badge (executed nodes only) */}
                {meta && (
                  <text x={p.x + NODE_W / 2} y={p.y + NODE_H - 7}
                    fill={meta.color} fontSize="8" textAnchor="middle"
                    fontFamily="'JetBrains Mono', monospace" opacity="0.8">
                    {meta.label} · ${result.cost?.toFixed(3)}
                  </text>
                )}

                {/* Tool-call count badge */}
                {result?.tool_calls?.length > 0 && (
                  <g>
                    <rect x={p.x + NODE_W - 18} y={p.y - 7} width={18} height={12} rx="3"
                      fill="#7c3aed20" stroke="#7c3aed60" strokeWidth="0.5" />
                    <text x={p.x + NODE_W - 9} y={p.y + 2}
                      fill="#a78bfa" fontSize="7" textAnchor="middle"
                      fontFamily="'JetBrains Mono', monospace" fontWeight="600">
                      ⚙{result.tool_calls.length}
                    </text>
                  </g>
                )}

                {/* Expand/collapse chevron */}
                {result?.content && (
                  <text x={p.x + NODE_W - 9} y={p.y + 11}
                    fill="#484f58" fontSize="8" textAnchor="middle"
                    fontFamily="'JetBrains Mono', monospace">
                    {isExpanded ? "▴" : "▾"}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </div>

      {/* Expanded step detail panel */}
      {expanded && executedMap[expanded] && (
        <ExpandedStep name={expanded} step={executedMap[expanded]} />
      )}

      {/* Run summary bar */}
      {steps.length > 0 && !running && (
        <div style={{
          marginTop: "6px", display: "flex", gap: "14px", flexWrap: "wrap",
          fontSize: "10px", color: "#6e7681", fontFamily: "'JetBrains Mono', monospace",
        }}>
          <span>cost <span style={{ color: "#22c55e" }}>${totalCost.toFixed(3)}</span></span>
          <span>latency <span style={{ color: "#3b82f6" }}>{totalLatency}ms</span></span>
          <span>steps <span style={{ color: "#e6edf3" }}>{steps.length}</span></span>
        </div>
      )}
    </div>
  );
}

// ─── Cost Trend Chart ─────────────────────────────────────────────────────

function CostTrendChart({ runs }) {
  const MAX_POINTS = 20;
  const points = [...runs].reverse().slice(0, MAX_POINTS); // oldest → newest
  if (points.length < 2) {
    return (
      <div style={{ fontSize: "10px", color: "#484f58", fontFamily: "'JetBrains Mono', monospace", padding: "8px 0" }}>
        run more agents to see trend
      </div>
    );
  }

  const W = 220, H = 56, PAD = 6;
  const costs = points.map(r => r.total_cost);
  const minC = Math.min(...costs);
  const maxC = Math.max(...costs);
  const range = maxC - minC || 0.001;

  const toX = i => PAD + (i / (points.length - 1)) * (W - PAD * 2);
  const toY = c => PAD + (1 - (c - minC) / range) * (H - PAD * 2);

  const polyPoints = points.map((r, i) => `${toX(i)},${toY(r.total_cost)}`).join(" ");

  return (
    <svg width={W} height={H} style={{ display: "block", overflow: "visible" }}>
      <polyline points={polyPoints} fill="none" stroke="#22c55e" strokeWidth="1.5" />
      {points.map((r, i) => (
        <circle key={i} cx={toX(i)} cy={toY(r.total_cost)} r="2.5" fill="#22c55e" opacity="0.8" />
      ))}
      <text x={PAD} y={H - 1} fontSize="8" fill="#484f58" fontFamily="'JetBrains Mono', monospace">
        ${minC.toFixed(3)}
      </text>
      <text x={W - PAD} y={H - 1} fontSize="8" fill="#484f58" fontFamily="'JetBrains Mono', monospace" textAnchor="end">
        ${maxC.toFixed(3)}
      </text>
    </svg>
  );
}

// ─── Run History Panel ─────────────────────────────────────────────────────

function RunHistoryPanel({ runs, onClose }) {
  const [selectedRun, setSelectedRun] = useState(null);

  return (
    <div style={{
      width: "300px", minWidth: "300px",
      background: "#161b22", borderLeft: "1px solid #21262d",
      display: "flex", flexDirection: "column", overflow: "hidden",
    }}>
      {/* Panel header */}
      <div style={{
        padding: "12px 16px", borderBottom: "1px solid #21262d",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexShrink: 0,
      }}>
        <span style={{ fontSize: "11px", fontWeight: 700, color: "#e6edf3", letterSpacing: "0.06em" }}>RUN HISTORY</span>
        <button onClick={onClose} style={{
          background: "none", border: "none", cursor: "pointer",
          color: "#6e7681", fontSize: "14px", padding: "2px 4px",
        }}>✕</button>
      </div>

      {/* Cost trend chart */}
      {runs.length > 1 && (
        <div style={{ padding: "10px 16px", borderBottom: "1px solid #21262d", flexShrink: 0 }}>
          <div style={{ fontSize: "9px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "6px" }}>COST TREND</div>
          <CostTrendChart runs={runs} />
        </div>
      )}

      {/* Run detail */}
      {selectedRun ? (
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
          <button onClick={() => setSelectedRun(null)} style={{
            background: "none", border: "none", cursor: "pointer",
            color: "#58a6ff", fontSize: "10px", padding: 0, marginBottom: "10px",
            fontFamily: "'JetBrains Mono', monospace",
          }}>← back</button>
          <div style={{ fontSize: "11px", color: "#e6edf3", marginBottom: "6px", wordBreak: "break-word" }}>
            {selectedRun.goal}
          </div>
          <div style={{ fontSize: "10px", color: "#6e7681", marginBottom: "10px", display: "flex", gap: "10px" }}>
            <span style={{ color: "#22c55e" }}>${selectedRun.total_cost?.toFixed(4)}</span>
            <span>{selectedRun.total_latency_ms}ms</span>
            <span>{selectedRun.timestamp?.slice(11, 19)}</span>
          </div>

          {/* Step table */}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>
            <thead>
              <tr>
                {["Step", "Tier", "Cost", "Latency", "Reason"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", color: "#6e7681", fontWeight: 600,
                    padding: "4px 6px 6px", borderBottom: "1px solid #21262d",
                    letterSpacing: "0.06em", fontSize: "9px",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(selectedRun.steps || []).map((s, i) => {
                const meta = TIER_META[s.model_tier] || TIER_META.stopped;
                return (
                  <tr key={i} style={{ borderBottom: "1px solid #21262d30" }}>
                    <td style={{ padding: "5px 6px", color: "#8b949e" }}>{s.step}</td>
                    <td style={{ padding: "5px 6px" }}>
                      <span style={{ color: meta.color, fontSize: "9px", fontWeight: 600 }}>{meta.label}</span>
                    </td>
                    <td style={{ padding: "5px 6px", color: "#22c55e" }}>${s.cost?.toFixed(3)}</td>
                    <td style={{ padding: "5px 6px", color: "#3b82f6" }}>{s.latency_ms}ms</td>
                    <td style={{ padding: "5px 6px", color: "#484f58", maxWidth: "80px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={s.decision}>{s.decision}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        /* Run list */
        <div style={{ flex: 1, overflowY: "auto" }}>
          {runs.length === 0 ? (
            <div style={{ padding: "20px 16px", fontSize: "11px", color: "#484f58", fontFamily: "'JetBrains Mono', monospace" }}>
              no runs yet
            </div>
          ) : (
            runs.map(run => (
              <button key={run.run_id} onClick={() => setSelectedRun(run)} style={{
                width: "100%", background: "none", border: "none",
                borderBottom: "1px solid #21262d", padding: "10px 14px",
                cursor: "pointer", textAlign: "left",
                transition: "background 0.1s",
              }}
                onMouseEnter={e => e.currentTarget.style.background = "#21262d"}
                onMouseLeave={e => e.currentTarget.style.background = "none"}
              >
                <div style={{
                  fontSize: "11px", color: "#c9d1d9", marginBottom: "4px",
                  fontFamily: "'JetBrains Mono', monospace",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {run.goal?.length > 55 ? run.goal.slice(0, 55) + "…" : run.goal}
                </div>
                <div style={{ display: "flex", gap: "10px", fontSize: "10px", fontFamily: "'JetBrains Mono', monospace" }}>
                  <span style={{ color: "#22c55e" }}>${run.total_cost?.toFixed(4)}</span>
                  <span style={{ color: "#484f58" }}>{run.timestamp?.slice(11, 19)}</span>
                  <span style={{ color: "#6e7681" }}>{run.steps?.length ?? 0} steps</span>
                </div>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ─── Message Bubble ───────────────────────────────────────────────────────

function Message({ msg }) {
  const isUser = msg.role === "user";

  return (
    <div style={{
      display: "flex", gap: "12px",
      flexDirection: isUser ? "row-reverse" : "row",
      alignItems: "flex-start",
      padding: "6px 0",
    }}>
      {/* Avatar */}
      <div style={{
        width: "28px", height: "28px", borderRadius: "50%", flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: isUser ? "#21262d" : "#161b22",
        border: `1px solid ${isUser ? "#30363d" : "#238636"}`,
        fontSize: "12px",
      }}>
        {isUser ? "👤" : "⚡"}
      </div>

      {/* Content */}
      <div style={{ maxWidth: "78%", minWidth: "120px" }}>
        {/* Text bubble — only rendered when there's content or agent is thinking */}
        {(msg.content || msg.thinking) && (
          <div style={{
            padding: "12px 16px",
            background: isUser ? "#21262d" : "#161b22",
            border: `1px solid ${isUser ? "#30363d" : "#21262d"}`,
            borderRadius: isUser ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
            fontSize: "14px", lineHeight: "1.7", color: "#e6edf3",
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            <ReactMarkdown>{msg.content}</ReactMarkdown>

            {/* Thinking dots */}
            {msg.role === "assistant" && msg.thinking && (
              <div style={{ marginTop: "8px", display: "flex", gap: "4px", alignItems: "center" }}>
                {[0, 1, 2].map(i => (
                  <div key={i} style={{
                    width: "5px", height: "5px", borderRadius: "50%",
                    background: "#3b82f6",
                    animation: `dotBounce 1.2s ${i * 0.2}s infinite`,
                  }} />
                ))}
                <span style={{ color: "#6e7681", fontSize: "12px", marginLeft: "6px" }}>
                  {msg.thinkingText || "processing..."}
                </span>
              </div>
            )}
          </div>
        )}

        {/* Dynamic step graph — shown whenever the message has a graph attached */}
        {msg.role === "assistant" && msg.graph && (
          <StepGraph
            graph={msg.graph}
            steps={msg.steps || []}
            running={!!msg.thinking}
          />
        )}
      </div>
    </div>
  );
}

// ─── Main App ─────────────────────────────────────────────────────────────

export default function App() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content: "Hello! I'm your cost-aware AI agent. I route each step through the policy engine to select the optimal model tier based on your budget and latency SLA.\n\nChoose a mode and model below, then send your goal.",
      steps: [],
    }
  ]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [selectedModel, setSelectedModel] = useState(MODELS[0]);
  const [selectedMode, setSelectedMode] = useState(MODES[0]);
  const [budget, setBudget] = useState(0.20);
  const [latency, setLatency] = useState(500);
  const [currentStepIdx, setCurrentStepIdx] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [totalMetrics, setTotalMetrics] = useState({ runs: 0, cost: 0, steps: 0 });
  const [runs, setRuns] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const bottomRef = useRef(null);
  const textareaRef = useRef(null);

  const fetchRuns = async () => {
    try {
      const res = await fetch("/runs");
      if (res.ok) setRuns(await res.json());
    } catch (_) { /* ignore — backend may not be running */ }
  };

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    fetchRuns();
  }, []);

  const handleSend = async () => {
    if (!input.trim() || running) return;
    const goal = input.trim();
    setInput("");
    setRunning(true);

    // Add user message
    setMessages(prev => [...prev, { role: "user", content: goal }]);

    // Add thinking assistant message
    const thinkingId = Date.now();
    setMessages(prev => [...prev, {
      id: thinkingId,
      role: "assistant",
      content: "",
      thinking: true,
      thinkingText: "Thinking",
      steps: [],
      graph: selectedMode.graph,
    }]);

    try {
      const res = await fetch("/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal,
          budget,
          priority: "normal",
          latency_sla_ms: latency,
          step_graph: selectedMode.graph,
        }),
      });

      if (!res.ok) throw new Error(`${res.status} — ${await res.text()}`);
      const raw = await res.json();

      const steps = (raw.steps || []).map(s => ({
        step:       s.step,
        model_tier: s.model_tier || null,
        cost:       s.cost       ?? 0,
        latency_ms: s.latency_ms ?? 0,
        decision:   s.decision   || "",
        content:    s.content    || "",
        hardStop:   !s.model_tier,
        tool_calls: s.tool_calls || [],
      }));

      // Animate steps appearing
      for (let i = 0; i <= steps.length; i++) {
        await new Promise(r => setTimeout(r, 300));
        setCurrentStepIdx(i);
        setMessages(prev => prev.map(m =>
          m.id === thinkingId
            ? { ...m, thinkingText: i < steps.length ? `running ${steps[i]?.step}...` : "finalising...", steps: steps.slice(0, i) }
            : m
        ));
      }

      // Final message
      setMessages(prev => prev.map(m =>
        m.id === thinkingId
          ? { ...m, content: raw.result || "(no result)", thinking: false, steps }
          : m
      ));

      setTotalMetrics(prev => ({
        runs: prev.runs + 1,
        cost: prev.cost + (raw.total_cost ?? 0),
        steps: prev.steps + steps.length,
      }));

    } catch (err) {
      setMessages(prev => prev.map(m =>
        m.id === thinkingId
          ? { ...m, content: `⚠ Connection error: ${err.message}\n\nMake sure your services are running:\n  docker-compose up --build`, thinking: false }
          : m
      ));
    } finally {
      setRunning(false);
      setCurrentStepIdx(0);
      fetchRuns();
    }
  };

  const handleKey = e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  return (
    <div style={{
      display: "flex", height: "100vh", background: "#0d1117",
      fontFamily: "'JetBrains Mono', monospace", color: "#e6edf3",
      overflow: "hidden",
    }}>
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap" rel="stylesheet" />

      {/* ── Sidebar ── */}
      <div style={{
        width: sidebarOpen ? "260px" : "0",
        minWidth: sidebarOpen ? "260px" : "0",
        overflow: "hidden",
        transition: "all 0.25s ease",
        background: "#161b22",
        borderRight: "1px solid #21262d",
        display: "flex", flexDirection: "column",
      }}>
        <div style={{ padding: "16px", flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: "20px" }}>

          {/* Brand */}
          <div style={{ paddingTop: "4px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
              <div style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px #22c55e", animation: "pulse 2s infinite" }} />
              <span style={{ fontSize: "12px", fontWeight: 700, letterSpacing: "0.04em", color: "#e6edf3" }}>AGENT ENGINE</span>
            </div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.06em" }}>policy control plane</div>
          </div>

          {/* Model picker */}
          <div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "8px" }}>MODEL</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              {MODELS.map(m => (
                <button key={m.id} onClick={() => setSelectedModel(m)} style={{
                  display: "flex", alignItems: "center", gap: "10px",
                  padding: "8px 10px", borderRadius: "6px",
                  background: selectedModel.id === m.id ? "#21262d" : "transparent",
                  border: `1px solid ${selectedModel.id === m.id ? m.color + "60" : "transparent"}`,
                  cursor: "pointer", textAlign: "left",
                  transition: "all 0.15s",
                }}>
                  <span style={{
                    fontSize: "9px", padding: "2px 5px", borderRadius: "3px",
                    background: m.color + "20", color: m.color,
                    fontWeight: 700, letterSpacing: "0.06em", minWidth: "32px", textAlign: "center",
                  }}>{m.badge}</span>
                  <span style={{ fontSize: "12px", color: selectedModel.id === m.id ? "#e6edf3" : "#8b949e" }}>{m.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Mode picker */}
          <div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "8px" }}>MODE</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              {MODES.map(mode => (
                <button key={mode.id} onClick={() => setSelectedMode(mode)} style={{
                  display: "flex", flexDirection: "column", gap: "2px",
                  padding: "8px 10px", borderRadius: "6px",
                  background: selectedMode.id === mode.id ? "#21262d" : "transparent",
                  border: `1px solid ${selectedMode.id === mode.id ? "#58a6ff40" : "transparent"}`,
                  cursor: "pointer", textAlign: "left", transition: "all 0.15s",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "7px" }}>
                    <span style={{ color: selectedMode.id === mode.id ? "#58a6ff" : "#6e7681", fontSize: "12px" }}>{mode.icon}</span>
                    <span style={{ fontSize: "12px", color: selectedMode.id === mode.id ? "#e6edf3" : "#8b949e", fontWeight: selectedMode.id === mode.id ? 600 : 400 }}>
                      {mode.label}
                    </span>
                  </div>
                  <div style={{ fontSize: "10px", color: "#6e7681", paddingLeft: "19px" }}>{mode.description}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Mode step preview */}
          <div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "8px" }}>STEP GRAPH</div>
            <div style={{
              padding: "10px", background: "#0d1117",
              border: "1px solid #21262d", borderRadius: "6px",
              display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center",
            }}>
              {Object.keys(selectedMode.graph.nodes).map((name, i, arr) => (
                <div key={name} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                  <span style={{
                    fontSize: "10px", padding: "2px 7px", borderRadius: "4px",
                    background: "#161b22", border: "1px solid #30363d",
                    color: "#8b949e",
                  }}>{name}</span>
                  {i < arr.length - 1 && <span style={{ color: "#30363d", fontSize: "10px" }}>→</span>}
                </div>
              ))}
            </div>
          </div>

          {/* Budget / SLA */}
          <div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "10px" }}>CONSTRAINTS</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
                  <span style={{ fontSize: "11px", color: "#8b949e" }}>Budget</span>
                  <span style={{ fontSize: "11px", color: "#22c55e" }}>${budget.toFixed(3)}</span>
                </div>
                <input type="range" min="0.01" max="0.50" step="0.01" value={budget}
                  onChange={e => setBudget(parseFloat(e.target.value))}
                  style={{ width: "100%", accentColor: "#22c55e", height: "3px" }} />
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
                  <span style={{ fontSize: "11px", color: "#8b949e" }}>Latency SLA</span>
                  <span style={{ fontSize: "11px", color: "#3b82f6" }}>{latency}ms</span>
                </div>
                <input type="range" min="80" max="500" step="10" value={latency}
                  onChange={e => setLatency(parseInt(e.target.value))}
                  style={{ width: "100%", accentColor: "#3b82f6", height: "3px" }} />
              </div>
            </div>
          </div>

          {/* Session metrics */}
          <div>
            <div style={{ fontSize: "10px", color: "#6e7681", letterSpacing: "0.1em", marginBottom: "8px" }}>SESSION</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              {[
                { label: "Runs",       value: totalMetrics.runs,                       color: "#e6edf3" },
                { label: "Total cost", value: `$${totalMetrics.cost.toFixed(3)}`,       color: "#22c55e" },
                { label: "Steps run",  value: totalMetrics.steps,                      color: "#3b82f6" },
              ].map(m => (
                <div key={m.label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 8px", background: "#0d1117", borderRadius: "4px" }}>
                  <span style={{ fontSize: "11px", color: "#6e7681" }}>{m.label}</span>
                  <span style={{ fontSize: "11px", color: m.color, fontWeight: 600 }}>{m.value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ── Main chat area ── */}
      <div style={{ flex: 1, display: "flex", flexDirection: "row", minWidth: 0 }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>

        {/* Header */}
        <div style={{
          padding: "12px 20px", borderBottom: "1px solid #21262d",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          background: "#161b22", flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <button onClick={() => setSidebarOpen(v => !v)} style={{
              background: "none", border: "none", cursor: "pointer",
              color: "#6e7681", fontSize: "16px", padding: "2px 6px",
            }}>☰</button>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{
                fontSize: "10px", padding: "2px 8px", borderRadius: "4px",
                background: selectedModel.color + "20", color: selectedModel.color,
                fontWeight: 700, border: `1px solid ${selectedModel.color}40`,
              }}>{selectedModel.badge}</span>
              <span style={{ fontSize: "13px", color: "#8b949e" }}>{selectedModel.label}</span>
              <span style={{ color: "#30363d" }}>·</span>
              <span style={{ fontSize: "13px", color: "#58a6ff" }}>{selectedMode.icon} {selectedMode.label}</span>
            </div>
          </div>
          <div style={{ display: "flex", gap: "16px", alignItems: "center" }}>
            {[["policy-engine", "8080", "#22c55e"], ["agent-executor", "8081", "#22c55e"]].map(([name, port, col]) => (
              <div key={port} style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                <div style={{ width: "5px", height: "5px", borderRadius: "50%", background: col, boxShadow: `0 0 4px ${col}`, animation: "pulse 2s infinite" }} />
                <span style={{ fontSize: "10px", color: "#6e7681" }}>{name}</span>
              </div>
            ))}
            <button onClick={() => setHistoryOpen(v => !v)} style={{
              background: historyOpen ? "#21262d" : "none",
              border: `1px solid ${historyOpen ? "#58a6ff40" : "#30363d"}`,
              borderRadius: "6px", cursor: "pointer",
              color: historyOpen ? "#58a6ff" : "#6e7681",
              fontSize: "10px", padding: "4px 10px",
              fontFamily: "'JetBrains Mono', monospace",
              transition: "all 0.15s",
            }}>
              history {runs.length > 0 && <span style={{ color: "#22c55e" }}>{runs.length}</span>}
            </button>
          </div>
        </div>

        {/* Messages */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px", display: "flex", flexDirection: "column", gap: "4px" }}>
          {messages.map((msg, i) => <Message key={i} msg={msg} />)}
          <div ref={bottomRef} />
        </div>

        {/* Input area */}
        <div style={{
          padding: "16px 20px", borderTop: "1px solid #21262d",
          background: "#161b22", flexShrink: 0,
        }}>
          {/* Config pills */}
          <div style={{ display: "flex", gap: "6px", marginBottom: "10px", flexWrap: "wrap" }}>
            {[
              { label: `budget $${budget.toFixed(2)}`,  color: "#22c55e" },
              { label: `sla ${latency}ms`,              color: "#3b82f6" },
              { label: selectedMode.label,              color: "#58a6ff" },
              { label: selectedModel.badge,             color: selectedModel.color },
            ].map((pill, i) => (
              <span key={i} style={{
                fontSize: "10px", padding: "2px 8px", borderRadius: "12px",
                background: pill.color + "15", color: pill.color,
                border: `1px solid ${pill.color}30`,
                fontWeight: 500,
              }}>{pill.label}</span>
            ))}
          </div>

          <div style={{ display: "flex", gap: "10px", alignItems: "flex-end" }}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKey}
              placeholder="Ask the agent anything... (Enter to send, Shift+Enter for newline)"
              rows={1}
              disabled={running}
              style={{
                flex: 1, background: "#0d1117",
                border: "1px solid #30363d", borderRadius: "8px",
                color: "#e6edf3", padding: "10px 14px",
                fontSize: "13px", fontFamily: "'JetBrains Mono', monospace",
                resize: "none", outline: "none", lineHeight: "1.5",
                minHeight: "42px", maxHeight: "120px",
                opacity: running ? 0.5 : 1,
                transition: "border-color 0.15s",
              }}
              onFocus={e => e.target.style.borderColor = "#58a6ff"}
              onBlur={e => e.target.style.borderColor = "#30363d"}
            />
            <button
              onClick={handleSend}
              disabled={running || !input.trim()}
              style={{
                padding: "10px 18px", borderRadius: "8px",
                background: running || !input.trim() ? "#21262d" : "#238636",
                border: `1px solid ${running || !input.trim() ? "#30363d" : "#2ea043"}`,
                color: running || !input.trim() ? "#6e7681" : "#fff",
                cursor: running || !input.trim() ? "not-allowed" : "pointer",
                fontSize: "13px", fontFamily: "'JetBrains Mono', monospace",
                fontWeight: 600, transition: "all 0.15s",
                display: "flex", alignItems: "center", gap: "6px",
                flexShrink: 0, height: "42px",
              }}
            >
              {running ? (
                <>
                  <div style={{ width: "10px", height: "10px", borderRadius: "50%", border: "2px solid #6e7681", borderTopColor: "#58a6ff", animation: "spin 0.8s linear infinite" }} />
                  running
                </>
              ) : "↵ send"}
            </button>
          </div>

          <div style={{ marginTop: "8px", fontSize: "10px", color: "#484f58", textAlign: "center" }}>
            cost-aware AI agent engine · policy engine routes each step to optimal model tier
          </div>
        </div>
      </div>{/* end flex column */}

      {/* ── Run History Panel ── */}
      {historyOpen && (
        <RunHistoryPanel runs={runs} onClose={() => setHistoryOpen(false)} />
      )}
      </div>{/* end flex row */}

      <style>{`
        @keyframes pulse       { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes spin        { to{transform:rotate(360deg)} }
        @keyframes dotBounce   { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-5px)} }
        @keyframes borderPulse { 0%,100%{opacity:0.6} 50%{opacity:1} }
        ::-webkit-scrollbar      { width:4px; height:4px }
        ::-webkit-scrollbar-track { background:#0d1117 }
        ::-webkit-scrollbar-thumb { background:#30363d; border-radius:2px }
        input[type=range] { -webkit-appearance:none; background:#21262d; border-radius:2px; cursor:pointer }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:12px; height:12px; border-radius:3px; background:currentColor; cursor:pointer }
        textarea::placeholder { color:#484f58 }
        * { box-sizing:border-box }
      `}</style>
    </div>
  );
}