#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一滴血的红警 · 一键应用 / 撤销脚本（原版红警2 + 尤里的复仇）

用法:
    python apply_onehp_mod.py "<游戏目录>"                 # 只改原版红警2（rules.ini）
    python apply_onehp_mod.py "<游戏目录>" --yr            # 只改尤里的复仇（rulesmd.ini）
    python apply_onehp_mod.py "<游戏目录>" --all           # 两个都改
    python apply_onehp_mod.py "<游戏目录>" --undo          # 还原（默认还原原版那边）
    python apply_onehp_mod.py "<游戏目录>" --all --undo    # 还原两个
    python apply_onehp_mod.py "<游戏目录>" --check         # 只看状态

做了什么:
    1. 规则文件 -> 覆盖（原版 rules.ini / 尤里 rulesmd.ini）
         * 所有 Strength / TreeStrength / BridgeStrength -> 1
         * RepairStep / IRepairStep -> 1   ← 关键！1/8 会被截断成 0，直接用原版值必崩
         * 0 血类型补 Strength=1
         * 降负载（1 血时连环爆炸极多，压碎片/烟尘）
    2. 任务包/地图里的"地图自带血量覆盖" -> 全部压成 1（等长替换，文件大小不变）
       例外：原版 [SAPC] 气垫船、[CANEWY04] 自由女神像 保留任务原值
             （这两个是任务脚本依赖的，压成 1 会直接过不了关）
    3. game.exe 若被 5 血维修补丁改过，自动还原（那个补丁把商固定成 5，1 血会被多收维修费）

被修改的文件会先备份到 <游戏目录>\\_onehp_backup\\（原版）或 _onehpmd_backup\\（尤里）
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HP = 1

MODES = {
    'ra2': dict(
        label='原版红警2',
        exe='game.exe',
        rules='rules.ini',
        backup='_onehp_backup',
        mixes=['maps01.mix', 'maps02.mix', 'MULTI.MIX', 'EB2.mmx', 'Hills.mmx'],
        keep={'sapc', 'canewy04'},
        exe_patch=True,
    ),
    'yr': dict(
        label='尤里的复仇',
        exe='gamemd.exe',
        rules='rulesmd.ini',
        backup='_onehpmd_backup',
        mixes=['mapsmd03.mix', 'multimd.mix', 'DeepFrze.yro', 'HighExpR.yro',
               'MonsterM.yro', 'Transylv.yro'],
        keep=set(),
        exe_patch=False,
    ),
}

EXE_OFFSET = 0x2DAEDC
EXE_VANILLA = bytes.fromhex('f7b934130000')
EXE_5HP = bytes.fromhex('b80500000090')

STR_LINE = re.compile(rb'(?im)^([ \t]*)Strength([ \t]*=[ \t]*)(\d+)')
SEC_NAME = re.compile(rb'(?im)^[ \t]*\[([^\]\r\n]{1,40})\]')
ACTIVE = re.compile(rb'(?im)^[ \t]*(?:Tree|Bridge)?Strength[ \t]*=[ \t]*(\S+)')
MANIFEST = 'manifest.txt'


def read_ini_sections(path: Path) -> set:
    with open(path, 'r', encoding='latin-1', newline='') as fh:
        text = fh.read()
    return {m.group(1).strip().lower() for m in re.finditer(r'(?im)^\[([^\]\r\n]+)\]', text)}


def backup_of(path: Path, bak_dir: Path) -> bool:
    bak_dir.mkdir(exist_ok=True)
    dst = bak_dir / path.name
    if not dst.exists():
        shutil.copy2(path, dst)
        return True
    return False


def flatten(path: Path, keep: set) -> dict:
    """把每个 Strength 覆盖压成 1（空格补齐保持等长）；keep 里的段落原样保留"""
    data = path.read_bytes()
    out = bytearray(data)
    res = dict(kept=[], set1=0, size=len(data))
    for m in STR_LINE.finditer(data):
        secs = SEC_NAME.findall(data, 0, m.start())
        sec = secs[-1].decode('latin-1').strip() if secs else ''
        digits = m.group(3)
        if sec.lower() in keep:
            res['kept'].append(f'[{sec}]={digits.decode()}')
            continue
        if digits == b'1':
            continue
        out[m.start(3):m.end(3)] = b'1' + b' ' * (len(digits) - 1)
        res['set1'] += 1
    if len(out) != len(data):
        raise SystemExit(f'!! {path.name}: 长度变化，已中止')
    if res['set1']:
        path.write_bytes(bytes(out))
    return res


def exe_state(exe: Path, cfg: dict) -> str:
    if not cfg.get('exe_patch'):
        return '不依赖 exe 补丁（RepairStep=1 已足够）'
    with open(exe, 'rb') as fh:
        fh.seek(EXE_OFFSET)
        raw = fh.read(6)
    if raw == EXE_VANILLA:
        return '原版未打补丁'
    if raw == EXE_5HP:
        return '被 5 血维修补丁改过（要还原）'
    return f'未知({raw.hex()})'


def report(game: Path, cfg: dict) -> None:
    rules = game / cfg['rules']
    if rules.is_file():
        vals = sorted({m.group(1) for m in ACTIVE.finditer(rules.read_bytes())})
        rp = re.search(r'(?im)^[ \t]*RepairStep[ \t]*=[ \t]*(\d+)',
                       rules.read_text(encoding='latin-1', errors='replace'))
        print(f'  {cfg["rules"]:13s} {rules.stat().st_size:>9} B   Strength 取值 {vals}  '
              f'RepairStep={rp.group(1) if rp else "?"}')
    else:
        print(f'  {cfg["rules"]:13s} （没有散装文件，游戏用打包里那份 = 原版血量）')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'    {name:14s} 不存在')
            continue
        d = p.read_bytes()
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(d)})
        print(f'    {name:14s} {len(d):>9} B   覆盖取值 {vals}')
    exe = game / cfg['exe']
    if exe.is_file():
        print(f'  {cfg["exe"]:13s} {exe.stat().st_size:>9} B   {exe_state(exe, cfg)}')


def apply_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    bak = game / cfg['backup']
    src_rules = HERE / cfg['rules']
    print(f'== {cfg["label"]} ==')
    if not src_rules.is_file():
        print(f'  !! 脚本旁边没有 {cfg["rules"]}')
        return 1

    dst_rules = game / cfg['rules']
    bak.mkdir(exist_ok=True)
    man = bak / MANIFEST
    first_time = True
    if man.is_file():
        first_time = not any(line.startswith(cfg['rules'] + '\t')
                             for line in man.read_text(encoding='utf-8').splitlines())
    if first_time:
        created = not dst_rules.is_file()
        if not created:
            backup_of(dst_rules, bak)
        with open(man, 'a', encoding='utf-8') as fh:
            fh.write(f'{cfg["rules"]}\t{"created" if created else "replaced"}\n')
    shutil.copy2(src_rules, dst_rules)
    print(f'  [1/4] {cfg["rules"]} 已写入（全员 {HP} 血 + RepairStep=1 + 降负载）')

    print('  [2/4] 任务包/地图里的地图自带覆盖:')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'        {name:14s} 不存在，跳过')
            continue
        backup_of(p, bak)
        res = flatten(p, cfg['keep'])
        extra = f'  保留任务原值 {len(res["kept"])} 处: {", ".join(res["kept"])}' if res['kept'] else ''
        print(f'        {name:14s} 压成 {HP} {res["set1"]:3d} 处 ({res["size"]} 字节){extra}')

    exe = game / cfg['exe']
    if not exe.is_file():
        print(f'  [3/4] !! 找不到 {cfg["exe"]}')
    elif exe_state(exe, cfg).startswith('被'):
        if (bak / cfg['exe']).is_file():
            shutil.copy2(bak / cfg['exe'], exe)
            print(f'  [3/4] {cfg["exe"]}: 之前被打过 5 血补丁 -> 已还原原版')
        else:
            print(f'  [3/4] !! {cfg["exe"]} 被打过 5 血补丁且没有备份')
    else:
        print(f'  [3/4] {cfg["exe"]}: {exe_state(exe, cfg)}')

    print(f'  [4/4] 检查: RepairStep 必须是 1（否则 1/8 截断成 0 会除零崩溃）\n')
    return 0


def undo_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    bak = game / cfg['backup']
    print(f'== 撤销 {cfg["label"]} ==')
    if not bak.is_dir():
        print(f'  没有备份目录 {bak}，跳过\n')
        return 0
    created = False
    man = bak / MANIFEST
    if man.is_file():
        for line in man.read_text(encoding='utf-8').splitlines():
            if line.startswith(cfg['rules'] + '\t'):
                created = line.endswith('created')
    n = 0
    for f in sorted(bak.iterdir()):
        if f.is_file() and f.name != MANIFEST:
            shutil.copy2(f, game / f.name)
            n += 1
            print(f'  还原 {f.name}')
    if created and (game / cfg['rules']).is_file():
        (game / cfg['rules']).unlink()
        print(f'  删除（原本没有的）{cfg["rules"]}')
    print(f'  共处理 {n} 个文件\n')
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description='一滴血的红警 一键应用/撤销')
    ap.add_argument('gamedir')
    ap.add_argument('--yr', action='store_true', help='只处理尤里的复仇')
    ap.add_argument('--all', action='store_true', help='原版 + 尤里 都处理')
    ap.add_argument('--undo', action='store_true', help='从备份还原')
    ap.add_argument('--check', action='store_true', help='只看状态')
    args = ap.parse_args()

    game = Path(args.gamedir)
    if not game.is_dir():
        print(f'找不到目录: {game}')
        return 1
    modes = ['ra2', 'yr'] if args.all else (['yr'] if args.yr else ['ra2'])

    if args.check:
        print(f'目录: {game}')
        for m in modes:
            print(f'\n[{MODES[m]["label"]}]')
            report(game, MODES[m])
        return 0

    if args.undo:
        for m in modes:
            undo_mode(game, m)
        return 0

    rc = 0
    for m in modes:
        rc |= apply_mode(game, m)
    print('完成。请完全退出游戏后重开（规则、地图覆盖都只在加载时读一次）。')
    print('注意: 存档自带当时的场景数据，应用后请【新开】关卡/遭遇战，别读旧存档。')
    print(f'撤销: python {Path(__file__).name} "{game}" --undo')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
