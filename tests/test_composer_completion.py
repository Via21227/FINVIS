"""UI completion gate; no DOM authority or model requests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('FINVIS_NODE') or shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')

class ComposerCompletionTests(unittest.TestCase):
    def check(self, body):
        setup = "import assert from 'node:assert/strict';globalThis.localStorage={getItem:()=>null};globalThis.document={getElementById:()=>null};globalThis.BroadcastChannel=class {};const {futureInputComplete}=await import("+json.dumps((ROOT/'web/compound_meaning.js').as_uri())+");"
        setup += "const view={claims:[{id:'parent',flow:'future',state:'pending'},{id:'child',flow:'future',state:'pending'}]};const trial={supported_claim_ids:['parent','child'],claim_options:{child:{id:'option'}},blocked:[],unsupported:[]};"
        subprocess.run([NODE,'--input-type=module','-e',setup+body],check=True,capture_output=True,text=True,timeout=15)

    def test_supported_parent_and_component_complete(self):
        self.check("assert.equal(futureInputComplete(view,trial),true);")
    def test_missing_component_preserves_input(self):
        self.check("trial.supported_claim_ids=['parent'];assert.equal(futureInputComplete(view,trial),false);")
    def test_pending_actual_preserves_input(self):
        self.check("view.claims.push({id:'payment',flow:'review',state:'pending'});assert.equal(futureInputComplete(view,trial),false);")
    def test_completed_actual_and_supported_future_complete(self):
        self.check("view.claims.push({id:'payment',flow:'review',state:'complete'});assert.equal(futureInputComplete(view,trial),true);")
    def test_material_question_preserves_input(self):
        self.check("view.question={id:'basis'};assert.equal(futureInputComplete(view,trial),false);")
    def test_incomplete_coverage_preserves_input(self):
        self.check("view.limited_understanding=true;assert.equal(futureInputComplete(view,trial),false);")
    def test_blocked_or_unsupported_preserves_input(self):
        self.check("trial.blocked=[{claim_id:'child'}];assert.equal(futureInputComplete(view,trial),false);trial.blocked=[];trial.unsupported=[{claim_id:'other'}];assert.equal(futureInputComplete(view,trial),false);")
    def test_active_review_preserves_input(self):
        self.check("view.active_review='payment';assert.equal(futureInputComplete(view,trial),false);")
    def test_empty_or_unsupported_claim_not_success(self):
        self.check("assert.equal(futureInputComplete({claims:[]},trial),false);view.claims.push({id:'other',flow:null,state:'unsupported'});assert.equal(futureInputComplete(view,trial),false);")
    def test_completed_guard_uses_existing_exact_submission_recovery(self):
        self.check("const {InputSession}=await import("+json.dumps((ROOT/'web/ux_state.js').as_uri())+");const saved=new Map();const session=new InputSession({getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v)});let text='First';session.start(text);const done=session.completion(()=>text,()=>text='');text='Later draft';session.edited();if(futureInputComplete(view,trial))done();assert.equal(text,'Later draft');assert.deepEqual(session.recent(),['First']);")

if __name__=='__main__':unittest.main()
