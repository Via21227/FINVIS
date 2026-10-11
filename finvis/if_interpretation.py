"""D2 bounded bilingual preparation, not a model or a financial writer.

Only literal, unambiguous cues are prepared. Other language stays unresolved
for explicit user clarification. The whole original utterance is retained.
"""
import re
from decimal import Decimal

from .decision_baseline import text


def hypothetical(value):
    return bool(re.search(r'\b(?:what\s+if|if|suppose)\b|如果|假如|假设|要是', value, re.I))


def _number(value):
    return str(Decimal(value.replace(',', '')))


def interpret(value, targets, context):
    value = text(value)
    draft = dict(kind=None, label=value, category='other', target=None,
                 amount=None, parameter=None, depends_on=[], assumptions=[value])
    if not hypothetical(value):
        return dict(status='not_hypothetical', draft=draft, questions=['hypothetical_only'])
    # Conflicting cues intentionally do not default to addition.
    cues = {
        'remove': r'\b(?:cancel(?:led|ed)?|remove|without|does\s+not\s+arrive|doesn.t\s+arrive|no\s+longer|(?:do\s+not|don.t|not)\s+(?:buy|attend|include))\b|取消|没有了|没了|不(?:再)?(?:来|到|发)|不买|去掉',
        'replace': r'\b(?:replace|upgrade|switch)\b|替换|升级|换成',
        'parameter': r'\b(?:days?|nights?|people|quantity|units?)\b.*\b(?:to|instead)\b|(?:天|晚|人|数量|件).*?(?:改成|变成|改为|到)',
        'add': r'\b(?:buy|add|attend|purchase|include)\b|\bgo\s+(?:to|see)\b|买|参加|去看|加上|增加|搬家',
    }
    kinds = [k for k, pattern in cues.items() if re.search(pattern, value, re.I)]
    # "do not buy" is removal; other overlapping meanings need a choice.
    if 'remove' in kinds and re.search(r'不买|\b(?:do\s+not|don.t|not)\s+(?:buy|attend|include)\b', value, re.I):
        kinds = [k for k in kinds if k != 'add']
    if len(kinds) == 1:
        draft['kind'] = kinds[0]
    # A price for an alternative to the same contemplated item substitutes its
    # cost. It is not an additional purchase. Bind automatically only when the
    # baseline has exactly one cost component; richer plans require a target.
    alternative = re.search(r'\b(?:used|second.hand|cheaper|instead|alternative|choose\s+the\s+.+?option)\b|二手|只需要|改买|选择.+?(?:方案|选项)', value, re.I)
    additional = re.search(r'\b(?:another|additional|also|extra)\b|另外|额外|再买|还买|加上', value, re.I)
    costs = [t for t in targets if t.get('kind', 'cost' if t.get('key', '').startswith('component:') else None) == 'cost']
    if alternative and not additional and draft['kind'] in (None, 'add', 'replace'):
        explicit_replace = draft['kind'] == 'replace'
        matched = None
        if len(costs) == 1:
            label = costs[0]['label']
            generic = {'what','used','second','hand','cheaper','instead','alternative',
                       'option','choose','buying','purchase','price','cost','package'}
            words = lambda s: {w.casefold() for w in re.findall(r'[A-Za-z]{4,}',s)} - generic
            pairs = lambda s: {p[i:i+2] for p in re.findall(r'[\u4e00-\u9fff]+',s) for i in range(len(p)-1)}
            shared = bool(words(value) & words(label) or
                          (pairs(value) & pairs(label)) - {'如果','二手','只需','需要','选择','方案','选项'})
            anaphora = bool(re.search(r'\b(?:one|option|same item)\b|这个|这款|同一[个款]|它',value,re.I))
            if shared or anaphora:
                matched = costs[0]['key']
        draft['kind'] = 'replace' if matched or explicit_replace else None
        draft['target'] = matched
    categories = {
        'camera': r'\bcamera\b|相机|镜头', 'concert': r'\bconcert\b|演唱会|音乐会',
        'accommodation': r'\bhotel\b|住宿|酒店', 'training': r'\b(?:course|class|training)\b|课程|培训',
        'moving': r'\b(?:moving|move)\b|搬家', 'electronics': r'\b(?:electronics|computer|laptop)\b|电脑|电子产品',
        'phone': r'\bphone\b|手机', 'flight': r'\bflight\b|航班',
    }
    categories = [k for k,p in categories.items() if re.search(p,value,re.I)]
    if len(categories)==1: draft['category']=categories[0]
    matches = [t['key'] for t in targets if t['label'].casefold() in value.casefold()]
    if len(matches) == 1:
        draft['target'] = matches[0]
    # Currency must be literal; never infer it from location or a balance.
    currencies = [c for c, pattern in [('CNY', r'CNY|RMB|人民币|(?<!欧)(?<!加坡)元|yuan'),
                                      ('SGD', r'SGD|新币|新加坡元|S\$'), ('EUR', r'EUR|欧元')]
                  if re.search(pattern, value, re.I)]
    # Native currency cues remain separate; 欧元 / 新加坡元 are not CNY.
    number = r'(\d+(?:,\d{3})*(?:\.\d+)?)(?![\dkK万千百]|[.,]\d|[eE][+-]?\d)'
    currency = r'(?:CNY|RMB|SGD|EUR|人民币|新币|欧元|S\$)'
    suffix = r'(?:元|yuan|新加坡元|欧元)'
    money_pattern = rf'(?:{currency}\s*(?:about\s+|approximately\s+|约\s*)?{number}(?:\s*(?:–|—|-|to|到|至)\s*{number})?|{number}(?:\s*(?:–|—|-|to|到|至)\s*{number})?\s*{suffix})'
    money_matches = list(re.finditer(money_pattern, value, re.I))
    if len(currencies) == 1 and len(money_matches) == 1 and not re.search(r'\b(?:each|per)\b|每',value,re.I):
        groups = money_matches[0].groups()
        first, last = (groups[0], groups[1]) if groups[0] else (groups[2], groups[3])
        draft['amount'] = dict(currency=currencies[0], precision='range' if last else
            'approximate' if re.search(r'\b(?:about|around|approximately)\b|大约|大概|左右|约|≈', value, re.I) else 'exact',
            value=None if last else _number(first), range_min=_number(first) if last else None,
            range_max=_number(last) if last else None)
    # An unqualified alternative price can inherit the exact single cost's
    # native currency. Never infer it from location, cash, or mixed costs.
    if draft['kind'] == 'replace' and draft['target'] and not draft['amount'] and not currencies and len(costs) == 1:
        numbers = re.findall(r'(?<![A-Za-z0-9.])(\d+(?:,\d{3})*(?:\.\d+)?)(?![A-Za-z0-9.]|\s*(?:days?|nights?|people|天|晚|人))', value, re.I)
        original = costs[0].get('amount') or {}
        native = (original.get('amount') or original.get('lower_bound') or {}).get('currency') or original.get('unknown_currency')
        if len(numbers) == 1 and native in ('CNY', 'SGD', 'EUR'):
            draft['amount'] = dict(currency=native, precision='approximate' if re.search(r'\b(?:about|around|approximately)\b|大约|大概|左右|约|≈', value, re.I) else 'exact',
                                   value=_number(numbers[0]), range_min=None, range_max=None)
    # Literal small Chinese numerals are sufficient for these context fields;
    # all other quantities remain a user clarification, not a guessed number.
    numeric = value
    for char, digit in zip('一二三四五六七八九', '123456789'):
        numeric = re.sub(r'(?<![零一二三四五六七八九十百千万两])'+char+r'(?=天|晚|人|件)', digit, numeric)
    params = [('duration_days', r'days?|天'), ('nights', r'nights?|晚'),
              ('party_size', r'people|persons?|人'), ('quantity', r'units?|quantity|件|数量')]
    parsed_params = []
    for name, unit in params:
        m = re.search(rf'(?<![\d零一二三四五六七八九十百千万两])(\d+)\s*(?:{unit})\s*(?:to|改成|变成|改为|到)\s*(\d+)(?![\d.])', numeric, re.I)
        if m:
            parsed_params.append(dict(name=name, before=int(m[1]), after=int(m[2])))
    if len(parsed_params) == 1 and kinds == ['parameter']:
        draft['kind'], draft['parameter'] = 'parameter', parsed_params[0]
    # Removal always uses the exact bound record/component amount, never a
    # second quoted price in the utterance. ADD cannot accidentally target it.
    if draft['kind'] == 'remove': draft['amount'] = None
    if draft['kind'] == 'add': draft['target'] = None
    questions = []
    if not draft['kind']: questions.append('choose_kind')
    if draft['kind'] in ('remove', 'replace', 'parameter') and not draft['target']:
        questions.append('choose_target')
    if draft['kind'] in ('add', 'replace', 'parameter') and not draft['amount']:
        questions.append('amount_or_unknown')
    if draft['kind'] == 'parameter' and not draft['parameter']:
        questions.append('parameter_values')
    return dict(status='clarification_required' if questions else 'interpreted', draft=draft, questions=questions)
