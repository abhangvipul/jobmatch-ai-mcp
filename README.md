# JobMatch AI — Phase 2 MCP Application Workflow

JobMatch AI is a modular Streamlit app for resume parsing, public job discovery, transparent matching, and a guarded application workflow. It uses Groq (optional) for profile extraction, Tavily for public job discovery, TF-IDF matching, SQLite for an application ledger, and an MCP client for portal-authorized application providers.

## Phase 2 capabilities

- Resume upload (PDF/DOCX) and editable candidate profile
- Job search and ranking using the existing Phase 1 modules
- Eligibility gate: jobs must score **strictly greater than 85%** to enter the auto-apply workflow
- Auto-apply is **off by default** and requires explicit in-app consent
- MCP connector contract for `prepare_application` and `submit_application`
- Portal domain allowlist (`AUTO_APPLY_ALLOWED_DOMAINS`)
- SQLite application history, audit events, and idempotency keys to reduce duplicate submissions
- Safe mock MCP server for testing; it never submits a real application

## Important limitation: a generic job URL is not an application API

Tavily discovers public pages; it cannot log in to a portal or reliably complete application forms. Actual automatic submission works only when you configure an MCP server implemented for a specific portal/employer integration that is authorized to submit applications. Do not configure a generic browser scraper or bypass CAPTCHAs, access controls, or portal terms. The included mock server is for testing only and reports `simulation_only`.

## 1. Install on Windows

Use Python 3.11 recommended for compatibility.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and add your own `TAVILY_API_KEY`; `GROQ_API_KEY` is optional. Never commit `.env` or API keys to GitHub.

## 2. Configure safe test mode

In `.env`, set:

```dotenv
MCP_SERVER_COMMAND=["python","mock_mcp_server.py"]
AUTO_APPLY_ALLOWED_DOMAINS=example.com
```

This is only a wiring test. The mock server reports `simulation_only` and does not submit applications. Real discovered job URLs usually will not be on `example.com`, so the domain gate will block them unless you are deliberately testing with a matching test URL.

## 3. Configure an authorized real provider

Build or obtain an MCP server for a specific portal/employer that explicitly permits the application workflow. The server must expose tools matching these contracts:

### `prepare_application`
Input fields:
- `job`: object with `title`, `company`, `location`, `description`, `url`, `source`
- `candidate_profile`: structured candidate profile
- `resume_filename`: original filename
- `resume_base64`: base64-encoded resume bytes
- `idempotency_key`: stable key for this job/profile pair

Return a JSON object such as:

```json
{"ready": true, "application_payload": {"portal_specific_fields": "..."}, "message": "All required fields validated"}
```

Return `ready: false` whenever required facts are missing, the role is ineligible, consent is missing, or a question cannot be answered truthfully. Never invent answers.

### `submit_application`
Input fields:
- `job`: same job summary
- `application_payload`: the payload returned by `prepare_application`
- `idempotency_key`: same stable key

Return a JSON object such as:

```json
{"submitted": true, "status": "submitted", "application_id": "provider-id", "message": "Provider confirmed submission"}
```

Only return `submitted: true` when the portal confirms successful submission. Make the provider itself idempotent using the supplied key. Follow the portal's API terms, user consent requirements, rate limits, and data-retention rules.

Set the provider command as a JSON array in `.env`, for example:

```dotenv
MCP_SERVER_COMMAND=["python","path/to/your_authorized_portal_server.py"]
AUTO_APPLY_ALLOWED_DOMAINS=careers.example.com,employer.example.org
```

Use only domains for which that provider is authorized. Domain allowlisting is an additional safety gate, not proof of authorization by itself.

## 4. Run

```powershell
streamlit run app.py
```

Upload a resume, review and correct the extracted profile, then click **Search and rank jobs**. To activate Phase 2, check both auto-apply and consent in the sidebar. Eligible results are those with a score **above 85**, not equal to 85. If a provider cannot validate every required field, the application must be held for review.

## 5. How matching scores should be interpreted

The existing match score is a ranking heuristic, not an independently validated probability. It is not evidence of 85% accuracy. Before unattended submission, evaluate false positives on a labeled set of real job descriptions, especially hard eligibility conditions such as location, work authorization, seniority, required certifications, and mandatory skills. Consider requiring hard eligibility checks in the provider before submission.

## 6. Data, deployment, and security

- Resume bytes stay in the active Streamlit session until sent to the MCP provider after the user enables auto-apply and confirms consent.
- The MCP provider receives the resume as base64. Treat that as personal data and use only a trusted provider.
- SQLite records job URL, score, status, timestamps, provider message, and a one-way profile fingerprint; it does not store the raw resume text. The SQLite file is local and may not persist across Streamlit Community Cloud restarts.
- Do not deploy as a public multi-user service without authentication, per-user storage isolation, encrypted persistent storage, secret management, retention/deletion controls, and a privacy review.
- Never commit `.env`, the SQLite database, resume files, or secrets to GitHub.

## Project layout

```text
jobmatch_ai/
├── app.py
├── mock_mcp_server.py
├── requirements.txt
├── .env.example
├── README.md
└── job_agent/
    ├── __init__.py
    ├── resume_parser.py
    ├── profile_extractor.py
    ├── job_sources.py
    ├── matcher.py
    ├── application_store.py
    └── mcp_applications.py
```
