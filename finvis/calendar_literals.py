"""Explicit calendar witnesses shared by current Meaning and Future timing.

No relative-date inference, currency conversion or monetary arithmetic.
"""
from datetime import date
import re
import unicodedata

MONTHS = ('january','february','march','april','may','june','july','august','september','october','november','december')
_MONTH = r'(?:' + '|'.join(m + '|' + m[:3] + r'\.?' for m in MONTHS) + r')'
CALENDAR = re.compile(r'(?<![\w])(?:' + _MONTH + r'\s+(?:(?P<day>\d{1,2})(?:st|nd|rd|th)?[,]?\s+)?(?P<year>\d{4}))(?![\w]|[.,]\d)', re.I)


def calendar_spans(source):
    from .compound_meaning import _CURRENCY
    result=[]
    for m in CALENDAR.finditer(source):
        # Explicit money denomination wins over a calendar-looking phrase.
        tail=source[m.end():]
        if any(re.match(pattern,tail.lstrip(),re.I) for pattern in _CURRENCY.values()):continue
        if re.match(r'\s*(?:[A-Z]{3}\b|(?i:EUR|CNY|SGD|USD|RMB|euros?|dollars?|yuan)\b|元|块|[€$¥￥])', tail):continue
        month=next(i for i,name in enumerate(MONTHS,1) if m.group().lower().startswith(name[:3]))
        try: value=date(int(m['year']),month,int(m['day'] or '1'))
        except ValueError:continue
        result.append((m.start(),m.end(), 'day' if m['day'] else 'month', value.isoformat() if m['day'] else value.isoformat()[:7]))
    return result


def explicit_calendar(source):
    """Prove a whole absolute literal, without an execution-date anchor."""
    spans=calendar_spans(source)
    if len(spans)==1 and source.strip()==source[spans[0][0]:spans[0][1]]:
        return spans[0][2:]
    # Match the entire expression: no partial-year, relative, trailing amount,
    # invalid day, or overflow may fall back to a shorter month witness.
    match=re.fullmatch(r'\s*(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?\s*', source)
    if not match:
        match=re.fullmatch(r'\s*(\d{4})-(\d{2})(?:-(\d{2}))?\s*', source)
    if not match:return None
    year,month,day=match.groups()
    try:value=date(int(year),int(month),int(day or '1'))
    except ValueError:return None
    return ('day',value.isoformat()) if day else ('month',value.isoformat()[:7])


def _absolute_calendar_spans(source):
    """Absolute calendar witnesses, including bounded Chinese and ISO dates."""
    from .compound_meaning import _CURRENCY
    result=calendar_spans(source)
    numeric=re.compile(r'(?<![0-9A-Za-z])(?:\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?|\d{4}-\d{2}(?:-\d{2})?)(?![0-9A-Za-z]|[.,]\d|\s*\d|-\d)')
    for match in numeric.finditer(source):
        proven=explicit_calendar(match.group())
        if not proven:continue
        tail=source[match.end():].lstrip()
        if any(re.match(pattern,tail,re.I) for pattern in _CURRENCY.values()):continue
        if re.match(r'[A-Z]{3}\b|[€$¥￥]',tail):continue
        result.append((match.start(),match.end(),*proven))
    # Currency symbols beyond the named native currencies are still monetary.
    return sorted(span for span in result if not (source[span[1]:].lstrip() and
        unicodedata.category(source[span[1]:].lstrip()[0])=='Sc'))


def monetary_precision_source(source, timing):
    """Remove only approximation cues attached directly to a proven calendar.

    A source-bound timing quote must contain exactly one absolute calendar and
    no monetary tokens or currency. Surrounding words remain untouched, so a
    model's overbroad or ambiguous quote cannot erase price approximation.
    """
    from .compound_meaning import _APPROX, _CURRENCY, _number_evidence, _NUM
    literal=timing.get('text')
    if not literal or literal not in source:return source
    dates=_absolute_calendar_spans(literal)
    if len(dates)!=1:return source
    start,end,_,_=dates[0]
    remainder=literal[:start]+' '*(end-start)+literal[end:]
    if _number_evidence(remainder) or any(re.search(p,remainder,re.I) for p in _CURRENCY.values()):return source
    if re.search(r'\b[A-Z]{3}\b',remainder) or any(unicodedata.category(c)=='Sc' for c in remainder):return source
    cues=[m for m in _APPROX.finditer(literal) if
          (m.end()<=start and not literal[m.end():start].strip()) or
          (m.start()>=end and not literal[end:m.start()].strip())]
    if not cues:return source
    proven_positions={(a,b) for a,b,_,_ in _absolute_calendar_spans(source)}
    chars=list(source)
    for occurrence in re.finditer(re.escape(literal),source):
        offset=occurrence.start()
        if (offset+start,offset+end) not in proven_positions:continue
        for cue in cues:
            # A clipped trailing cue may actually introduce the following price.
            # Reject this ambiguity without needing a temporal-verb vocabulary.
            tail=source[offset+cue.end():].lstrip()
            if cue.start()>=end and (_NUM.match(tail) or
                    re.match(r'[A-Z]{3}\b',tail) or
                    (tail and unicodedata.category(tail[0])=='Sc') or
                    any(re.match(p,tail,re.I) for p in _CURRENCY.values())):continue
            chars[offset+cue.start():offset+cue.end()]=' '*(cue.end()-cue.start())
    return ''.join(chars)


def money_numbers(source):
    from .compound_meaning import _number_evidence
    chars=list(source)
    for a,b,_,_ in calendar_spans(source):chars[a:b]=' '*(b-a)
    # The legacy quantity filter treats 日 as a day suffix, including 日元.
    # A recognized currency word is not a date unit; keep offsets unchanged.
    clean=''.join(chars).replace('日元','  ')
    return _number_evidence(clean)
