"""Curated release regression, not the complete historical Core suite."""
import os,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MODULES=['test_platform_storage','test_competition_environment','test_competition_reset','test_session_openai_key','test_production_openai','test_openai_request_compatibility','test_persistent_world','test_graduation_trip','test_simulation_surface_v2','test_future_timing','test_plan_reality','test_plan_reality_http','test_actual_transient_continuity','test_why_dialog','test_composer_completion']
EXCLUDED={'tests.test_competition_environment.CompetitionTests.test_original_launcher_hash_unchanged':'Historical personal-launcher baseline remains verified only in complete Core; personal launcher is intentionally excluded from this Competition distribution.'}
def flatten(suite):
 for item in suite:
  if isinstance(item,unittest.TestSuite):yield from flatten(item)
  else:yield item
if __name__=='__main__':
 sys.path.insert(0,str(ROOT));os.chdir(ROOT)
 for key in ('OPENAI_API_KEY','OPENAI_ADMIN_KEY','FINVIS_OPENAI_ENABLED'):os.environ.pop(key,None)
 loader=unittest.TestLoader();cases=list(flatten(loader.loadTestsFromNames(['tests.'+n for n in MODULES])))
 for name,reason in EXCLUDED.items():print('SOURCE_ONLY:',name,reason)
 selected=unittest.TestSuite(c for c in cases if c.id() not in EXCLUDED)
 result=unittest.TextTestRunner(verbosity=2).run(selected)
 sys.exit(0 if result.wasSuccessful() else 1)
