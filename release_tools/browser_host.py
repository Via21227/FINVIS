"""Isolated release E2E host. Hand-authored synthetic proposals; ZERO inference."""
from pathlib import Path
import sys,json,signal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.graduation_trip_host import start
from release_tools.synthetic_fixture import TEXT,trip
from tests.test_compound_workflow import BundleProvider
from tests.test_compound_future import claim,bundle
from tests.test_generalization_gate import candidate,fact
zh=sys.argv[2]=='zh-CN'
refund='我可能收到1000欧元报销，但还没有到账。' if zh else 'I may receive EUR 1000 reimbursement, not yet received.'
b=bundle(claim('r','报销' if zh else 'reimbursement','1000',currency='EUR',kind='expected_inflow',source=refund))
funds=candidate([fact('bank','current_asset','银行账户' if zh else 'Bank account',3000,'EUR',account='银行账户' if zh else 'Bank account')],'EUR')
funds['language']='zh-CN' if zh else 'en'
s=start(sys.argv[1],sys.argv[2],int(sys.argv[3]),seed=False)
s.ensure_user_workflow().understanding.provider=BundleProvider([funds,trip(),b,{'invalid':'synthetic failure'}])
s.app.local_ai_status=lambda:dict(provider='offline-fixture',cloud=False,connection='unavailable',endpoint='https://api.openai.com/v1/responses',selected_model='gpt-6-astra',models=[],loaded_model_ids=[],state='missing_api_key',ready=False,requests_enabled=False,busy=False,last_successful_check=None,error_category=None,provider_choices=True,credential_present=False,credential_source=None)
signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
print(json.dumps(dict(url=s.origin,root=sys.argv[1],text=TEXT,reimbursement=refund,funds='银行账户余额为3000欧元。' if zh else 'Bank account balance EUR 3000.',fixture='HAND_AUTHORED_SYNTHETIC_NOT_MODEL_OUTPUT',inference_calls=0)),flush=True)
try:s.serve_forever(poll_interval=.05)
except KeyboardInterrupt:pass
finally:s.server_close()
