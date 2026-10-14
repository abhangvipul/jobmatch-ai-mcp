import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _normalise(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _skill_in_text(skill: str, text: str) -> bool:
    skill = _normalise(skill)
    text = _normalise(text)
    if not skill:
        return False
    # Word boundaries avoid treating e.g. 'R' as a match inside unrelated words.
    pattern = r"(?<![a-z0-9+#])" + re.escape(skill) + r"(?![a-z0-9+#])"
    return re.search(pattern, text) is not None


def _semantic_scores(candidate_text: str, job_texts: list[str]) -> list[float]:
    if not job_texts:
        return []
    try:
        corpus = [candidate_text] + job_texts
        matrix = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=12000).fit_transform(corpus)
        return [float(x) for x in cosine_similarity(matrix[0:1], matrix[1:]).flatten()]
    except ValueError:
        return [0.0] * len(job_texts)


def rank_jobs(candidate: dict, jobs: list[dict]) -> list[dict]:
    skills = [str(s).strip() for s in candidate.get("skills", []) if str(s).strip()]
    profile_text = " ".join([
        candidate.get("summary", ""), " ".join(skills),
        " ".join(candidate.get("target_roles", [])),
        " ".join(str(x) for x in candidate.get("education", [])),
        " ".join(str(x) for x in candidate.get("projects", [])),
    ])
    job_texts = [" ".join([j.get("title", ""), j.get("description", "")]) for j in jobs]
    semantic = _semantic_scores(profile_text, job_texts)
    max_sem = max(semantic, default=0.0)
    ranked = []
    for i, original in enumerate(jobs):
        job = dict(original)
        text = job_texts[i]
        matched = [s for s in skills if _skill_in_text(s, text)]
        missing = [s for s in skills if not _skill_in_text(s, text)]
        skill_score = len(matched) / max(1, len(skills))
        # Normalize TF-IDF cosine similarity relative to results in this search batch.
        sem_score = (semantic[i] / max_sem) if max_sem > 0 else 0.0
        title = _normalise(job.get("title", ""))
        roles = [_normalise(r) for r in candidate.get("target_roles", [])]
        title_score = 1.0 if any(role and (role in title or title in role) for role in roles) else (0.55 if any(any(token in title for token in role.split() if len(token) > 3) for role in roles) else 0.25)
        score = 100 * (0.45 * skill_score + 0.35 * sem_score + 0.20 * title_score)
        job["matched_skills"] = matched
        job["missing_skills"] = missing
        job["skill_score"] = round(skill_score * 100, 1)
        job["semantic_score"] = round(sem_score * 100, 1)
        job["title_score"] = round(title_score * 100, 1)
        job["match_score"] = round(min(100, max(0, score)), 1)
        ranked.append(job)
    return sorted(ranked, key=lambda x: x.get("match_score", 0), reverse=True)
