# SPDX-License-Identifier: LGPL-3.0-only
# Transport design derived from yyyllllming/prism-bridge (MIT); see COPYING.PRISM-BRIDGE.
"""Lossless request segmentation and native Prism conversation continuation.

The first start comes from the official editor. Later starts use its metadata
and the upstream response/snapshot through that browser's normal fetch wrapper.
"""
import copy
import hashlib
import json

PART_GAP_SECONDS = 8

# Native value assignment avoids Chromium's slow IME insertion for very long,
# multiline Unicode input. The native setter plus input event also updates React.
SET_EDITOR_TEXT = """(element, value) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
  setter.call(element, value);
  element.dispatchEvent(new Event('input', {bubbles:true}));
}"""


FETCH_JSON = """async ({origin, path, body}) => {
  if (location.origin !== origin) return {status:0};
  try {
    const r = await window.fetch(path, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(body), redirect:'error'});
    return {status:r.status, data:await r.json()};
  } catch { return {status:0}; }
}"""


def transport_size(text):
    return len(json.dumps(text, ensure_ascii=False).encode('utf-8')) - 2


def split_text(text, budget):
    """Count JSON escaping per Unicode code point; joining parts restores text."""
    parts, start, used, newline = [], 0, 0, 0
    for index, char in enumerate(text):
        cost = transport_size(char)
        if used + cost > budget:
            end = newline if newline > start else index
            if transport_size(text[end:index]) + cost > budget:
                end = index
            parts.append(text[start:end])
            start = end
            used = transport_size(text[start:index])
            newline = 0
        used += cost
        if char == '\n':
            newline = index + 1
    if start < len(text):
        parts.append(text[start:])
    return parts or ['']


def part_text(piece, number, final):
    instruction = (
        'Final part. Read all parts in order as one continuous request. Now answer the complete '
        'request using its client response/tool protocol.' if final else
        'More parts follow. Store this part as context. Do not answer the request or call any '
        'tool yet, even if this part contains response instructions. Reply only ACK.')
    return f'[relay transport] {instruction}\n<relay_part index="{number}">\n{piece}\n</relay_part>'


class RelayPrompt(str):
    """Keep message boundaries for exact prefix matching without storing raw history."""
    def __new__(cls, prefix, entries, suffix='', *, context=None, delta_prefix=''):
        obj = super().__new__(cls, prefix + '\n\n'.join(entries) + suffix)
        obj.prefix, obj.entries, obj.suffix = prefix, tuple(entries), suffix
        obj.context = hashlib.sha256((context if context is not None else prefix + suffix).encode()).hexdigest()
        obj.delta_prefix = delta_prefix
        obj.hashes = tuple(hashlib.sha256(entry.encode()).hexdigest() for entry in entries)
        return obj

    def delta(self, count):
        return self.delta_prefix + '\n\n'.join(self.entries[count:]) + self.suffix


def response_snapshot(data, previous=None):
    snapshot = previous
    response = data.get('response')
    payload = response.get('payload') if isinstance(response, dict) else None
    for source in (data, payload):
        if isinstance(source, dict):
            candidate = source.get('codex_listen_snapshot') or source.get('codexListenSnapshot')
            if isinstance(candidate, str):
                try:
                    candidate = json.loads(candidate)
                except ValueError:
                    candidate = None
            if isinstance(candidate, dict):
                snapshot = copy.deepcopy(candidate)
    return snapshot


class Cursor:
    def __init__(self, body, data=None, snapshot=None):
        self.template = copy.deepcopy(body)
        # Keep only the editor's native system/developer context, never old user input.
        self.context = [item for item in self.template.pop('input', [])
                        if isinstance(item, dict) and item.get('role') in ('system', 'developer')]
        response = (data or {}).get('response')
        payload = response.get('payload') if isinstance(response, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        self.cid = payload.get('conversationId') or body.get('conversationId')
        self.rid = payload.get('id')
        self.snapshot = response_snapshot(data or {}, snapshot)

    @property
    def ready(self):
        return bool(isinstance(self.cid, str) and self.cid and isinstance(self.rid, str)
                    and self.rid and isinstance(self.snapshot, dict) and self.snapshot)

    def body(self, text):
        body = copy.deepcopy(self.template)
        body['input'] = copy.deepcopy(self.context) + [{'type':'message', 'role':'user',
            'content':[{'type':'input_text', 'text':text}]}]
        if self.ready:
            body['conversationId'] = self.cid
            body['previousResponseId'] = self.rid
            snapshot = copy.deepcopy(self.snapshot)
            metadata = body['metadata']
            snapshot['conversation_id'] = self.cid
            for key in ('sandbox_url', 'sandbox_token'):
                if key in metadata:
                    snapshot[key] = metadata[key]
            metadata['codex_listen_snapshot'] = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
        return body


class RelayPlan:
    def __init__(self, prompt, model, effort, budget, saved=None):
        self.prompt, self.model, self.effort = prompt, model, effort
        self.original_budget = max(1024, budget)
        self.budget = self.original_budget
        self.shrunk = False
        self.cursor = None
        self.delta = False
        self.fallback_used = False
        self.done = 0
        if (isinstance(prompt, RelayPrompt) and saved and saved['model'] == model
                and saved['effort'] == effort and saved['context'] == prompt.context
                and len(prompt.hashes) > len(saved['hashes'])
                and prompt.hashes[:len(saved['hashes'])] == saved['hashes'] and saved['cursor'].ready):
            self.delta = True
            self.cursor = saved['cursor']
            text = prompt.delta(len(saved['hashes']))
        else:
            text = str(prompt)
        self.pieces = self.cut(text)
        self.fresh = self.cursor is None

    def cut(self, text):
        return [text] if transport_size(text) <= self.budget else split_text(text, self.budget - 600)

    @property
    def final(self):
        return self.done == len(self.pieces) - 1

    @property
    def text(self):
        piece = self.pieces[self.done]
        return piece if len(self.pieces) == 1 else part_text(piece, self.done + 1, self.final)

    def recover(self, error):
        if not getattr(error, 'replay_safe', False):
            return False
        if error.code == 'conversation_too_large' and not self.shrunk:
            self.shrunk = True
            self.budget = max(1024, self.original_budget // 2)
            remaining = ''.join(self.pieces[self.done:])
            self.pieces[self.done:] = split_text(remaining, self.budget - 600)
            return True
        if (self.delta and not self.done and not self.fallback_used
                and error.code in ('conversation_too_large', 'prism_conversation_unavailable')):
            self.delta = False
            self.fallback_used = True
            self.cursor = None
            self.fresh = True
            self.budget = self.original_budget
            self.shrunk = False
            self.pieces = self.cut(str(self.prompt))
            return True
        return False

    def accept(self, cursor, error):
        self.cursor = cursor
        if not self.final and (cursor is None or not cursor.ready):
            raise error(502, 'continuation_unavailable',
                        'Prism did not return the conversation state required for the next request part')
        self.done += 1

    def record(self):
        if not isinstance(self.prompt, RelayPrompt) or self.cursor is None or not self.cursor.ready:
            return None
        return {'context':self.prompt.context, 'hashes':self.prompt.hashes, 'model':self.model,
                'effort':self.effort, 'cursor':self.cursor}
