"""Isolated D1 browser fixture Host. Fixed candidate; never calls any model."""
from pathlib import Path
import signal
import sys
from finvis.application import DemoApplication
from finvis.user_runtime import runtime_location
from finvis.web import DemoServer
from tests.test_agent_understanding import SequenceProvider
from tests.test_generalization_gate import candidate, fact

TEXT = 'I am considering Coastal break for about CNY 6500. My monthly essential outflow is about CNY 4300.'


def output():
    monthly=fact('monthly','current_asset','monthly essential outflow',4300,currency='CNY',precision='approximate')
    monthly['type']='monthly_essential_outflow'
    decision=fact('choice','contemplated_decision','Coastal break',6500,currency='CNY',precision='approximate')
    return candidate([decision,monthly],currency='CNY')


if __name__=='__main__':
    server=DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=SequenceProvider([output()])),
                      user_location=runtime_location(Path(sys.argv[1])))
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    print('READY '+server.origin,flush=True)
    try:server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:pass
    finally:server.server_close()
