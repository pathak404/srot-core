import os
import time
from datetime import datetime, timezone
import streamlit as st
import requests
import json

API_BASE = os.getenv("BACKEND_URL", "http://localhost:8000")

st.title("Code Knowledge")
st.caption(
    "Index a TypeScript/JS codebase to make Jira tickets reference exact enum names, "
    "service names, and existing values. Manual entries work as a quick fallback."
)

tab_index, tab_domain, tab_manual = st.tabs(["Index Project", "Domain Entities", "Manual Entities"])

# Tab 1: Index Project
with tab_index:
    st.subheader("Index a Codebase")
    st.info(
        "Requires Neo4j (`bolt://localhost:7687`) and Qdrant (`http://localhost:6333`) running. "
        "See `.env` for connection settings."
    )

    with st.form("index_project_form"):
        col1, col2 = st.columns(2)
        with col1:
            project_name = st.text_input("Project name", placeholder="e.g. payments-service")
        with col2:
            root_path = st.text_input(
                "Root path (absolute)",
                placeholder="e.g. /home/user/projects/payments-service",
            )
        col_btn, col_force = st.columns([2, 1])
        submitted = col_btn.form_submit_button("Index Project")
        force = col_force.form_submit_button("Force Re-index")

    if submitted or force:
        if not project_name.strip() or not root_path.strip():
            st.error("Both project name and root path are required.")
        else:
            try:
                resp = requests.post(
                    f"{API_BASE}/index-project",
                    json={
                        "project_name": project_name.strip(),
                        "root_path": root_path.strip(),
                        "force": bool(force),
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "already_indexed":
                    st.warning(
                        f"Path already indexed as **{data['project_name']}** "
                        f"(job #{data['job_id']}, {data.get('node_count', 0)} nodes). "
                        "Use **Force Re-index** to rebuild."
                    )
                elif data.get("status") == "already_running":
                    st.info(
                        f"Indexing already in progress for **{data['project_name']}** "
                        f"(job #{data['job_id']})."
                    )
                    st.session_state["active_job_id"] = data["job_id"]
                    st.rerun()
                else:
                    st.session_state["active_job_id"] = data["job_id"]
                    st.rerun()
            except requests.exceptions.RequestException as e:
                st.error(f"Error: {e}")

    # Active job progress widget
    active_job_id = st.session_state.get("active_job_id")
    if active_job_id:
        try:
            job = requests.get(f"{API_BASE}/index-jobs/{active_job_id}", timeout=15).json()
            status = job.get("status", "")
            progress = job.get("progress", 0)
            step = job.get("current_step", "")

            if status in ("pending", "running"):
                bar_label = f"**{progress}%** — {step}"
                st.progress(progress / 100, text=bar_label)

                created_raw = job.get("created_at")
                if created_raw and progress > 5:
                    try:
                        if hasattr(created_raw, "timestamp"):
                            created_ts = created_raw.timestamp()
                        else:
                            dt = datetime.fromisoformat(str(created_raw))
                            created_ts = dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
                        elapsed = time.time() - created_ts
                        total_est = elapsed / (progress / 100)
                        remaining = max(0, total_est - elapsed)
                        if remaining >= 60:
                            time_str = f"~{int(remaining // 60)}m {int(remaining % 60)}s remaining"
                        else:
                            time_str = f"~{int(remaining)}s remaining"
                        st.caption(time_str)
                    except Exception:
                        pass

                time.sleep(2)
                st.rerun()

            elif status == "completed":
                st.success(
                    f"Indexing complete — {job.get('node_count', 0)} nodes, "
                    f"{job.get('edge_count', 0)} edges. {step}"
                )
                del st.session_state["active_job_id"]

            elif status == "failed":
                st.error(f"Indexing failed: {job.get('error', 'unknown error')}")
                del st.session_state["active_job_id"]

        except Exception:
            st.warning("Could not fetch job status.")

    # All jobs table
    st.subheader("All Jobs")

    try:
        jobs = requests.get(f"{API_BASE}/index-jobs", timeout=10).json()
    except Exception:
        jobs = []

    if not jobs:
        st.info("No indexing jobs yet.")
    else:
        for job in jobs:
            status = job.get("status", "")
            icon = {"completed": "✓", "running": "⏳", "failed": "✗", "pending": "·"}.get(status, "·")
            cols = st.columns([1, 3, 1, 2, 3])
            cols[0].markdown(f"**#{job['id']}**")
            cols[1].text(job.get("project_name", ""))
            cols[2].markdown(f"`{icon} {status}`")
            cols[3].text(f"{job.get('node_count', 0)}N / {job.get('edge_count', 0)}E")
            cols[4].caption(job.get("current_step", "")[:60])
            if status in ("running", "pending"):
                pct = job.get("progress", 0)
                st.progress(pct / 100, text=f"{pct}% — {job.get('current_step', '')}")
            if job.get("error"):
                st.caption(f"  ↳ {job['error']}")


# Tab 2: Domain Entities
with tab_domain:
    st.subheader("Domain Entities")
    st.caption(
        "Map business concepts (Payout, Partner, Incentive) to services in Neo4j. "
        "This bridges vague human language to actual code for better Jira tickets."
    )

    try:
        projects_resp = requests.get(f"{API_BASE}/projects", timeout=5)
        project_list = [p["name"] for p in (projects_resp.json() if projects_resp.ok else [])]
    except Exception:
        project_list = []

    col_add, col_link = st.columns(2)

    with col_add:
        st.markdown("**Add Domain Entity**")
        with st.form("add_domain_entity", clear_on_submit=True):
            de_name = st.text_input("Name", placeholder="e.g. Payout, Partner, Incentive")
            de_project = st.selectbox(
                "Project",
                options=[""] + project_list,
                format_func=lambda x: x or "— select project —",
            )
            de_desc = st.text_area("Description (optional)", height=60)
            de_submitted = st.form_submit_button("Add")

        if de_submitted:
            if not de_name.strip():
                st.error("Name is required.")
            else:
                try:
                    resp = requests.post(
                        f"{API_BASE}/domain-entities",
                        json={
                            "name": de_name.strip(),
                            "project_name": de_project or None,
                            "description": de_desc.strip() or None,
                        },
                        timeout=10,
                    )
                    resp.raise_for_status()
                    st.success(f"Added domain entity: **{de_name.strip()}**")
                    st.rerun()
                except requests.exceptions.RequestException as e:
                    st.error(f"Error: {e}")

    # Link domain entity to service
    with col_link:
        st.markdown("**Link to Service**")
        link_project = st.selectbox(
            "Project",
            options=[""] + project_list,
            key="link_project_select",
            format_func=lambda x: x or "— select project —",
        )
        svc_options: list[str] = []
        if link_project:
            try:
                svc_resp = requests.get(f"{API_BASE}/services/{link_project}", timeout=5)
                svc_options = svc_resp.json() if svc_resp.ok else []
            except Exception:
                pass

        with st.form("link_domain_entity", clear_on_submit=True):
            link_domain = st.text_input("Domain entity name", placeholder="e.g. Payout")
            link_service = st.selectbox(
                "Service",
                options=[""] + svc_options,
                format_func=lambda x: x or "— select service —",
            )
            link_rel = st.selectbox("Relationship", ["HANDLES", "EXPOSES", "IMPLEMENTS"])
            link_submitted = st.form_submit_button("Link")

        if link_submitted:
            if not link_domain.strip() or not link_service or not link_project:
                st.error("Project, domain entity name, and service are all required.")
            else:
                try:
                    resp = requests.post(
                        f"{API_BASE}/domain-entities/link",
                        json={
                            "domain_name": link_domain.strip(),
                            "service_name": link_service,
                            "project_name": link_project,
                            "rel_type": link_rel,
                        },
                        timeout=10,
                    )
                    resp.raise_for_status()
                    st.success(f"Linked **{link_domain.strip()}** → {link_service} ({link_rel})")
                    st.rerun()
                except requests.exceptions.RequestException as e:
                    st.error(f"Error: {e}")

    st.subheader("Registered Domain Entities")
    filter_project = st.selectbox(
        "Filter by project",
        options=["All"] + project_list,
        key="de_filter_project",
    )
    try:
        de_query = "" if filter_project == "All" else f"?project={filter_project}"
        entities_resp = requests.get(f"{API_BASE}/domain-entities{de_query}", timeout=10)
        domain_entities = entities_resp.json() if entities_resp.ok else []
    except Exception:
        domain_entities = []

    if not domain_entities:
        st.info("No domain entities registered yet.")
    else:
        for de in domain_entities:
            cols = st.columns([2, 2, 4, 1])
            cols[0].markdown(f"**{de['name']}**")
            cols[1].text(de.get("project_name") or "—")
            linked = de.get("linked_services") or []
            if linked:
                cols[2].caption(f"→ {', '.join(linked)}")
            elif de.get("description"):
                cols[2].caption(de["description"])
            else:
                cols[2].caption("—")
            if cols[3].button("Delete", key=f"del_de_{de['id']}"):
                try:
                    requests.delete(f"{API_BASE}/domain-entities/{de['id']}", timeout=10)
                    st.rerun()
                except Exception:
                    pass


# Tab 3: Manual Entities
with tab_manual:
    st.subheader("Add Entity Manually")
    st.caption("Quick additions that don't require a full codebase index.")

    with st.form("add_entity", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            entity_type = st.selectbox("Type", ["service", "enum", "field", "feature"])
            name = st.text_input("Name", placeholder="e.g. PayoutStatus or PaymentService")
        with col2:
            service = st.text_input("Service (parent)", placeholder="e.g. PaymentService")
            values = st.text_input(
                "Enum values (comma-separated)",
                placeholder="e.g. PENDING, PROCESSED=2, FAILED=3",
            )
        description = st.text_area("Description (optional)", height=60)
        add_submitted = st.form_submit_button("Add")

    if add_submitted:
        if not name.strip():
            st.error("Name is required.")
        else:
            values_json = None
            if entity_type == "enum" and values.strip():
                val_list = [v.strip() for v in values.split(",") if v.strip()]
                values_json = json.dumps(val_list)

            payload = {
                "name": name.strip(),
                "type": entity_type,
                "service": service.strip() or None,
                "values_json": values_json,
                "description": description.strip() or None,
            }
            try:
                resp = requests.post(f"{API_BASE}/code-knowledge", json=payload, timeout=10)
                resp.raise_for_status()
                st.success(f"Added {entity_type}: **{name.strip()}**")
                st.rerun()
            except requests.exceptions.RequestException as e:
                st.error(f"Error: {e}")

    st.subheader("Registered Entities")
    try:
        resp = requests.get(f"{API_BASE}/code-knowledge", timeout=10)
        entities = resp.json() if resp.ok else []
    except Exception:
        entities = []

    if not entities:
        st.info("No manual entities registered yet.")
    else:
        by_type: dict = {}
        for e in entities:
            by_type.setdefault(e["type"], []).append(e)

        for etype in ["service", "enum", "field", "feature"]:
            group = by_type.get(etype, [])
            if not group:
                continue
            st.markdown(f"**{etype.capitalize()}s**")
            for e in group:
                col1, col2, col3, col4 = st.columns([2, 2, 4, 1])
                col1.text(e["name"])
                col2.text(e.get("service") or "—")
                if e["type"] == "enum" and e.get("values_json"):
                    try:
                        vals = json.loads(e["values_json"])
                        col3.caption(f"values: {', '.join(str(v) for v in vals)}")
                    except Exception:
                        col3.caption(e.get("values_json", "—"))
                else:
                    col3.caption(e.get("description") or "—")
                if col4.button("Delete", key=f"del_{e['id']}"):
                    try:
                        requests.delete(f"{API_BASE}/code-knowledge/{e['id']}", timeout=10)
                        st.rerun()
                    except Exception:
                        pass
