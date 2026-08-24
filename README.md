# kankaku-oss

漢字アプリ「漢カク」（`kankaku`）の公開情報を置くリポジトリ。

- [プライバシーポリシー](https://juzow13.github.io/kankaku-oss/privacy-policy.html)
- `dict/` … 辞書データ（JMdict / KANJIDIC2）
- `kanjivg/` … 筆順データ（KanjiVG）

## なぜこのリポジトリがあるか

漢カクは JMdict / KANJIDIC2（CC BY-SA 4.0）と KanjiVG（CC BY-SA 3.0）を加工して
使っている。どちらも SA（継承）条件付きのライセンスのため、**加工プログラムと
加工後のデータを、元と同じライセンスで公開する**必要がある（アプリ本体のコードは
対象外）。このリポジトリはその公開先。

| ディレクトリ | 元データ | ライセンス | 帰属表示 |
|---|---|---|---|
| `dict/` | JMdict / KANJIDIC2 | CC BY-SA 4.0 | Electronic Dictionary Research and Development Group (EDRDG) |
| `kanjivg/` | KanjiVG | CC BY-SA 3.0 | Ulrich Apel |

各ディレクトリの中身:

- `build_*.py` … 元データをダウンロードしてアプリ同梱用 SQLite を作るプログラム。
- `export_*.py` … その SQLite の中身を、圧縮を解いて誰でも読める JSON に書き出すプログラム。
- `entries.json` / `strokes.json` … 実際に書き出した中身。

## ライセンス表記

> This repository uses JMdict/KANJIDIC2 by the Electronic Dictionary Research
> and Development Group (EDRDG), licensed under CC BY-SA 4.0, and KanjiVG by
> Ulrich Apel, licensed under CC BY-SA 3.0. The processing scripts and the
> resulting data in this repository are made available under the same
> licenses (CC BY-SA 4.0 for `dict/`, CC BY-SA 3.0 for `kanjivg/`).
