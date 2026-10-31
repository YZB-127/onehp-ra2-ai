#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一滴血的红警 · 一键应用 / 撤销（原版红警2 + 尤里的复仇）

用法:
    python apply_onehp_mod.py "<游戏目录>"                # 只处理原版红警2（rules.ini）
    python apply_onehp_mod.py "<游戏目录>" --yr           # 只处理尤里的复仇（rulesmd.ini）
    python apply_onehp_mod.py "<游戏目录>" --all          # 两个都处理
    python apply_onehp_mod.py "<游戏目录>" --undo         # 从备份还原（默认原版那边）
    python apply_onehp_mod.py "<游戏目录>" --all --undo   # 两个都还原
    python apply_onehp_mod.py "<游戏目录>" --check        # 只看当前状态

规则口径:
    * 单位（步兵/载具/飞机）Strength -> 1
    * 建筑（含建造厂、防御、超武）Strength -> 2
      建筑不能写 1：最大血量=1 会踩到引擎按血量比例算的逻辑，把地形/水面渲染成一片青色。
      2 血和 1 血实战手感一样（任何武器都是秒），但能绕开这个边界。
    * RepairStep / IRepairStep -> 1   否则 1/8 被截断成 0，维修时除零崩溃
    * 例外（保留原版值，见 rules 文件本身）：工程师 75、墙/沙袋/栅栏、藤树、旗杆等

地图处理（等长替换，打包文件大小一字节不变）:
    * 任务图里"地图自带血量覆盖" -> 压成 2；原本 > 1500 的任务目标保留原值
      （要占领的建造厂 30000、核电站 32000、导弹井 100000 …压掉会让关卡直接没法打）
    * 两版盟军最后一关：给建造厂写 Strength=1500
      红警2 ALL12（超时空风暴）、尤里 ALL07md。定位靠地图 [Basic] 里的 Briefing 标记，
      不靠编号猜；腾字节的做法是把图里 0.880000 这类小数去掉无意义尾零（等值）。

被改的文件都会先备份到 <游戏目录>\\_onehp_backup\\（原版）或 _onehpmd_backup\\（尤里），
--undo 从那里还原。game.exe 若被旧的"5 血维修补丁"改过，会自动还原。
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent

MAP_HP = 2          # 地图自带覆盖压到这个值（建筑不能是 1，见上面说明）
BOOST_HP = 1500     # 盟军最后一关的建造厂
KEEP_ABOVE = 1500   # 地图里原本比这更硬的任务目标：保留原值

MODES: dict[str, dict] = {
    'ra2': dict(
        label='原版红警2',
        exe='game.exe',
        rules='rules.ini',
        backup='_onehp_backup',
        mixes=['maps01.mix', 'maps02.mix', 'MULTI.MIX', 'EB2.mmx', 'Hills.mmx'],
        keep={'sapc', 'canewy04', 'gacnst'},
        final_map=dict(
            pack='maps01.mix',
            mark=b'Brief:ALL12',          # 盟军最后一关：超时空风暴
            block=b'[GACNST]\nStrength=1500\n',
        ),
        exe_patch=True,
    ),
    'yr': dict(
        label='尤里的复仇',
        exe='gamemd.exe',
        rules='rulesmd.ini',
        backup='_onehpmd_backup',
        mixes=['mapsmd03.mix', 'multimd.mix', 'DeepFrze.yro', 'HighExpR.yro',
               'MonsterM.yro', 'Transylv.yro'],
        keep={'gacnst', 'nacnst'},
        final_map=dict(
            pack='mapsmd03.mix',
            mark=b'Brief:ALL07md',        # 盟军最后一关：要占领废弃苏军基地那关
            block=b'[GACNST]\nStrength=1500\n[NACNST]\nStrength=1500\n',
        ),
        exe_patch=False,
    ),
}

EXE_OFFSET = 0x2DAEDC
EXE_VANILLA = bytes.fromhex('f7b934130000')
EXE_5HP = bytes.fromhex('b80500000090')

STR_LINE = re.compile(rb'(?im)^([ \t]*)Strength([ \t]*=[ \t]*)(\d+)')
SEC_NAME = re.compile(rb'(?im)^[ \t]*\[([^\]\r\n]{1,40})\]')
ACTIVE = re.compile(rb'(?im)^[ \t]*(?:Tree|Bridge)?Strength[ \t]*=[ \t]*(\S+)')
def section_pattern(name: bytes) -> bytes:
    """匹配某个段落的整块（含段体）"""
    return rb'(?ims)^\[' + name + rb'\][^\r\n]*\r?\n(.*?)(?=^\[|\Z)'
DECIMAL = re.compile(rb'(?m)^([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(-?\d+\.\d+)[ \t]*\r?$')
MANIFEST = 'manifest.txt'


# ---------------------------------------------------------------- 备份

def backup_of(path: Path, bak_dir: Path) -> bool:
    """第一次见到的文件才备份（保留最原始的一份）；返回是否真的备份了"""
    bak_dir.mkdir(exist_ok=True)
    dst = bak_dir / path.name
    if dst.exists():
        return False
    shutil.copy2(path, dst)
    return True


def manifest_mode(bak: Path, rules_name: str) -> str | None:
    """读到 rules 文件当初是 created 还是 replaced；没有记录返回 None"""
    man = bak / MANIFEST
    if not man.is_file():
        return None
    for line in man.read_text(encoding='utf-8').splitlines():
        name, _, how = line.partition('\t')
        if name == rules_name:
            return how
    return None


# ---------------------------------------------------------------- 地图改血量

def flatten(path: Path, keep: set[str]) -> dict:
    """把地图里的 Strength 覆盖压成 MAP_HP（空格补齐，保持文件等长）"""
    data = path.read_bytes()
    out = bytearray(data)
    res = dict(kept=[], changed=0, size=len(data))
    target = str(MAP_HP).encode()
    for m in STR_LINE.finditer(data):
        secs = SEC_NAME.findall(data, 0, m.start())
        sec = secs[-1].decode('latin-1').strip() if secs else ''
        digits = m.group(3)
        if sec.lower() in keep or int(digits) > KEEP_ABOVE:
            res['kept'].append(f'[{sec}]={digits.decode()}')
            continue
        if digits == target:
            continue
        out[m.start(3):m.end(3)] = target + b' ' * (len(digits) - len(target))
        res['changed'] += 1
    if len(out) != len(data):
        raise SystemExit(f'!! {path.name}: 长度变化，已中止')
    if res['changed']:
        path.write_bytes(bytes(out))
    return res


def _make_room(seg: bytes, region: int, need: int) -> list[tuple[int, bytes, bytes]] | None:
    """在图内找出等价改写，腾出 need 字节（小数去尾零：0.880000 -> 0.88）

    返回 [(绝对位置, 原字节, 新字节)]，总长度不变；腾不够则返回 None。
    """
    jobs, freed = [], 0
    for m in DECIMAL.finditer(seg):
        old = m.group(2)
        short = old.rstrip(b'0').rstrip(b'.') or b'0'
        try:
            if len(short) >= len(old) or float(short) != float(old):
                continue
        except ValueError:
            continue
        jobs.append((region + m.start(2), old, short))
        freed += len(old) - len(short)
        if freed >= need:
            return jobs
    return None


def boost_final_conyard(path: Path, mark: bytes, block: bytes) -> str:
    """给"盟军最后一关"那张图的建造厂写入 Strength=1500

    MIX 打包文件长度不能变（索引里存着偏移和大小），所以先等价腾字节，再插入覆盖段，
    差额用空行补齐。幂等：已经有 Strength 就跳过。
    """
    data = bytearray(path.read_bytes())
    orig_len = len(data)
    raw = bytes(data)

    starts = [m.start() for m in re.finditer(rb'(?im)^\[Basic\]', raw)]
    bounds = list(zip(starts, starts[1:] + [len(raw)]))
    target = None
    for idx, (start, end) in enumerate(bounds):
        basic = re.search(section_pattern(rb'Basic'), raw[start:end])
        if basic and mark in basic.group(1):
            target = (idx, start, end)
            break
    if target is None:
        return f'没找到 {mark.decode()} 地图，跳过'
    idx, region, region_end = target
    seg = raw[region:region_end]

    existing = re.search(section_pattern(rb'GACNST'), seg)
    if existing and re.search(rb'(?im)^[ \t]*Strength[ \t]*=', existing.group(1)):
        return f'第 {idx+1} 张图({mark.decode()})的 [GACNST] 已经有 Strength，跳过'
    if existing:
        block = f'Strength={BOOST_HP}\n'.encode()      # 只往已有的 [GACNST] 段里补一行

    jobs = _make_room(seg, region, len(block))
    if jobs is None:
        return f'第 {idx+1} 张图({mark.decode()})腾不出 {len(block)} 字节，跳过'
    freed = sum(len(old) - len(new) for _, old, new in jobs)
    for pos, old, new in sorted(jobs, reverse=True):
        data[pos:pos + len(old)] = new

    shifted = bytes(data)[region:region_end]
    if existing:
        anchor = region + re.search(section_pattern(rb'GACNST'), shifted).start(1)
    else:
        anchor_map = re.search(rb'(?m)^\[Map\]', shifted)
        if not anchor_map:
            return '没找到 [Map] 段，跳过'
        anchor = region + anchor_map.start()

    data[anchor:anchor] = block + b'\n' * (freed - len(block))
    if len(data) != orig_len:
        raise SystemExit('!! 地图长度变化，已中止')
    path.write_bytes(bytes(data))
    return f'第 {idx+1} 张图({mark.decode()})：建造厂 Strength={BOOST_HP} 已写入（腾挪 {freed} 字节，长度不变）'


# ---------------------------------------------------------------- exe / 状态

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
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(rules.read_bytes())})
        m = re.search(r'(?im)^[ \t]*RepairStep[ \t]*=[ \t]*(\d+)',
                      rules.read_text(encoding='latin-1', errors='replace'))
        print(f'  {cfg["rules"]:13s} {rules.stat().st_size:>9} B   Strength 取值 {vals}  '
              f'RepairStep={m.group(1) if m else "?"}')
    else:
        print(f'  {cfg["rules"]:13s} （没有散装文件，游戏用打包里那份 = 原版血量）')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'    {name:14s} 不存在')
            continue
        vals = sorted({m.group(1).decode() for m in ACTIVE.finditer(p.read_bytes())})
        print(f'    {name:14s} {p.stat().st_size:>9} B   覆盖取值 {vals}')
    exe = game / cfg['exe']
    if exe.is_file():
        print(f'  {cfg["exe"]:13s} {exe.stat().st_size:>9} B   {exe_state(exe, cfg)}')


# ---------------------------------------------------------------- 应用 / 撤销

def apply_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    print(f'== {cfg["label"]} ==')
    src_rules = HERE / cfg['rules']
    if not src_rules.is_file():
        print(f'  !! 脚本旁边没有 {cfg["rules"]}')
        return 1

    bak = game / cfg['backup']
    dst_rules = game / cfg['rules']
    bak.mkdir(exist_ok=True)
    if manifest_mode(bak, cfg['rules']) is None:
        created = not dst_rules.is_file()
        if not created:
            backup_of(dst_rules, bak)
        with open(bak / MANIFEST, 'a', encoding='utf-8') as fh:
            fh.write(f'{cfg["rules"]}\t{"created" if created else "replaced"}\n')
    shutil.copy2(src_rules, dst_rules)
    print(f'  [1/4] {cfg["rules"]} 已写入（单位 1 血 / 建筑 {MAP_HP} 血 + RepairStep=1）')

    print('  [2/4] 任务图里的地图自带覆盖:')
    for name in cfg['mixes']:
        p = game / name
        if not p.is_file():
            print(f'        {name:14s} 不存在，跳过')
            continue
        backup_of(p, bak)
        res = flatten(p, cfg['keep'])
        extra = f'   保留原值 {len(res["kept"])} 处: {", ".join(res["kept"])}' if res['kept'] else ''
        print(f'        {name:14s} 压成 {MAP_HP} {res["changed"]:3d} 处 ({res["size"]} 字节){extra}')

    fm = cfg.get('final_map')
    if fm:
        p = game / fm['pack']
        if p.is_file():
            backup_of(p, bak)
            print(f'  [3/4] {p.name}: {boost_final_conyard(p, fm["mark"], fm["block"])}')

    exe = game / cfg['exe']
    if not exe.is_file():
        print(f'  [4/4] !! 找不到 {cfg["exe"]}')
    elif exe_state(exe, cfg).startswith('被'):
        if (bak / cfg['exe']).is_file():
            shutil.copy2(bak / cfg['exe'], exe)
            print(f'  [4/4] {cfg["exe"]}: 之前被打过 5 血补丁 -> 已还原原版')
        else:
            print(f'  [4/4] !! {cfg["exe"]} 被打过 5 血补丁且没有备份')
    else:
        print(f'  [4/4] {cfg["exe"]}: {exe_state(exe, cfg)}   RepairStep 必须是 1')
    print()
    return 0


def undo_mode(game: Path, mode: str) -> int:
    cfg = MODES[mode]
    bak = game / cfg['backup']
    print(f'== 撤销 {cfg["label"]} ==')
    if not bak.is_dir():
        print(f'  没有备份目录 {bak}，跳过\n')
        return 0
    for f in sorted(bak.iterdir()):
        if f.is_file() and f.name != MANIFEST:
            shutil.copy2(f, game / f.name)
            print(f'  还原 {f.name}')
    if manifest_mode(bak, cfg['rules']) == 'created':
        rules = game / cfg['rules']
        if rules.is_file():
            rules.unlink()
            print(f'  删除（原本没有的）{cfg["rules"]}')
    print()
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
    print('完成。请完全退出游戏后重开（规则、地图覆盖只在加载时读一次）。')
    print('注意：存档自带当时的场景数据，应用后请【新开】关卡/遭遇战，别读旧存档。')
    print(f'撤销: python {Path(__file__).name} "{game}" --undo')
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
