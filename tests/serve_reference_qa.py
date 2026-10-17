"""Isolated local GUI fixture; never contacts source or judgment services."""
import sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from game_stack_planner.api import GameStackApplication
from game_stack_planner.model_catalog import ModelCatalog
from game_stack_planner.model_oauth import SketchfabOAuth
from game_stack_planner.server import create_server
from test_model_discovery import MODEL,Provider
from test_model_reference_auth import MemoryStore
with tempfile.TemporaryDirectory(prefix='fafnir-ui-qa-') as folder:
    app=GameStackApplication(Path(folder)/'qa.db');provider=Provider();provider.value.update(title='[QA fixture] Worn metal pipe',thumbnail=None)
    app._model_catalog=ModelCatalog(Path(folder)/'qa.db',providers={'polyhaven':provider});app._model_catalog.oauth=SketchfabOAuth(store=MemoryStore());app._model_catalog.detail(MODEL['id'])
    server=create_server(app,port=18783)
    try:server.serve_forever()
    finally:server.server_close();app.close()
