# SPDX-License-Identifier: LGPL-3.0-only
import json
import unittest
from types import SimpleNamespace

from test_server import adapter
from test_tool_bridge import FUNCTION, request
from tool_bridge import ToolBridge
from relay_transport import Cursor, RelayPlan, RelayPrompt, part_text, split_text, transport_size


def cursor(cid='conversation-one', rid='response-one'):
    return Cursor({'conversationId':cid, 'input':[], 'metadata':{'model':'gpt-6-luna',
        'reasoning_effort':'xhigh', 'projectId':'project', 'sandbox_token':'fixture-token'}},
        {'response':{'payload':{'id':rid, 'conversationId':cid,
            'codex_listen_snapshot':{'conversation_id':cid, 'codex_session_id':'session', 'transcript_cursor':42}}}})


def too_large():
    return adapter.terminal_text({'status':'failed', 'response':{'status':'error',
        'payload':{'reason':'conversation_too_large'}}})


class RelayTransportTests(unittest.TestCase):
    def test_unicode_escaping_and_long_lines_split_without_loss(self):
        for text in ('中😀\\"\n\t\x00' * 5000, 'x' * 128000, 'a\n' + '😀' * 500, '\n' * 2000):
            for budget in (1024, 4096, 86000):
                with self.subTest(chars=len(text), budget=budget):
                    parts = split_text(text, budget)
                    self.assertEqual(''.join(parts), text)
                    self.assertTrue(all(transport_size(part) <= budget for part in parts))
                    plan = RelayPlan(text, 'gpt-6-luna', 'xhigh', budget)
                    for i in range(len(plan.pieces)):
                        plan.done = i
                        self.assertLessEqual(transport_size(plan.text), budget)

    def test_explicit_size_refusal_only_recuts_undelivered_suffix(self):
        prompt = 'first\n' + '中😀"\\' * 25000
        plan = RelayPlan(prompt, 'gpt-6-luna', 'xhigh', 86000)
        accepted = plan.pieces[0]
        state = cursor()
        plan.accept(state, adapter.AdapterError)
        self.assertTrue(plan.recover(too_large()))
        self.assertEqual(plan.budget, 43000)
        self.assertIs(plan.cursor, state)
        self.assertEqual(plan.pieces[0], accepted)
        self.assertEqual(''.join(plan.pieces), prompt)
        for i in range(plan.done, len(plan.pieces)):
            plan.done = i
            self.assertLessEqual(transport_size(plan.text), 43000)
        self.assertFalse(plan.recover(too_large()))

    def test_unknown_failure_or_partial_output_never_replays(self):
        plan = RelayPlan('hello', 'gpt-6-luna', 'xhigh', 86000)
        for error in (adapter.AdapterError(502, 'unknown_outcome', 'fixture'),
                      adapter.terminal_text({'status':'failed', 'response':{'status':'error','payload':{
                          'reason':'conversation_too_large','output':[{'type':'message'}]}}})):
            self.assertFalse(plan.recover(error))
        self.assertEqual(plan.pieces, ['hello'])

    def test_minimum_segment_budget_does_not_create_an_unbounded_retry_loop(self):
        plan = RelayPlan('hello', 'gpt-6-luna', 'xhigh', 1024)
        self.assertTrue(plan.recover(too_large()))
        self.assertFalse(plan.recover(too_large()))

    def test_cursor_preserves_native_context_snapshot_and_requested_options(self):
        state = cursor()
        body = state.body('next')
        self.assertEqual((body['conversationId'], body['previousResponseId']), ('conversation-one', 'response-one'))
        snapshot = json.loads(body['metadata']['codex_listen_snapshot'])
        self.assertEqual((snapshot['codex_session_id'], snapshot['transcript_cursor']), ('session',42))
        self.assertEqual((body['metadata']['model'], body['metadata']['reasoning_effort']), ('gpt-6-luna','xhigh'))
        self.assertEqual(body['input'][0]['content'][0]['text'], 'next')
        body['metadata']['model'] = 'changed'
        self.assertEqual(state.body('again')['metadata']['model'], 'gpt-6-luna')

    def test_tool_catalog_is_not_repeated_and_new_marker_is_used_on_continuation(self):
        payload = request(model='gpt-6-luna', tools=[dict(FUNCTION,description='large-catalog' * 9000)])
        first = ToolBridge(payload,adapter)
        plan = RelayPlan(first.prompt, 'gpt-6-luna','xhigh',86000)
        plan.cursor = cursor()
        saved = plan.record()
        payload['input'] += [{'role':'assistant','content':'answer'}, {'role':'user','content':'follow-up'}]
        second = ToolBridge(payload,adapter)
        delta = RelayPlan(second.prompt,'gpt-6-luna','xhigh',86000,saved)
        self.assertTrue(delta.delta)
        self.assertNotIn('large-catalog', delta.text)
        self.assertIn(second.marker, delta.text)
        self.assertNotIn(first.marker, delta.text)
        self.assertIn('follow-up',delta.text)
        self.assertEqual(len(delta.pieces),1)
        for model, effort in (('gpt-5.6-sol','xhigh'),('gpt-6-luna','high')):
            self.assertFalse(RelayPlan(second.prompt,model,effort,86000,saved).delta)
        changed = ToolBridge(request(model='gpt-6-luna',tools=[FUNCTION]),adapter)
        self.assertFalse(RelayPlan(changed.prompt,'gpt-6-luna','xhigh',86000,saved).delta)

    def test_identical_or_changed_history_starts_fresh(self):
        prompt = RelayPrompt('rules', ['first','second'])
        plan = RelayPlan(prompt,'gpt-6-luna','xhigh',86000)
        plan.cursor = cursor()
        saved = plan.record()
        self.assertFalse(RelayPlan(prompt,'gpt-6-luna','xhigh',86000,saved).delta)
        self.assertFalse(RelayPlan(RelayPrompt('rules',['different','second','third']),
                                  'gpt-6-luna','xhigh',86000,saved).delta)

    def test_expired_continuation_replays_full_only_before_any_part_is_accepted(self):
        prompt = RelayPrompt('rules', ['first'])
        first = RelayPlan(prompt,'gpt-6-luna','xhigh',86000)
        first.cursor = cursor()
        expanded = RelayPrompt('rules',['first','second'])
        plan = RelayPlan(expanded,'gpt-6-luna','xhigh',86000,first.record())
        error = adapter.terminal_text({'error':{'code':'invalid_previous_response_id'}})
        self.assertTrue(plan.recover(error))
        self.assertEqual(''.join(plan.pieces), expanded)
        self.assertIsNone(plan.cursor)
        self.assertFalse(plan.recover(error))
        plan = RelayPlan(RelayPrompt('rules',['first','x'*180000]),'gpt-6-luna','xhigh',86000,first.record())
        plan.accept(cursor(),adapter.AdapterError)
        self.assertFalse(plan.recover(error))

    def test_size_refusal_without_request_id_is_terminal_and_retryable(self):
        for data in ({'error':{'code':'conversation_too_large'}},
                     {'status':'error','reason':'conversation_too_large'},
                     {'response':{'payload':{'reason':'conversation_too_large'}}}):
            error = adapter.terminal_text(data)
            self.assertEqual(error.code,'conversation_too_large')
            self.assertTrue(error.replay_safe)
        self.assertIsNone(adapter.terminal_text({'status':'running','reason':'conversation_too_large'}))
