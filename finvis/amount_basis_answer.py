"""Resolve a scoped price clarification to an existing choice, never money.

Every clause must be a supported assertion of the same basis. Reference nouns
are licensed by the unique paid claim in the existing question. This is not a
new fact extractor, fuzzy matcher, general intent parser or model fallback.
"""
import re
import unicodedata


def amount_basis_answer(answer, paid):
    """Return total/additional or None; preserve the raw answer at the caller."""
    if not isinstance(answer, str) or not 0 < len(answer) <= 1000:
        return None
    text = unicodedata.normalize('NFKC', answer).casefold().replace('’', "'")
    # A choice-only answer cannot supply new quantities, uncertainty, negation,
    # questions, recoverability, installments, conditions or other new facts.
    if re.search(r"\d|[一二两三四五六七八九十百千万]|[?？]|\b(?:not|no|never|maybe|perhaps|possibly|unsure|uncertain|either|or|if|unless|refund\w*|return\w*|cancel\w*|except|excluding|only|part|half|some)\b|n['’]t\b|不|没|未|可能|也许|或者|还是|如果|退|取消|部分|一半|只", text):
        return None
    # Normalize grammatical contractions, not arbitrary apostrophes or text.
    text = re.sub(r"\b(it|that|this)'s\b", r'\1 is', text)
    text = re.sub(r"\b(i|we)'ve\b", r'\1 have', text)
    text = re.sub(r'\s+', ' ', text).strip()
    label = paid.get('label', '').strip().casefold()
    source = paid.get('source', '').casefold()
    deposit = bool(re.search(r'\bdeposit\b|定金|订金|押金', label + ' ' + source))
    # A generic reference points only at the already-established unique paid
    # claim. "Deposit" is not an alias for any arbitrary historical expense.
    person = r'(?:i|we)'
    paid_verb = rf'{person} (?:have )?(?:(?:already|previously) )?paid'
    en_refs = [rf'what {paid_verb}', rf'(?:the |my |our )?(?:amount|money) {paid_verb}',
               r'(?:the |my |our )?(?:previous|earlier|original|prior) payment',
               r'(?:the )?(?:already )?paid amount']
    if label and re.fullmatch(r'[a-z][a-z -]{0,79}', label):
        en_refs.append(r'(?:the |my |our )?' + re.escape(label))
    if deposit:
        en_refs.append(r'(?:the |my |our )?(?:(?:paid|original|earlier) )?deposit')
    en_paid = '(?:' + '|'.join(en_refs) + ')'
    total = r'(?:(?:the|this|that) )?(?:full|total|whole|overall)(?: (?:price|cost|amount))?'
    subject = r'(?:(?:(?:it|that|this) is|i mean|we mean|the quote is|the price is) )?'
    extra = r'(?:(?:an?|the) )?(?:additional|extra|incremental)(?: (?:payment|amount|cost|fee))?'
    total_en = [rf'{subject}{total}(?: (?:including|with) {en_paid})?',
                rf'{subject}{total} (?:already )?includes {en_paid}',
                rf'{en_paid} (?:is |was |has been )?(?:already )?(?:included|counted)(?: (?:in|towards) {total})?']
    extra_en = [rf'{subject}{extra}(?: (?:on top of|in addition to|over and above) {en_paid})?',
                rf'{subject}(?:on top of|in addition to|over and above) {en_paid}',
                rf'{person} (?:still )?(?:need|have) to pay (?:this|that|it|this amount|that amount) (?:extra|in addition|on top of {en_paid})']
    zh_refs = [r'(?:之前|以前|原来|已经|已)(?:付|支付)(?:过)?的(?:钱|金额|那笔钱)?',
               r'(?:我|我们)?(?:之前|以前|已经|已)(?:付|支付)(?:过)?的(?:钱|金额)?',
               r'(?:已付款|已付金额|之前的付款)']
    if label and re.fullmatch(r'[\u4e00-\u9fff]{1,30}', label):
        zh_refs.append(re.escape(label))
    if deposit:
        zh_refs.append(r'(?:已付的|之前付的|原来的)?(?:定金|订金|押金)')
    zh_paid = '(?:' + '|'.join(zh_refs) + ')'
    zh_total = r'(?:总价|全价|总额|总金额|总费用|全部费用)'
    zh_subject = r'(?:(?:这|那|这个|那个)?(?:就)?是|指的是|我说的是|我们说的是|报价是)?'
    inside = rf'(?:里面|里边|{zh_total}(?:里|内|里面)?|报价里)'
    total_zh = [rf'{zh_subject}{zh_total}(?:(?:已经|已)?(?:包含|包括|算上|计入){zh_paid})?',
                rf'{zh_subject}(?:包含|包括|算上|计入){zh_paid}的?{zh_total}',
                rf'{zh_paid}(?:已经|已)?(?:算在|计入|包含在|包括在){inside}了?']
    extra_zh = [rf'{zh_subject}(?:追加金额|额外付款|额外费用|补差价|补差|追加付款|追加费用|追加)',
                rf'(?:我|我们)?(?:还)?(?:需要|要|得)?(?:另外|额外)(?:再)?(?:付|支付)(?:这些钱|这笔钱|这个金额|这笔金额)',
                rf'{zh_subject}在{zh_paid}(?:之外|基础上)(?:的)?(?:额外付款|追加金额)']
    # All clauses must agree. A recognized keyword cannot hide another intent.
    clauses = [c.strip() for c in re.split(r'[,.!;，。！；]+|\s+and\s+|而且|并且', text) if c.strip()]
    if not clauses or len(clauses) > 4:
        return None
    meanings = set()
    for clause in clauses:
        matches = {kind for kind, patterns in [('total', total_en + total_zh), ('additional', extra_en + extra_zh)]
                   if any(re.fullmatch(pattern, clause) for pattern in patterns)}
        if len(matches) != 1:
            return None
        meanings.update(matches)
    return next(iter(meanings)) if len(meanings) == 1 else None
