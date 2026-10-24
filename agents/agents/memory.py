"""Agent memory: what each person liked, disliked and told their agent.

SQLite keeps every memory and the newest ones. Moss finds the ones relevant to a question
("how did they feel about the camera role?"). Without Moss credentials, or if Moss fails,
recall falls back to the newest memories, so the demo never depends on it.

Moss writes (add_docs) hold Python's GIL for seconds, which would freeze every request in this
service, so they run in a worker process. Loading and querying don't, and stay in-process.
"""

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

from moss import DocumentInfo, MossClient, MutationOptions, QueryOptions

from .store import Store

log = logging.getLogger(__name__)


def _write_docs(project_id: str, project_key: str, index: str, docs: list[dict]) -> str | None:
    """Runs in the worker process: upsert docs, return the Moss job id to wait on."""

    async def run() -> str | None:
        client = MossClient(project_id, project_key)
        try:
            result = await client.add_docs(index, [DocumentInfo(**d) for d in docs], MutationOptions(upsert=True))
            return getattr(result, "job_id", None)
        finally:
            await client.close()

    return asyncio.run(run())


class Memory:
    def __init__(self, store: Store, project_id: str = "", project_key: str = "", index: str = "agent-memory"):
        self.store = store
        self.index = index
        self.moss = MossClient(project_id, project_key) if project_id and project_key else None
        self._credentials = (project_id, project_key)
        self._writer: ProcessPoolExecutor | None = None
        self.ready = False  # index exists and is loaded locally
        self._reload_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self.moss is None:
            log.info("no Moss credentials: memory recall uses the newest memories only")
            return
        try:
            if self.index not in {i.name for i in await self.moss.list_indexes()}:
                # create_index needs at least one document
                seed = DocumentInfo(id="hello", text="Agent memory index.", metadata={"agent_id": "_"})
                await self.moss.create_index(self.index, [seed])
            await self.moss.load_index(self.index)
            self.ready = True
            log.info("Moss index %s loaded", self.index)
        except Exception as e:  # Moss is an enhancement; recall still works without it
            log.warning("Moss unavailable: %s", e)

    async def remember(self, agent_id: str, text: str, kind: str = "note", memory_id: str | None = None) -> str:
        memory_id = self.store.add_memory(agent_id, kind, text, memory_id)
        if self.ready:
            self._spawn(self._push(memory_id, agent_id, kind, text))
        return memory_id

    async def remember_many(self, items: list[tuple[str, str, str, str]]) -> None:
        """Bulk write of (memory_id, agent_id, kind, text), e.g. persona seeds. One Moss job, one reload."""
        for memory_id, agent_id, kind, text in items:
            self.store.add_memory(agent_id, kind, text, memory_id)
        if self.ready and items:
            self._spawn(self._push_docs([{"id": m, "text": t, "metadata": {"agent_id": a, "kind": k}} for m, a, k, t in items]))

    async def recall(self, agent_id: str, about: str, k: int = 4) -> list[str]:
        """Memories relevant to `about` (Moss), plus the newest ones (SQLite), without duplicates."""
        found: list[str] = []
        if self.ready and about.strip():
            try:
                options = QueryOptions(
                    top_k=k, filter={"$and": [{"field": "agent_id", "condition": {"$eq": agent_id}}]}
                )
                found = [d.text for d in (await self.moss.query(self.index, about, options)).docs]
            except Exception as e:
                log.warning("Moss query failed: %s", e)
        merged = found + self.store.recent_memories(agent_id, k)
        return list(dict.fromkeys(merged))[: k + 2]

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._writer is not None:
            self._writer.shutdown(wait=False, cancel_futures=True)
        if self.moss is not None:
            await self.moss.close()

    # --- Moss writes -------------------------------------------------------------

    async def _push(self, memory_id: str, agent_id: str, kind: str, text: str) -> None:
        await self._push_docs([{"id": memory_id, "text": text, "metadata": {"agent_id": agent_id, "kind": kind}}])

    async def _push_docs(self, docs: list[dict]) -> None:
        """Upsert (in the worker process), wait for Moss to index, then reload the local copy for queries."""
        try:
            self._writer = self._writer or ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"))
            job_id = await asyncio.get_running_loop().run_in_executor(
                self._writer, _write_docs, *self._credentials, self.index, docs
            )
            if job_id:
                await self.moss.wait_for_job(job_id, poll_interval_seconds=1.0, timeout_seconds=120)
            async with self._reload_lock:
                await self.moss.load_index(self.index)
        except Exception as e:
            log.warning("Moss write failed (memory is still in SQLite): %s", e)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
