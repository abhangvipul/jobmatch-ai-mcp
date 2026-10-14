import os
import re
from urllib.parse import urlparse


def _config_value(name: str, default: str = "") -> str:
    """Read local .env values or Streamlit Community Cloud secrets."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st
        return str(st.secrets.get(name, default)).strip()
    except Exception:
        return default


def _get_tavily_client():
    key = _config_value("TAVILY_API_KEY")
    if not key:
        return None
    from tavily import TavilyClient
    return TavilyClient(api_key=key)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _infer_title(title: str, content: str) -> str:
    title = _clean_text(title)
    for sep in [" | ", " - ", " — ", " :: "]:
        if sep in title:
            parts = [p.strip() for p in title.split(sep) if p.strip()]
            if parts:
                # Search engines commonly append a portal or company name.
                title = parts[0]
                break
    return title[:180] or "Job listing (title not identified)"


def _infer_company(title: str, url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    known = {"linkedin.com": "LinkedIn", "naukri.com": "Naukri", "indeed.com": "Indeed", "foundit.in": "Foundit", "wellfound.com": "Wellfound", "cutshort.io": "Cutshort"}
    for domain, name in known.items():
        if host.endswith(domain):
            return name + " (listing source)"
    return "Not identified"


def search_jobs(candidate: dict, max_results: int = 10):
    """Discover publicly indexed job pages with Tavily. Designed to be replaced/extended by API connectors."""
    warnings = []
    client = _get_tavily_client()
    if client is None:
        return [], ["TAVILY_API_KEY is not configured. Add it to your .env file and restart the app."]

    roles = candidate.get("target_roles") or ["jobs matching candidate skills"]
    skills = candidate.get("skills", [])[:8]
    location = candidate.get("location", "India") or "India"
    work_mode = candidate.get("work_mode", "Any")
    experience_level = candidate.get("experience_level", "Any")
    role_queries = roles[:4]
    results = []
    seen_urls = set()

    for role in role_queries:
        query = f'India {role} job opening {" ".join(skills[:5])} {location}'
        if work_mode != "Any":
            query += f" {work_mode}"
        if experience_level != "Any":
            query += f" {experience_level}"
        try:
            response = client.search(query=query, topic="general", search_depth="advanced", max_results=max(5, min(10, max_results)), include_answer=False)
            for item in response.get("results", []):
                url = (item.get("url") or "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = _infer_title(item.get("title", ""), item.get("content", ""))
                content = _clean_text(item.get("content", ""))
                results.append({
                    "title": title,
                    "company": _infer_company(title, url),
                    "location": location,
                    "description": content,
                    "url": url,
                    "source": urlparse(url).netloc.removeprefix("www."),
                    "published_date": item.get("published_date") or "",
                })
                if len(results) >= max_results * 2:
                    break
        except Exception as exc:
            warnings.append(f"Search failed for '{role}': {type(exc).__name__}. Check the Tavily quota/API key and retry.")
        if len(results) >= max_results * 2:
            break

    if not results and not warnings:
        warnings.append("No public job pages were returned for these queries. Try broader job titles or a different location.")
    return results[:max_results], warnings
