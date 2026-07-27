"""Streamlit showcase for bilingual, cited Gipuzkoa weather and climate answers."""

import os
from html import escape
from pathlib import Path

import streamlit as st

from app.assistant import create_assistant
from app.db_feedback import save_feedback
from app.db_init import connect
from app.db_save import save_conversation
from app.judge import evaluate_relevance
from app.snapshot import read_snapshot_metadata


SAMPLE_QUESTIONS = (
    "¿Qué es el cambio climático y por qué es un problema global?",
    "¿Qué previsión muestra la instantánea para hoy en Hernani?",
    "What forecast was captured for tomorrow in Lasarte-Oria?",
    "¿Qué tiempo se capturó para pasado mañana en Irun?",
    "¿Qué avisos se capturaron para la costa de Gipuzkoa?",
    "What warning response was captured for inland Gipuzkoa?",
)


st.set_page_config(
    page_title="Ask Gipuzkoa Climate",
    page_icon="G",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
      --coast: #082f3d;
      --signal: #ef6c35;
      --mist: #e8f0ed;
      --paper: #fbf8ef;
    }
    .stApp { background: var(--paper); color: #102f35; }
    [data-testid="stHeader"] { background: transparent; }
    .hero {
      border-top: 7px solid var(--signal);
      background: var(--coast);
      color: white;
      padding: 2.2rem 2.4rem 1.8rem;
      margin-bottom: 1.2rem;
    }
    .hero h1 { font-size: clamp(2.4rem, 6vw, 5rem); line-height: .92; margin: 0; }
    .hero p { max-width: 720px; margin: 1rem 0 0; color: #cce0dd; }
    .emergency {
      border: 1px solid #c74528;
      border-left: 8px solid #c74528;
      padding: .85rem 1rem;
      background: #fff1e9;
      margin: .8rem 0 1.4rem;
    }
    .source-card {
      border-top: 3px solid var(--signal);
      background: white;
      padding: 1rem;
      min-height: 150px;
      box-shadow: 0 5px 18px rgba(8,47,61,.08);
    }
    .source-card small { color: #567078; }
    .answer-panel {
      background: white;
      border-left: 8px solid var(--coast);
      padding: 1.3rem 1.5rem;
      box-shadow: 0 8px 24px rgba(8,47,61,.08);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def assistant_resource():
    return create_assistant()


@st.cache_resource
def monitoring_ready():
    with connect() as connection:
        connection.execute("SELECT 1 FROM conversations LIMIT 0")
    return True


def persist_answer(result, question, enabled):
    if not enabled:
        return None
    try:
        monitoring_ready()
        return save_conversation(result, question)
    except Exception as error:
        st.warning(f"Answer generated, but monitoring persistence failed: {error}")
        return None


def render_metrics(result):
    call = result.call
    columns = st.columns(5)
    columns[0].metric("Route", result.route.replace("_", " ").title())
    columns[1].metric("Language", result.language.upper())
    columns[2].metric("Retriever", result.retrieval_backend)
    columns[3].metric("Latency", f"{call.response_time:.2f}s" if call else "rule")
    columns[4].metric("Tokens", f"{call.total_tokens:,}" if call else "0")
    if call and call.cost:
        st.caption(f"Estimated model cost: ${call.cost:.6f}")


def render_sources(result):
    if not result.citations:
        return
    st.subheader("Official evidence")
    columns = st.columns(min(3, len(result.citations)))
    for index, citation in enumerate(result.citations):
        with columns[index % len(columns)]:
            freshness = "Stale snapshot" if citation.stale else "Current or archival"
            title = escape(citation.title)
            organization = escape(citation.organization)
            publication_date = escape(citation.publication_date or "not provided")
            retrieved_at = escape(citation.retrieved_at or "not provided")
            st.markdown(
                f"""
                <div class="source-card">
                  <strong>[{citation.citation_id}] {title}</strong><br>
                  <small>{organization} · {freshness}</small><br><br>
                  <small>Published: {publication_date}<br>
                  Retrieved: {retrieved_at}</small>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if citation.url:
                st.link_button("Open official source", citation.url, use_container_width=True)


def render_feedback(conversation_id, question, result):
    if conversation_id is None:
        return
    st.subheader("Evaluate this answer")
    comment = st.text_input(
        "Optional feedback comment",
        key=f"comment-{conversation_id}",
        max_chars=500,
    )
    positive, negative, judge_column = st.columns([1, 1, 2])
    if positive.button("Useful +1", key=f"positive-{conversation_id}", use_container_width=True):
        try:
            save_feedback(conversation_id, "user", score=1, comment=comment)
            st.success("Feedback saved.")
        except Exception as error:
            st.error(f"Feedback could not be saved: {error}")
    if negative.button(
        "Not useful -1", key=f"negative-{conversation_id}", use_container_width=True
    ):
        try:
            save_feedback(conversation_id, "user", score=-1, comment=comment)
            st.success("Feedback saved.")
        except Exception as error:
            st.error(f"Feedback could not be saved: {error}")
    if judge_column.button(
        "Run LLM-as-Judge",
        key=f"judge-{conversation_id}",
        use_container_width=True,
        disabled=result.call is None,
    ):
        try:
            with st.spinner("Independent relevance check..."):
                verdict, usage = evaluate_relevance(question, result.answer)
                save_feedback(
                    conversation_id,
                    "judge",
                    relevance=verdict.relevance,
                    explanation=verdict.explanation,
                )
            st.info(f"{verdict.relevance}: {verdict.explanation}")
            st.caption(f"Judge tokens: {usage['total_tokens']}")
        except Exception as error:
            st.error(f"The judge could not complete: {error}")


st.markdown(
    """
    <section class="hero">
      <h1>ASK<br>GIPUZKOA</h1>
      <p>Official-source answers for local weather, climate history, risk and
      preparedness. Search in Spanish or English; inspect every source.</p>
    </section>
    """,
    unsafe_allow_html=True,
)
st.markdown(
    """
    <div class="emergency"><strong>Immediate danger?</strong> Call <strong>112</strong>.
    This project is informational and is not an official emergency-alert service.</div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("System")
    data_mode = os.getenv("DATA_MODE", "snapshot")
    st.write("Data mode", data_mode.title())
    st.write("Retrieval", os.getenv("RETRIEVAL_BACKEND", "pgvector"))
    st.write("Generation", os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini"))
    st.write("Embeddings", os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"))
    if data_mode.casefold() == "snapshot":
        metadata = read_snapshot_metadata(
            Path(os.getenv("SQLITE_DATABASE", "data/processed/ingestion.sqlite"))
        )
        st.caption(
            f"Snapshot {metadata['snapshot_id']} · acquisition "
            f"{metadata['capture_started_at'] or 'unknown'} to "
            f"{metadata['capture_completed_at'] or 'unknown'}. Weather and warnings "
            "are historical, not current conditions."
        )
    else:
        st.caption("Official data was refreshed externally. Freshness is shown per source.")

st.subheader("Try an example")
sample_columns = st.columns(2)
for index, sample in enumerate(SAMPLE_QUESTIONS):
    if sample_columns[index % 2].button(sample, key=f"sample-{index}", use_container_width=True):
        st.session_state["question"] = sample

question = st.text_area(
    "Your question",
    key="question",
    height=100,
    placeholder="Ask about a warning, tomorrow's forecast, climate history, or preparedness...",
)
store_for_monitoring = st.checkbox(
    "Store this question, answer and model metrics in local PostgreSQL for monitoring",
    value=False,
)
st.caption(
    "Storage is optional. If enabled, the full question, answer, grounded prompt and "
    "optional feedback are retained until manually deleted. OpenAI response storage "
    "is disabled by the application."
)

if st.button("Ask official sources", type="primary", use_container_width=True):
    if not question.strip():
        st.error("Enter a question first.")
    else:
        try:
            with st.spinner("Routing, retrieving and grounding the answer..."):
                result = assistant_resource().ask(question)
            conversation_id = persist_answer(result, question, store_for_monitoring)
            st.session_state["last_answer"] = (question, result, conversation_id)
        except Exception as error:
            st.error(f"The answer could not be generated: {error}")

if "last_answer" in st.session_state:
    answered_question, result, conversation_id = st.session_state["last_answer"]
    st.markdown("<div class='answer-panel'>", unsafe_allow_html=True)
    st.markdown(result.answer)
    st.markdown("</div>", unsafe_allow_html=True)
    if not result.citation_valid:
        st.warning("The generated answer failed the citation contract and was replaced.")
    render_metrics(result)
    render_sources(result)
    render_feedback(conversation_id, answered_question, result)
