import streamlit as st
from dotenv import load_dotenv
load_dotenv()
from job_agent.resume_parser import extract_resume_text
from job_agent.profile_extractor import build_candidate_profile
from job_agent.job_sources import search_jobs
from job_agent.matcher import rank_jobs
from job_agent.application_store import (
    init_db, profile_fingerprint, make_idempotency_key, has_attempt,
    record_attempt, update_status, recent_applications,
)
from job_agent.mcp_applications import mcp_configured, submit_job_application

init_db()

st.set_page_config(page_title="JobMatch AI", page_icon="🎯", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1250px;}
.hero {padding: 1.5rem 1.7rem; border-radius: 18px; background: linear-gradient(120deg,#102a43,#176b87); color: white; margin-bottom: 1.2rem;}
.hero h1 {margin:0; font-size:2.15rem;}
.hero p {margin:.4rem 0 0 0; color:#e6f4f7;}
.score {font-size:1.7rem; font-weight:750;}
</style>
<div class="hero"><h1>🎯 JobMatch AI</h1><p>Discover Indian job opportunities matched to your resume.</p></div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("Search preferences")
    target_roles = st.text_input("Target job titles", "Data Analyst, Business Analyst, Power BI Developer")
    location = st.text_input("Preferred location", "India")
    work_mode = st.selectbox("Work mode", ["Any", "Remote", "Hybrid", "On-site"])
    experience = st.selectbox("Experience level", ["Any", "Entry level", "1–3 years", "3–5 years", "5+ years"])
    max_results = st.slider("Maximum results per search", 5, 20, 10)
    st.divider()
    st.subheader("Phase 2 · Auto-apply")
    auto_apply_enabled = st.checkbox("Enable automatic applications for matches above 85%", value=False)
    application_consent = st.checkbox(
        "I authorize JobMatch AI to send my resume/profile to the configured application provider and submit eligible applications.",
        value=False,
        disabled=not auto_apply_enabled,
    )
    if auto_apply_enabled:
        if not application_consent:
            st.warning("Auto-apply is paused until you confirm consent.")
        elif not mcp_configured():
            st.warning("No MCP application server is configured. Matching will work, but no applications can be submitted.")
        else:
            st.success("Auto-apply is enabled. Only domains in AUTO_APPLY_ALLOWED_DOMAINS can be submitted.")
    st.caption("Job discovery uses public search results. Real submission requires an authorized portal MCP server; unsupported portals will not be automated.")

st.subheader("1. Upload your resume")
upload = st.file_uploader("Choose a PDF or DOCX resume", type=["pdf", "docx"])

if upload:
    try:
        resume_text = extract_resume_text(upload.getvalue(), upload.name)
        if len(resume_text.strip()) < 80:
            st.error("Very little text was extracted. If this is a scanned PDF, run OCR on it and upload a searchable PDF.")
            st.stop()
        with st.expander("Preview extracted resume text"):
            st.text(resume_text[:6000])
        if st.button("Analyze resume and find matching jobs", type="primary", use_container_width=True):
            with st.spinner("Analyzing your resume..."):
                profile = build_candidate_profile(resume_text)
            st.session_state["candidate_profile"] = profile
            st.session_state["resume_text"] = resume_text
            st.session_state["resume_bytes"] = upload.getvalue()
            st.session_state["resume_filename"] = upload.name
            st.session_state["search_complete"] = False
            st.success("Resume analysis complete.")

    except Exception as exc:
        st.error(f"Could not read this resume: {exc}")

profile = st.session_state.get("candidate_profile")
if profile:
    st.subheader("2. Review your extracted profile")
    c1, c2 = st.columns(2)
    with c1:
        roles_text = st.text_input("Detected / preferred roles", value=target_roles, key="roles_override")
        skills_text = st.text_area("Skills (edit if needed)", value=", ".join(profile.get("skills", [])), height=110)
    with c2:
        years = st.number_input("Relevant experience (years, approximate)", min_value=0.0, max_value=50.0, value=float(profile.get("years_experience", 0) or 0), step=0.5)
        summary = st.text_area("Profile summary (edit if needed)", value=profile.get("summary", ""), height=110)

    if st.button("Search and rank jobs", type="primary", use_container_width=True):
        candidate = dict(profile)
        candidate["skills"] = [s.strip() for s in skills_text.replace("\n", ",").split(",") if s.strip()]
        candidate["years_experience"] = years
        candidate["summary"] = summary
        candidate["target_roles"] = [r.strip() for r in roles_text.split(",") if r.strip()]
        candidate["location"] = location
        candidate["work_mode"] = work_mode
        candidate["experience_level"] = experience
        with st.spinner("Searching public job listings and calculating match scores..."):
            jobs, warnings = search_jobs(candidate, max_results=max_results)
            ranked = rank_jobs(candidate, jobs)
        st.session_state["ranked_jobs"] = ranked
        st.session_state["search_warnings"] = warnings
        st.session_state["search_complete"] = True

        # Submission is opt-in, requires explicit consent, and is blocked unless an
        # authorized MCP provider plus a domain allowlist are configured.
        if auto_apply_enabled and application_consent:
            eligible = [j for j in ranked if float(j.get("match_score", 0)) > 85]
            st.session_state["auto_apply_results"] = []
            if not eligible:
                st.session_state["auto_apply_results"].append({"title": "No eligible jobs", "status": "not_eligible", "message": "No result scored strictly above 85%."})
            elif not mcp_configured():
                st.session_state["auto_apply_results"].append({"title": "Auto-apply unavailable", "status": "blocked_no_mcp", "message": "Configure an authorized MCP server before submissions can occur."})
            else:
                fingerprint = profile_fingerprint(candidate)
                resume_bytes = st.session_state.get("resume_bytes", b"")
                resume_filename = st.session_state.get("resume_filename", "resume.pdf")
                for job in eligible:
                    title = job.get("title") or "Untitled job"
                    url = job.get("url", "")
                    if not url:
                        st.session_state["auto_apply_results"].append({"title": title, "status": "blocked_no_url", "message": "Job has no valid listing URL."})
                        continue
                    key = make_idempotency_key(url, fingerprint)
                    # Reserve the idempotency key before calling any external provider.
                    reserved = record_attempt(url, fingerprint, key, float(job.get("match_score", 0)), "processing", "Submission workflow started.")
                    if not reserved:
                        st.session_state["auto_apply_results"].append({"title": title, "status": "skipped_duplicate", "message": "An attempt for this job/profile already exists."})
                        continue
                    try:
                        outcome = submit_job_application(
                            job=job,
                            candidate_profile=candidate,
                            resume_bytes=resume_bytes,
                            resume_filename=resume_filename,
                            idempotency_key=key,
                        )
                        status = str(outcome.get("status", "unknown"))
                        message = str(outcome.get("message", ""))
                        update_status(key, status, message)
                        st.session_state["auto_apply_results"].append({"title": title, "status": status, "message": message, "url": url})
                    except Exception as exc:
                        # Avoid logging resume contents, secrets, or provider payloads.
                        status = "error"
                        message = f"Application connector failed: {type(exc).__name__}. No success is assumed."
                        update_status(key, status, message)
                        st.session_state["auto_apply_results"].append({"title": title, "status": status, "message": message, "url": url})

if st.session_state.get("search_complete"):
    warnings = st.session_state.get("search_warnings", [])
    for warning in warnings:
        st.warning(warning)
    ranked = st.session_state.get("ranked_jobs", [])
    st.subheader(f"3. Matching jobs ({len(ranked)} found)")
    if not ranked:
        st.info("No jobs were returned. Check your Tavily API key, broaden the role/location, or try again later.")
    else:
        st.caption("Match scores are ranking estimates, not a guarantee of hiring eligibility or an independently validated accuracy percentage.")
        for idx, job in enumerate(ranked, 1):
            score = int(round(job.get("match_score", 0)))
            with st.container(border=True):
                left, right = st.columns([5, 1])
                with left:
                    st.markdown(f"### {idx}. {job.get('title') or 'Job title not found'}")
                    st.write(f"**Company:** {job.get('company') or 'Not identified'}  ·  **Location:** {job.get('location') or 'Not specified'}")
                    if job.get("source"):
                        st.caption(f"Source: {job['source']}")
                with right:
                    st.markdown(f"<div class='score'>{score}%</div><div>match score</div>", unsafe_allow_html=True)
                st.write(job.get("description", "No description available.")[:1000])
                matched = job.get("matched_skills", [])
                missing = job.get("missing_skills", [])
                if matched:
                    st.markdown("**Matching skills:** " + ", ".join(matched[:15]))
                if missing:
                    st.markdown("**Potential skill gaps:** " + ", ".join(missing[:10]))
                if job.get("url"):
                    st.link_button("Open original job listing", job["url"])


    auto_results = st.session_state.get("auto_apply_results", [])
    if auto_results:
        st.subheader("4. Auto-apply results")
        st.caption("Only an explicit confirmation from the configured MCP provider is treated as submitted. Review every status below.")
        for result in auto_results:
            status = result.get("status", "unknown")
            if status in {"submitted", "success", "applied"}:
                st.success(f"{result.get('title', 'Job')}: {status} — {result.get('message', '')}")
            elif status in {"needs_review", "blocked_domain", "blocked_no_mcp", "blocked_no_url", "skipped_duplicate", "not_eligible"}:
                st.warning(f"{result.get('title', 'Job')}: {status} — {result.get('message', '')}")
            else:
                st.error(f"{result.get('title', 'Job')}: {status} — {result.get('message', '')}")
            if result.get("url"):
                st.caption(result["url"])

st.divider()
st.subheader("Application history")
history = recent_applications(50)
if history:
    st.dataframe(history, use_container_width=True, hide_index=True)
else:
    st.caption("No application attempts recorded yet.")

st.caption("Privacy note: resume bytes are kept in the active Streamlit session and are sent to the configured MCP provider only after you enable auto-apply and confirm consent. Application history stores job URLs, scores, statuses, timestamps, and a one-way profile fingerprint—not the resume text. Use authentication and a persistent protected database before exposing this app to multiple users.")
