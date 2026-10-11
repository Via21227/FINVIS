"""Closed estimate-to-actual language adapter; proposes no financial writes.

Only a named quoted option followed by an explicit actual price and paid
confirmation is reduced to the existing lifecycle action. Everything else
keeps ordinary Compound Meaning review. Original source is never rewritten.
"""
import re
from decimal import Decimal

_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?'
_MONEY = r'(?:(?:about|approximately|约|大约)\s*)?(?:(?:EUR|CNY|SGD|€|人民币|欧元|新币)\s*'+_NUMBER+r'|'+_NUMBER+r'\s*(?:欧元|人民币|新币))'
_NEGATIVE = r'\b(?:not|never|no|maybe|might|if|unless|refund\w*|deposit|remaining|additional|partial|tax|fee|instead|and|or)\b|没|未|不|如果|可能|退款|定金|押金|余款|补差|手续费|以及|或者|和'


def parse_actual_report(source):
    if not isinstance(source,str) or len(source)>1000:return None
    # Subject is an exact later lookup, not an inferred financial identity.
    en=re.fullmatch(r"\s*I\s+(?:booked|bought|purchased|ordered)\s+(?:the\s+)?(?P<quoted>"+_MONEY+r")\s+(?P<subject>[^.;，。；\n]{1,80}?)\s+option[.;]\s*(?:The\s+)?(?:actual|final)\s+(?:price|cost)\s+(?:was|is|came\s+to)\s+(?P<actual>"+_MONEY+r")[,;]?\s+(?:and\s+)?I\s+(?:have\s+)?paid\s+(?:it|for\s+it)[.!]?\s*",source,re.I)
    zh=re.fullmatch(r'\s*我(?:买了|购买了|订了|预订了)(?:原估价|原价|报价)(?P<quoted>'+_MONEY+r')(?:的)?(?P<subject>[^，。；\n]{1,80}?)(?:选项)?[，。；]\s*(?:实际|最终)(?:价格|花费|价钱)(?:是|为)?\s*(?P<actual>'+_MONEY+r')[，。；]\s*(?:我)?(?:已经|已)(?:付款|支付|付了|付清)(?:了)?[。！]?\s*',source)
    found=en or zh
    if not found:return None
    subject=found['subject'].strip()
    if re.search(_NEGATIVE,subject,re.I) or re.search(r'\d|[€$¥￥]|\b(?:paid|spent|bought|price|cost)\b|支付|花费',subject,re.I):return None
    from .interaction_meaning import parse_amount
    quoted=parse_amount(found['quoted'].replace('€','EUR '));actual=parse_amount(found['actual'].replace('€','EUR '))
    if quoted['question'] or actual['question'] or not quoted['amount'] or not actual['amount']:return None
    q,a=quoted['amount'],actual['amount']
    if q['currency']!=a['currency'] or q['currency'] not in ('CNY','SGD','EUR'):return None
    if q['precision'] not in ('exact','approximate') or a['precision'] not in ('exact','approximate'):return None
    return dict(subject=subject,quoted=q,actual=a,source_text=source)


def matches_report(report,label,estimate):
    """Exact named identity plus native quoted value; amount alone never binds."""
    if label.strip().casefold()!=report['subject'].casefold():return False
    from .world_values import AmountPrecision
    q=report['quoted']
    return bool(estimate.precision in (AmountPrecision.EXACT_REPORTED,AmountPrecision.APPROXIMATE)
        and estimate.amount and estimate.amount.currency.value==q['currency']
        and estimate.amount.minor_units==int(Decimal(q['value'])*100))
