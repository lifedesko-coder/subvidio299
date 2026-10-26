"""
Entrypoint for Hugging Face Spaces (and platforms defaulting to app.py)
Loads and executes the full-featured BollywoodSub AI Streamlit application.
"""
import sys
from pathlib import Path

# Set working directory to project root
current_dir = Path(__file__).parent.resolve()
sys.path.insert(0, str(current_dir))

# Execute the primary Streamlit app
app_file = current_dir / "streamlit_app.py"
if app_file.exists():
    with open(app_file, "r", encoding="utf-8") as f:
        code = compile(f.read(), str(app_file), "exec")
        exec(code, globals())
else:
    import streamlit as st
    st.error("❌ ملف streamlit_app.py غير موجود.")
