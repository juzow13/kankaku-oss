#!/usr/bin/env python3
"""assets/strokes.sqlite を、圧縮を展開して読める JSON に書き出す。

使い方:
    python3 cc-by-sa/kanjivg/export_strokes.py \
        --db assets/strokes.sqlite --out cc-by-sa/kanjivg/strokes.json

strokes テーブルの paths 列は、SVG パス列＋番号座標を 1 本の文字列に
まとめて zlib 圧縮した BLOB（build_strokes.py 参照）。ここではそれを
展開し、パス列と番号座標を分けて JSON に出す。

出力の 1 件:
    {
      "char": "亜",
      "count": 7,
      "paths": ["M...", "M...", ...],   // 書き順どおり、1 画 1 要素
      "numbers": [[x, y], ...]           // 画の番号を置く座標。無い字は []
    }

データ: KanjiVG (C) Ulrich Apel, CC BY-SA 3.0。抽出プログラムは
build_strokes.py（同じディレクトリ）。
"""

import argparse
import json
import sqlite3
import zlib


def parse_blob(blob: bytes) -> tuple[list[str], list[list[float]]]:
    text = zlib.decompress(blob).decode()
    paths_text, _, numbers_text = text.partition("\n\n")
    paths = paths_text.split("\n") if paths_text else []
    numbers = []
    if numbers_text:
        for pair in numbers_text.split(" "):
            x, y = pair.split(",")
            numbers.append([float(x), float(y)])
    return paths, numbers


def export(db_path: str, out_path: str) -> int:
    db = sqlite3.connect(db_path)
    rows = []
    for char, blob, count in db.execute(
        "SELECT char, paths, count FROM strokes ORDER BY char"
    ):
        paths, numbers = parse_blob(blob)
        rows.append(
            {"char": char, "count": count, "paths": paths, "numbers": numbers}
        )
    with open(out_path, "w") as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=0)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="assets/strokes.sqlite")
    parser.add_argument("--out", default="cc-by-sa/kanjivg/strokes.json")
    args = parser.parse_args()
    count = export(args.db, args.out)
    print(f"{count} 字 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
