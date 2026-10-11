"""Hand-authored synthetic test proposal, NOT a model response or language benchmark."""
from tests.test_compound_future import claim, bundle

TEXT = "I'm planning a graduation trip in November. It includes a €900 economy flight, a €1,200 hotel, and an optional €600 camera. I haven't paid for any of them yet."

def trip():
    intro = "I'm planning a graduation trip in November."
    listing = 'It includes a €900 economy flight, a €1,200 hotel, and an optional €600 camera.'
    rows = [claim('trip','graduation trip',None,currency=None,precision='unknown',kind='planned_outflow',source=intro,timing=dict(text='November',iso=None,precision='literal')),
            claim('flight','economy flight','900',currency='EUR',kind='planned_outflow',source='€900 economy flight'),
            claim('hotel','hotel','1200',currency='EUR',kind='planned_outflow',source='€1,200 hotel'),
            claim('camera','camera','600',currency='EUR',source='optional €600 camera')]
    return bundle(*rows,relationships=[dict(kind='part_of',**{'from':key,'to':'trip'},source=intro+' '+listing) for key in ('flight','hotel','camera')])
