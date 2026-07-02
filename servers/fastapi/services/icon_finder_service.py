import asyncio
import json
import os
import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2


# Cap concurrent ONNX embedding inferences. Each search_icons call runs the
# MiniLM embedder in a worker thread, but tokenisation is pure-Python and
# holds the GIL. Under load (many presentations fetching assets at once)
# dozens of these fire simultaneously, starving the event loop — the
# heartbeat coroutine never gets scheduled, so `updated_at` stops advancing
# and the external watchdog kills the task as "silent". Serialising icon
# search to a small number keeps the loop responsive. 0/unset → default 2.
_ICON_SEARCH_CONCURRENCY = int(os.getenv("ICON_SEARCH_CONCURRENCY", "2") or 2)
_icon_search_semaphore: asyncio.Semaphore | None = None


def _get_icon_search_semaphore() -> asyncio.Semaphore:
    # Lazily created so it binds to the running event loop, not import time.
    global _icon_search_semaphore
    if _icon_search_semaphore is None:
        _icon_search_semaphore = asyncio.Semaphore(max(1, _ICON_SEARCH_CONCURRENCY))
    return _icon_search_semaphore


class IconFinderService:
    def __init__(self):
        self.collection_name = "icons"
        self.client = chromadb.PersistentClient(
            path="chroma", settings=Settings(anonymized_telemetry=False)
        )
        print("Initializing icons collection...")
        self._initialize_icons_collection()
        print("Icons collection initialized.")

    def _initialize_icons_collection(self):
        self.embedding_function = ONNXMiniLM_L6_V2()
        self.embedding_function.DOWNLOAD_PATH = "chroma/models"
        self.embedding_function._download_model_if_not_exists()
        try:
            self.collection = self.client.get_collection(
                self.collection_name, embedding_function=self.embedding_function
            )
        except Exception:
            with open("assets/icons.json", "r") as f:
                icons = json.load(f)

            documents = []
            ids = []

            for i, each in enumerate(icons["icons"]):
                if each["name"].split("-")[-1] == "bold":
                    doc_text = f"{each['name']} {each['tags']}"
                    documents.append(doc_text)
                    ids.append(each["name"])

            if documents:
                self.collection = self.client.create_collection(
                    name=self.collection_name,
                    embedding_function=self.embedding_function,
                    metadata={"hnsw:space": "cosine"},
                )
                self.collection.add(documents=documents, ids=ids)

    async def search_icons(self, query: str, k: int = 1):
        async with _get_icon_search_semaphore():
            result = await asyncio.to_thread(
                self.collection.query,
                query_texts=[query],
                n_results=k,
            )
        return [f"/static/icons/bold/{each}.svg" for each in result["ids"][0]]


ICON_FINDER_SERVICE = IconFinderService()
