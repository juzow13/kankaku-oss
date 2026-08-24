#!/usr/bin/env python3
"""assets/dict.sqlite の entries テーブルを、そのまま JSON に書き出す。

使い方:
    python3 cc-by-sa/dict/export_entries.py \
        --db assets/dict.sqlite --out cc-by-sa/dict/entries.json

entries テーブルの列を増やしたら、この抽出リストもあわせて直すこと。

データ: JMdict/KANJIDIC2 (EDRDG, CC BY-SA 4.0)。抽出プログラムは
build_dict.py（同じディレクトリ）。
"""

import argparse
import json
import sqlite3


def export(db_path: str, out_path: str) -> int:
    db = sqlite3.connect(db_path)
    rows = [
        {"reading": r, "surface": s, "freq": f, "is_kanji": k, "kind": kd}
        for r, s, f, k, kd in db.execute(
            "SELECT reading, surface, freq, is_kanji, kind FROM entries"
            " ORDER BY reading, freq DESC"
        )
    ]
    with open(out_path, "w") as fp:
        json.dump(rows, fp, ensure_ascii=False, indent=0)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="assets/dict.sqlite")
    parser.add_argument("--out", default="cc-by-sa/dict/entries.json")
    args = parser.parse_args()
    count = export(args.db, args.out)
    print(f"{count} 件 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
