"""Scenario oracles, production calculations; no model responses or new policy."""
import tempfile,unittest,json
from pathlib import Path
from finvis.simple_surface import SimpleSurface
from finvis.future_space import FutureError
from tests.graduation_trip_host import start,build_trip
class GraduationTripTests(unittest.TestCase):
 def setup_story(self,lang='en',seed=True):
  root=Path(tempfile.mkdtemp(prefix='finvis-graduation-')).resolve();s=start(root,lang,seed=seed);self.addCleanup(s.server_close)
  selection,refs,opts=build_trip(s.user_runtime.future_experience,lang)
  return s,selection,refs,opts,SimpleSurface(s.user_runtime.if_interaction,s.user_workflow.future_adoption)
 def test_months_conditional_native_amounts(self):
  for lang in ('en','zh-CN'):
   s,sel,_,_,service=self.setup_story(lang);before=s.user_workflow.store.snapshot;r=service.simulate(sel);b=r['buckets'][0]
   self.assertEqual(b['currency'],'EUR');self.assertEqual(b['starting']['lower_minor'],300000);self.assertEqual(b['known_cost']['lower_minor'],270000);self.assertEqual(b['known_inflow']['lower_minor'],100000);self.assertEqual(b['after']['lower_minor'],130000)
   t=r['timing']['currencies'][0];self.assertTrue(t['conditional_inflow']);self.assertEqual([g['balance']['lower_minor'] for g in t['groups']],[210000,90000,130000]);self.assertEqual(s.user_workflow.store.snapshot,before)
 def test_without_conditional_income_and_optional_camera(self):
  s,sel,refs,o,service=self.setup_story();sel['roots']=[refs['trip']];sel['options'].remove(o['reimbursement']);self.assertEqual(service.simulate(sel)['buckets'][0]['after']['lower_minor'],30000)
  sel['options'].remove(o['camera']);self.assertEqual(service.simulate(sel)['buckets'][0]['after']['lower_minor'],90000)
 def test_ab_delta_is_500_and_selection_unchanged(self):
  s,sel,refs,o,service=self.setup_story();before=json.dumps(sel,sort_keys=True);r=service.simulate(sel);a=r['alternatives'][0];self.assertEqual(a['cost_difference']['lower_minor'],50000);self.assertEqual(a['result']['buckets'][0]['after']['lower_minor'],80000);self.assertEqual(json.dumps(sel,sort_keys=True),before)
  self.assertTrue(a['result']['timing']['currencies'][0]['undated'])
 def test_mutually_exclusive_and_parent_not_double_counted(self):
  s,sel,_,o,service=self.setup_story();sel['options'].append(o['b'])
  with self.assertRaises(FutureError):service.simulate(sel)
 def test_no_funds_means_signed_changes_only(self):
  s,sel,_,_,service=self.setup_story(seed=False);r=service.simulate(sel);self.assertIsNone(r['buckets'][0]['starting']);self.assertIsNone(r['buckets'][0]['after']);self.assertEqual(r['buckets'][0]['known_cost']['lower_minor'],270000)
 def test_provider_cannot_return_fixture_response(self):
  from finvis.agent_providers import ProviderError
  s,*_=self.setup_story()
  with self.assertRaises(ProviderError):s.app.agent.provider.complete('synthetic','synthetic')
