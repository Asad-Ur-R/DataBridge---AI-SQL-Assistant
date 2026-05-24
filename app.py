import streamlit as st
import plotly.express as px
import pandas as pd
from utils.ai import generate_response, detect_intent
from utils.db import (
    run_query, execute_crud,
    log_query, get_query_history,
    get_table_stats
)
from utils.schema_builder import (
    process_csv_upload,
    get_uploaded_tables,
    ensure_metadata_table
)
from utils.security import validate_file_upload
from utils.db import get_connection


# PAGE CONFIG
st.set_page_config(
    page_title  = "DataBridge AI",
    page_icon   = "🌿",
    layout      = "wide",
    initial_sidebar_state = "expanded"
)


# ── CUSTOM CSS ────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&family=DM+Sans:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Root variables ── */
:root {
    --bg:           #F7F3EE;
    --surface:      #FFFFFF;
    --surface-2:    #F0EBE3;
    --border:       #E2D9CE;
    --green-deep:   #2D6A4F;
    --green-mid:    #52B788;
    --green-light:  #B7E4C7;
    --green-pale:   #D8F3DC;
    --text-primary: #1C1C1E;
    --text-secondary: #6B7280;
    --text-muted:   #9CA3AF;
    --sql-bg:       #1A2332;
    --sql-text:     #95D5B2;
    --coral:        #E76F51;
    --coral-pale:   #FDF0ED;
    --gold:         #D4A853;
    --shadow-sm:    0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md:    0 4px 16px rgba(0,0,0,0.08), 0 2px 6px rgba(0,0,0,0.04);
    --radius:       12px;
    --radius-sm:    8px;
}

/* ── Global ── */
.stApp {
    background-color: var(--bg) !important;
    font-family: 'DM Sans', sans-serif !important;
}
.main .block-container {
    padding: 2rem 2.5rem !important;
    max-width: 1200px !important;
}

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: var(--surface) !important;
    border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebar"] .block-container {
    padding: 1.5rem 1rem !important;
}

/* ── Hide Streamlit chrome ── */
#MainMenu, footer, header { visibility: hidden; }
.stDeployButton { display: none; }

/* ── Buttons ── */
.stButton > button {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    border-radius: var(--radius-sm) !important;
    border: 1.5px solid var(--green-deep) !important;
    background: var(--green-deep) !important;
    color: white !important;
    padding: 0.5rem 1.2rem !important;
    transition: all 0.2s ease !important;
    letter-spacing: 0.01em !important;
}
.stButton > button:hover {
    background: #1E4D38 !important;
    border-color: #1E4D38 !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(45,106,79,0.25) !important;
}

/* ── Text input ── */
.stTextInput > div > div > input {
    font-family: 'DM Sans', sans-serif !important;
    background: var(--surface) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    color: var(--text-primary) !important;
    padding: 0.65rem 1rem !important;
    font-size: 0.95rem !important;
    transition: border-color 0.2s ease !important;
}
.stTextInput > div > div > input:focus {
    border-color: var(--green-mid) !important;
    box-shadow: 0 0 0 3px rgba(82,183,136,0.15) !important;
}
.stTextInput > div > div > input::placeholder {
    color: var(--text-muted) !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] {
    background: var(--surface) !important;
    border: 2px dashed var(--border) !important;
    border-radius: var(--radius) !important;
    padding: 1rem !important;
    transition: border-color 0.2s !important;
}
[data-testid="stFileUploader"]:hover {
    border-color: var(--green-mid) !important;
}

/* ── Dataframe ── */
[data-testid="stDataFrame"] {
    border: 1px solid var(--border) !important;
    border-radius: var(--radius) !important;
    overflow: hidden !important;
}

/* ── Selectbox ── */
.stSelectbox > div > div {
    background: var(--surface) !important;
    border: 1.5px solid var(--border) !important;
    border-radius: var(--radius-sm) !important;
    font-family: 'DM Sans', sans-serif !important;
}

/* ── Divider ── */
hr { border-color: var(--border) !important; margin: 1.5rem 0 !important; }

/* ── Expander ── */
.streamlit-expanderHeader {
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 500 !important;
    color: var(--text-primary) !important;
    background: var(--surface-2) !important;
    border-radius: var(--radius-sm) !important;
}

/* ── Spinner ── */
.stSpinner > div {
    border-top-color: var(--green-mid) !important;
}

/* ── Alert ── */
.stAlert {
    border-radius: var(--radius-sm) !important;
    font-family: 'DM Sans', sans-serif !important;
}
</style>
""", unsafe_allow_html=True)


# ── COMPONENT HELPERS ─────────────────────────────────────────
# Why helper functions for HTML components:
# We reuse the same card, badge, and block patterns throughout
# the UI. Centralizing them means one change updates everywhere
# and keeps app logic clean and readable.

def card(content: str, padding: str = "1.5rem") -> str:
    return f"""
    <div style="background:var(--surface); border:1px solid var(--border);
                border-radius:var(--radius); padding:{padding};
                box-shadow:var(--shadow-sm); margin-bottom:1rem;">
        {content}
    </div>"""


def section_title(icon: str, title: str, subtitle: str = "") -> str:
    sub = f'<div style="font-family:DM Sans;font-size:0.82rem;color:var(--text-secondary);margin-top:0.2rem;">{subtitle}</div>' if subtitle else ""
    return f"""
    <div style="margin-bottom:1rem;">
        <div style="font-family:Playfair Display,serif;font-size:1.25rem;
                    font-weight:600;color:var(--text-primary);">
            {icon} {title}
        </div>
        {sub}
    </div>"""


def mode_badge(mode: str) -> str:
    configs = {
        "query": ( "QUERY",   "#EDF7F1", "var(--green-deep)"),
        "crud":  (  "MODIFY",  "#FDF0ED", "var(--coral)"),
        "learn": ( "LEARN",   "#FFF8ED", "var(--gold)"),
    }
    icon, label, bg, color = configs.get(mode, ("💬", mode.upper(), "#F3F4F6", "#374151"))
    return f"""
    <span style="background:{bg};color:{color};border:1px solid {color};
                 border-radius:20px;padding:0.25rem 0.75rem;
                 font-family:DM Sans,sans-serif;font-size:0.72rem;
                 font-weight:600;letter-spacing:0.05em;">{icon} {label}</span>"""


def sql_block(sql: str) -> str:
    # Escape HTML special chars so SQL displays correctly
    safe = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
    <div style="background:var(--sql-bg);border-radius:var(--radius);
                padding:1.2rem 1.4rem;margin:0.75rem 0;
                box-shadow:var(--shadow-md);position:relative;">
        <div style="font-family:JetBrains Mono,monospace;font-size:0.82rem;
                    color:var(--sql-text);line-height:1.7;
                    white-space:pre-wrap;word-break:break-word;">{safe}</div>
        <div style="position:absolute;top:0.7rem;right:1rem;
                    font-family:DM Sans,sans-serif;font-size:0.65rem;
                    color:#4A6FA5;letter-spacing:0.1em;">SQL</div>
    </div>"""


def stat_card(icon: str, value: str, label: str) -> str:
    return f"""
    <div style="background:var(--surface);border:1px solid var(--border);
                border-radius:var(--radius-sm);padding:0.9rem 1rem;
                margin-bottom:0.5rem;text-align:center;
                box-shadow:var(--shadow-sm);">
        <div style="font-size:1.4rem;margin-bottom:0.2rem;">{icon}</div>
        <div style="font-family:Playfair Display,serif;font-size:1.4rem;
                    font-weight:700;color:var(--green-deep);">{value}</div>
        <div style="font-family:DM Sans,sans-serif;font-size:0.7rem;
                    color:var(--text-muted);text-transform:uppercase;
                    letter-spacing:0.08em;">{label}</div>
    </div>"""


def warning_box(message: str) -> str:
    return f"""
    <div style="background:var(--coral-pale);border:1.5px solid var(--coral);
                border-left:4px solid var(--coral);border-radius:var(--radius-sm);
                padding:1rem 1.2rem;margin:0.75rem 0;
                font-family:DM Sans,sans-serif;font-size:0.88rem;
                color:#B34A30;line-height:1.6;">{message}</div>"""


def success_box(message: str) -> str:
    return f"""
    <div style="background:var(--green-pale);border:1.5px solid var(--green-mid);
                border-left:4px solid var(--green-deep);border-radius:var(--radius-sm);
                padding:1rem 1.2rem;margin:0.75rem 0;
                font-family:DM Sans,sans-serif;font-size:0.88rem;
                color:var(--green-deep);line-height:1.6;">{message}</div>"""


def learn_box(explanation: str) -> str:
    # Convert **bold** markdown to HTML
    import re
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', explanation)
    html = html.replace("\n", "<br>")
    return f"""
    <div style="background:#FFFDF7;border:1px solid #E8D5A3;
                border-left:4px solid var(--gold);border-radius:var(--radius);
                padding:1.4rem 1.6rem;margin:0.75rem 0;">
        <div style="font-family:DM Sans,sans-serif;font-size:0.9rem;
                    color:var(--text-primary);line-height:1.9;">{html}</div>
    </div>"""


# ── AUTO CHART ────────────────────────────────────────────────
def auto_chart(df: pd.DataFrame):
    """Generates best Plotly chart for any DataFrame."""
    if df is None or df.empty or len(df) < 2:
        return None

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    text_cols    = df.select_dtypes(include="object").columns.tolist()

    if not numeric_cols:
        return None

    theme = dict(
        template     = "plotly_white",
        paper_bgcolor= "#FFFFFF",
        plot_bgcolor = "#FAFAFA",
        font         = dict(family="DM Sans", color="#4B5563"),
    )

    GREEN_PALETTE = [
        "#2D6A4F","#52B788","#95D5B2",
        "#B7E4C7","#D8F3DC","#74C69D"
    ]

    # Bar chart — text + numeric
    if text_cols and numeric_cols:
        x_col, y_col = text_cols[0], numeric_cols[0]
        if df[x_col].nunique() <= 30:
            fig = px.bar(
                df, x=x_col, y=y_col,
                title=f"{y_col.replace('_',' ').title()} by {x_col.replace('_',' ').title()}",
                color_discrete_sequence=GREEN_PALETTE,
            )
            fig.update_layout(
                **theme,
                title_font = dict(family="Playfair Display", size=15, color="#1C1C1E"),
                xaxis_title= x_col.replace("_"," ").title(),
                yaxis_title= y_col.replace("_"," ").title(),
                showlegend = False,
                height     = 380,
                margin     = dict(t=50, b=40, l=40, r=20),
            )
            fig.update_traces(
                marker_line_width  = 0,
                marker_cornerradius= 4,
            )
            return fig

    # Scatter — two numeric columns
    if len(numeric_cols) >= 2:
        fig = px.scatter(
            df, x=numeric_cols[0], y=numeric_cols[1],
            title=f"{numeric_cols[1].replace('_',' ').title()} vs {numeric_cols[0].replace('_',' ').title()}",
            color_discrete_sequence=GREEN_PALETTE,
        )
        fig.update_layout(
            **theme,
            title_font=dict(family="Playfair Display", size=15, color="#1C1C1E"),
            height=380,
        )
        return fig

    return None


# SESSION STATE
# Why all these keys:
# Streamlit reruns the whole script on every interaction.
# Session state is the only way to persist data between reruns.
# Each key stores one piece of UI state.

defaults = {
    "active_table":    None,   # Currently selected table name
    "active_schema":   None,   # That table's column→type dict
    "result_df":       None,   # Last SELECT results
    "last_sql":        None,   # Last SQL generated
    "last_question":   None,   # Last question asked
    "last_mode":       None,   # Last intent mode
    "error_msg":       None,   # Last error if any
    "explanation":     None,   # Last LEARN response
    "crud_pending":    None,   # CRUD awaiting confirmation
    "crud_preview_df": None,   # Preview of rows to be affected
    "crud_action":     None,   # Human-readable CRUD description
    "crud_operation":  None,   # DELETE / UPDATE / INSERT
    "input_value":     "",     # Current input box value
    "main_input":         "", 
    "rows_affected":   None,   # After CRUD executes
    "last_uploaded_key":  None,
}
for key, val in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = val


# ── SUGGESTED QUESTIONS
SUGGESTIONS = {
    "query": [
        "Show me the first 10 rows",
        "How many rows are in this table?",
        "Show summary statistics",
        "Which rows have missing values?",
    ],
    "crud": [
        "Delete rows where sales is zero",
        "Update quantity to 1 where it is null",
    ],
    "learn": [
        "What does GROUP BY do?",
        "Explain the last query",
        "What is a JOIN?",
        "When should I use WHERE vs HAVING?",
    ]
}


# SIDEBAR

with st.sidebar:

    # Logo
    st.markdown("""
    <div style="text-align:center;padding:1rem 0 0.5rem;">
        <div style="font-family:'Playfair Display',serif;font-size:1.5rem;
                    font-weight:700;color:var(--green-deep);">
            🌿 DataBridge
        </div>
        <div style="font-family:'DM Sans',sans-serif;font-size:0.7rem;
                    color:var(--text-muted);letter-spacing:0.12em;
                    text-transform:uppercase;margin-top:0.2rem;">
            AI · Database · Interface
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # Upload new dataset
    st.markdown("""
    <div style="font-family:'DM Sans',sans-serif;font-size:0.75rem;
                font-weight:600;color:var(--text-secondary);
                text-transform:uppercase;letter-spacing:0.08em;
                margin-bottom:0.6rem;">
        📁 Upload Dataset
    </div>
    """, unsafe_allow_html=True)

    uploaded_file = st.file_uploader(
        "Upload CSV",
        type        = ["csv"],
        label_visibility = "collapsed",
        help        = "Upload any CSV file to start querying"
    )

    if uploaded_file is not None:

        # Build a unique key from filename + filesize
        # Why: When st.rerun() fires, Streamlit reruns the whole
        # script and uploaded_file is still in memory — without
        # this check the app would reprocess the same file on
        # every rerun, creating an infinite processing loop.
        file_bytes  = uploaded_file.getvalue()
        file_key    = f"{uploaded_file.name}_{len(file_bytes)}"
        already_done = (st.session_state.last_uploaded_key == file_key)

        if already_done:
            # File already processed — just show a quiet confirmation
            st.markdown(f"""
            <div style="background:var(--green-pale);border:1px solid var(--green-mid);
                        border-radius:var(--radius-sm);padding:0.5rem 0.8rem;
                        font-family:'DM Sans',sans-serif;font-size:0.78rem;
                        color:var(--green-deep);">
                 {uploaded_file.name} loaded
            </div>
            """, unsafe_allow_html=True)

        else:
            # New file — validate and process
            ok, reason = validate_file_upload(uploaded_file.name, len(file_bytes))

            if not ok:
                st.error(reason)
            else:
                with st.spinner("Processing your dataset..."):
                    import io
                    result, error = process_csv_upload(
                        io.BytesIO(file_bytes),
                        uploaded_file.name
                    )

                if error:
                    st.error(f"Upload failed: {error}")
                else:
                    # Save everything to session state BEFORE rerun
                    st.session_state.active_table      = result["table_name"]
                    st.session_state.active_schema     = result["schema"]
                    st.session_state.last_uploaded_key = file_key

                    # Clear stale results from previous table
                    for key in [
                        "result_df", "last_sql", "error_msg",
                        "explanation", "crud_pending",
                        "crud_preview_df", "rows_affected"
                    ]:
                        st.session_state[key] = None

                    # Now rerun — this time already_done will be True
                    # so the upload block is skipped and the main
                    # interface renders with the new table active
                    st.rerun()

    st.divider()

    # Previously uploaded tables 
    st.markdown("""
    <div style="font-family:'DM Sans',sans-serif;font-size:0.75rem;
                font-weight:600;color:var(--text-secondary);
                text-transform:uppercase;letter-spacing:0.08em;
                margin-bottom:0.6rem;">
        🗄️ Your Tables
    </div>
    """, unsafe_allow_html=True)

    try:
        conn = get_connection()
        ensure_metadata_table(conn)
        tables = get_uploaded_tables(conn)
        conn.close()
    except Exception:
        tables = []

    if tables:
        table_names = [t["table_name"] for t in tables]
        selected = st.selectbox(
            "Select table",
            table_names,
            index = table_names.index(st.session_state.active_table)
                    if st.session_state.active_table in table_names else 0,
            label_visibility="collapsed"
        )

        # Update active table when user switches
        if selected != st.session_state.active_table:
            selected_meta = next(t for t in tables if t["table_name"] == selected)
            st.session_state.active_table  = selected
            st.session_state.active_schema = selected_meta["schema"]
            for key in ["result_df","last_sql","error_msg",
                        "explanation","crud_pending","rows_affected"]:
                st.session_state[key] = None
            st.rerun()

        # Show stats for active table
        if st.session_state.active_table:
            stats = get_table_stats(st.session_state.active_table)
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(
                    stat_card("📋", f"{stats['row_count']:,}", "Rows"),
                    unsafe_allow_html=True
                )
            with col2:
                st.markdown(
                    stat_card("🔤", str(stats["column_count"]), "Columns"),
                    unsafe_allow_html=True
                )
    else:
        st.markdown("""
        <div style="text-align:center;padding:1rem;
                    font-family:'DM Sans',sans-serif;font-size:0.82rem;
                    color:var(--text-muted);">
            No tables yet.<br>Upload a CSV to get started.
        </div>
        """, unsafe_allow_html=True)

    st.divider()

    # Schema reference
    if st.session_state.active_schema:
        with st.expander("📐 Column Reference"):
            for col, dtype in st.session_state.active_schema.items():
                color = {
                    "TEXT":      "#2D6A4F",
                    "NUMERIC":   "#1D4ED8",
                    "BIGINT":    "#6D28D9",
                    "TIMESTAMP": "#B45309",
                    "BOOLEAN":   "#B91C1C",
                }.get(dtype, "#374151")
                st.markdown(f"""
                <div style="display:flex;justify-content:space-between;
                            align-items:center;padding:0.3rem 0;
                            border-bottom:1px solid var(--border);
                            font-family:'DM Sans',sans-serif;font-size:0.78rem;">
                    <span style="color:var(--text-primary);">{col}</span>
                    <span style="color:{color};font-weight:500;
                                 font-size:0.68rem;">{dtype}</span>
                </div>
                """, unsafe_allow_html=True)

    st.divider()

    # ── Query history ──────────────────────────────────────────
    st.markdown("""
    <div style="font-family:'DM Sans',sans-serif;font-size:0.75rem;
                font-weight:600;color:var(--text-secondary);
                text-transform:uppercase;letter-spacing:0.08em;
                margin-bottom:0.6rem;">
        🕐 Recent Activity
    </div>
    """, unsafe_allow_html=True)

    history_df = get_query_history(limit=6)
    if history_df is not None and not history_df.empty:
        mode_icons = {"query": "📊", "crud": "✏️", "learn": "📖"}
        for _, row in history_df.iterrows():
            q     = str(row["Question"])
            mode  = str(row.get("Mode", "query"))
            icon  = mode_icons.get(mode, "💬")
            short = q[:38] + "..." if len(q) > 38 else q
            st.markdown(f"""
            <div style="padding:0.45rem 0.6rem;margin-bottom:0.3rem;
                        background:var(--surface-2);border-radius:6px;
                        font-family:'DM Sans',sans-serif;font-size:0.75rem;
                        color:var(--text-secondary);cursor:default;
                        border:1px solid var(--border);">
                {icon} {short}
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="font-family:'DM Sans',sans-serif;font-size:0.78rem;
                    color:var(--text-muted);text-align:center;padding:0.5rem;">
            No activity yet
        </div>
        """, unsafe_allow_html=True)


# MAIN AREA
# ── Header
if st.session_state.active_table:
    table_display = st.session_state.active_table.replace("_", " ").title()
    st.markdown(f"""
    <div style="margin-bottom:1.5rem;">
        <div style="font-family:'Playfair Display',serif;font-size:2.2rem;
                    font-weight:700;color:var(--text-primary);line-height:1.2;">
            Ask your data anything
        </div>
        <div style="font-family:'DM Sans',sans-serif;font-size:0.9rem;
                    color:var(--text-secondary);margin-top:0.4rem;">
            Working with
            <span style="background:var(--green-pale);color:var(--green-deep);
                         padding:0.15rem 0.6rem;border-radius:20px;
                         font-weight:500;font-size:0.85rem;">
                🗄️ {table_display}
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)
else:
    # Welcome screen
    st.markdown("""
    <div style="text-align:center;padding:4rem 2rem;">
        <div style="font-size:3rem;margin-bottom:1rem;">🌿</div>
        <div style="font-family:'Playfair Display',serif;font-size:2.4rem;
                    font-weight:700;color:var(--text-primary);margin-bottom:0.8rem;">
            Welcome to DataBridge
        </div>
        <div style="font-family:'DM Sans',sans-serif;font-size:1rem;
                    color:var(--text-secondary);max-width:480px;margin:0 auto 2rem;
                    line-height:1.7;">
            Upload any CSV file from the sidebar to start querying,
            modifying, and learning from your data — all in plain English.
            No SQL knowledge required.
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Feature cards
    cols = st.columns(3)
    features = [
        ( "Query",  "Ask questions about your data in plain English. Get instant results and beautiful charts."),
        (  "Modify", "Update, delete, or insert records just by describing what you want to change."),
        ( "Learn",  "Understand SQL through your own data. Ask what any query means and get clear explanations."),
    ]
    for col, (icon, title, desc) in zip(cols, features):
        with col:
            st.markdown(f"""
            <div style="background:var(--surface);border:1px solid var(--border);
                        border-radius:var(--radius);padding:1.5rem;
                        text-align:center;box-shadow:var(--shadow-sm);
                        height:100%;">
                <div style="font-size:2rem;margin-bottom:0.8rem;">{icon}</div>
                <div style="font-family:'Playfair Display',serif;font-size:1.1rem;
                            font-weight:600;color:var(--text-primary);
                            margin-bottom:0.5rem;">{title}</div>
                <div style="font-family:'DM Sans',sans-serif;font-size:0.83rem;
                            color:var(--text-secondary);line-height:1.6;">{desc}</div>
            </div>
            """, unsafe_allow_html=True)
    st.stop()  # Don't render the rest until a table is loaded


# ── Suggestion chips 
st.markdown("""
<div style="font-family:'DM Sans',sans-serif;font-size:0.75rem;
            font-weight:600;color:var(--text-secondary);
            text-transform:uppercase;letter-spacing:0.08em;
            margin-bottom:0.6rem;">
    💡 Try asking
</div>
""", unsafe_allow_html=True)

all_suggestions = (
    SUGGESTIONS["query"][:2] +
    SUGGESTIONS["crud"][:1] +
    SUGGESTIONS["learn"][:1]
)

chip_cols = st.columns(len(all_suggestions))
for col, suggestion in zip(chip_cols, all_suggestions):
    with col:
        if st.button(suggestion, key=f"chip_{suggestion}"):
            st.session_state.main_input  = suggestion
            st.session_state.input_value = suggestion
            st.rerun()

st.divider()

# ── Input area ────────────────────────────────────────────────
st.markdown("""
<div style="font-family:'Playfair Display',serif;font-size:1.1rem;
            font-weight:600;color:var(--text-primary);margin-bottom:0.6rem;">
    What would you like to do?
</div>
""", unsafe_allow_html=True)

# Live intent preview
# Why: Show the detected mode BEFORE the user submits so they
# know the app understood their intent correctly. Builds trust.
current_input = st.session_state.get("main_input", "")
if current_input:
    live_intent = detect_intent(current_input)
    st.markdown(
        f'<div style="margin-bottom:0.5rem;">'
        f'{mode_badge(live_intent)}'
        f'<span style="font-family:DM Sans,sans-serif;font-size:0.78rem;'
        f'color:var(--text-muted);margin-left:0.5rem;">detected</span>'
        f'</div>',
        unsafe_allow_html=True
    )

input_col, btn_col = st.columns([5, 1])

with input_col:
    question = st.text_input(
        "question",
        label_visibility = "collapsed",
        placeholder      = "e.g. Show me the top 10 rows by sales · Delete rows where profit is 0 · What does AVG do?",
        key              = "main_input"
)

with btn_col:
    ask_btn = st.button("Ask →", use_container_width=True)


# ── Pipeline 
if ask_btn and question.strip():

    # Clear previous state
    for key in ["result_df","last_sql","error_msg","explanation",
                "crud_pending","crud_preview_df","crud_action",
                "crud_operation","rows_affected"]:
        st.session_state[key] = None

    st.session_state.last_question = question.strip()

    with st.spinner("Thinking..."):
        result = generate_response(
            question    = question.strip(),
            table_name  = st.session_state.active_table,
            schema      = st.session_state.active_schema,
            last_sql    = st.session_state.last_sql
        )

    st.session_state.last_mode = result["mode"]

    if result.get("error"):
        st.session_state.error_msg = result["error"]

    elif result["mode"] == "query":
        sql = result["sql"]
        st.session_state.last_sql = sql

        with st.spinner("Fetching results..."):
            df, db_err = run_query(sql)

        if db_err:
            st.session_state.error_msg = db_err
        else:
            st.session_state.result_df = df
            log_query(question.strip(), sql, len(df), "query")

    elif result["mode"] == "crud":
        # Don't execute yet — show preview and ask for confirmation
        st.session_state.crud_pending   = result["sql"]
        st.session_state.crud_action    = result["action"]
        st.session_state.crud_operation = result["operation"]
        st.session_state.last_sql       = result["sql"]

        # Fetch preview rows
        if result.get("preview_sql"):
            with st.spinner("Previewing affected rows..."):
                preview_df, _ = run_query(result["preview_sql"])
            st.session_state.crud_preview_df = preview_df

    elif result["mode"] == "learn":
        st.session_state.explanation = result["explanation"]
        log_query(question.strip(), "LEARN MODE", 0, "learn")

elif ask_btn and not question.strip():
    st.warning("Please type a question first.")


# RESULTS DISPLAY

# Error
if st.session_state.error_msg:
    st.markdown(
        warning_box(f"⚠️ {st.session_state.error_msg}"),
        unsafe_allow_html=True
    )

# QUERY results 
if st.session_state.last_sql and st.session_state.last_mode == "query":
    st.markdown(
        section_title( "Generated SQL", "AI-translated your question to this query"),
        unsafe_allow_html=True
    )
    st.markdown(sql_block(st.session_state.last_sql), unsafe_allow_html=True)

if st.session_state.result_df is not None:
    df = st.session_state.result_df

    res_col, badge_col = st.columns([3, 1])
    with res_col:
        st.markdown(
            section_title("📋", "Results"),
            unsafe_allow_html=True
        )
    with badge_col:
        st.markdown(
            f'<div style="text-align:right;padding-top:1.2rem;">'
            f'<span style="background:var(--green-pale);color:var(--green-deep);'
            f'border-radius:20px;padding:0.2rem 0.8rem;'
            f'font-family:DM Sans,sans-serif;font-size:0.75rem;font-weight:600;">'
            f' {len(df):,} rows</span></div>',
            unsafe_allow_html=True
        )

    st.dataframe(df, use_container_width=True, height=320)

    # Auto chart
    fig = auto_chart(df)
    if fig:
        st.markdown(
            section_title("📈", "Visualization"),
            unsafe_allow_html=True
        )
        st.plotly_chart(fig, use_container_width=True)

    # CSV download
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label     = "⬇️  Download as CSV",
        data      = csv,
        file_name = "results.csv",
        mime      = "text/csv",
    )


# ── CRUD confirmation flow ────────────────────────────────────
if st.session_state.crud_pending:
    op       = st.session_state.crud_operation or "MODIFY"
    action   = st.session_state.crud_action    or ""
    sql      = st.session_state.crud_pending

    # Show SQL
    st.markdown(
        section_title("✏️", f"{op} Operation", "Review carefully before confirming"),
        unsafe_allow_html=True
    )
    st.markdown(sql_block(sql), unsafe_allow_html=True)

    # Human-readable description
    st.markdown(
        warning_box(f"⚠️ <strong>What will happen:</strong> {action}"),
        unsafe_allow_html=True
    )

    # Preview of affected rows
    if st.session_state.crud_preview_df is not None:
        preview = st.session_state.crud_preview_df
        if not preview.empty:
            st.markdown(f"""
            <div style="font-family:'DM Sans',sans-serif;font-size:0.82rem;
                        color:var(--text-secondary);margin-bottom:0.4rem;">
                 Preview — {len(preview)} affected row(s) shown:
            </div>
            """, unsafe_allow_html=True)
            st.dataframe(preview, use_container_width=True, height=200)
        else:
            st.markdown(
                success_box(" No rows match this condition — operation would affect 0 rows."),
                unsafe_allow_html=True
            )

    # Confirm / Cancel buttons
    st.markdown("<div style='margin-top:1rem;'></div>", unsafe_allow_html=True)
    confirm_col, cancel_col, _ = st.columns([1, 1, 4])

    with confirm_col:
        if st.button(" Confirm", key="confirm_crud"):
            with st.spinner("Executing..."):
                rows_affected, err = execute_crud(sql)

            if err:
                st.session_state.error_msg = err
            else:
                st.session_state.rows_affected = rows_affected
                log_query(
                    st.session_state.last_question or "",
                    sql, rows_affected, "crud"
                )

            # Clear pending state
            st.session_state.crud_pending    = None
            st.session_state.crud_preview_df = None
            st.rerun()

    with cancel_col:
        if st.button(" Cancel", key="cancel_crud"):
            st.session_state.crud_pending    = None
            st.session_state.crud_preview_df = None
            st.rerun()

# Show CRUD result after execution
if st.session_state.rows_affected is not None:
    n = st.session_state.rows_affected
    st.markdown(
        success_box(f"Done — <strong>{n:,} row{'s' if n != 1 else ''}</strong> affected successfully."),
        unsafe_allow_html=True
    )


# ── LEARN results ─────────────────────────────────────────────
if st.session_state.explanation:
    st.markdown(
        section_title("📖", "SQL Explained", "Plain English breakdown just for you"),
        unsafe_allow_html=True
    )
    st.markdown(
        learn_box(st.session_state.explanation),
        unsafe_allow_html=True
    )

    # Show the SQL it's explaining if relevant
    if st.session_state.last_sql and "last query" in (st.session_state.last_question or "").lower():
        with st.expander(" The SQL being explained"):
            st.markdown(sql_block(st.session_state.last_sql), unsafe_allow_html=True)
