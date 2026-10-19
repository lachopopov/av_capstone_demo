# ShopUNow Streamlit Demo

A minimal Streamlit UI that connects to the deployed FastAPI FAQ lookup API, which uses Chroma Cloud for retrieval.

## Run locally

From the repository root, run: uv run streamlit run deployment_probe/streamlit_app.py

Configure SHOPUNOW_DEMO_PASSWORD and SHOPUNOW_API_TOKEN in a local .env file or Streamlit secrets. SHOPUNOW_API_URL is optional and defaults to the demo FastAPI service. Keep secret values out of Git.
