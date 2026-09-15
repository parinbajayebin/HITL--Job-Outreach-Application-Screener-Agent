"""HITL Job Outreach & Application Screener — Modernized Streamlit UI.
"""

from __future__ import annotations

import io
import json
import time
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------
API_BASE = "http://127.0.0.1:8000"
POLL_INTERVAL_SECS = 2

INDIAN_LOCATIONS = [
    "Bengaluru, Karnataka (560001)",
    "Hyderabad, Telangana (500081)",
    "Pune, Maharashtra (411057)",
    "Mumbai, Maharashtra (400051)",
    "Delhi NCR / Gurgaon, Haryana (122002)",
    "Noida, Uttar Pradesh (201301)",
    "Chennai, Tamil Nadu (600096)",
    "Ahmedabad, Gujarat (380015)",
    "Kolkata, West Bengal (700091)",
    "Remote (India)",
    "Other / Custom Location",
]

PREDEFINED_ROLES = [
    "Full Stack Developer",
    "Senior Backend Engineer (Python / FastAPI)",
    "Frontend Engineer (React / Next.js)",
    "AI / ML Engineer",
    "Data Scientist / Analytics Engineer",
    "DevOps / SRE / Cloud Engineer",
    "Product Manager / Tech Lead",
    "Mobile Engineer (Flutter / React Native)",
    "Add Custom Role...",
]

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="HITL Job Screener & Outreach",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Modern Theme CSS (Glassmorphism, High Contrast, Dark/Light Friendly)
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* Global styles */
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    }

    /* Gradient Hero Header */
    .hero-banner {
        background: linear-gradient(135deg, #1e1b4b 0%, #312e81 40%, #4338ca 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        color: #ffffff;
        box-shadow: 0 10px 25px -5px rgba(49, 46, 129, 0.3);
        margin-bottom: 2rem;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .hero-banner h1 { margin: 0; font-size: 2.2rem; font-weight: 800; letter-spacing: -0.02em; color: #ffffff !important; }
    .hero-banner p  { margin: 0.5rem 0 0 0; color: #c7d2fe; font-size: 1.05rem; }

    /* Source Badge in Job Results */
    .source-badge {
        display: inline-flex;
        align-items: center;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 700;
        text-transform: uppercase;
        margin-right: 8px;
    }
    .source-linkedin    { background: #0077b5; color: white !important; }
    .source-naukri      { background: #004c8f; color: white !important; }
    .source-yc          { background: #ff6600; color: white !important; }
    .source-indeed      { background: #2164f3; color: white !important; }
    .source-internshala { background: #00a8ea; color: white !important; }
    .source-company     { background: #475569; color: white !important; }

    /* Modern Outreach Draft Box (HIGH CONTRAST) */
    .draft-box {
        background: #0f172a;
        color: #f8fafc;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 1.5rem;
        margin-top: 1rem;
        box-shadow: 0 4px 15px rgba(0,0,0,0.15);
    }
    .draft-box h4 {
        color: #38bdf8 !important;
        margin-top: 0;
        font-size: 1.15rem;
        border-bottom: 1px solid #334155;
        padding-bottom: 0.5rem;
    }
    .draft-box p, .draft-box div {
        color: #e2e8f0 !important;
        line-height: 1.65;
        white-space: pre-wrap;
        font-size: 0.98rem;
    }

    /* Company Research Modern Card */
    .company-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 1.4rem;
        margin-bottom: 1.2rem;
        color: #1e293b;
    }
    .company-card h3 {
        margin: 0 0 0.5rem 0;
        color: #0f172a;
        font-weight: 700;
    }
    .link-pill {
        display: inline-block;
        padding: 5px 12px;
        background: #e0e7ff;
        color: #3730a3 !important;
        border-radius: 20px;
        font-size: 0.85rem;
        font-weight: 600;
        text-decoration: none;
        margin-right: 8px;
        margin-top: 6px;
    }
    .link-pill:hover {
        background: #c7d2fe;
    }

    /* Score Card */
    .metric-card {
        background: #f8fafc;
        border: 2px solid #e2e8f0;
        border-radius: 14px;
        text-align: center;
        padding: 1.2rem;
    }
    .metric-card .val {
        font-size: 2.8rem;
        font-weight: 800;
        line-height: 1;
        color: #10b981;
    }
    .metric-card .lbl {
        color: #64748b;
        font-size: 0.9rem;
        font-weight: 600;
        margin-top: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _extract_text_from_upload(uploaded_file) -> str:
    if uploaded_file is None:
        return ""
    try:
        raw_bytes = uploaded_file.read()
        uploaded_file.seek(0)
        if uploaded_file.type == "text/plain" or uploaded_file.name.endswith(".txt"):
            return raw_bytes.decode("utf-8", errors="replace")
        if uploaded_file.name.endswith(".pdf"):
            try:
                from pdfminer.high_level import extract_text
                text = extract_text(io.BytesIO(raw_bytes))
                if text and text.strip():
                    return text.strip()
            except Exception:
                pass
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(raw_bytes))
                pages = [page.extract_text() or "" for page in reader.pages]
                return "\n".join(pages).strip()
            except Exception:
                pass
        return raw_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        st.warning(f"File extract notice: {e}")
        return ""


def _api(method: str, path: str, **kwargs) -> Optional[Dict[str, Any]]:
    url = f"{API_BASE}{path}"
    try:
        resp = getattr(requests, method)(url, timeout=30, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        st.error("⚠️ Cannot reach backend on port 8000.")
        return None
    except requests.HTTPError as exc:
        st.error(f"API error: {exc.response.status_code}")
        return None
    except Exception as exc:
        st.error(f"Error: {exc}")
        return None


def _get_badge(source: str) -> str:
    s = (source or "").lower()
    if "linkedin" in s:
        return '<span class="source-badge source-linkedin">LinkedIn Jobs</span>'
    if "naukri" in s:
        return '<span class="source-badge source-naukri">Naukri.com</span>'
    if "y combinator" in s or "yc" in s:
        return '<span class="source-badge source-yc">Y Combinator</span>'
    if "indeed" in s:
        return '<span class="source-badge source-indeed">Indeed India</span>'
    if "internshala" in s:
        return '<span class="source-badge source-internshala">Internshala</span>'
    return '<span class="source-badge source-company">Company Portal</span>'


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "page" not in st.session_state:
    st.session_state.page = "new_run"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/briefcase.png", width=60)
    st.title("HITL Job Screener")
    st.caption("AI-Powered Agent for Indian & Global Markets")

    choice = st.radio(
        "Navigation",
        ["🚀 New Search", "📊 Dashboard", "⚙️ Settings"],
        index=["new_run", "dashboard", "settings"].index(st.session_state.page),
        label_visibility="collapsed",
    )
    st.session_state.page = {"🚀 New Search": "new_run", "📊 Dashboard": "dashboard", "⚙️ Settings": "settings"}[choice]

    st.divider()

    st.markdown("##### 🔄 Resume Run")
    resume_id = st.text_input("Thread ID", placeholder="Paste thread id...", label_visibility="collapsed")
    if st.button("Load Workflow", use_container_width=True):
        if resume_id.strip():
            st.session_state.thread_id = resume_id.strip()
            st.session_state.page = "dashboard"
            st.rerun()

    st.divider()
    st.markdown(
        """
        **Connected Sources:**
        - 🌐 LinkedIn Jobs
        - 🇮🇳 Naukri.com
        - 🚀 Y Combinator
        - 📄 Indeed India
        """
    )


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: New Run                                                         ║
# ╚═════════════════════════════════════════════════════════════════════════╝
if st.session_state.page == "new_run":
    st.markdown(
        """
        <div class="hero-banner">
            <h1>🎯 Intelligent Job Screener & Outreach Agent</h1>
            <p>Upload your resume, choose your Indian hub or global location, paste target company URLs, and let AI discover openings & draft high-converting outreach.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([1.1, 1], gap="large")

    with col1:
        st.subheader("1️⃣ Upload Candidate Resume")
        tab_upload, tab_paste = st.tabs(["📁 PDF / Word File", "✍️ Paste Plain Text"])

        with tab_upload:
            uploaded = st.file_uploader("Upload resume file", type=["pdf", "txt"], label_visibility="collapsed")
            parsed_text = ""
            if uploaded:
                parsed_text = _extract_text_from_upload(uploaded)
                st.success(f"✅ Extracted **{uploaded.name}** ({len(parsed_text):,} characters parsed)")

        with tab_paste:
            pasted_text = st.text_area("Paste resume text", height=230, placeholder="Paste your resume here...", label_visibility="collapsed")

        resume_content = parsed_text or pasted_text

        st.subheader("2️⃣ Key Strengths to Validate")
        strengths_str = st.text_area(
            "Enter key technical strengths (one per line)",
            value="Python\nFastAPI\nPostgreSQL\nDocker & Kubernetes\nSystem Design & Scalability",
            height=130,
        )
        strengths = [s.strip() for s in strengths_str.splitlines() if s.strip()]

    with col2:
        st.subheader("3️⃣ Target Role & India / Global Hub")

        # Role Selector with Custom Option
        role_choice = st.selectbox("Select Target Role", PREDEFINED_ROLES)
        if role_choice == "Add Custom Role...":
            role = st.text_input("Enter Custom Job Title", placeholder="e.g. LLM Infrastructure Engineer")
        else:
            role = role_choice

        # Location Selector with Pincode Support
        loc_choice = st.selectbox("Preferred City & Pincode", INDIAN_LOCATIONS)
        if loc_choice == "Other / Custom Location":
            city = st.text_input("Enter City, State, or Pincode", placeholder="e.g. Chandigarh (160017) or Singapore")
        else:
            city = loc_choice

        st.subheader("4️⃣ Target Companies (Names or Direct URLs)")
        st.info("💡 You can enter company names **or paste their exact website / careers URLs** (e.g. `https://zerodha.com` or `https://stripe.com`) to prevent ambiguity!")
        
        company_input = st.text_area(
            "Companies / URLs (comma-separated or one per line)",
            value="Zerodha, Swiggy, https://razorpay.com",
            height=90,
        )
        companies = [c.strip() for c in company_input.replace("\n", ",").split(",") if c.strip()]

    st.divider()

    can_submit = bool(resume_content.strip() and strengths and role.strip() and city.strip())
    if st.button("🚀 Start Intelligent Screener & Discover Jobs", disabled=not can_submit, type="primary", use_container_width=True):
        with st.spinner("Initializing LangGraph state and spinning up multi-board search..."):
            resp = _api(
                "post",
                "/run",
                json={
                    "resume_text": resume_content,
                    "strengths": strengths,
                    "city": city,
                    "role": role,
                    "dream_companies": companies,
                },
            )
            if resp and "thread_id" in resp:
                st.session_state.thread_id = resp["thread_id"]
                st.session_state.page = "dashboard"
                st.toast("Workflow initialized! Searching LinkedIn, Naukri, and Y Combinator...", icon="🔎")
                time.sleep(1)
                st.rerun()


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: Dashboard                                                       ║
# ╚═════════════════════════════════════════════════════════════════════════╝
elif st.session_state.page == "dashboard":
    tid = st.session_state.thread_id
    if not tid:
        st.warning("No active screening thread found. Start a new search from the sidebar.")
        st.stop()

    state = _api("get", f"/state/{tid}")
    if state is None:
        st.stop()

    status = state.get("status", "running")

    # Header Bar
    st.markdown(
        f"""
        <div class="hero-banner" style="padding: 1.5rem 2rem; margin-bottom: 1.5rem;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h2 style="margin: 0; color: white;">Candidate Review & Match Results</h2>
                    <p style="margin: 0.2rem 0 0 0; color: #c7d2fe;">Targeting: <strong>{state.get('role', 'Role')}</strong> in <strong>{state.get('city', 'Location')}</strong></p>
                </div>
                <div style="text-align: right;">
                    <span style="background: rgba(255,255,255,0.2); padding: 6px 14px; border-radius: 20px; font-weight: 700; color: white; font-size: 0.9rem;">
                        Status: {status.upper()}
                    </span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 1. Match Score & Evidence Section
    match_score = state.get("match_score", 84.0)
    col_score, col_ev, col_miss = st.columns([1, 1.6, 1.4], gap="medium")

    with col_score:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="val">{match_score:.0f}%</div>
                <div class="lbl">Resume & Strength Fit Score</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_ev:
        st.markdown("##### ✅ Evidenced Strengths (From Resume)")
        evidenced = state.get("evidenced_strengths", [])
        if evidenced:
            for item in evidenced:
                if isinstance(item, dict):
                    st.markdown(f"- **{item.get('strength', 'Skill')}:** {item.get('evidence', '')}")
                else:
                    st.markdown(f"- {item}")
        else:
            st.write("Evaluating resume evidence...")

    with col_miss:
        st.markdown("##### ⚠️ Missing or Unsubstantiated Areas")
        missing = state.get("missing_strengths", [])
        if missing:
            for m in missing:
                st.markdown(f"- ❌ *{m}* (not evidenced in resume body)")
        else:
            st.success("All declared strengths substantiated in resume.")

    st.divider()

    # 2. Multi-Board Discovered Job Matches (LinkedIn, Naukri, YC, Indeed)
    st.subheader("🎯 Discovered Job Openings (Parsed from LinkedIn, Naukri, YC, Indeed)")
    st.caption("The agent automatically crawls top job boards for your specific role and location.")

    jobs = state.get("job_matches", [])
    if jobs:
        for idx, job in enumerate(jobs):
            source_badge = _get_badge(job.get("source", ""))
            with st.container():
                st.markdown(
                    f"""
                    <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 1rem 1.4rem; margin-bottom: 0.8rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                {source_badge}
                                <strong style="font-size: 1.1rem; color: #0f172a;">{job.get('title', 'Job Opening')}</strong>
                                <span style="color: #64748b; margin-left: 10px;">@ <strong>{job.get('company', 'Enterprise')}</strong></span>
                            </div>
                            <div>
                                <a href="{job.get('url', '#')}" target="_blank" style="background: #2563eb; color: white !important; padding: 6px 14px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600;">View Job Posting ↗</a>
                            </div>
                        </div>
                        <p style="margin: 0.6rem 0 0 0; color: #475569; font-size: 0.92rem;">{job.get('snippet', '')}</p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.info("🔎 Agent is scanning job boards (LinkedIn, Naukri, Y Combinator)...")

    st.divider()

    # 3. Company Intelligence (Structured & Cleanly Aligned)
    st.subheader("🏢 Deep Company Research & Verified Profiles")
    insights = state.get("company_insights", {})
    if insights:
        for cname, cinfo in insights.items():
            if isinstance(cinfo, dict):
                comp_display = cinfo.get("name", cname)
                web = cinfo.get("website", "#")
                li = cinfo.get("linkedin", "#")
                careers = cinfo.get("careers_page", "#")
                email = cinfo.get("contact_email", "careers@company.com")
                tech = ", ".join(cinfo.get("tech_stack", []))

                st.markdown(
                    f"""
                    <div class="company-card">
                        <h3>{comp_display}</h3>
                        <p style="color: #334155; margin-bottom: 0.8rem; font-size: 0.96rem;">{cinfo.get('summary', '')}</p>
                        <div style="margin-bottom: 0.8rem;">
                            <strong>Tech Stack:</strong> <span style="color: #4338ca; font-weight: 600;">{tech}</span>
                        </div>
                        <div>
                            <a class="link-pill" href="{web}" target="_blank">🌐 Official Website</a>
                            <a class="link-pill" href="{careers}" target="_blank">💼 Careers & Openings</a>
                            <a class="link-pill" href="{li}" target="_blank">🔗 LinkedIn Company Page</a>
                            <span class="link-pill" style="background: #f1f5f9; color: #475569 !important;">✉️ Talent Contact: {email}</span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.info("Gathering company intelligence, official links, and tech stacks...")

    st.divider()

    # 4. High-Contrast Outreach Drafts (Fixed dark background + white readable fonts)
    st.subheader("✉️ High-Impact Outreach Copy (Reviewed by Human)")
    draft = state.get("outreach_draft")
    if draft:
        draft_dict = draft if isinstance(draft, dict) else {}
        t_email, t_li = st.tabs(["📧 Cold Outreach Email", "💼 LinkedIn Connection Note"])

        with t_email:
            st.markdown(
                f"""
                <div class="draft-box">
                    <h4>Subject: {draft_dict.get('email_subject', 'Application & Discussion')}</h4>
                    <div>{draft_dict.get('email_body', '')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with t_li:
            st.markdown(
                f"""
                <div class="draft-box">
                    <h4>LinkedIn Connection Note (&le; 300 Characters)</h4>
                    <div>{draft_dict.get('linkedin_note', '')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.caption("Highlighted points used: " + ", ".join(draft_dict.get("key_points_used", [])))

        # 5. Human-In-The-Loop Decision
        st.subheader("🧑‍⚖️ Your Decision")
        c_app, c_rev, c_rej = st.columns(3)
        with c_app:
            if st.button("✅ Approve & Dispatch Note", type="primary", use_container_width=True):
                resp = _api("post", f"/feedback/{tid}", json={"human_feedback": "approve"})
                if resp:
                    st.success("Approved! Dispatched to recipient queue.")
                    time.sleep(1)
                    st.rerun()
        with c_rev:
            if st.button("🔄 Request Revision", use_container_width=True):
                resp = _api("post", f"/feedback/{tid}", json={"human_feedback": "revise"})
                if resp:
                    st.info("Revision requested. LLM is generating updated copy...")
                    time.sleep(1)
                    st.rerun()
        with c_rej:
            if st.button("❌ Reject Outreach", use_container_width=True):
                resp = _api("post", f"/feedback/{tid}", json={"human_feedback": "reject"})
                if resp:
                    st.warning("Rejected. Workflow terminated.")
                    time.sleep(1)
                    st.rerun()
    else:
        st.caption("Agent is generating personalized outreach copy...")

    # Auto refresh if not done
    if status in ("running", "initialized", "scored") and not draft:
        time.sleep(POLL_INTERVAL_SECS)
        st.rerun()


# ╔═════════════════════════════════════════════════════════════════════════╗
# ║  PAGE: Settings                                                        ║
# ╚═════════════════════════════════════════════════════════════════════════╝
elif st.session_state.page == "settings":
    st.markdown("<h2>⚙️ Settings & LinkedIn Dispatch Configuration</h2>", unsafe_allow_html=True)

    st.subheader("🔐 LinkedIn Session Cookie (`li_at`)")
    st.info("Required for dispatching connection notes on your behalf via headless browser.")
    cookie_val = st.text_input("Cookie Value", type="password", placeholder="li_at=AQEDAS...")
    if st.button("Save LinkedIn Cookie", type="primary"):
        if cookie_val.startswith("li_at="):
            res = _api("post", "/settings/linkedin_cookie", json={"cookie": cookie_val.strip()})
            if res:
                st.success("LinkedIn cookie updated!")
        else:
            st.error("Cookie must start with li_at=")

    st.divider()
    st.subheader("🏥 Backend Status Check")
    if st.button("Test Health Endpoint"):
        try:
            r = requests.get(f"{API_BASE}/health", timeout=5)
            st.success(f"Backend healthy! Response: {r.json()}")
        except Exception as e:
            st.error(f"Backend unreachable: {e}")
