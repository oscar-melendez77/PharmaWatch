import gradio as gr

from main import app
from ui import demo

# the page is served at the root of the same service, the API routes and /docs keep working
app = gr.mount_gradio_app(app, demo, path="/")
