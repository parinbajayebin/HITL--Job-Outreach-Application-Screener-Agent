"""HITL Job Outreach & Application Screener — Streamlit UI.

A full-featured Streamlit dashboard that communicates with the FastAPI
backend to orchestrate human-in-the-loop resume screening, job matching,
outreach drafting, and dispatch.

Run with:
    streamlit run ui/app.py
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE = "http://127.0.0.1:8000"
POLL_INTERVAL_SECS = 3

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="HITL Job Screener",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Header banner */
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem 2rem;
        border-radius: 12px;
        color: white;
        margin-bottom: 1.5rem;
    }
    .main-header h1 { margin: 0; font-size: 1.8rem; }
    .main-header p  { margin: 0.3rem 0 0 0; opacity: 0.85; font-size: 0.95rem; }

    /* Status chips */
    .status-chip {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 16px;
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    .status-running   { background: #fef3c7; color: #92400e; }
    .status-scored    { background: #dbeafe; color: #1e40af; }
    .status-drafted   { background: #d1fae5; color: #065f46; }
    .status-approved  { background: #bbf7d0; color: #14532d; }
    .status-rejected  { background: #fecaca; color: #991b1b; }
    .status-awaiting  { background: #fde68a; color: #78350f; }

    /* Score gauge */
    .score-card {
        text-align: center;
        padding: 1rem;
        border-radius: 12px;
        border: 2px solid #e5e7eb;
    }
    .score-card .number { font-size: 2.5rem; font-weight: 800; }
    .score-high   { color: #059669; border-color: #34d399; }
    .score-medium { color: #d97706; border-color: #fbbf24; }
    .score-low    { color: #dc2626; border-color: #f87171; }

    /* Draft card */
    .draft-card {
        background: #f9fafb;
        border: 1px solid #e5e7eb;
        border-radius: 10px;
        padding: 1.2rem;
        margin-bottom: 1rem;
    }
    .draft-card h4 { margin-top: 0; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Utility helpers                                                       ║
# ╚═════════════════════════════════════════════════════════════════════════╝


def _api(method: str, path: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Call the FastAPI backend.  Returns parsed JSON or ``None`` on error."""
    url = f"{API_BASE}{path}"
    try:
        resp = getattr(requests, method)(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        st.error("⚠️ Cannot reach the API server. Is `uvicorn app.main:app` running?")
        return None
    except requests.HTTPError as exc:
        st.error(f"API error: {exc.response.status_code} — {exc.response.text}")
        return None
    except Exception as exc:
        st.error(f"Unexpected error: {exc}")
        return None


def _status_chip(status: str) -> str:
    """Return an HTML <span> chip for a workflow status."""
    cls = {
        "running": "status-running",
        "scored": "status-scored",
        "drafted": "status-drafted",
        "approved": "status-approved",
        "rejected": "status-rejected",
        "awaiting_human": "status-awaiting",
    }.get(status, "status-running")
    return f'<span class="status-chip {cls}">{status}</span>'


def _score_class(score: float) -> str:
    if score >= 75:
        return "score-high"
    if score >= 50:
        return "score-medium"
    return "score-low"


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Session state initialisation                                          ║
# ╚═════════════════════════════════════════════════════════════════════════╝

if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "workflow_state" not in st.session_state:
    st.session_state.workflow_state = None
if "page" not in st.session_state:
    st.session_state.page = "new_run"


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  Sidebar                                                               ║
# ╚═════════════════════════════════════════════════════════════════════════╝

with st.sidebar:
    st.image(
        "https://img.icons8.com/color/96/parse-from-clipboard.png",
        width=64,
    )
    st.title("Navigation")

    page = st.radio(
        "Go to",
        ["🚀 New Run", "📊 Dashboard", "⚙️ Settings"],
        index=["🚀 New Run", "📊 Dashboard", "⚙️ Settings"].index(
            {
                "new_run": "🚀 New Run",
                "dashboard": "📊 Dashboard",
                "settings": "⚙️ Settings",
            }.get(st.session_state.page, "🚀 New Run")
        ),
        label_visibility="collapsed",
    )
    page_map = {
        "🚀 New Run": "new_run",
        "📊 Dashboard": "dashboard",
        "⚙️ Settings": "settings",
    }
    st.session_state.page = page_map[page]

    st.divider()

    # Quick resume thread
    st.subheader("Resume a Thread")
    resume_tid = st.text_input(
        "Thread ID",
        placeholder="Paste thread_id here…",
        label_visibility="collapsed",
    )
    if st.button("Load Thread", use_container_width=True):
        if resume_tid.strip():
            st.session_state.thread_id = resume_tid.strip()
            st.session_state.page = "dashboard"
            st.rerun()
        else:
            st.warning("Please enter a valid thread ID.")

    st.divider()
    st.caption("HITL Job Screener v1.0")


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: New Run                                                         ║
# ╚═════════════════════════════════════════════════════════════════════════╝

if st.session_state.page == "new_run":
    st.markdown(
        '<div class="main-header">'
        "<h1>🎯 HITL Job Outreach Agent</h1>"
        "<p>Upload your resume, declare your strengths, and let the AI find, "
        "score, and draft outreach for your dream jobs.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([3, 2])

    with col1:
        st.subheader("📄 Resume")

        upload_tab, paste_tab = st.tabs(["Upload PDF / TXT", "Paste Text"])

        with upload_tab:
            uploaded = st.file_uploader(
                "Upload your resume",
                type=["pdf", "txt", "docx"],
                label_visibility="collapsed",
            )
            resume_text_from_upload = ""
            if uploaded is not None:
                if uploaded.type == "text/plain":
                    resume_text_from_upload = uploaded.read().decode("utf-8", errors="replace")
                else:
                    resume_text_from_upload = uploaded.read().decode("utf-8", errors="replace")
                st.success(f"Loaded **{uploaded.name}** ({len(resume_text_from_upload):,} chars)")

        with paste_tab:
            resume_text_pasted = st.text_area(
                "Paste resume content",
                height=250,
                placeholder="Paste your full resume text here…",
                label_visibility="collapsed",
            )

        # Merge
        resume_text = resume_text_from_upload or resume_text_pasted

    with col2:
        st.subheader("💪 Strengths")
        strengths_raw = st.text_area(
            "List your key strengths (one per line)",
            height=180,
            placeholder="Python\nKubernetes\nCI/CD\nFastAPI\nPostgreSQL",
            label_visibility="collapsed",
        )
        strengths = [s.strip() for s in strengths_raw.strip().splitlines() if s.strip()]

        st.subheader("🎯 Target Profile")
        city = st.text_input("City", placeholder="San Francisco")
        role = st.text_input("Role", placeholder="Senior Backend Engineer")
        dream_companies_raw = st.text_input(
            "Dream Companies (comma-separated)",
            placeholder="Stripe, Datadog, Cloudflare",
        )
        dream_companies = [
            c.strip()
            for c in dream_companies_raw.split(",")
            if c.strip()
        ]

    st.divider()

    # Preview
    with st.expander("📋 Preview payload", expanded=False):
        payload = {
            "resume_text": resume_text[:500] + ("…" if len(resume_text) > 500 else ""),
            "strengths": strengths,
            "city": city,
            "role": role,
            "dream_companies": dream_companies,
        }
        st.json(payload)

    # Submit
    can_submit = bool(resume_text and strengths and city and role)
    submit = st.button(
        "🚀 Start Screening Workflow",
        disabled=not can_submit,
        type="primary",
        use_container_width=True,
    )

    if submit:
        with st.spinner("Submitting to the API…"):
            resp = _api(
                "post",
                "/run",
                json={
                    "resume_text": resume_text,
                    "strengths": strengths,
                    "city": city,
                    "role": role,
                    "dream_companies": dream_companies,
                },
            )
            if resp and "thread_id" in resp:
                st.session_state.thread_id = resp["thread_id"]
                st.session_state.page = "dashboard"
                st.balloons()
                st.success(f"Workflow started! Thread: `{resp['thread_id']}`")
                time.sleep(1)
                st.rerun()
            elif resp:
                st.error(f"Unexpected response: {resp}")


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: Dashboard                                                       ║
# ╚═════════════════════════════════════════════════════════════════════════╝

elif st.session_state.page == "dashboard":
    st.markdown(
        '<div class="main-header">'
        "<h1>📊 Workflow Dashboard</h1>"
        "<p>Monitor your screening run, review drafts, and provide feedback.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    tid = st.session_state.thread_id
    if not tid:
        st.warning("No active thread. Start a new run or load a thread from the sidebar.")
        st.stop()

    st.markdown(f"**Thread ID:** `{tid}`")

    # Fetch state
    state = _api("get", f"/state/{tid}")
    if state is None:
        st.stop()
    st.session_state.workflow_state = state

    # ── Status Row ──────────────────────────────────────────────────
    status = state.get("overall_status", state.get("status", "unknown"))
    st.markdown(f"**Status:** {_status_chip(status)}", unsafe_allow_html=True)

    st.divider()

    # ── Score Card ──────────────────────────────────────────────────
    match_score = state.get("match_score")
    if match_score is not None:
        col_s, col_e, col_m = st.columns(3)
        with col_s:
            cls = _score_class(match_score)
            st.markdown(
                f'<div class="score-card {cls}">'
                f'<div class="number">{match_score:.0f}</div>'
                "<div>Match Score</div></div>",
                unsafe_allow_html=True,
            )
        with col_e:
            evidenced = state.get("evidenced_strengths", [])
            st.metric("Evidenced Strengths", len(evidenced))
            if evidenced:
                for item in evidenced:
                    if isinstance(item, dict):
                        st.markdown(
                            f"- **{item.get('strength', '?')}** — "
                            f"{item.get('evidence', '')} "
                            f"({item.get('score', item.get('confidence_score', '?'))}%)"
                        )
                    else:
                        st.markdown(f"- {item}")
        with col_m:
            missing = state.get("missing_strengths", [])
            st.metric("Missing Strengths", len(missing))
            for s in missing:
                st.markdown(f"- ❌ {s}")

    # ── Job Matches Table ───────────────────────────────────────────
    job_matches = state.get("job_matches", [])
    if job_matches:
        st.subheader("🔍 Job Matches")
        import pandas as pd

        df = pd.DataFrame(job_matches)
        display_cols = [c for c in ["title", "company", "url", "relevance_score", "source"] if c in df.columns]
        if display_cols:
            st.dataframe(
                df[display_cols],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "url": st.column_config.LinkColumn("Link"),
                    "relevance_score": st.column_config.ProgressColumn(
                        "Relevance", min_value=0, max_value=100, format="%d%%"
                    ),
                },
            )
        else:
            st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Company Insights ────────────────────────────────────────────
    insights = state.get("company_insights")
    if insights:
        st.subheader("🏢 Company Research")
        if isinstance(insights, dict):
            for company, info in insights.items():
                with st.expander(f"📌 {company}", expanded=False):
                    if isinstance(info, str):
                        st.write(info)
                    elif isinstance(info, dict):
                        st.write(info.get("summary", ""))
                        if info.get("tech_stack"):
                            st.markdown(
                                "**Tech Stack:** " + ", ".join(info["tech_stack"])
                            )
                        if info.get("recent_news"):
                            st.markdown("**Recent News:**")
                            for n in info["recent_news"]:
                                st.markdown(f"- {n}")

    # ── Outreach Drafts ─────────────────────────────────────────────
    draft = state.get("outreach_draft")
    if draft:
        st.subheader("✉️ Outreach Drafts")

        draft_obj = draft if isinstance(draft, dict) else {}
        email_tab, linkedin_tab = st.tabs(["📧 Email Draft", "💼 LinkedIn Note"])

        with email_tab:
            st.markdown(
                f'<div class="draft-card">'
                f'<h4>Subject: {draft_obj.get("email_subject", "N/A")}</h4>'
                f'<p>{draft_obj.get("email_body", "No draft generated yet.")}</p>'
                f"</div>",
                unsafe_allow_html=True,
            )

        with linkedin_tab:
            st.markdown(
                f'<div class="draft-card">'
                f'<h4>LinkedIn Connection Note</h4>'
                f'<p>{draft_obj.get("linkedin_note", "No draft generated yet.")}</p>'
                f"</div>",
                unsafe_allow_html=True,
            )

        # Key points used
        kp = draft_obj.get("key_points_used", [])
        if kp:
            st.markdown("**Key points highlighted:**")
            st.markdown(" · ".join(f"`{p}`" for p in kp))

    # ── Human Feedback Panel ────────────────────────────────────────
    if status in ("awaiting_human", "drafted"):
        st.divider()
        st.subheader("🧑‍⚖️ Your Decision")

        feedback_col1, feedback_col2, feedback_col3 = st.columns(3)

        with feedback_col1:
            if st.button("✅ Approve", type="primary", use_container_width=True):
                with st.spinner("Sending approval…"):
                    resp = _api(
                        "post",
                        f"/feedback/{tid}",
                        json={"human_feedback": "approve"},
                    )
                    if resp:
                        st.success("Approved! Dispatch in progress…")
                        time.sleep(1.5)
                        st.rerun()

        with feedback_col2:
            if st.button("🔄 Revise", use_container_width=True):
                with st.spinner("Requesting revision…"):
                    resp = _api(
                        "post",
                        f"/feedback/{tid}",
                        json={"human_feedback": "revise"},
                    )
                    if resp:
                        st.info("Revision requested. The LLM will re-draft.")
                        time.sleep(1.5)
                        st.rerun()

        with feedback_col3:
            if st.button("❌ Reject", use_container_width=True):
                with st.spinner("Sending rejection…"):
                    resp = _api(
                        "post",
                        f"/feedback/{tid}",
                        json={"human_feedback": "reject"},
                    )
                    if resp:
                        st.warning("Rejected. Workflow terminated.")
                        time.sleep(1.5)
                        st.rerun()

        revision_count = state.get("revision_count", 0)
        if revision_count > 0:
            st.caption(f"Revision #{revision_count}")

    # ── Error display ───────────────────────────────────────────────
    error = state.get("error")
    if error:
        st.error(f"⚠️ Workflow error: {error}")

    # ── Auto-refresh ────────────────────────────────────────────────
    if status in ("running", "initialized", "scored", "jobs_fetched", "research_done"):
        st.caption(f"⏳ Auto-refreshing every {POLL_INTERVAL_SECS}s…")
        time.sleep(POLL_INTERVAL_SECS)
        st.rerun()

    # ── Raw state (debug) ───────────────────────────────────────────
    with st.expander("🔧 Raw State (debug)", expanded=False):
        st.json(state)


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: Settings                                                        ║
# ╚═════════════════════════════════════════════════════════════════════════╝

elif st.session_state.page == "settings":
    st.markdown(
        '<div class="main-header">'
        "<h1>⚙️ Settings</h1>"
        "<p>Configure your LinkedIn Selenium cookie and other preferences.</p>"
        "</div>",
        unsafe_allow_html=True,
    )

    # ── LinkedIn Cookie ─────────────────────────────────────────────
    st.subheader("🔐 LinkedIn Selenium Cookie")

    st.info(
        "The LinkedIn Selenium cookie (`li_at`) is required for posting "
        "connection notes from your account via the headless browser.  "
        "It expires when you log out of LinkedIn or after ~90 days."
    )

    with st.expander("📖 How to get your li_at cookie", expanded=False):
        st.markdown(
            """
1. **Open LinkedIn** in Chrome and log in to your account.
2. Press **F12** (or right-click → Inspect) to open Developer Tools.
3. Go to the **Application** tab → **Cookies** → `https://www.linkedin.com`.
4. Find the cookie named **`li_at`**.
5. Copy its **Value** (it's a long string like `AQEDAS...`).
6. Paste it below in the format: `li_at=AQEDAS...`

> ⚠️ **Never share this cookie!** It grants full access to your LinkedIn session.
> The cookie expires when you log out or after ~90 days of inactivity.
            """
        )

    cookie_input = st.text_input(
        "li_at cookie value",
        type="password",
        placeholder="li_at=AQEDASx...",
    )

    if st.button("💾 Update LinkedIn Cookie", type="primary"):
        if not cookie_input.strip():
            st.warning("Please paste your li_at cookie value.")
        elif not cookie_input.strip().startswith("li_at="):
            st.error("Cookie must start with `li_at=`. Example: `li_at=AQEDAS...`")
        else:
            with st.spinner("Saving…"):
                resp = _api(
                    "post",
                    "/settings/linkedin_cookie",
                    json={"cookie": cookie_input.strip()},
                )
                if resp and resp.get("status") == "success":
                    st.success("✅ LinkedIn cookie updated successfully!")
                elif resp:
                    st.error(f"Failed: {resp}")

    st.divider()

    # ── API Server Health ───────────────────────────────────────────
    st.subheader("🏥 API Health Check")

    if st.button("Check API Server"):
        try:
            resp = requests.get(f"{API_BASE}/health", timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                st.success(
                    f"✅ API is healthy! Timestamp: {data.get('timestamp', 'N/A')}"
                )
            else:
                st.error(f"API returned status {resp.status_code}")
        except requests.ConnectionError:
            st.error("❌ Cannot reach API server at " + API_BASE)
        except Exception as exc:
            st.error(f"Error: {exc}")

    st.divider()

    # ── About ───────────────────────────────────────────────────────
    st.subheader("ℹ️ About")
    st.markdown(
        """
**HITL Job Outreach & Application Screener Agent**

- **Backend:** FastAPI + LangGraph
- **LLM:** OpenAI GPT-4o-mini
- **Storage:** Supabase (PostgreSQL + file storage)
- **Search:** Tavily API (with DuckDuckGo fallback)
- **Dispatch:** SMTP email + LinkedIn Selenium
- **Scheduler:** APScheduler (6-hour job fetch interval)

Built with ❤️ as a Human-in-the-Loop AI agent.
        """
    )
