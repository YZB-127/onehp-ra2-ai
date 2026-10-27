# -*- coding: utf-8 -*-
"""build_1hp.py -- 生成「一滴血」版规则：原版 rules.ini + 尤里 rulesmd.ini

关键点（比 5 血更极端，血量只有 1）：
  * Strength / TreeStrength / BridgeStrength -> 1
  * RepairStep / IRepairStep -> 1
      维修费 = 造价 / (血量/RepairStep) * RepairPercent；血量 1 < RepairStep 8 时 1/8 截断成 0 → 除零崩溃。
      把 RepairStep 也改成 1，则 1/1 = 1，安全。
  * 类型表里有、但段落缺失或没有 Strength 键的类型 -> 补 Strength=1（0 血同样会除零）
  * 降负载：MaxDebris<=4 / MinDebris<=1 / 粒子系统每类 1 套 / [OilExplosionWH] DelayKillFrames=15
      （1 血时连环爆炸极多，这层很有必要；连锁伤害本身一点没改）
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
G = Path(r'D:\RAY2\红色警戒2 完整版')
BAK = G / '_crispy_backup'
HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'onehp-ra2-ai'
HP = 1
YR_MARK = b'; RULE*.INI'

NUM = re.compile(r'(?im)^(?P<pre>[ \t]*(?P<key>Tree|Bridge)?Strength[ \t]*=[ \t]*)(?P<num>-?\d+)(?P<rest>[^\r\n]*)')
SEC = re.compile(r'(?im)^\[(?P<name>[^\]\r\n]+)\][^\r\n]*')
TYPES = ['InfantryTypes', 'VehicleTypes', 'AircraftTypes', 'BuildingTypes']
LINE = re.compile(
    r'^(?P<ind>[ \t]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)'
    r'(?P<eq>[ \t]*=[ \t]*)(?P<val>[^;\r\n]*?)(?P<sp>[ \t]*)(?P<cmt>;[^\r\n]*)?(?P<cr>\r?)$'
)
SECLINE = re.compile(r'^\[(?P<name>[^\]]+)\]')


def yr_base() -> str:
    """从 expandmd01.mix 里取 YR 实际生效的 rulesmd.ini（明文，不压缩）"""
    d = (G / 'expandmd01.mix').read_bytes()
    start = d.find(YR_MARK)
    tail = (HERE.parent / '_yr' / 'rulesmd_from_expand01.ini').read_bytes()[-160:]
    end = d.find(tail, start, start + 2_000_000)
    if start < 0 or end < 0:
        raise SystemExit('取不到 YR rulesmd.ini')
    return d[start:end + len(tail)].decode('latin-1')


def tune(text: str) -> tuple:
    m = re.search(r'(?mi)^\[BuildingTypes\]\s*$', text)
    btypes = set()
    if m:
        end = text.find('\n[', m.end() + 1)
        body = text[m.end():end if end > 0 else len(text)]
        btypes = {x.group(1).strip().upper() for x in re.finditer(r'(?m)^\s*\d+\s*=\s*([A-Za-z0-9_]+)', body)}
    lines = text.split('\n')
    maxd, cur = {}, ''
    for ln in lines:
        s = SECLINE.match(ln.strip())
        if s:
            cur = s.group('name').strip().upper()
            continue
        mm = LINE.match(ln)
        if mm and mm.group('key').upper() == 'MAXDEBRIS':
            try:
                maxd[cur] = min(int(mm.group('val').strip()), 4)
            except ValueError:
                pass
    out, stat, cur = [], {}, ''
    for ln in lines:
        s = SECLINE.match(ln.strip())
        if s:
            cur = s.group('name').strip().upper()
            out.append(ln)
            continue
        mm = LINE.match(ln)
        if not mm:
            out.append(ln)
            continue
        key, val = mm.group('key').upper(), mm.group('val').strip()
        new = None
        if key == 'MAXDEBRIS':
            try:
                v = min(int(val), 4)
                if v != int(val):
                    new = str(v); stat['MaxDebris'] = stat.get('MaxDebris', 0) + 1
            except ValueError:
                pass
        elif key == 'MINDEBRIS':
            try:
                v = min(int(val), 1 if maxd.get(cur, 1) > 0 else 0)
                if v != int(val):
                    new = str(v); stat['MinDebris'] = stat.get('MinDebris', 0) + 1
            except ValueError:
                pass
        elif key == 'DAMAGEPARTICLESYSTEMS':
            items = [x.strip() for x in val.split(',') if x.strip()]
            if len(items) > 1:
                pick = ('BigGreySmokeSys' if cur in btypes
                        else ('SmallGreySSys' if 'SmallGreySSys' in items else items[0]))
                if pick != val:
                    new = pick; stat['DamageParticleSystems'] = stat.get('DamageParticleSystems', 0) + 1
        elif key == 'DELAYKILLFRAMES' and cur == 'OILEXPLOSIONWH':
            if val != '15':
                new = '15'; stat['DelayKillFrames'] = stat.get('DelayKillFrames', 0) + 1
        out.append(ln if new is None else
                   f"{mm.group('ind')}{mm.group('key')}{mm.group('eq')}{new}"
                   f"{mm.group('sp') or ''}{mm.group('cmt') or ''}{mm.group('cr')}")
    return '\n'.join(out), stat


def convert(text: str, label: str) -> str:
    counts = {}

    def repl(m):
        key = m.group('key') or 'Strength'
        counts[key] = counts.get(key, 0) + 1
        return f'{m.group("pre")}{HP}{m.group("rest")}'

    text = NUM.sub(repl, text)

    for k in ['RepairStep', 'IRepairStep']:
        text, n = re.subn(r'(?im)^([ \t]*' + k + r'[ \t]*=[ \t]*)(\d+)', lambda m: m.group(1) + str(HP), text)
        counts[k] = n

    sections = {}
    for m in SEC.finditer(text):
        nxt = SEC.search(text, m.end())
        sections[m.group('name').strip().lower()] = (m, text[m.end(): nxt.start() if nxt else len(text)])
    missing = []
    for t in TYPES:
        tm = re.search(r'(?im)^\[' + t + r'\][^\r\n]*\r?\n', text)
        if not tm:
            continue
        nxt = SEC.search(text, tm.end())
        body = text[tm.end(): nxt.start() if nxt else len(text)]
        for nm in (x.group(1).strip() for x in re.finditer(r'(?m)^[ \t]*\d+[ \t]*=[ \t]*([A-Za-z0-9_]+)', body)):
            low = nm.lower()
            if low not in sections:
                missing.append((nm, 'no-section'))
            elif not re.search(r'(?im)^[ \t]*Strength[ \t]*=', sections[low][1]):
                missing.append((nm, 'no-key'))
    inserts, appended = [], []
    for nm, why in missing:
        if why == 'no-key':
            nl = text.find('\n', sections[nm.lower()][0].end())
            inserts.append(nl + 1)
        else:
            appended.append(nm)
    for pos in sorted(inserts, reverse=True):
        text = text[:pos] + f'Strength={HP}\r\n' + text[pos:]
    if appended:
        if not text.endswith('\r\n'):
            text += '\r\n'
        text += ('\r\n; --- 1HP fix: listed in Type lists but section missing ---\r\n'
                 '; --- engine default Strength 0 -> repair divide-by-zero ---\r\n')
        for nm in appended:
            text += f'[{nm}]\r\nStrength={HP}\r\n'

    text, tstat = tune(text)
    print(f'  {label}: 改动 {counts}')
    print(f'  降负载 {tstat}')
    print(f'  补血类型: 补键 {len(inserts)} 个, 补段落 {len(appended)} 个 -> {appended}')
    return text


def main() -> int:
    OUT.mkdir(exist_ok=True)
    jobs = [('原版', BAK / 'rules.ini', 'rules.ini', None),
            ('尤里', None, 'rulesmd.ini', yr_base())]
    for label, src, name, pre in jobs:
        raw = pre if pre is not None else open(src, 'r', encoding='latin-1', newline='').read()
        print(f'== {label} == 基线 {len(raw.encode("latin-1"))} B')
        text = convert(raw, label)
        dst = OUT / name
        with open(dst, 'w', encoding='latin-1', newline='') as fh:
            fh.write(text)
        vals = {x.group(1).split(';')[0].strip() for x in
                re.finditer(r'(?mi)^[ \t]*(?:Tree|Bridge)?Strength[ \t]*=[ \t]*(\S+)', text)}
        print(f'  -> {name} {len(text.encode("latin-1"))} B   Strength 取值 {sorted(vals)}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
