# SPDX-License-Identifier: LGPL-3.0-only
"""Exercise relay through real Chromium, local Prism fixture and adapter HTTP."""
import argparse
import json
import re
import tempfile
import threading
import uuid
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, ProxyHandler, build_opener

import server as api
import multiplex_browser
from multiplex_browser import MultiplexBrowser
from multiplex_runtime import AsyncBrowserWorker
from smoke_model_fixture import PICKER
from relay_transport import transport_size


PAGE = '''<button onclick="document.querySelector('[role=menuitem]').hidden=false">New</button>
<button role="menuitem" hidden onclick="location.href='/?u='+crypto.randomUUID()">Blank project</button>
<button onclick="window.cid=crypto.randomUUID()">New chat tab</button>
<textarea placeholder="Ask anything"></textarea>
<script>
window.cid=crypto.randomUUID();
window.SentinelSDK={token:()=>Promise.resolve('fixture')};
const originalFetch=window.fetch;
window.fetch=(...args)=>originalFetch(...args);
document.querySelector('textarea').addEventListener('keydown', async (e)=>{
 if(e.key!=='Enter')return; e.preventDefault();
 const start=await fetch('/api/llm/response_with_tools_start',{method:'POST',body:JSON.stringify({
   conversationId:window.cid,
   metadata:{model:currentModel,reasoning_effort:currentEffort,
     projectId:new URL(location.href).searchParams.get('u'),sandbox_token:'fixture-runtime',
     codex_listen_snapshot:JSON.stringify({conversation_id:window.cid,transcript_cursor:0})},
   input:[{type:'message',role:'user',content:[{type:'input_text',text:e.target.value}]}]
 })}).then(r=>r.json());
 if(start.request_id && start.status==='started')
   await fetch('/api/llm/response_with_tools_status',{method:'POST',body:JSON.stringify(start)});
});
</script>''' + PICKER


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body, content_type='application/json'):
        raw = body.encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(200,PAGE,'text/html')

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with self.server.lock:
            if self.path=='/api/projects':
                self.reply(200,json.dumps({'uuid':body['project_uuid']}))
                return
            if self.path==api.STATUS:
                self.reply(200,json.dumps(self.server.jobs[body['request_id']]))
                return
            assert self.path==api.START
            metadata=body['metadata']
            assert (metadata['model'],metadata['reasoning_effort'])==('gpt-6-luna','xhigh')
            text=body['input'][-1]['content'][0]['text']
            cid=body['conversationId']
            conv=self.server.conversations[cid]
            if self.server.expire_next and body.get('previousResponseId'):
                self.server.expire_next=False
                self.reply(409,json.dumps({'error':{'code':'invalid_previous_response_id'}}))
                return
            if conv['rid']:
                snapshot=json.loads(metadata['codex_listen_snapshot'])
                assert body.get('previousResponseId')==conv['rid'], 'response id was not continued'
                assert snapshot.get('transcript_cursor')==conv['count'], 'snapshot was not continued'
                assert snapshot.get('codex_session_id')==cid, 'native model session was lost'
            if self.server.fail_second and conv['count']==1:
                self.reply(502,json.dumps({'error':{'code':'server_error'}}))
                return
            if self.server.reject_first and not conv['count'] and transport_size(text)>43000:
                self.server.reject_first=False
                self.reply(422,json.dumps({'error':{'code':'conversation_too_large'}}))
                return
            if self.server.reject_second and conv['count']==1 and transport_size(text)>43000:
                self.server.reject_second=False
                self.server.refusals+=1
                self.reply(422,json.dumps({'status':'failed','response':{'status':'error',
                    'payload':{'reason':'conversation_too_large'}}}))
                return
            self.server.accepted.append({'cid':cid,'text':text,'model':metadata['model'],'effort':metadata['reasoning_effort']})
            wrapped=text.startswith('[relay transport]')
            piece=text.split('>\n',1)[1].rsplit('\n</relay_part>',1)[0] if wrapped else text
            conv['text']+=piece
            conv['count']+=1
            rid=uuid.uuid4().hex
            conv['rid']=rid
            final=not wrapped or 'Final part.' in text.split('\n',1)[0]
            markers=re.findall(r'PRISM_CLIENT_TOOLS_V1:[a-f0-9]+',conv['text'])
            if not final:
                answer='ACK'
            elif markers:
                result=({'kind':'final','text':'client-value received'} if 'CLIENT_TOOL_RESULT' in piece else
                    {'kind':'calls','calls':[{'name':'lookup','arguments':{'key':'fixture'}}]})
                answer=markers[-1]+'\n'+json.dumps(result)
            else:
                answer='complete'
            snapshot={'conversation_id':cid,'codex_session_id':cid,'transcript_cursor':conv['count']}
            request_id=uuid.uuid4().hex
            terminal={'request_id':request_id,'status':'completed','response':{'status':'success','payload':{
                'id':rid,'conversationId':cid,'codex_listen_snapshot':snapshot,
                'output':[{'type':'message','content':[{'text':answer}]}]}}}
            self.server.jobs[request_id]=terminal
            self.reply(200,json.dumps({'request_id':request_id,'turn_state':'fixture-state','status':'started'}))


def run_mode(mode, chrome):
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    upstream.daemon_threads=True
    upstream.lock=threading.Lock()
    upstream.jobs={}
    upstream.conversations=defaultdict(lambda:{'text':'','count':0,'rid':None})
    upstream.accepted=[]
    upstream.reject_second=upstream.fail_second=False
    upstream.expire_next=upstream.reject_first=False
    upstream.refusals=0
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    api.BASE=f'http://127.0.0.1:{upstream.server_port}'
    api.PART_GAP_SECONDS=multiplex_browser.PART_GAP_SECONDS=0
    try:
        with tempfile.TemporaryDirectory() as directory:
            state=api.State(directory)
            if mode=='browser':
                worker=api.BrowserWorker(lambda:api.BrowserTurn(state,chrome))
            else:
                worker=AsyncBrowserWorker(lambda:MultiplexBrowser(state,chrome,api,poll_seconds=0.01),api)
            handler=type('RelayHandler',(api.Handler,),{'browser_turn':worker,'serialize_requests':mode=='browser',
                'tool_state':api.ToolState(directory,api.AdapterError)})
            gateway=ThreadingHTTPServer(('127.0.0.1',0),handler)
            gateway.daemon_threads=True
            threading.Thread(target=gateway.serve_forever,daemon=True).start()
            opener=build_opener(ProxyHandler({}))
            try:
                catalog='COMPLETE_TOOL_DESCRIPTION:'+'x'*50000
                payload={'model':'gpt-6-luna','reasoning':{'effort':'xhigh'},'stream':True,
                    'tools':[{'type':'function','name':'lookup','description':catalog,
                        'parameters':{'type':'object','properties':{'key':{'type':'string'}},'required':['key']}}],
                    'input':[{'role':'user','content':'COMPLETE_HISTORY:'+'y'*70000}]}
                headers={'X-Prism-Account-ID':'300','X-Prism-OAuth-Token':'fixture-oauth',
                    'X-Prism-Caller-ID':'b'*64,'X-Prism-Session-ID':'a'*64,'Content-Type':'application/json'}
                def send():
                    req=Request(f'http://127.0.0.1:{gateway.server_port}/v1/responses',
                                data=json.dumps(payload).encode(),headers=headers)
                    with opener.open(req,timeout=60) as response:
                        raw=response.read().decode()
                    if payload['stream']:
                        events=[json.loads(line[6:]) for line in raw.splitlines() if line.startswith('data: ')]
                        assert events[-1]['type']=='response.completed'
                        assert 'ACK' not in raw
                        return events[-1]['response']
                    return json.loads(raw)
                first=send()
                assert first['output'][0]['type']=='function_call',first
                assert len(upstream.accepted)==2, len(upstream.accepted)
                cid=upstream.accepted[0]['cid']
                assert upstream.accepted[1]['cid']==cid
                assert catalog in upstream.conversations[cid]['text']
                assert payload['input'][0]['content'] in upstream.conversations[cid]['text']
                before=len(upstream.accepted)
                payload['input']+=first['output']+[{'type':'function_call_output',
                    'call_id':first['output'][0]['call_id'],'output':'client-value'}]
                payload['stream']=False
                second=send()
                assert second['output'][0]['content'][0]['text']=='client-value received',second
                assert len(upstream.accepted)==before+1
                assert upstream.accepted[-1]['cid']==cid
                assert 'COMPLETE_TOOL_DESCRIPTION' not in upstream.accepted[-1]['text']
                assert 'COMPLETE_HISTORY' not in upstream.accepted[-1]['text']
                upstream.expire_next=True
                payload['input'] += [{'role':'assistant','content':'client-value received'},
                                     {'role':'user','content':'Continue after expiration'}]
                before=len(upstream.accepted)
                recovered=send()
                assert recovered['output'][0]['content'][0]['text']=='client-value received'
                assert len(upstream.accepted)>before+1
                assert upstream.accepted[-1]['cid']!=cid
                assert catalog in upstream.conversations[upstream.accepted[-1]['cid']]['text']
                # A different caller using the same external session starts from its full input.
                payload['input']=[payload['input'][0]]
                headers['X-Prism-Caller-ID']='c'*64
                third=send()
                assert third['output'][0]['type']=='function_call'
                assert upstream.accepted[-1]['cid']!=cid
                before=len(upstream.accepted)
                upstream.reject_second=True
                prompt,_=api.parse_prompt({'model':'gpt-6-luna','input':'UNCHANGED:'+('😀"\\\n'*18000)})
                _,answer=worker.run('300','fixture-oauth',prompt,'d'*64,'gpt-6-luna','xhigh')
                assert answer=='complete'
                assert upstream.refusals==1
                accepted=upstream.accepted[before:]
                assert len(accepted)>2
                content=''.join(turn['text'].split('>\n',1)[1].rsplit('\n</relay_part>',1)[0] for turn in accepted)
                assert content==prompt, 'accepted prefix was resent or content was lost'
                assert all(transport_size(turn['text'])<=43000 for turn in accepted[1:])
                assert not list(state.pending.iterdir())
                upstream.reject_first=True
                before=len(upstream.accepted)
                _,answer=worker.run('300','fixture-oauth',prompt,'f'*64,'gpt-6-luna','xhigh')
                assert answer=='complete' and not upstream.reject_first
                assert all(transport_size(turn['text'])<=43000 for turn in upstream.accepted[before:])
                assert not list(state.pending.iterdir())
                # A transport failure after ACK must not replay the earlier part.
                upstream.fail_second=True
                before=len(upstream.accepted)
                try:
                    worker.run('300','fixture-oauth',prompt,'e'*64,'gpt-6-luna','xhigh')
                    raise AssertionError('unknown result was treated as success')
                except api.AdapterError as error:
                    assert not error.not_submitted
                assert len(upstream.accepted)==before+1
                assert list(state.pending.iterdir())
                print(json.dumps({'relay_smoke':mode,'result':'passed','full_tools':True,
                    'native_snapshot':True,'incremental':True,'caller_isolation':True,
                    'size_retry_without_prefix_replay':True,'unknown_result_retained':True,'real_upstream':False}),flush=True)
            finally:
                gateway.shutdown()
                gateway.server_close()
                worker.close()
    finally:
        upstream.shutdown()
        upstream.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome',required=True)
    args=parser.parse_args()
    for mode in ('browser','multiplex'):
        run_mode(mode,args.chrome)
