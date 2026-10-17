"""Manual QA server using an isolated DB, without automatic RAG/model downloads."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from game_stack_planner.api import GameStackApplication
from game_stack_planner.server import create_server

app = GameStackApplication(Path(__file__).resolve().parents[1] / ".model-qa.sqlite3")
server = create_server(app, port=18779)
try:
    server.serve_forever()
finally:
    server.server_close()
    app.close()
