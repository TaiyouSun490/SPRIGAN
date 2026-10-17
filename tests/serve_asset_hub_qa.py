"""Short-lived, isolated GUI fixture: no accounts, external downloads or generation charges."""
from pathlib import Path
import os
import tempfile
import threading
import wave
from game_stack_planner.api import GameStackApplication
from game_stack_planner.asset_hub import AssetHub
from game_stack_planner.server import create_server
from test_asset_hub import Upstream


def main():
    with tempfile.TemporaryDirectory(prefix="fafnir-hub-qa-") as temporary:
        root=Path(temporary)
        os.environ["LOCALAPPDATA"]=str(root/"appdata")
        app=GameStackApplication(root/"catalog.db")
        app._asset_hub=AssetHub(app.repository.path,upstream=Upstream(),cache_root=root/"cache")
        assets=root/"owned";assets.mkdir()
        with wave.open(str(assets/"fixture.wav"),"wb") as sound:
            sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(16000);sound.writeframes(b"\0\0"*1600)
        folder=app._asset_hub.register_root({"source":"booth","path":str(assets)})
        app._asset_hub.import_owned({"source":"booth","owned_confirmation":True,"items":[{"title":"Forest footsteps — QA fixture", "url":"https://booth.pm/items/123", "type":"sfx", "root_id":folder["id"], "file":"fixture.wav"}]})
        server=create_server(app,port=0)
        print(f"http://127.0.0.1:{server.server_port}/hub.html",flush=True)
        timer=threading.Timer(300,server.shutdown);timer.daemon=True;timer.start()
        try:server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:timer.cancel();server.server_close();app.close()


if __name__=="__main__":main()
