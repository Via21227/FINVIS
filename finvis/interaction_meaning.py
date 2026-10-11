"""Bounded, read-only Interaction 2.0 meaning proposals.

This is deterministic preparation, not unrestricted language understanding.
It never calls a provider, stores facts or authorizes a financial operation.
Only supported Future language gets an adapter payload. Reality and Plan go to
existing reviewed paths. Context cannot overrule explicit object language.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
import unicodedata

from .flow_direction import flow_direction

VERSION = 'meaning-proposal-v1'
MAX_OBJECTS = 512
MAX_CANDIDATES = 6

# Small bilingual vocabulary used only to retrieve objects already in the read.
# These words are not financial facts, recommendations, or a currency locale.
NOUNS = {
    'phone': r'\b(?:phones?|smartphones?)\b|手机',
    'camera': r'\bcameras?\b|相机',
    'gloves': r'\bgloves?\b|拳套|手套',
    'shoes': r'\b(?:shoes?|boots?)\b|鞋',
    'hotel': r'\b(?:hotels?|accommodation|lodging)\b|酒店|住宿|旅馆',
    'flight': r'\b(?:flights?|airfare|tickets?)\b|机票|航班',
    'trip': r'\b(?:trips?|travel|holiday|vacation)\b|旅行|旅游|行程',
    'food': r'\b(?:food|meals?|dining)\b|餐饮|伙食|餐费',
    'concert': r'\bconcerts?\b|演唱会|音乐会',
    'course': r'\b(?:courses?|classes|tuition)\b|课程|学费|培训',
    'bonus': r'\bbonuses?\b|奖金',
    'salary': r'\b(?:salary|wages?|stipend)\b|工资|薪水|津贴',
    'rent': r'\brent\b|租金|房租',
    'bicycle': r'\b(?:bicycles?|bikes?)\b|自行车',
    'renovation': r'\b(?:renovation|remodel)\b|装修',
    'camp': r'\bcamps?\b|夏令营',
    'laptop': r'\b(?:laptops?|computers?)\b|电脑|笔记本',
    'furniture': r'\b(?:furniture|sofas?|desks?)\b|家具|沙发|书桌',
}
HYPOTHETICAL = r'\b(?:what\s+if|if|suppose|imagine)\b|如果|假如|假设|要是'
ACTUAL = r'\b(?:paid|spent|bought|purchased|received|refunded?|rebooked)\b|买好了|买了|买到了|已经买|花了|支付了|付了|到账|到了|退款|退了|重新订'
PLAN = r'\b(?:I\s+(?:choose|decide)|decided|booked|ordered|reserved|add\s+to\s+(?:my\s+)?plan)\b|我决定|我选|已经决定|订好了|预订了|下单了|加入计划'
CORRECTION = r'\b(?:not\s+(?:this|that)|I\s+mean|I\s+meant|correction)\b|不是这个|不是那个|不是[，,]|我说的是|说错了|更正'
PRONOUN = r'\b(?:it|this|that|these|those|the\s+other\s+one|previous\s+one)\b|那个|这个|这些|其中|上一个|前面那个'
ADVICE = r'\b(?:should\s+I|recommend|afford|worth\s+it|financial\s+advice)\b|值得|买得起|你觉得|建议|该不该'
READ_QUESTION = r'^\s*(?:why\b|how\s+much\b|show\b|what\s+(?:is|are)\b)|为什么|怎么回事|有什么|现在多少'
ABSENCE = r'\b(?:do\s+not\s+buy|don.t\s+buy|no\s+purchase|not\s+attend|skip|cancel)\b|不买|不参加|不去了|没了|取消'
TRADE_IN = r'\btrade[ -]?in\b|以旧换新'
UNKNOWN = r'\b(?:unknown|not\s+sure|don.t\s+know|unsure)\b|不知道|不确定|未知|还没定'
PRICE = r'\b(?:costs?|price|budget|total|together|about|around|for|only)\b|一共|总共|只要|只需|大概|大约|左右|预算|贵点|便宜点'
CONSIDERING = r'\b(?:I\s+am\s+considering|I.m\s+considering)\b|我在考虑|我想考虑|我还在思考|我在想'
STOP = set('a an the my our your if what for only about around cost costs total buy buying would could me i and of to is are in on instead please'.split())


def _normal(text):
    return unicodedata.normalize('NFKC', str(text)).casefold()


def _key(ref):
    if not isinstance(ref, dict):
        return None
    if set(ref) != {'kind', 'id', 'version'} or not isinstance(ref['id'], str) or type(ref['version']) is not int:
        return None
    return ref['kind'], ref['id'], ref['version']


def _label(row):
    return str(row.get('title') or row.get('label') or row.get('name') or '').strip()


def _contains(source, term):
    term = _normal(term).strip()
    if len(term) < 2:
        return False
    # English object words must not match camera in cameraman, or car in card.
    pattern = re.escape(term)
    if term[0].isascii() and term[0].isalnum():
        pattern = r'(?<![a-z0-9])' + pattern
    if term[-1].isascii() and term[-1].isalnum():
        pattern += r'(?![a-z0-9])'
    return re.search(pattern, source) is not None


def _nouns(text):
    return {kind for kind, pattern in NOUNS.items() if re.search(pattern, text, re.I)}


def _qualifiers(text):
    words = set(re.findall(r'[a-z]{3,}', _normal(text))) - STOP
    for pattern in NOUNS.values():
        words = {word for word in words if not re.search(pattern, word, re.I)}
    # Relation/location words are literal evidence, not guessed geography.
    han = set(re.findall(r'日本|韩国|新加坡|自己的|妈妈|母亲|奶奶|首尔|东京|大阪|台北|工作|家庭|儿童|成人', text))
    if re.search(r'新(?!加坡|西兰)(?=[\u4e00-\u9fff])', text):
        han.add('new')
    if re.search(r'两[部台个]|两个', text):
        han.add('two')
    return words | han


def retrieve_targets(text, objects, *, context=None, aliases=None):
    """Return all best supported targets; never use focus to break a tie."""
    if not isinstance(objects, (list, tuple)) or len(objects) > MAX_OBJECTS:
        return {'candidates': [], 'basis': 'capacity', 'ambiguous': True}
    rows = [row for row in objects if isinstance(row, dict) and _key(row.get('ref')) and not row.get('archived')]
    source = _normal(text)
    # An explicit child title can include its parent's title; choose the more
    # specific contained phrase only. Different explicit names remain a tie.
    exact = [row for row in rows if _contains(source, _label(row))]
    if exact:
        specific = [r for r in exact if not any(r is not other and _normal(_label(r)) in _normal(_label(other)) and _label(r) != _label(other) for other in exact)]
        return {'candidates': deepcopy(specific[:MAX_CANDIDATES]), 'basis': 'explicit_name', 'ambiguous': len(specific) != 1, 'overflow': len(specific) > MAX_CANDIDATES}
    nouns = _nouns(source)
    semantic = [row for row in rows if nouns & _nouns(_label(row))]
    if semantic:
        qualifiers = _qualifiers(source)
        by_key = {_key(r['ref']): r for r in rows}
        def score(row):
            parent = by_key.get(_key(row.get('parent')), {})
            return len(qualifiers & _qualifiers(_label(row) + ' ' + _label(parent)))
        maximum = max(score(row) for row in semantic)
        if maximum:
            semantic = [row for row in semantic if score(row) == maximum]
        else:
            # A unique category is not a unique semantic identity. Korea hotel
            # must not mean the only Japan hotel; own phone must not silently
            # mean somebody else's phone. Ignore price/operation adjectives,
            # but retain unmatched explicit identity qualifiers as material.
            modifiers = qualifiers - set('better nicer premium cheaper expensive more less trade trading try change make alternative actually still just can cny sgd rmb budget revised roughly approximately unknown unspecified disappears fifteen thousand might price'.split())
            target_qualifiers = set().union(*(_qualifiers(_label(row)+' '+_label(by_key.get(_key(row.get('parent')),{}))) for row in semantic))
            if modifiers and target_qualifiers:
                return {'candidates':deepcopy(semantic[:MAX_CANDIDATES]),'basis':'explicit_qualifier_conflict','ambiguous':True}
        return {'candidates': deepcopy(semantic[:MAX_CANDIDATES]), 'basis': 'explicit_semantics', 'ambiguous': len(semantic) != 1, 'overflow': len(semantic) > MAX_CANDIDATES}
    # Browser presentation aliases are explicit user aids, not authority. The
    # referenced version must exist in this current read. Explicit nouns above
    # outrank them; an unrelated explicit noun prevents use of a stale alias.
    if not nouns:
        alias_rows = []
        stale_alias = False
        for row in rows:
            names = row.get('aliases', [])
            if isinstance(names, list) and any(isinstance(name, str) and _contains(source, name) for name in names):
                alias_rows.append(row)
        if isinstance(aliases, list):
            for alias in aliases[:512]:
                if not isinstance(alias, dict) or not _contains(source, alias.get('name', '')):
                    continue
                row = next((r for r in rows if _key(r['ref']) == _key(alias.get('ref'))), None)
                if row is None:
                    stale_alias = True
                if row and not any(_key(r['ref']) == _key(row['ref']) for r in alias_rows):
                    alias_rows.append(row)
        if alias_rows:
            return {'candidates': deepcopy(alias_rows[:MAX_CANDIDATES]), 'basis': 'visible_alias', 'ambiguous': len(alias_rows) != 1, 'overflow': len(alias_rows) > MAX_CANDIDATES}
        if stale_alias:
            return {'candidates': [], 'basis': 'stale_alias', 'ambiguous': True}
    focus = (context or {}).get('focus') if isinstance(context, dict) else None
    if not nouns and re.search(PRONOUN, source, re.I):
        row = next((r for r in rows if _key(r['ref']) == _key(focus)), None)
        if row:
            return {'candidates': [deepcopy(row)], 'basis': 'explicit_focus_pronoun', 'ambiguous': False}
    return {'candidates': [], 'basis': 'unresolved_explicit' if nouns else 'unresolved', 'ambiguous': True}


_DIGITS = dict(zip('零〇一二两三四五六七八九', [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
_UNITS = {'十': 10, '百': 100, '千': 1000, '万': 10000}


def chinese_number(value):
    """Finite ordinary price notation, including 八千五 / 一万五 shorthand."""
    if not value or not re.fullmatch(r'[零〇一二两三四五六七八九十百千万]+', value):
        raise ValueError('unsupported_number')
    total = section = digit = 0
    last_unit = 1
    for char in value:
        if char in _DIGITS:
            digit = _DIGITS[char]
        else:
            unit = _UNITS[char]
            if unit == 10000:
                section = (section + digit) or 1
                total += section * unit
                section = 0
            else:
                section += (digit or 1) * unit
            digit = 0
            last_unit = unit
    # A final single digit after 千/万 colloquially names the next decimal
    # place. Explicit 零 makes 一万零五 = 10005 rather than 15000.
    if len(value) >= 3 and value[-1] in _DIGITS and value[-2] in '百千万':
        digit *= last_unit // 10
    return total + section + digit


_NUMBER = r'(?:\d+(?:,\d{3})*(?:\.\d{1,2})?\s*[kK万千]?|[零〇一二两三四五六七八九十百千万]+)'


def _number(value):
    raw = value.replace(',', '').strip()
    if re.fullmatch(r'[零〇一二两三四五六七八九十百千万]+', raw):
        return Decimal(chinese_number(raw))
    factor = Decimal(1)
    if raw[-1:] in ('k', 'K', '千', '万'):
        factor = Decimal(10000 if raw[-1] == '万' else 1000)
        raw = raw[:-1].strip()
    amount = Decimal(raw) * factor
    if amount < 0 or amount > 10 ** 12 or amount.as_tuple().exponent < -2:
        raise ValueError('unsupported_number')
    return amount


def _format(value):
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else str(value)


def parse_amount(text, currency=None):
    """Parse a single price expression, never quantities/dates as money.

    Returning a material question is safer than guessing which of two prices is
    the intended replacement. Currency may come from the uniquely bound object.
    """
    source = _normal(text)
    currencies = []
    for code, pattern in [('CNY', r'(?<![a-z])(?:cny|rmb)(?![a-z])|人民币'), ('SGD', r'(?<![a-z])sgd(?![a-z])|新币|新加坡元'), ('EUR', r'(?<![a-z])(?:eur|euros?)(?![a-z])|欧元')]:
        if re.search(pattern, source, re.I):
            currencies.append(code)
    unsupported = bool(re.search(r'(?<![a-z])(?:usd|gbp|jpy|hkd)(?![a-z])|美元|日元|港币|\$', source, re.I))
    if unsupported or len(currencies) > 1:
        return {'amount': None, 'question': 'currency', 'currency': None}
    stated = currencies[0] if currencies else None
    if stated and currency and stated != currency:
        return {'amount': None, 'question': 'currency_conflict', 'currency': stated}
    code = stated or currency
    if code not in (None, 'CNY', 'SGD', 'EUR'):
        return {'amount': None, 'question': 'currency', 'currency': None}
    # Do not turn an explicit but unsupported verbal number into "unknown" or
    # silently keep only its last numeral. Ask for the amount in that case.
    if re.search(r'\b(?:hundred|thousand|million)\b|[一二两三四五六七八九]{2,}[百千万]|[一二两三四五六七八九]+(?:或|或者)[一二两三四五六七八九]+[千万元]', source, re.I):
        return {'amount': None, 'question': 'amount', 'currency': code}
    cleaned = re.sub(r'\d+(?:\.\d+)?\s*(?:days?|nights?|months?|years?|oz|天|晚|个月|年)|[一二两三四五六七八九十]+\s*(?:个|部|台|晚|天|年)', ' ', source)
    # Exclude dates and negative amounts; don't silently discard a minus sign.
    if re.search(r'\d{4}[-/]\d|(?<!\d)-\s*\d', cleaned):
        return {'amount': None, 'question': 'amount', 'currency': code}
    range_match = re.search('(' + _NUMBER + r')\s*(?:–|~|至|到|\bto\b)\s*(' + _NUMBER + ')', cleaned, re.I)
    try:
        if range_match:
            lower, upper = (_number(range_match.group(i)) for i in (1, 2))
            if lower > upper:
                raise ValueError('invalid_range')
            if not code:
                return {'amount': None, 'question': 'currency', 'currency': None}
            return {'amount': dict(currency=code, precision='range', value=None, range_min=_format(lower), range_max=_format(upper)), 'question': None, 'currency': code}
        tokens = list(re.finditer(_NUMBER, cleaned))
        # Chinese 一/两 occurring in ordinary grammar are not price evidence.
        tokens = [token for token in tokens if re.search(r'\d|十|百|千|万', token.group())]
        if len(tokens) > 1:
            return {'amount': None, 'question': 'amount', 'currency': code}
        if not tokens:
            return {'amount': None, 'question': None, 'currency': code}
        if not code:
            return {'amount': None, 'question': 'currency', 'currency': None}
        value = _number(tokens[0].group())
        precision = 'approximate' if re.search(r'\b(?:about|around|approximately|roughly)\b|大概|大约|左右|约', source, re.I) else 'exact'
        return {'amount': dict(currency=code, precision=precision, value=_format(value), range_min=None, range_max=None), 'question': None, 'currency': code}
    except (ValueError, InvalidOperation):
        return {'amount': None, 'question': 'amount', 'currency': code}


def _currency(row):
    amount = row.get('amount') or {}
    return row.get('currency') or amount.get('currency') or (amount.get('amount') or {}).get('currency') or amount.get('unknown_currency')


def _layer(text):
    if not re.search(ACTUAL,text,re.I) and flow_direction(text)=='INFLOW' and re.search(r'可能|也许|\b(?:may|might|will)\b',text,re.I):
        return 'future'
    if re.search(HYPOTHETICAL+'|'+CONSIDERING, text, re.I):
        return 'future'
    if re.search(ACTUAL, text, re.I):
        return 'reality'
    if re.search(PLAN, text, re.I):
        return 'plan'
    if re.search(READ_QUESTION, text, re.I) or re.search(ADVICE, text, re.I) or '?' in text or '？' in text:
        return 'question'
    return 'unknown'


def resolve_meaning(text, objects, *, context=None, antecedent=None, aliases=None, _reviewed_target=None):
    """Build a non-authoritative proposal for the existing If adapter.

    Caller must revalidate exact object/version and all domain invariants.
    ``supported`` means finite Future preparation only, never permission to
    retain or adopt. ``unhandled`` leaves existing Plan/Reality/Agent routing.
    """
    source = text if isinstance(text, str) else ''
    result = dict(schema=VERSION, source_text=source, read_only=True, financial_authority=False,
                  status='unhandled', layer='unknown', target=None, candidates=[], amount=None,
                  operation=None, question=None, evidence=[], clauses=[], source_spans=[], payload=None, direction=flow_direction(source))
    if not source.strip() or len(source) > 2000:
        result['reason'] = 'unsupported_input'
        return result
    if _layer(source) == 'question':
        result.update(layer='question', reason='read_only_or_unsupported_judgment')
        return result
    structured = _structure(source, objects, context=context, aliases=aliases)
    if structured is not None:
        result.update(structured)
        return result
    # Punctuation and explicit clause connectors only. Do not split two phones,
    # thousands separators, quoted names, or attach another clause's price.
    pieces = [(m.group().strip(), m.start(), m.end()) for m in re.finditer(r'[^;；。!！\n]+', source) if m.group().strip()]
    if len(pieces) == 1:
        separated = re.split(r'[，,]\s*(?=(?:酒店|机票|奖金|手机|工资|另外|对了|(?:the\s+)?(?:hotel|flight|bonus|phone|salary)\b))|\s+and\s+(?=(?:I\s+)?(?:paid|spent|bought|received|what\s+if)\b)', source, flags=re.I)
        if len(separated) > 1:
            offset = 0
            pieces = []
            for piece in separated:
                start = source.find(piece, offset)
                pieces.append((piece.strip(), start, start + len(piece)))
                offset = start + len(piece)
    result['clauses'] = [dict(text=s, start=a, end=b, layer=_layer(s)) for s, a, b in pieces]
    if len(pieces) > 1:
        # Multiple ordinary factual sentences still belong to the existing
        # Candidate reconciler. This Future adapter must not intercept account
        # intake merely because punctuation produced several clauses.
        future_clauses = [s for s, _, _ in pieces if _layer(s) == 'future' or
                          (_layer(s) == 'unknown' and re.search(PRICE, s, re.I) and _nouns(s))]
        if not future_clauses:
            layers = {_layer(s) for s, _, _ in pieces}
            result.update(layer='reality' if 'reality' in layers else 'plan' if 'plan' in layers else 'unknown',
                          reason='existing_reviewed_route')
            return result
        result.update(status='clarify', layer='mixed', question=dict(key='separate_clauses', choices=[]), reason='independent_review_required')
        return result
    result['source_spans'] = [dict(start=0, end=len(source), text=source)]
    layer = _layer(source)
    result['layer'] = layer
    if layer in ('reality', 'plan', 'question'):
        result['reason'] = 'existing_reviewed_route' if layer != 'question' else 'read_only_or_unsupported_judgment'
        return result
    if flow_direction(source) == 'TRANSFER' or re.search(r'\b(?:ignore\s+(?:all|previous)|execute|transfer\s+money|delete\s+all)\b|忽略规则|删除所有|清空所有|转账', source, re.I):
        result['reason'] = 'outside_future_preparation'
        return result
    correction = bool(re.search(CORRECTION, source, re.I))
    retrieval = retrieve_targets(source, objects, context=context, aliases=aliases)
    if _reviewed_target is not None:
        reviewed = [r for r in retrieval['candidates'] if _key(r['ref']) == _key(_reviewed_target)]
        if len(reviewed) == 1:
            retrieval = dict(candidates=reviewed,basis='explicit_target_answer',ambiguous=False)
    candidates = retrieval['candidates']
    result['candidates'] = [r['ref'] for r in candidates]
    result['evidence'].append(dict(kind=retrieval['basis'], refs=result['candidates']))
    supported_phrase = layer == 'future' or correction or bool(re.search(PRICE + '|' + ABSENCE + '|' + TRADE_IN, source, re.I))
    if not supported_phrase:
        result['reason'] = 'unrecognized_meaning'
        return result
    result['layer'] = 'future'
    if not candidates or retrieval['ambiguous']:
        if not candidates and retrieval['basis'] not in ('capacity','stale_alias') and not correction:
            fresh = _new_future(source)
            if fresh is not None:
                result.update(fresh)
                return result
        result.update(status='clarify', question=dict(key='target', choices=[dict(ref=r['ref'], label=_label(r)) for r in candidates]))
        return result
    target = candidates[0]
    result['target'] = target['ref']
    if target.get('container_only') and not correction and not re.search(PRICE+'|'+ABSENCE+'|'+TRADE_IN,source,re.I):
        result.update(status='supported',operation='explore',payload=dict(text=source,node=deepcopy(target['ref'])))
        return result
    parsed = parse_amount(source, _currency(target))
    if parsed['question']:
        result.update(status='clarify', question=dict(key=parsed['question'], choices=[]))
        return result
    reported = parsed['amount']
    if correction:
        if not isinstance(antecedent, dict) or antecedent.get('layer') != 'future' or antecedent.get('retained') is not False:
            result.update(status='clarify', question=dict(key='correction_scope', choices=[]))
            return result
        if reported is None and antecedent.get('amount'):
            old = deepcopy(antecedent['amount'])
            if old.get('currency') != _currency(target):
                result.update(status='clarify', question=dict(key='currency_conflict', choices=[]))
                return result
            reported = old
        result['replaces_draft'] = True
        result['evidence'].append(dict(kind='explicit_unretained_antecedent', source_text=antecedent.get('text', '')))
    operation = 'correction' if correction else 'alternative'
    if re.search(ABSENCE, source, re.I) and not re.search(r'不取消|没有取消|\bnot\s+cancel', source, re.I):
        operation = 'absence'
    elif re.search(TRADE_IN, source, re.I):
        operation = 'alternative'
        if reported is None and parsed['currency']:
            reported = dict(currency=parsed['currency'], precision='unknown', value=None, range_min=None, range_max=None)
        elif reported is None:
            result.update(status='clarify', question=dict(key='currency', choices=[]))
            return result
    if reported is None and operation != 'absence':
        if parsed['currency']:
            # Missing price does not block a reversible Future possibility.
            # Its impact is explicitly incomplete, never zero or old price.
            reported = dict(currency=parsed['currency'], precision='unknown', value=None, range_min=None, range_max=None)
        else:
            result.update(status='clarify', question=dict(key='currency', choices=[]))
            return result
    payload = dict(text=source, node=deepcopy(target['ref']))
    if reported is not None:
        payload['amount'] = deepcopy(reported)
    if parsed['currency']:
        payload['currency'] = parsed['currency']
    result.update(status='supported', amount=reported, operation=operation, payload=payload)
    return result


def _structure(source, objects, *, context=None, aliases=None):
    match = re.search(r'\b(?:includes?|consists?\s+of|split\s+into)\b|包括|分成|拆成', source, re.I)
    if not match or re.search(ACTUAL, source[:match.start()], re.I):
        return None
    prefix, body = source[:match.start()], source[match.end():].strip(' :：')
    found = retrieve_targets(prefix, objects, context=context, aliases=aliases)
    rows = found['candidates']
    if len(rows) != 1 or found['ambiguous']:
        return dict(status='clarify', layer='future', operation='structure', candidates=[r['ref'] for r in rows],
                    question=dict(key='target', choices=[dict(ref=r['ref'], label=_label(r)) for r in rows]))
    parent = rows[0]
    parts = []
    texts = re.split(r'[、，,;；]|\s+and\s+', body)
    # This grammar deliberately requires at least two separately named parts.
    if not 2 <= len(texts) <= 8:
        return dict(status='clarify', layer='future', question=dict(key='parts', choices=[]))
    for part in texts:
        part = part.strip().rstrip('呢？?。.')
        if re.search(ACTUAL+'|'+HYPOTHETICAL, part, re.I):
            return dict(status='clarify', layer='mixed', question=dict(key='separate_clauses', choices=[]))
        explicit_currency = bool(re.search(r'\b(?:CNY|SGD|EUR|RMB)\b|人民币|新币', part, re.I))
        parsed = parse_amount(part, None if explicit_currency else _currency(parent))
        if parsed['question']:
            return dict(status='clarify', layer='future', question=dict(key=parsed['question'], choices=[]))
        # A terminal amount is the only number removed from the title.
        title = re.sub(r'(?:(?:about|around|approximately|大约|大概|约)\s*)?(?:(?:CNY|SGD|EUR|RMB|人民币|新币)\s*)?'+_NUMBER+r'\s*(?:元|块|左右)?$', '', part, flags=re.I).strip(' :：')
        if not title or re.search(r'\d', title) or len(title)>80:
            return dict(status='clarify', layer='future', question=dict(key='parts', choices=[]))
        item = dict(title=title)
        if parsed['amount']:
            item['amount'] = parsed['amount']
        if parsed['currency']:
            item['currency'] = parsed['currency']
        parts.append(item)
    if len({_normal(p['title']) for p in parts}) != len(parts):
        return dict(status='clarify', layer='future', question=dict(key='parts', choices=[]))
    return dict(status='supported', layer='future', operation='structure', target=deepcopy(parent['ref']),
                candidates=[deepcopy(parent['ref'])], source_spans=[dict(start=0,end=len(source),text=source)],
                evidence=[dict(kind='explicit_part_list', refs=[parent['ref']])],
                payload=dict(text=source, node=deepcopy(parent['ref']), kind='structure', parts=parts))


def _new_future(source, reviewed_currency=None):
    """Finite explicit creation language; names are user text, not a taxonomy."""
    incoming=flow_direction(source)=='INFLOW' and bool(re.search(r'可能|也许|\b(?:may|might|will)\b',source,re.I))
    if not incoming and not re.search(HYPOTHETICAL+'|'+CONSIDERING, source, re.I):
        return None
    if re.search(PRONOUN+'|'+ACTUAL+'|'+ABSENCE+'|'+ADVICE, source, re.I):
        return None
    body=re.sub(r'^\s*(?:what\s+if|suppose|imagine|I\s+am\s+considering|I.m\s+considering)\s+(?:(?:I|we)\s+)?(?:(?:buy|attend|try|get|take|plan)\s+)?', '', source, flags=re.I)
    body=re.sub(r'^\s*(?:如果|假如|假设|要是|我在考虑|我想考虑)\s*(?:我(?:想|打算)?)?(?:购买|买|参加|去)?', '', body)
    if (body == source and not incoming) or not body.strip():
        return None
    parsed=parse_amount(source,reviewed_currency)
    # A terminal price may be present; do not remove arbitrary internal names
    # or infer that an unmatched conversational sentence names a new object.
    tail=r'(?:(?:\b(?:for|costs?|at|about|around|only)\b|预算|只要|需要|大约|大概|约|要)\s*)*(?:(?:CNY|SGD|EUR|RMB|人民币|新币)\s*)?'+_NUMBER+r'\s*(?:人民币|新加坡元|新币|欧元|euros?|CNY|SGD|EUR|元|块|左右)?[呢?？。.]*$'
    title=re.sub(tail,'',body,flags=re.I).strip(' ,，:：?？。.呢')
    title=re.sub(r'\s+(?:for|costs?|at|about|around|only)$','',title,flags=re.I).strip(' ,，:：')
    if not title or len(title)>80 or re.search(r'\b(?:is|are|would|could|should|and|because|when|where|how|cost)\b|怎么|是否|会不会|但是|而且',title,re.I):
        return None
    if re.search(r'\d|[;；。!！\n]',title):
        return None
    out=dict(layer='future',operation='create',target=None,title=title,candidates=[],amount=parsed['amount'],
             evidence=[dict(kind='explicit_new_event_name',text=title)],
             partial=dict(text=source,title=title))
    if parsed['question']:
        out.update(status='clarify',question=dict(key=parsed['question'],choices=[]),payload=None)
    elif not parsed['currency']:
        out.update(status='clarify',question=dict(key='currency',choices=[]),payload=None)
    else:
        amount=parsed['amount'] or dict(currency=parsed['currency'],precision='unknown',value=None,range_min=None,range_max=None)
        out.update(status='supported',question=None,amount=amount,
                   payload=dict(text=source,title=title,currency=parsed['currency'],amount=amount))
    return out


def resolve(payload, space):
    """Application wrapper over an immutable FS2 listing or merged trial space.

    Browser aliases/context are non-authoritative proposals only. Current exact
    references are still checked here and by IfInteraction's existing adapter.
    No identifier from a previous version is silently upgraded.
    """
    if not isinstance(payload, dict) or not isinstance(space, dict):
        return resolve_meaning('', [])
    from .actual_report import parse_actual_report
    if parse_actual_report(payload.get('text')):
        return dict(status='unhandled',layer='reality',target=None,bounded_followup=True,
                    source_text=payload['text'],read_only=True,financial_authority=False)
    records = space.get('records', [])
    objects = []
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict) or (row.get('ref') or {}).get('kind') != 'node':
            continue
        obj = deepcopy(row)
        obj['container_only'] = any(r.get('parent')==row.get('ref') for r in records) and not any(r.get('node')==row.get('ref') for r in records)
        variables = [v for v in records if v.get('ref', {}).get('kind') == 'variable' and v.get('node') == row.get('ref')]
        variables_keys = {_key(v['ref']) for v in variables}
        currencies = set()
        for option in records:
            if option.get('ref', {}).get('kind') != 'option' or _key(option.get('variable')) not in variables_keys:
                continue
            for effect in option.get('effects', []):
                for field in ('observed', 'planning'):
                    amount = effect.get(field) or {}
                    code = amount.get('currency') or (amount.get('amount') or {}).get('currency') or (amount.get('lower_bound') or {}).get('currency') or amount.get('unknown_currency')
                    if code:
                        currencies.add(code)
        if len(currencies) == 1:
            obj['currency'] = next(iter(currencies))
        objects.append(obj)
    context = payload.get('context')
    if isinstance(context, dict) and 'focus' not in context and isinstance(context.get('node'), dict):
        context = dict(context, focus=context['node'])
    from .future_followup import resolve_followup
    followup=resolve_followup(payload,space,objects,context)
    if followup is not None:
        followup['binding']={name:deepcopy(space.get(name)) for name in ('future_revision','financial_revision')}
        return followup
    from .future_timing import resolve_timing
    timed=resolve_timing(payload,space,objects,context)
    if timed is not None:
        timed['binding']={name:deepcopy(space.get(name)) for name in ('future_revision','financial_revision')}
        return timed
    result = resolve_meaning(payload.get('text'), objects, context=context, aliases=payload.get('aliases'), antecedent=payload.get('antecedent'))
    # A clarification is a new explicit human answer, never a context hint.
    # Narrow only to a candidate offered by the current immutable read. A
    # forged, stale or unrelated ref cannot become a reviewed target here.
    reviewed_target = payload.get('reviewed_target')
    if reviewed_target is not None:
        if (_key(reviewed_target) is None or
                _key(reviewed_target) not in {_key(r) for r in result['candidates']}):
            result.update(status='clarify', payload=None, reason='answer_stale',
                          question=dict(key='target', choices=[]))
        else:
            objects = [row for row in objects if _key(row['ref']) == _key(reviewed_target)]
            result = resolve_meaning(payload.get('text'), objects, context=context,
                                     aliases=payload.get('aliases'), antecedent=payload.get('antecedent'), _reviewed_target=reviewed_target)
            result['evidence'].append(dict(kind='explicit_target_answer', refs=[deepcopy(reviewed_target)]))
    name_resolved=False
    answer = payload.get('reviewed_name')
    if answer is not None:
        key = (result.get('question') or {}).get('key')
        valid = (key == 'target' and isinstance(answer,str) and 1 <= len(answer.strip()) <= 80
                 and not re.search(r'[;；。!?！？\n\d]|'+ACTUAL+'|'+PLAN, answer, re.I))
        if valid:
            offered = [o for o in objects if o['ref'] in result['candidates']] if result['candidates'] else objects
            named = retrieve_targets(answer.strip(), offered, aliases=payload.get('aliases'))
            if len(named['candidates']) == 1 and not named['ambiguous']:
                target = named['candidates'][0]
                # Resolve price from original text, with the now explicitly named target.
                amended = dict(payload, text=target['title']+' '+payload['text'], reviewed_target=target['ref'])
                amended.pop('reviewed_name',None)
                result = resolve(amended, space)
                name_resolved=True
                if result.get('payload'):
                    result['payload']['text'] = payload['text']+'\nTarget clarified: '+answer.strip()
                result['source_text'] = payload['text']
                result['evidence'].append(dict(kind='explicit_target_name',text=answer.strip(),refs=[target['ref']]))
            elif not result['candidates'] and not named['candidates'] and _layer(payload['text']) == 'future':
                name_resolved=True
                parsed = parse_amount(payload['text'], payload.get('reviewed_currency'))
                result.update(operation='create',title=answer.strip(),target=None,amount=parsed['amount'])
                if parsed['question'] or not parsed['currency']:
                    result.update(status='clarify',question=dict(key=parsed['question'] or 'currency',choices=[]),payload=None)
                else:
                    a=parsed['amount'] or dict(currency=parsed['currency'],precision='unknown',value=None,range_min=None,range_max=None)
                    result.update(status='supported',question=None,payload=dict(text=payload['text']+'\nNamed by user: '+answer.strip(),title=answer.strip(),amount=a,currency=parsed['currency']))
            else:
                result['reason']='answer_not_specific'
        else:
            result.update(status='clarify',payload=None,reason='answer_stale',question=dict(key='target',choices=[]))
    reviewed_currency = None if name_resolved else payload.get('reviewed_currency')
    if reviewed_currency is not None:
        current_question = (result.get('question') or {}).get('key')
        target = next((row for row in objects if row['ref'] == result.get('target')), None)
        if reviewed_currency in ('CNY','SGD','EUR') and current_question=='currency' and result.get('operation')=='create':
            result.update(_new_future(payload['text'],reviewed_currency))
            result['evidence'].append(dict(kind='explicit_currency_answer',currency=reviewed_currency))
        elif reviewed_currency not in ('CNY', 'SGD', 'EUR') or current_question != 'currency' or target is None or _currency(target):
            result.update(status='clarify', payload=None, reason='answer_stale',
                          question=dict(key='currency', choices=[]))
        else:
            target['currency'] = reviewed_currency
            result = resolve_meaning(payload.get('text'), objects, context=context,
                                     aliases=payload.get('aliases'), antecedent=payload.get('antecedent'), _reviewed_target=reviewed_target)
            result['evidence'].append(dict(kind='explicit_currency_answer', currency=reviewed_currency))
    # An explicit If-only denomination correction is not FX. Preserve the
    # reported numeric amount and rebind to the current unique semantic target.
    if (result.get('question') or {}).get('key') == 'currency_conflict':
        stated = parse_amount(payload.get('text', ''), None)
        if stated['amount'] is not None:
            result['denomination'] = deepcopy(stated['amount'])
    reviewed_denomination = payload.get('reviewed_denomination')
    if reviewed_denomination is not None:
        stated = result.get('denomination')
        if result['layer'] == 'future' and stated and stated['currency'] == reviewed_denomination and result.get('target'):
            result.update(status='supported', question=None, operation='alternative', amount=deepcopy(stated),
                payload=dict(text=payload['text']+'\nDenomination explicitly corrected; no currency conversion.',
                             node=deepcopy(result['target']), amount=deepcopy(stated), currency=stated['currency']))
            result['evidence'].append(dict(kind='explicit_denomination_correction', currency=stated['currency']))
        else:
            result.update(status='clarify', payload=None, reason='answer_stale', question=dict(key='currency', choices=[]))
    reviewed_amount = payload.get('reviewed_amount')
    if reviewed_amount is not None:
        current_question = (result.get('question') or {}).get('key')
        target = next((row for row in objects if row['ref'] == result.get('target')), None)
        if current_question != 'amount' or target is None or not isinstance(reviewed_amount, str) or len(reviewed_amount) > 100:
            result.update(status='clarify', payload=None, reason='answer_stale',
                          question=dict(key='amount', choices=[]))
        else:
            parsed = parse_amount(reviewed_amount, _currency(target))
            if not parsed['question'] and parsed['amount'] is not None:
                result.update(status='supported', question=None, amount=parsed['amount'], operation='alternative',
                    payload=dict(text=payload['text']+'\nAmount clarified: '+reviewed_amount,
                                 node=deepcopy(target['ref']), amount=deepcopy(parsed['amount']), currency=parsed['currency']))
                result['evidence'].append(dict(kind='explicit_amount_answer', text=reviewed_amount))
            else:
                result.update(status='clarify', payload=None, question=dict(key=parsed['question'] or 'amount', choices=[]))
    if result['status'] == 'clarify':
        # Preserve known meaning for display; this is not an executable payload.
        # The caller must resolve the explicit answer again before proposing.
        result['partial'] = dict(text=payload.get('text'), **({'node': result['target']} if result['target'] else {}), **({'title':result['title']} if result.get('title') else {}))
        if (result.get('question') or {}).get('key') == 'currency':
            result['question']['choices'] = [dict(value=c, label=c) for c in ('CNY', 'SGD', 'EUR')]
        if (result.get('question') or {}).get('key') == 'separate_clauses':
            result['question']['choices'] = [dict(text=c['text'], label=c['text']) for c in result['clauses'][:4]]
    if result.get('payload',{} ) and result['payload'].get('amount') and result.get('target'):
        variables=[r['ref'] for r in records if r.get('node')==result['target'] and r['ref']['kind']=='variable']
        selected=[r for r in records if r.get('variable') in variables and r.get('ref') in (context or {}).get('selected',[])]
        if len(selected)==1:result['payload']['timing_source']=deepcopy(selected[0]['ref'])
    result['binding'] = {name: deepcopy(space.get(name)) for name in ('future_revision', 'financial_revision') if name in space}
    return result
