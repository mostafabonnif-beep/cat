# -*- coding: utf-8 -*-
"""ViralCutter WebUI stylesheet (extracted from app.py for organization)."""

CSS = """
#logs_output textarea {
    min-height: 300px !important;
    max-height: 520px !important;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace !important;
    line-height: 1.6 !important;
}

.vc-topbar {
    position: sticky;
    top: 0;
    z-index: 20;
    background: rgba(2, 6, 23, 0.92);
    backdrop-filter: blur(10px);
    padding: 10px 0;
    margin-bottom: 12px;
    gap: 12px;
    align-items: center;
}

.vc-topbar > div,
.vc-topbar > button {
    flex: 0 0 auto !important;
}

.vc-panels > div {
    min-width: 0;
}

body, .gradio-container {
    background-color: #0b0b0b !important;
    color: #ffffff !important;
}

input[type="password"], textarea, select {
    background-color: #1f1f1f !important;
    color: #ffffff !important;
    border: 1px solid #333 !important;
}

footer {visibility: hidden}

/* Keep the Arabic workbench readable on small screens. */
@media (max-width: 900px) {
    .gradio-container { max-width: 96% !important; width: 96% !important; }
    .gradio-container .tab-nav button { min-width: 0 !important; flex: 1 1 42% !important; }
}

.gradio-container {
    max-width: 98% !important;
    width: 98% !important;
    margin: 0 auto !important;
}

/* --- RTL layout for the Arabic UI ---
   Text fields keep per-content direction (URLs stay LTR) via plaintext bidi. */
body, .gradio-container {
    direction: rtl !important;
    font-family: "Cairo", "Tajawal", "Segoe UI", Tahoma, Arial, sans-serif !important;
}

/* Arabic help cards: compact guidance without visual clutter */
.vc-help-card {
    direction: rtl !important;
    text-align: right !important;
    background: rgba(249,115,22,0.08) !important;
    border-right: 3px solid #f97316 !important;
    border-radius: 10px !important;
    color: #fed7aa !important;
    padding: 8px 12px !important;
    margin: 4px 0 10px !important;
    line-height: 1.7 !important;
}

/* Section headings inside the app: subtle separator for clean formatting */
.gradio-container h3 {
    margin-top: 16px !important;
    padding-bottom: 4px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
}

/* Header card: force light text regardless of Gradio theme defaults */
#vc-header, #vc-header p, #vc-header li, #vc-header strong, #vc-header ul {
    color: #e2e8f0 !important;
}
#vc-header h1 {
    color: #f8fafc !important;
}

.gradio-container input,
.gradio-container textarea {
    unicode-bidi: plaintext !important;
}

/* --- Tab bar: rounded pills, subtle dark surface (v6.15 UI polish) --- */
.gradio-container .tab-nav {
    background: rgba(255,255,255,0.04) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 14px !important;
    padding: 6px !important;
    margin: 18px 0 !important;
    gap: 6px !important;
    display: flex !important;
    flex-wrap: wrap !important;
    justify-content: center !important;
}
.gradio-container .tab-nav button {
    border-radius: 10px !important;
    border: none !important;
    background: transparent !important;
    color: #cbd5e1 !important;
    font-weight: 600 !important;
    padding: 10px 14px !important;
    min-width: 118px !important;
    transition: all 0.15s ease;
}
.gradio-container .tab-nav button:hover {
    background: rgba(255,255,255,0.07) !important;
    color: #f1f5f9 !important;
}
.gradio-container .tab-nav button.selected {
    background: linear-gradient(90deg, #f97316, #ea580c) !important;
    color: #fff !important;
}

/* Create New: the source/workflow column stays visually left while the
   AI/editing column stays right; each column's content remains Arabic RTL. */
#vc-create-layout {
    direction: ltr !important;
    align-items: flex-start !important;
    gap: 22px !important;
}
#vc-create-layout > div {
    min-width: 0 !important;
}
.vc-create-toolbar {
    direction: rtl !important;
    align-items: stretch !important;
    gap: 12px !important;
    margin: 6px 0 12px !important;
}
.vc-create-toolbar button {
    min-height: 42px !important;
    white-space: nowrap !important;
}
.vc-validation {
    direction: rtl !important;
    text-align: right !important;
    border-radius: 12px !important;
    padding: 10px 14px !important;
    line-height: 1.8 !important;
    border: 1px solid rgba(148,163,184,.22) !important;
    background: rgba(15,23,42,.72) !important;
}
.vc-validation-ok {
    color: #bbf7d0 !important;
    border-color: rgba(34,197,94,.4) !important;
}
.vc-validation-warn {
    color: #fde68a !important;
    border-color: rgba(245,158,11,.45) !important;
}
.vc-validation-error {
    color: #fecaca !important;
    border-color: rgba(239,68,68,.5) !important;
}
.vc-validation-neutral {
    color: #cbd5e1 !important;
}
@media (max-width: 900px) {
    #vc-create-layout {
        flex-direction: column !important;
        gap: 8px !important;
    }
    #vc-create-layout > div {
        width: 100% !important;
    }
    .vc-create-toolbar {
        flex-direction: column !important;
    }
    .vc-create-toolbar button {
        width: 100% !important;
    }
}
#vc-create-layout > div {
    direction: rtl !important;
    text-align: right !important;
}
#vc-create-layout h3,
#vc-create-layout .prose,
#vc-create-layout label,
#vc-create-layout .wrap,
#vc-create-layout .block {
    direction: rtl !important;
    text-align: right !important;
}
#vc-create-layout input:not([type="checkbox"]),
#vc-create-layout textarea,
#vc-create-layout select {
    direction: rtl !important;
    text-align: right !important;
}
#vc-create-layout textarea[placeholder*="youtube"],
#vc-create-layout input[placeholder*="youtube"] {
    direction: ltr !important;
    text-align: left !important;
}
#vc-bottom-actions {
    direction: rtl !important;
    justify-content: flex-start !important;
    margin-top: 18px !important;
    padding: 14px !important;
    border-top: 1px solid rgba(249,115,22,0.22) !important;
    background: rgba(255,255,255,0.035) !important;
    border-radius: 14px !important;
    gap: 12px !important;
}
#vc-bottom-actions button {
    min-height: 48px !important;
    font-size: 1.05em !important;
    font-weight: 700 !important;
    direction: rtl !important;
}
#vc-bottom-actions button.primary {
    order: 0 !important;
}
#vc-bottom-actions button.stop {
    order: 1 !important;
}

/* --- Top action bar: glass card --- */
#vc-monitor {
    margin: 10px 0 4px !important;
    border: 1px solid rgba(249,115,22,0.20) !important;
    border-radius: 14px !important;
    background: rgba(255,255,255,0.025) !important;
}
#vc-monitor > .label-wrap {
    color: #fed7aa !important;
    font-weight: 700 !important;
}
.vc-topbar {
    background: rgba(255,255,255,0.05) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 14px !important;
    padding: 10px 14px !important;
}

/* --- Segment review table: readable Arabic safety explanations --- */
#review_segments_df {
    direction: rtl !important;
    text-align: right !important;
}
#review_segments_df .table-wrap {
    overflow-x: auto !important;
    border-radius: 12px !important;
}
#review_segments_df table {
    min-width: 1080px !important;
}
#review_segments_df th,
#review_segments_df td {
    white-space: normal !important;
    vertical-align: top !important;
    line-height: 1.55 !important;
}
#review_segments_df th:nth-child(10),
#review_segments_df td:nth-child(10),
#review_segments_df th:nth-child(11),
#review_segments_df td:nth-child(11) {
    min-width: 220px !important;
}

/* --- Progress/tasks/errors panels: subtle cards --- */
.vc-panels > div {
    background: rgba(255,255,255,0.03) !important;
    border: 1px solid rgba(255,255,255,0.06) !important;
    border-radius: 12px !important;
    padding: 10px 12px !important;
}
.vc-panels h3 {
    border-bottom: none !important;
    margin-top: 0 !important;
}

/* --- Primary CTA gets a gradient --- */
.vc-topbar button.primary {
    background: linear-gradient(90deg, #f97316, #ea580c) !important;
    border: none !important;
    color: #fff !important;
    font-weight: 700 !important;
}

/* --- Subtle scrollbar for the log --- */
#logs_output textarea::-webkit-scrollbar { width: 8px; }
#logs_output textarea::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
"""
