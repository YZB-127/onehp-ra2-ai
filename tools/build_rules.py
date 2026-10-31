#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_rules.py -- 按"一滴血"口径把一份规则文件规整成目标血量

用法:
    python tools/build_rules.py <源 rules 文件> <输出文件> [--check]

口径:
    * 单位（InfantryTypes / VehicleTypes / AircraftTypes）Strength -> 1
    * 建筑（BuildingTypes）Strength -> 2      ← 不能写 1，见 README「建筑为什么是 2」
    * RepairStep / IRepairStep -> 1            ← 否则 血量/8 截断成 0，维修时除零崩溃
    * 下表 KEEP 里的类型保留原版值（工程师、墙/沙袋/栅栏、藤树、旗杆、UFO）

幂等：对已经规整过的文件再跑一次，结果不变。
"""
from __future__ import annotations

import argparse
import re
import sys

UNIT_LISTS = ('InfantryTypes', 'VehicleTypes', 'AircraftTypes')
BUILDING_LIST = 'BuildingTypes'

# 保留原版值的类型（键一律小写）
KEEP = {
    'engineer': 75, 'sengineer': 75,                       # 任务工具：占领 / 拆弹
    'gawall': 300, 'nawall': 300, 'gafwll': 300,           # 围墙 / 铁丝网
    'gasand': 100, 'cafncb': 100, 'cafncw': 100,            # 沙袋 / 栅栏
    'cakrmw': 300, 'cafncp': 100,
    'veintree': 1000, 'ufo': 1000, 'cairfgl': 20,           # 装饰 / 占位
    'catime01': 1000, 'catime02': 1000,                     # 时间机器（尤里苏军第一关的任务物件）
}
UNIT_HP, BUILDING_HP = 1, 2


def members(text: str, name: str) -> list[str]:
    m = re.search(r'(?ims)^\[' + name + r'\][^\r\n]*\r?\n(.*?)(?=^\[|\Z)', text)
    if not m:
        return []
    return [x.lower() for x in re.findall(r'(?m)^[ \t]*\d+[ \t]*=[ \t]*([A-Za-z0-9_]+)', m.group(1))]


def classify(text: str) -> tuple[set[str], set[str]]:
    units: set[str] = set()
    for lst in UNIT_LISTS:
        units |= set(members(text, lst))
    buildings = set(members(text, BUILDING_LIST)) - units
    return units, buildings


def rewrite(src: str) -> tuple[str, dict]:
    units, buildings = classify(src)
    lines = src.split('\n')
    sec = None
    stats = dict(units=0, buildings=0, keep=0, repair=0)
    for i, ln in enumerate(lines):
        m = re.match(r'^\s*\[([^\]]+)\]', ln)
        if m:
            sec = m.group(1).strip().lower()
            continue
        if sec is None or ln.lstrip().startswith(';'):
            continue
        m = re.match(r'^(\s*Strength\s*=\s*)(\d+)(.*)$', ln, re.I)
        if m:
            if sec in KEEP:
                lines[i] = m.group(1) + str(KEEP[sec]) + m.group(3)
                stats['keep'] += 1
            elif sec in units:
                lines[i] = m.group(1) + str(UNIT_HP) + m.group(3)
                stats['units'] += 1
            elif sec in buildings:
                lines[i] = m.group(1) + str(BUILDING_HP) + m.group(3)
                stats['buildings'] += 1
            continue
        m = re.match(r'^(\s*I?RepairStep\s*=\s*)(\d+)(.*)$', ln, re.I)
        if m:
            lines[i] = m.group(1) + '1' + m.group(3)
            stats['repair'] += 1
    return '\n'.join(lines), stats


def main() -> int:
    ap = argparse.ArgumentParser(description='按一滴血口径规整规则文件')
    ap.add_argument('source')
    ap.add_argument('output', nargs='?')
    ap.add_argument('--check', action='store_true', help='只报告会改多少处，不写文件')
    args = ap.parse_args()

    with open(args.source, 'r', encoding='latin-1', newline='') as fh:
        src = fh.read()
    out, stats = rewrite(src)
    print(f'单位 -> {UNIT_HP}: {stats["units"]} 处   建筑 -> {BUILDING_HP}: {stats["buildings"]} 处   '
          f'保留原版: {stats["keep"]} 处   RepairStep: {stats["repair"]} 处')
    if args.check:
        changed = sum(1 for a, b in zip(src.split('\n'), out.split('\n')) if a != b)
        print(f'需要改动 {changed} 行（--check 不写文件）')
        return 0
    if not args.output:
        print('缺少输出文件路径', file=sys.stderr)
        return 2
    # 统一 CRLF，保证同一份输入在任何机器上产出逐字节相同的文件
    out = '\r\n'.join(line.rstrip('\r') for line in out.split('\n'))
    with open(args.output, 'w', encoding='latin-1', newline='') as fh:
        fh.write(out)
    print(f'已写入 {args.output}（{len(out.encode("latin-1"))} B）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
