"""Frozen-result Why presentation and read-only question routing regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
NODE=os.environ.get('FINVIS_NODE') or shutil.which('node') or str(Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')

class WhyDialogTests(unittest.TestCase):
    def check_js(self, body, language='en'):
        setup="""import assert from 'node:assert/strict';
class Element {constructor(){this.children=[];this.dataset={};this.textContent='';}append(...children){this.children.push(...children);}get content(){return this.textContent+' '+this.children.map(c=>c.content||c.textContent||'').join(' ');}}
globalThis.localStorage={getItem:()=>LANG};globalThis.document={getElementById:()=>null,createElement:()=>new Element()};globalThis.BroadcastChannel=class {};
""".replace('LANG',json.dumps(language))
        setup += 'const {requestedMonths,explanationGroups,renderSimulationWhy}=await import('+json.dumps((ROOT/'web/simulation_why.js').as_uri())+');'
        setup += 'const {explanationOnlyQuestion}=await import('+json.dumps((ROOT/'web/future_explanation.js').as_uri())+');'
        setup += """const interval=n=>({lower_minor:n,upper_minor:n});const item=(title,amount)=>({title,label:title,effect_type:'cost',amount:interval(amount)});
const out={items:[],buckets:[{currency:'EUR',after:interval(30000)}],timing:{currencies:[{currency:'EUR',starting_known:true,conditional_inflow:true,unknown_count:1,undated:[{}],groups:[{when:'2028-11',items:[item('Workshop',120000)],known_change:interval(-120000),balance:interval(30000),approximate:false},{when:'2029-11-15',items:[item('Course',20000)],known_change:interval(-20000),balance:interval(10000)}]},{currency:'JPY',starting_known:false,groups:[{when:'2028-11',items:[item('Supplies',700)],known_change:interval(-700),balance:null}]}]}};const host=new Element();"""
        r=subprocess.run([NODE,'--input-type=module','-e',setup+body],capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_standalone_questions_en_zh(self):
        self.check_js("for(const q of ['Why does my balance change in November?','Why did I pay more?','Which factor is largest?','为什么十一月余额变了？','我应该买吗？'])assert.ok(explanationOnlyQuestion(q),q);")
    def test_mixed_actuals_are_not_swallowed(self):
        self.check_js("for(const q of ['I paid EUR 100. Why did it change?','Why did it change? I paid EUR 100.','Why did it change, and I paid EUR 100 for a lamp','为什么余额变了，我支付了100元','Why does it change; please update my balance'])assert.equal(explanationOnlyQuestion(q),false,q);")
    def test_unrelated_statements_not_routed(self):
        self.check_js("for(const q of ['I bought a camera','Pay in November','The main impact is spending','I should buy a lamp'])assert.equal(explanationOnlyQuestion(q),false);")
    def test_english_chinese_and_iso_months(self):
        self.check_js("for(const q of ['Why November?','为什么11月变化？','为什么十一月变化？','Why 2028-11?'])assert.deepEqual(requestedMonths(q),[11]);")
    def test_month_across_years_remains_explicit(self):
        self.check_js("assert.deepEqual(explanationGroups(out,'Why November?').map(x=>x.group.when),['2028-11','2029-11-15','2028-11']);assert.deepEqual(explanationGroups(out,'Why November 2028?').map(x=>x.group.when),['2028-11','2028-11']);")
    def test_month_projection_reuses_frozen_values_without_mutation(self):
        self.check_js("const before=JSON.stringify(out);renderSimulationWhy(host,out,'Why November 2028?');assert.match(host.content,/Workshop/);assert.doesNotMatch(host.content,/Course/);assert.match(host.content,/EUR -1,200/);assert.match(host.content,/EUR 300/);assert.match(host.content,/JPY/);assert.equal(JSON.stringify(out),before);")
    def test_unknown_month_no_invented_balance(self):
        self.check_js("renderSimulationWhy(host,out,'Why January?');assert.match(host.content,/no dated change/);assert.doesNotMatch(host.content,/EUR 300/);")
    def test_material_omissions_visible_and_not_zero(self):
        self.check_js("renderSimulationWhy(host,out,'Why November?');for(const t of ['if received','Unknown amounts excluded','Undated items excluded','Starting funds unknown','not zero'])assert.ok(host.content.includes(t),host.content);")
    def test_prior_snapshot_is_labeled(self):
        self.check_js("renderSimulationWhy(host,out,'Why November?',{earlier:true,stale:true});assert.match(host.content,/Earlier simulation/);assert.match(host.content,/Information has changed/);")
    def test_chinese_month_and_omissions(self):
        self.check_js("renderSimulationWhy(host,out,'为什么十一月变化？');assert.match(host.content,/已知收支/);assert.match(host.content,/未计时间未定项目/);assert.match(host.content,/不代表零/);",'zh-CN')
    def test_advice_question_remains_non_advisory(self):
        self.check_js("renderSimulationWhy(host,out,'Should I buy this?');assert.match(host.content,/not whether you should buy/);")
    def test_module_is_registered_static_asset(self):
        from finvis.web import STATIC,WEB_ROOT
        asset,mime=STATIC['/simulation_why.js']
        self.assertEqual(mime,'text/javascript')
        self.assertTrue((WEB_ROOT/asset).is_file())

if __name__=='__main__':unittest.main()
