import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from company_assistant.security import topic_guard
from company_assistant.security.topic_guard import (
    REVIEW_INSTRUCTION,
    SCOPE_REFUSAL,
    STEP_REFUSAL,
    TopicGuard,
)

pytestmark = pytest.mark.integration


class AllowingModel:
    model = "cohere_chat/test-model"

    def __init__(self):
        self.payloads = []

    async def generate_content_async(self, request, stream=False):
        self.payloads.append(json.loads(request.contents[0].parts[0].text))
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text='{"allowed": true}')])
        )


def context():
    return SimpleNamespace(agent_name="company_assistant", invocation_id="i", state={})


def test_guard_knows_the_companys_own_clients(seeded):
    names = topic_guard.company_client_names()
    assert {"Acme", "Delta", "Juniper"} <= set(names)
    assert len(names) == 10


async def test_every_scope_review_carries_the_client_list(seeded):
    model = AllowingModel()
    guard = TopicGuard(model, "client_project_agent", Mock(return_value=None))
    assert await guard._review(context(), "what projects does Delta have", "input") is True
    assert await guard._review(context(), "Delta has 3 projects", "output", "Delta has 3") is True
    for payload in model.payloads:
        assert "Delta" in payload["company_clients"]
        assert "Juniper" in payload["company_clients"]


def test_client_list_is_cached_and_fails_safe(seeded, monkeypatch):
    calls = []
    real_query = topic_guard.query

    def counting_query(statement, parameters=()):
        calls.append(statement)
        return real_query(statement, parameters)

    monkeypatch.setattr(topic_guard, "query", counting_query)
    topic_guard._CLIENT_CACHE.clear()
    topic_guard.company_client_names()
    topic_guard.company_client_names()
    assert len(calls) == 1

    def broken(statement, parameters=()):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(topic_guard, "query", broken)
    topic_guard._CLIENT_CACHE.clear()
    assert topic_guard.company_client_names() == []


def test_instruction_keeps_clients_in_scope_and_outsiders_out():
    assert "company_clients" in REVIEW_INSTRUCTION
    assert "even when" in REVIEW_INSTRUCTION and "real-world business" in REVIEW_INSTRUCTION
    assert "outside organization's own" in REVIEW_INSTRUCTION
    assert "what projects does Acme have" in REVIEW_INSTRUCTION
    assert "tell me about the employee" in REVIEW_INSTRUCTION
    assert "another company or organization is never in scope" not in REVIEW_INSTRUCTION


def state_context(agent, state):
    return SimpleNamespace(agent_name=agent, invocation_id="i", state=state)


async def test_a_specialist_check_sees_the_question_that_was_already_approved(seeded):
    model = AllowingModel()
    approved = "How many people left in 2025 and 2026, and who has been on the bench longest?"
    state = {"scope_context:company_assistant": approved}
    guard = TopicGuard(model, "workforce_agent", Mock(return_value=None))
    task = "Answer each of these requests: (1) exits in 2025; (2) the longest bench stay."
    assert await guard._review(state_context("workforce_agent", state), task, "input") is True
    assert (
        await guard._review(state_context("workforce_agent", state), task, "output", "ok") is True
    )
    assert [p["approved_user_question"] for p in model.payloads] == [approved, approved]


async def test_the_coordinator_check_has_no_approved_question_of_its_own(seeded):
    model = AllowingModel()
    guard = TopicGuard(model, "company_assistant", Mock(return_value=None))
    state = {"scope_context:company_assistant": "an earlier question"}
    await guard._review(state_context("company_assistant", state), "headcount", "input")
    assert model.payloads[0]["approved_user_question"] is None


async def test_a_specialist_without_an_approved_question_gets_none(seeded):
    model = AllowingModel()
    guard = TopicGuard(model, "workforce_agent", Mock(return_value=None))
    await guard._review(state_context("workforce_agent", {}), "headcount", "input")
    assert model.payloads[0]["approved_user_question"] is None


def test_the_instruction_lets_a_long_step_of_an_approved_question_through():
    assert "approved_user_question" in REVIEW_INSTRUCTION
    assert "even when the\nstep is long or lists several items" in REVIEW_INSTRUCTION
    assert "reject only work that is unrelated to it" in REVIEW_INSTRUCTION


def test_a_blocked_step_says_so_instead_of_looking_like_an_out_of_scope_question():
    gate = Mock(return_value=None)
    nested = TopicGuard(AllowingModel(), "workforce_agent", gate)
    top = TopicGuard(AllowingModel(), "company_assistant", gate)
    blocked_step = nested._reject(state_context("workforce_agent", {}), "out_of_scope_input")
    refused_question = top._reject(state_context("company_assistant", {}), "out_of_scope_input")
    assert blocked_step.parts[0].text == STEP_REFUSAL
    assert refused_question.parts[0].text == SCOPE_REFUSAL
    assert "One part of your request" in STEP_REFUSAL
    assert "only help" not in STEP_REFUSAL
