"""Read-only semantic hints; never authorizes a transaction or converts money."""
import re

def flow_direction(text):
    if re.search(r'\btransfer\b|转账|从.+转.{0,30}(?:到|至)', text, re.I):
        return 'TRANSFER'
    incoming=bool(re.search(r'\b(?:give|gives)\s+(?:me|us)\b|\b(?:salary|bonus|inflow|receive|received)\b|\bgift\s+from\b|给我|给的钱|工资|奖金|收入|流入',text,re.I))
    outgoing=bool(re.search(r'\b(?:buy|buying|costs?|spend|spending|expense|purchase|donate)\b|\bgive\b.{0,40}\bto\b|我(?:会|可能|打算|要)?给(?:他|她|他们|别人)|购买|买|支出|要花|花费|费用',text,re.I))
    return 'INFLOW' if incoming and not outgoing else 'OUTFLOW' if outgoing and not incoming else 'UNKNOWN'

def effect_direction(effect_type):
    return {'expected_inflow':'INFLOW','cost':'OUTFLOW'}.get(effect_type,'UNKNOWN')
