"""Broker: asks agents in parallel whether a plan fits and collects their replies.

An agent gets a question one of three ways:
- callback_url set: the coordinator POSTs the question there. The agent may answer in the
  response body ({"answer": "yes"}) or later via POST /agents/{id}/reply.
- no callback_url: the question waits in GET /agents/{id}/questions until the agent replies.
- auto_reply (seeded personas with no live agent): answered "yes" immediately.
No answer within the timeout counts as "no".
"""

import asyncio
import logging
import uuid

import httpx

from .models import Agent, Question, Reply

log = logging.getLogger(__name__)


class Broker:
    def __init__(self, http: httpx.AsyncClient, timeout: float = 20.0):
        self.http = http
        self.timeout = timeout
        self._open: dict[str, tuple[Question, asyncio.Future[Reply]]] = {}

    def questions_for(self, agent_id: str) -> list[Question]:
        return [q for q, _ in self._open.values() if q.agent_id == agent_id]

    def reply(self, agent_id: str, reply: Reply) -> bool:
        """Resolve an open question. Without question_id, answers the agent's oldest open question."""
        qid = reply.question_id or next((q.question_id for q in self.questions_for(agent_id)), None)
        entry = self._open.get(qid) if qid else None
        if entry is None or entry[0].agent_id != agent_id:
            return False
        if not entry[1].done():
            entry[1].set_result(reply.model_copy(update={"question_id": qid}))
        return True

    async def ask(self, agent: Agent, question: str, plan: dict | None = None) -> Reply:
        q = Question(question_id=uuid.uuid4().hex[:12], agent_id=agent.agent_id, question=question, plan=plan)
        if agent.auto_reply:
            return Reply(question_id=q.question_id, answer="yes", note="seeded persona")

        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._open[q.question_id] = (q, future)
        try:
            if agent.callback_url:
                await self._push(agent, q)
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError:
            return Reply(question_id=q.question_id, answer="no", note="no reply in time")
        finally:
            self._open.pop(q.question_id, None)

    async def ask_all(self, agents: list[Agent], question: str, plan: dict | None = None) -> dict[str, Reply]:
        replies = await asyncio.gather(*(self.ask(a, question, plan) for a in agents))
        return {a.agent_id: r for a, r in zip(agents, replies)}

    async def _push(self, agent: Agent, q: Question) -> None:
        try:
            resp = await self.http.post(agent.callback_url, json=q.model_dump(), timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json() if resp.content else None
            if isinstance(body, dict) and "answer" in body:
                self.reply(agent.agent_id, Reply.model_validate({**body, "question_id": q.question_id}))
        except (httpx.HTTPError, ValueError) as e:  # ValueError: bad JSON or an invalid reply
            log.warning("callback to %s failed: %s", agent.agent_id, e)
            # the agent can still answer via /reply before the timeout
