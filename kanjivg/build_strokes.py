#!/usr/bin/env python3
"""KanjiVG から、アプリ同梱用の筆順 SQLite を作る。

使い方:
    python3 tool/build_strokes.py --work build/dict-src --out assets/strokes.sqlite

--work に main.zip（KanjiVG の個別 SVG 一式）が無ければ GitHub のリリースから
ダウンロードする。統合 XML（kanjivg-*.xml.gz）ではなくこちらを使うのは、
筆順番号の表示座標（`kvg:StrokeNumbers_*`）が個別 SVG にしか入っていないため。

出力する strokes テーブルは「文字 → 1 画ずつの SVG パス ＋ 番号の位置」。
KanjiVG の `<path d="...">` を書き順どおりに並べたものと、`<text>` の
座標（画の番号がある位置。KanjiVG 公式の値）を、1 本の文字列にまとめて
zlib 圧縮し BLOB で持つ（読み出し側は 1 字ぶんだけ展開すればよい）。

BLOB の中身は次の形式（パス列は空行を含まないので \n\n を区切りに使える）:

    <パス1>\n<パス2>\n…<パスN>\n\n<x1>,<y1> <x2>,<y2> … <xN>,<yN>

番号の数がパスの数と一致しない字や、番号がそもそも無い字は、
番号セクション（\n\n 以降）を空文字列にする。

座標系は KanjiVG のまま（109 x 109 の viewBox）なので、描画側は
その正方形を画面サイズへスケールするだけでよい。

データ: KanjiVG (C) Ulrich Apel, CC BY-SA 3.0。
"""

import argparse
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
import zlib

KANJIVG_URL = (
    "https://github.com/KanjiVG/kanjivg/releases/download/"
    "r20250816/kanjivg-20250816-main.zip"
)

# `kanji/06f22.svg` のようなファイル名から符号位置を取る。main.zip には
# `-Kaisho` などの異体字グループは入っていないので、5 桁 16 進のみでよい。
KANJI_FILE = re.compile(r"^kanji/([0-9a-f]{5})\.svg$")

# `<text transform="matrix(1 0 0 1 X Y)">` から座標を取る。
NUMBER_TRANSFORM = re.compile(
    r"matrix\(1 0 0 1 (-?\d+\.?\d*) (-?\d+\.?\d*)\)"
)

# 座標の桁を落として容量を減らす（109 の座標系なので 0.1 で十分細かい）。
NUMBER = re.compile(r"-?\d+\.\d+")


def wanted(code: int) -> bool:
    """漢字・かなだけ入れる。ラテン文字や記号の筆順は要らない。"""
    return (
        0x3041 <= code <= 0x30FF  # ひらがな・カタカナ
        or 0x3400 <= code <= 0x4DBF  # CJK 拡張 A
        or 0x4E00 <= code <= 0x9FFF  # CJK 統合漢字
        or 0xF900 <= code <= 0xFAFF  # 互換漢字
    )


def shrink(d: str) -> str:
    """パスの数値を小数第 1 位に丸める。見た目は変わらないが 2 割ほど小さい。"""
    return NUMBER.sub(lambda m: f"{round(float(m.group()), 1):g}", d)


def download(url: str, dest: str) -> None:
    if os.path.exists(dest):
        print(f"  すでにある: {dest}")
        return
    print(f"  ダウンロード: {url}")
    urllib.request.urlretrieve(url, dest)


def parse_svg(data: bytes) -> tuple[list[str], list[tuple[float, float]]]:
    """1 字ぶんの SVG から (パス列, 番号座標列) を取る。どちらも文書順。"""
    root = ET.fromstring(data)
    paths = [
        shrink(p.get("d")) for p in root.iter("{http://www.w3.org/2000/svg}path")
        if p.get("d")
    ]
    numbers = []
    for text in root.iter("{http://www.w3.org/2000/svg}text"):
        match = NUMBER_TRANSFORM.search(text.get("transform") or "")
        if match:
            numbers.append((round(float(match.group(1)), 1), round(float(match.group(2)), 1)))
    return paths, numbers


def parse_kanjivg(zip_path: str):
    """(文字, 改行区切りのパス列+番号の圧縮 BLOB, 画数) を生成する。"""
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            match = KANJI_FILE.match(name)
            if not match:
                continue
            code = int(match.group(1), 16)
            if not wanted(code):
                continue
            paths, numbers = parse_svg(zf.read(name))
            if not paths:
                continue
            if len(numbers) != len(paths):
                numbers = []
            numbers_text = " ".join(f"{x:g},{y:g}" for x, y in numbers)
            text = "\n".join(paths) + "\n\n" + numbers_text
            blob = zlib.compress(text.encode(), 9)
            yield chr(code), blob, len(paths)


def build(work: str, out: str) -> None:
    os.makedirs(work, exist_ok=True)
    zip_path = os.path.join(work, "main.zip")

    print("1) ソースの準備")
    download(KANJIVG_URL, zip_path)

    if os.path.exists(out):
        os.remove(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    print("2) SQLite を作る")
    db = sqlite3.connect(out)
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE strokes (
          char    TEXT NOT NULL PRIMARY KEY,
          paths   BLOB NOT NULL,  -- パス列＋番号座標を zlib で圧縮したもの
          count   INTEGER NOT NULL
        );
        """
    )

    print("3) KanjiVG を読む")
    rows = 0
    batch = []
    for row in parse_kanjivg(zip_path):
        batch.append(row)
        if len(batch) >= 2000:
            db.executemany("INSERT INTO strokes VALUES (?, ?, ?)", batch)
            rows += len(batch)
            batch = []
    if batch:
        db.executemany("INSERT INTO strokes VALUES (?, ?, ?)", batch)
        rows += len(batch)
    print(f"   KanjiVG: {rows} 字")

    print("4) 仕上げ")
    db.commit()
    db.execute("VACUUM")
    db.close()

    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"完了: {out} ({size_mb:.1f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="build/dict-src",
                        help="main.zip の置き場")
    parser.add_argument("--out", default="assets/strokes.sqlite",
                        help="出力する SQLite ファイル")
    args = parser.parse_args()
    build(args.work, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
