#!/usr/bin/env python3
"""JMdict / KANJIDIC2 / JMnedict から、アプリ同梱用の SQLite 辞書を作る。

使い方:
    python3 tool/build_dict.py --work /tmp/kanjinow-dict --out assets/dict.sqlite

--work に JMdict_e / kanjidic2.xml / JMnedict.xml が無ければ EDRDG から
ダウンロードする。

出力する entries テーブルは「読み（ひらがな）→ 表記」の 1 対 1 の行。
JMdict / JMnedict の 1 エントリは複数の表記・読みを持つので、その組み合わせを
展開して入れている。検索は reading の前方一致だけなので、reading に対する
`entries` の主キー（reading, surface）の範囲検索で足りる（後述）。

`kind` 列は候補の種別のビット和。0 = 一般語（JMdict / KANJIDIC2）、それ以外は
1 = 地名、2 = 駅名、4 = 姓、8 = 名（いずれも JMnedict）を OR で足したもの
（「田中」は place, surname を兼ねるので 1|4 = 5）。固有名詞は JMdict /
KANJIDIC2 を入れ終わったあとに `INSERT OR IGNORE` で足すので、既存の一般語を
上書きしない（「東京」のように両方にある語は一般語として扱われる）。
固有名詞の `freq` は JMnedict に頻度情報が無いため、立っているビット数
（＝何役兼ねているか）を有名さの代理指標にして負の値で入れる。一般語の
freq は 0 以上なので、固有名詞は必ず一般語より下に出る。

`idx_reading` インデックスは作らない。`entries` は `WITHOUT ROWID` で主キーが
`(reading, surface)` なので、前方一致の範囲検索は主キーだけで足りる
（`ORDER BY` はどの索引を張っても一時ソートになるため、reading 用の
インデックスは速度に寄与しない）。

データ: JMdict / KANJIDIC2 / JMnedict (C) Electronic Dictionary Research and
Development Group, CC BY-SA 4.0。
"""

import argparse
import gzip
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET

JMDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz"
KANJIDIC_URL = "http://www.edrdg.org/kanjidic/kanjidic2.xml.gz"
JMNEDICT_URL = "http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz"

# JMnedict の name_type → kind のビット。複数該当するときは OR で足しこむ
# （「田中」は place, surname の両方なので 1|4 = 5。どちらか一方に決めると
# 「田中＝地名」のような誤ったラベルになるため、集合として持つ）。
NAME_TYPE_KIND = {
    "place": 1,
    "station": 2,
    "surname": 4,
    "given": 8,
    "fem": 8,
    "masc": 8,
    "person": 8,
}

# JMdict の優先度タグ → スコア。よく使う語ほど候補の上に出す。
PRI_SCORE = {
    "ichi1": 60,
    "news1": 50,
    "spec1": 50,
    "gai1": 30,
    "ichi2": 20,
    "news2": 20,
    "spec2": 20,
    "gai2": 10,
}

# 候補として見せたくない表記・読み。JMdict の実体参照名で判定する
# （rK=まれな漢字表記, iK=不規則, oK=旧字体, sK=検索専用）。
SKIP_KE_INF = {"rK", "iK", "oK", "sK"}
SKIP_RE_INF = {"ik", "ok", "sk", "rk"}

# 古語・廃語のたぐい。1 つでも付いていない語義があれば残す。
ARCHAIC_MISC = {"arch", "obs", "obsc", "rare", "hist"}

# 慣用句・ことわざなど「単語ではないもの」。全語義がこれなら落とす。
PHRASE_POS = {"exp", "proverb", "quote"}

KATA_TO_HIRA_OFFSET = 0x60


def katakana_to_hiragana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - KATA_TO_HIRA_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def is_kana(text: str) -> bool:
    return all(0x3041 <= ord(ch) <= 0x309F or ch in "ー・" for ch in text)


def has_kanji(text: str) -> bool:
    return any(0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF
               for ch in text)


def download(url: str, dest: str) -> None:
    if os.path.exists(dest):
        print(f"  すでにある: {dest}")
        return
    print(f"  ダウンロード: {url}")
    gz = dest + ".gz"
    urllib.request.urlretrieve(url, gz)
    with gzip.open(gz, "rb") as fin, open(dest, "wb") as fout:
        fout.write(fin.read())


def load_xml(path: str) -> ET.Element:
    """DTD 内で定義された実体参照（&unc; など）を素通しさせて読む。"""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    # ElementTree は内部 DTD の ENTITY を解決しないので、名前そのものに置換する。
    raw = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;)([A-Za-z0-9_.-]+);", r"\1", raw)
    return ET.fromstring(raw)


def priority_score(elem: ET.Element, tag: str) -> int:
    score = 0
    for pri in elem.findall(tag):
        text = (pri.text or "").strip()
        if text in PRI_SCORE:
            score += PRI_SCORE[text]
        elif text.startswith("nf"):
            # nf01（最頻）〜 nf48。小さいほど頻出。
            try:
                score += max(0, 50 - int(text[2:]))
            except ValueError:
                pass
    return score


def skipped(elem: ET.Element, tag: str, codes: set) -> bool:
    return any((inf.text or "").strip() in codes for inf in elem.findall(tag))


def unwanted_entry(entry: ET.Element, freq: int) -> bool:
    """古語・専門語・慣用句を落とす。"""
    senses = entry.findall("sense")
    if not senses:
        return True

    def tags(sense, tag):
        return {(t.text or "").strip() for t in sense.findall(tag)}

    # すべての語義が古語・廃語なら落とす。
    if all(tags(s, "misc") & ARCHAIC_MISC for s in senses):
        return True
    # すべての語義が慣用句・ことわざなら落とす（単語だけを出したい）。
    if all(tags(s, "pos") & PHRASE_POS for s in senses):
        return True
    # すべての語義が分野タグ付き（専門語）で、かつよく使う語でもないなら落とす。
    if freq == 0 and all(tags(s, "field") for s in senses):
        return True
    return False


def parse_jmdict(path: str):
    """(reading, surface, freq) を生成する。"""
    root = load_xml(path)
    for entry in root.findall("entry"):
        kanji_forms = []
        for k_ele in entry.findall("k_ele"):
            keb = k_ele.findtext("keb")
            if not keb or skipped(k_ele, "ke_inf", SKIP_KE_INF):
                continue
            if not has_kanji(keb):
                continue
            kanji_forms.append((keb, priority_score(k_ele, "ke_pri")))
        if not kanji_forms:
            continue  # かなだけの語は「書けない漢字」ではないので落とす。

        readings = []
        for r_ele in entry.findall("r_ele"):
            reb = r_ele.findtext("reb")
            if not reb or skipped(r_ele, "re_inf", SKIP_RE_INF):
                continue
            hira = katakana_to_hiragana(reb)
            if not is_kana(hira):
                continue
            restr = [r.text for r in r_ele.findall("re_restr") if r.text]
            readings.append((hira, priority_score(r_ele, "re_pri"), restr))
        if not readings:
            continue

        best_freq = max(k for _, k in kanji_forms) + max(r for _, r, _ in readings)
        if unwanted_entry(entry, best_freq):
            continue

        for keb, k_score in kanji_forms:
            for hira, r_score, restr in readings:
                # re_restr があるときは、その表記にだけ結び付く読み。
                if restr and keb not in restr:
                    continue
                yield hira, keb, k_score + r_score


def drop_derived_compounds(rows):
    """「顧客ニーズ」のような、既存の語＋αの複合語を落とす。

    表記も読みも他の語の前方一致になっているものを派生とみなす。ただし
    「日本語」のようによく使う語は、派生形でもそれ自体が 1 語なので残す。
    """
    by_surface = {}
    for reading, surface, _ in rows:
        by_surface.setdefault(surface, []).append(reading)

    kept = []
    dropped = 0
    for reading, surface, freq in rows:
        if freq == 0 and _is_derived(reading, surface, by_surface):
            dropped += 1
            continue
        kept.append((reading, surface, freq))
    print(f"   複合語として除外: {dropped} 行")
    return kept


def _is_derived(reading: str, surface: str, by_surface: dict) -> bool:
    for cut in range(1, len(surface)):
        base = surface[:cut]
        for base_reading in by_surface.get(base, ()):
            if len(base_reading) < len(reading) and reading.startswith(base_reading):
                return True
    return False


def parse_kanjidic(path: str):
    """単漢字を (reading, surface, freq) で生成する。"""
    root = load_xml(path)
    for character in root.findall("character"):
        literal = character.findtext("literal")
        if not literal:
            continue

        misc = character.find("misc")
        freq_rank = None
        if misc is not None:
            freq_text = misc.findtext("freq")
            if freq_text:
                try:
                    freq_rank = int(freq_text)
                except ValueError:
                    pass
        # freq は 1〜2500 位の頻度順位。常用漢字は順位が無くても少し下駄をはかせる。
        if freq_rank:
            score = max(10, 120 - freq_rank // 25)
        elif misc is not None and misc.findtext("grade"):
            score = 15
        else:
            continue  # 順位も学年も無い字は候補が荒れるので入れない。

        readings = {}
        for reading in character.iter("reading"):
            r_type = reading.get("r_type")
            text = reading.text or ""
            if r_type == "ja_on":
                readings[katakana_to_hiragana(text)] = score
            elif r_type == "ja_kun":
                # 「おこな.う」は送り仮名を落として「おこな」にするが、それ単体は
                # 語として成立しないので（「ばら」→ 散 など）スコアを大きく下げ、
                # ちゃんとした語の下に沈める。
                stem = text.split(".")[0].replace("-", "")
                trimmed = "." in text
                readings[stem] = max(readings.get(stem, 0),
                                     score // 5 if trimmed else score)
        for reading, reading_score in readings.items():
            if reading and is_kana(reading):
                yield reading, literal, reading_score


def parse_jmnedict(path: str):
    """(reading, surface, kind) を生成する。kind は該当するビットの OR。

    name_type から kind のビット（1=地名, 2=駅名, 4=姓, 8=名）を決める。
    複数の name_type を持つエントリはビットを OR で足しこむ（「田中」なら
    place と surname の両方が立って 1|4 = 5）。place/station/surname/
    given/fem/masc/person のどれでもない（unclass や organization など）
    エントリは落とす。漢字表記（keb）が無いエントリも落とす
    （書けない字が無いので辞書に足す用が無い）。

    同じ（読み, 表記）の組が複数のエントリにまたがって出てくることがある
    （例えば「田中」が姓のエントリと地名のエントリの両方に載っている）ため、
    ここでは 1 エントリぶんの kind だけを返す。エントリをまたいだ OR の
    積み上げは呼び出し側（`build`）でまとめて行う。
    """
    root = load_xml(path)
    for entry in root.findall("entry"):
        kebs = [k.text for k in entry.findall("k_ele/keb") if k.text]
        if not kebs:
            continue

        kind = 0
        for trans in entry.findall("trans"):
            for name_type in trans.findall("name_type"):
                kind |= NAME_TYPE_KIND.get((name_type.text or "").strip(), 0)
        if kind == 0:
            continue

        rebs = [r.text for r in entry.findall("r_ele/reb") if r.text]
        for keb in kebs:
            for reb in rebs:
                hira = katakana_to_hiragana(reb)
                if not is_kana(hira):
                    continue
                yield hira, keb, kind


def merge_jmnedict_kinds(rows):
    """エントリをまたいだ同じ（読み, 表記）の kind を OR でまとめる。"""
    merged = {}
    for reading, surface, kind in rows:
        key = (reading, surface)
        merged[key] = merged.get(key, 0) | kind
    for (reading, surface), kind in merged.items():
        yield reading, surface, kind


def freq_for_kind(kind: int) -> int:
    """固有名詞の freq。JMnedict には頻度情報が無いので、立っているビット数
    （何役兼ねているか）を有名さの代理指標にして負の値にする。一般語の freq
    は 0 以上なので、これで固有名詞は必ず一般語より下に出る。
    """
    bit_count = bin(kind).count("1")
    return -(4 - min(bit_count, 3))


def build(work: str, out: str) -> None:
    os.makedirs(work, exist_ok=True)
    jmdict = os.path.join(work, "JMdict_e")
    kanjidic = os.path.join(work, "kanjidic2.xml")
    jmnedict = os.path.join(work, "JMnedict.xml")

    print("1) ソースの準備")
    download(JMDICT_URL, jmdict)
    download(KANJIDIC_URL, kanjidic)
    download(JMNEDICT_URL, jmnedict)

    if os.path.exists(out):
        os.remove(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    print("2) SQLite を作る")
    db = sqlite3.connect(out)
    db.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        CREATE TABLE entries (
          reading  TEXT NOT NULL,
          surface  TEXT NOT NULL,
          freq     INTEGER NOT NULL DEFAULT 0,
          is_kanji INTEGER NOT NULL DEFAULT 0,
          kind     INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (reading, surface)
        ) WITHOUT ROWID;
        """
    )

    print("3) JMdict を読む")
    jm_rows = list(parse_jmdict(jmdict))
    print(f"   JMdict: {len(jm_rows)} 行")

    print("4) 複合語をふるい落とす")
    jm_rows = drop_derived_compounds(jm_rows)
    for chunk in batched(iter(jm_rows), 20000):
        db.executemany(
            "INSERT OR REPLACE INTO entries (reading, surface, freq, is_kanji, kind)"
            " VALUES (?, ?, ?, 0, 0)",
            chunk,
        )
    print(f"   残り: {len(jm_rows)} 行")

    print("5) KANJIDIC2 を読む")
    kanji_rows = 0
    for chunk in batched(parse_kanjidic(kanjidic), 20000):
        # 同じ読み・表記が JMdict 側にもある場合は、単漢字としての情報を優先する。
        db.executemany(
            "INSERT OR REPLACE INTO entries (reading, surface, freq, is_kanji, kind)"
            " VALUES (?, ?, ?, 1, 0)",
            chunk,
        )
        kanji_rows += len(chunk)
    print(f"   KANJIDIC2: {kanji_rows} 行")

    print("6) JMnedict（地名・駅名・姓・名）を読む")
    # 一般語（JMdict / KANJIDIC2）を上書きしないよう OR IGNORE で足す。
    # 「東京」のように両方にある語は一般語のまま（freq 付き）で残る。
    # 同じ（読み, 表記）が複数の JMnedict エントリにまたがることがあるので、
    # まず kind を OR でまとめてから freq を決める（立っているビット数が
    # 多いほど「地名でも姓でもある」= 有名、の代理指標として freq を上げる）。
    name_rows = 0
    merged = merge_jmnedict_kinds(parse_jmnedict(jmnedict))
    for chunk in batched(merged, 20000):
        rows = [
            (reading, surface, freq_for_kind(kind), kind)
            for reading, surface, kind in chunk
        ]
        db.executemany(
            "INSERT OR IGNORE INTO entries (reading, surface, freq, is_kanji, kind)"
            " VALUES (?, ?, ?, 0, ?)",
            rows,
        )
        name_rows += len(rows)
    print(f"   JMnedict: {name_rows} 行")

    print("7) 仕上げ")
    # idx_reading は張らない。entries は WITHOUT ROWID で主キーが
    # (reading, surface) なので、前方一致の範囲検索は主キーの
    # SEARCH ... USING PRIMARY KEY で足りる。ORDER BY はどの索引でも
    # 一時 B-tree ソートになり索引の恩恵を受けないため、reading 用の
    # インデックスは容量が増えるだけの重複になる。
    db.commit()
    db.execute("VACUUM")
    db.close()

    size_mb = os.path.getsize(out) / 1024 / 1024
    print(f"完了: {out} ({size_mb:.1f} MB)")


def batched(iterable, size):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", default="build/dict-src",
                        help="JMdict_e / kanjidic2.xml / JMnedict.xml の置き場")
    parser.add_argument("--out", default="assets/dict.sqlite",
                        help="出力する SQLite ファイル")
    args = parser.parse_args()
    build(args.work, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
