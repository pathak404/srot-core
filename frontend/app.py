import streamlit as st

st.set_page_config(page_title="AI Meeting Assistant", layout="wide")

upload_page = st.Page("views/upload.py", title="Upload", default=True)
meetings_page = st.Page("views/meetings.py", title="Meetings")
code_knowledge_page = st.Page("views/code_knowledge.py", title="Code Knowledge")
detail_page = st.Page("views/meeting_detail.py", title="Meeting Detail", url_path="meeting", visibility="hidden")

st.session_state["_meeting_detail_page"] = detail_page

nav = st.navigation([upload_page, meetings_page, code_knowledge_page, detail_page])
nav.run()
