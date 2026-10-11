"""Isolated lifecycle browser/HTTP host. Fixed synthetic records; no model calls."""
from dataclasses import replace
from pathlib import Path
import signal,sys
from finvis.application import DemoApplication
from finvis.web import DemoServer
from finvis.user_runtime import runtime_location
from finvis.domain import Ref,Currency,DecisionStatus
from finvis.world_values import TypedRef,ObservationRole,AmountEstimate
from finvis.world_planning import WorldDecision
from finvis.world_domain import Earmark,EarmarkRole
from finvis.world_fulfillment import ExpenseFulfillment
from finvis.world_commands import PutRecord,record_ref
from tests.test_agent_understanding import SequenceProvider
from tests.test_settlement_expansion import fixture,live
from tests.test_world_assets import observation,exact,money
from tests.test_persistent_world import changes,prepare
from tests.test_decision_baseline import component,context,amount
from tests.test_if_lines import make
from finvis.reviewed_proposals import _wire


def start(root,port=0):
    server=DemoServer(('127.0.0.1',port),DemoApplication(agent_provider=SequenceProvider([])),user_location=runtime_location(Path(root)))
    w=server.ensure_user_workflow();s=w.store
    if s.snapshot.revision:return server
    f=fixture();o,sid=s.snapshot.world.owner_id,s.snapshot.world.world_id
    records=[replace(f.snapshot.get(r),owner_id=o,world_id=sid) for r in f.snapshot.world.members if r.record_kind in ('account','asset_position')]
    records.append(Earmark(Ref('reserve/studio',1),o,sid,TypedRef('asset_position',Ref('position/reserve',1)),exact(300),'Studio savings · 工作室储蓄',EarmarkRole.GOAL,False,observation('studio')))
    base=live(f,'planned_expense')
    for key,label,value,gradual in [('flight','Flight · 机票',2000,False),('transport','Local transport · 交通预算',700,True),('hotel','Hotel · 酒店',3000,True),('food','Food · 餐饮预算',500,True),('device','Laptop · 电脑',4000,False),('service','Service · 保洁服务',400,False)]:
        records.append(replace(base,owner_id=o,world_id=sid,ref=Ref('expense/'+key,1),purpose=label,
            expected_amount=AmountEstimate.approximate(money(value)),fulfillment=ExpenseFulfillment() if gradual else None))
    nextstate=TypedRef('world_state',Ref(s.snapshot.world.state_ref.id,s.snapshot.world.state_ref.version+1))
    d=WorldDecision(Ref('decision/leisure',1),o,sid,'Consider optional activities?', 'Activities · 活动',nextstate,DecisionStatus.EXPLORING,None,None,
        base.timing,(),observation('leisure',ObservationRole.CONTEMPLATED));records.append(d)
    s.execute(prepare(s,changes(s,[PutRecord('seed/'+r.ref.id,r) for r in records]),'seed'))
    c=component(amount('500'),'activity');c['label']='General activities'
    b=w.baselines.establish(dict(decision_ref=_wire(record_ref(d)),expected_version=0,context=context('other'),components=[c],dependency_refs=[],scope_complete=True,buffer=None))
    make(w.if_lines,b,'If I buy concert tickets for about CNY 1500?')
    make(w.if_lines,b,'If I buy a reference book for CNY 30?')
    return server

if __name__=='__main__':
    server=start(Path(sys.argv[1]).resolve(),int(sys.argv[2]) if len(sys.argv)>2 else 0)
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('READY '+server.origin,flush=True)
    try:server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:pass
    finally:server.server_close()
