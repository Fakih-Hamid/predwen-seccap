import argparse
import html
import os
import shutil
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSIONS = os.path.join(ROOT, "content", "missions")
OUT = os.path.join(ROOT, "guides")

ORDER = ["m1-digital-footprint.yaml",
         "m2-fake-infrastructure.yaml",
         "m3-threat-intelligence.yaml"]

LANGS = ["ja", "en"]

STEPS = [("資料を調べる", "Explore the sources"),
         ("観察メモを書く", "Post observations"),
         ("根拠をつけて答える", "Answer with evidence"),
         ("見直してロックする", "Review and lock")]

CATEGORY = {
    "correctness":  ("正答", "Correct answers"),
    "evidence":     ("根拠", "Evidence attached"),
    "reasoning":    ("論証", "Written reasoning"),
    "completeness": ("網羅性", "Coverage"),
    "speed":        ("時間ボーナス", "Pace bonus"),
}

TYPES = {
    "short_text":   ("短答式", "short answer"),
    "choice":       ("選択式（1つ）", "one answer"),
    "multi_choice": ("選択式（複数）", "several answers"),
    "free_text":    ("記述式", "written"),
    "order":        ("並べ替え", "ordering"),
}


def esc(s):
    return html.escape(str(s or ""))


def tx(node, lang):
    return node.get(lang) or "" if isinstance(node, dict) else (node or "")


def wanted(ja, en):
    """The (lang, text) pairs this sheet prints, in order, deduplicated."""
    out, seen = [], set()
    for lang, text in (("ja", ja), ("en", en)):
        if lang in LANGS and text and text not in seen:
            seen.add(text)
            out.append((lang, text))
    return out


def H(ja, en):
    """A section heading: lead language large, the other beside it."""
    parts = wanted(ja, en)
    if not parts:
        return ""
    if len(parts) == 1:
        return esc(parts[0][1])
    return '%s <span class="en">%s</span>' % (esc(parts[0][1]), esc(parts[1][1]))


def block(ja, en, cls=""):
    """Stacked paragraphs, one per language."""
    return "".join('<p class="%s %s">%s</p>' % (lang, cls, esc(text))
                   for lang, text in wanted(ja, en))


def inline(ja, en):
    """Both languages inside one cell, the second one small."""
    parts = wanted(ja, en)
    if not parts:
        return ""
    if len(parts) == 1:
        return esc(parts[0][1])
    return '%s<span class="en">%s</span>' % (esc(parts[0][1]), esc(parts[1][1]))


def label(ja, en, joiner=" / "):
    """A short caption, both languages on one line."""
    return joiner.join(esc(text) for _, text in wanted(ja, en))


def node(n, cls=""):
    return block(tx(n, "ja"), tx(n, "en"), cls)


def minutes(seconds):
    return "%d" % round((seconds or 0) / 60.0)


def build(path):
    d = yaml.safe_load(open(path, encoding="utf-8"))
    n = d.get("order", 0)
    out = []
    a = out.append

    a('<section class="sheet">')

    a('<header>')
    a('<div class="eyebrow">Predwen SECCAP · %s %d</div>'
      % (label("ミッション", "Mission"), n))
    titles = wanted(tx(d.get("title"), "ja"), tx(d.get("title"), "en"))
    a('<h1>%s</h1>' % esc(titles[0][1]))
    if len(titles) > 1:
        a('<h2>%s</h2>' % esc(titles[1][1]))
    a('<p class="sub">%s</p>'
      % inline(tx(d.get("subtitle"), "ja"), tx(d.get("subtitle"), "en")))
    a('<div class="meta"><span>%s %s</span><span>%s %s %s</span><span>%s</span></div>'
      % (d.get("max_points", 0), label("点", "points"),
         label("目安", "about"), minutes(d.get("duration_seconds")),
         label("分", "min"),
         label("設問：%d問" % len(d.get("questions") or []),
               "questions %d" % len(d.get("questions") or []))))
    a('</header>')

    a('<div class="block"><h3>%s</h3><div class="cols">' % H("状況", "The situation"))
    ja_paras = tx(d.get("narrative"), "ja").split("\n\n")
    en_paras = tx(d.get("narrative"), "en").split("\n\n")
    for i in range(max(len(ja_paras), len(en_paras))):
        a(block(ja_paras[i] if i < len(ja_paras) else "",
                en_paras[i] if i < len(en_paras) else ""))
    a('</div></div>')

    a('<div class="block"><h3>%s</h3><ol class="obj cols">'
      % H("このミッションですること", "What you are asked to do"))
    for o in d.get("objectives") or []:
        a('<li>%s</li>' % inline(tx(o, "ja"), tx(o, "en")))
    a('</ol></div>')

    if d.get("first_step"):
        a('<div class="block callout"><h3>%s</h3>%s</div>'
          % (H("まず取り組むこと", "Your first move"), node(d["first_step"])))

    a('<div class="block"><h3>%s</h3><ol class="steps">'
      % H("画面での操作手順", "How the screen works"))
    for i, (ja, en) in enumerate(STEPS, 1):
        a('<li><b>%d.</b> %s</li>' % (i, inline(ja, en)))
    a('</ol>')
    loop = d.get("loop") or []
    if loop:
        for lang, joiner, lead in (("ja", " ／ ", "時間配分の目安："),
                                   ("en", " · ", "Suggested pacing: ")):
            if lang in LANGS:
                a('<p class="hint %s">%s%s</p>'
                  % (lang, lead, joiner.join(esc(tx(l, lang)) for l in loop)))
    a('</div>')

    a('<div class="block"><h3>%s</h3><table class="src"><thead><tr>'
      '<th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>'
      % (H("資料", "Your sources"),
         label("資料", "Source"),
         label("記録内容", "What it records"),
         label("ツール", "Tool")))
    for art in d.get("artifacts") or []:
        a('<tr><td class="name"><b>%s</b></td><td>%s</td><td class="tool">%s</td></tr>'
          % (inline(tx(art.get("title"), "ja"), tx(art.get("title"), "en")),
             inline(tx(art.get("note"), "ja"), tx(art.get("note"), "en")),
             esc(art.get("tool_name") or art.get("tool") or "")))
    a('</tbody></table>')
    a(block("表示されている資料をすべて開くと、次の資料が順に届きます。まず、表示中の資料を確認してください。",
            "Open the sources already on the screen and the next one arrives. "
            "Start with what you can see.", "hint"))
    a('</div>')

    rows = []
    for art in d.get("artifacts") or []:
        how = art.get("how_to") or {}
        cmds = [(s.get("os"), s["cmd"]) for s in (how.get("steps") or [])
                if isinstance(s, dict) and s.get("cmd")]
        cmds += [(c.get("os"), c["cmd"]) for c in (how.get("commands") or [])]
        for os_label, cmd in cmds:
            rows.append((art.get("title"), os_label, cmd))
    if rows:
        a('<div class="block"><h3>%s</h3><table class="cmd"><tbody>'
          % H("コマンド", "Commands"))
        last = None
        for title, os_label, cmd in rows:
            key = tx(title, "en")
            cell = inline(tx(title, "ja"), tx(title, "en")) if key != last else ""
            last = key
            a('<tr><td class="for">%s</td><td class="os">%s</td>'
              '<td><code>%s</code></td></tr>'
              % (cell, label(tx(os_label, "ja"), tx(os_label, "en")), esc(cmd)))
        a('</tbody></table>')
        a(block("各資料のページに、同じコマンドと、インストールできない場合の代替手段があります。",
                "Each source page carries the same commands, plus what to do if you "
                "cannot install the tool.", "hint"))
        a('</div>')

    notices, seen = [], set()
    for art in d.get("artifacts") or []:
        sn = art.get("safety_notice")
        if sn and tx(sn, "en") not in seen:
            seen.add(tx(sn, "en"))
            notices.append(sn)
    if notices:
        a('<div class="block rules"><h3>%s</h3><ul>' % H("守ること", "Rules"))
        for sn in notices:
            a('<li>%s</li>' % inline(tx(sn, "ja"), tx(sn, "en")))
        a('</ul></div>')

    a('<div class="block"><h3>%s</h3><table class="pts"><tbody>'
      % H("配点", "How this is scored"))
    for key, value in (d.get("budget") or {}).items():
        ja, en = CATEGORY.get(key, (key, key))
        a('<tr><td>%s</td><td class="n">%s %s</td></tr>'
          % (inline(ja, en), value, label("点", "pts")))
    a('</tbody></table>')
    a(block("回答の正確さだけでなく、根拠となる資料の提示も採点対象です。回答を入力したら、根拠となる資料を必ず選んでください。",
            "Evidence is worth almost as much as the answer itself. After writing an "
            "answer, always name the source it came from.", "hint"))

    kinds = {}
    for q in d.get("questions") or []:
        if q.get("points", 0) or q.get("evidence_points", 0):
            kinds[q["type"]] = kinds.get(q["type"], 0) + 1
    if kinds:
        def group(kind, n):
            ja_label, en_label = TYPES.get(kind, (kind, kind))
            return label("%s：%d問" % (ja_label, n), "%d %s" % (n, en_label))

        a('<p class="hint">'
          + " · ".join(group(k, v) for k, v in kinds.items())
          + '</p>')
    rubric = d.get("rubric") or []
    if rubric:
        total = sum(r["points"] for r in rubric)
        a(block("このほかに記述式の設問が%d問あり、合計%d点です。記述内容はファシリテーターが読んで採点します。"
                % (len(rubric), total),
                "There are also %d written questions, worth %d points together, read "
                "and marked by the facilitator." % (len(rubric), total), "hint"))
    a('</div>')

    a('<div class="block callout"><h3>%s</h3>' % H("行き詰まったら", "If you get stuck"))
    a(block("設問ごとに「この設問のヒント」があります。最初のヒントでは、確認する場所を案内します。ボタンを押すたびに、より具体的なヒントが1つずつ表示されます。資料ページの「行き詰まりましたか？」からは、その資料に関するヒントを確認できます。ヒントを使っても減点はありません。",
            'Every question has its own "Hint for this question". The first press asks '
            '"Where do I look?", and each press after that is one step more specific. '
            'Every source page has "Stuck?" for hints about that source. Hints do not '
            'reduce your score.'))
    a(block("資料が読み込めない場合や、アクセスがブロックされたことを示すページが表示された場合は、スマートフォンのテザリングに接続して、もう一度試してください。",
            "If a source does not load, or you get a page saying it is blocked, "
            "connect to a phone hotspot and try again.", "net"))
    a('</div>')

    a('<footer><span>Predwen SECCAP · %s</span><span>%s</span></footer>'
      % (esc(d["slug"]), label("この用紙に答えは載っていません", "no answers on this sheet")))
    a('</section>')
    return "\n".join(out)


CSS = """
/* ONE SHEET OF PAPER PER MISSION, printed both sides. The first draft ran to
   three A4 pages for Mission 3 — nine pages per team across the afternoon,
   which is a stack nobody reads. The prose sets in two columns, the source
   notes are small, and the type is tuned so the densest mission (eleven
   sources) still lands inside two sides. */
@page { size: A4; margin: 10mm 9mm; }
* { box-sizing: border-box; }
body { margin: 0; font-family: "Yu Gothic UI", "Hiragino Kaku Gothic ProN",
       "Noto Sans JP", "Segoe UI", system-ui, sans-serif;
       font-size: 8.2pt; line-height: 1.28; color: #111; background: #f4f4f6; }
.cols { column-count: 2; column-gap: 5mm; }
.cols > * { break-inside: avoid; }
.sheet { background: #fff; margin: 0 auto 10mm; max-width: 188mm; }
@media print { body { background: #fff; } .sheet { page-break-after: always; margin: 0; }
               .sheet:last-child { page-break-after: auto; } }

header { border-bottom: 2.2pt solid #111; padding-bottom: 2mm; margin-bottom: 3mm; }
.eyebrow { font-size: 7.4pt; letter-spacing: .12em; text-transform: uppercase;
           color: #666; font-weight: 600; }
h1 { font-size: 13.4pt; margin: .8mm 0 0; line-height: 1.18; }
h2 { font-size: 9.4pt; margin: .5mm 0 0; font-weight: 600; color: #333; }
.sub { margin: 1.4mm 0 0; font-size: 8.6pt; color: #444; }
.sub .en { display: block; font-size: 7.9pt; color: #666; }
.meta { display: flex; gap: 4mm; margin-top: 1.8mm; font-size: 8pt; }
.meta span { background: #eef0f3; padding: .5mm 2mm; border-radius: 2pt; font-weight: 600; }

h3 { font-size: 8.8pt; margin: 0 0 1.1mm; padding-bottom: .5mm;
     border-bottom: .6pt solid #c9ccd2; }
h3 .en { font-weight: 500; color: #6a6f78; font-size: 8pt; margin-left: 2mm; }
.block { margin-bottom: 2.1mm; }
p { margin: 0 0 1.1mm; }
.en { color: #565b64; }
p.en { font-size: 7.7pt; margin-bottom: 1.3mm; }
.hint { font-size: 7pt; color: #4a4f57; margin-top: .5mm; }

.callout { background: #f2f5fa; border-left: 2.4pt solid #2b5f9e; padding: 2mm 2.6mm; }
.rules { background: #fdf4f2; border-left: 2.4pt solid #b0442c; padding: 2mm 2.6mm; }
.rules ul, .obj { margin: 0; padding-left: 4.6mm; }
.obj li, .rules li { margin-bottom: 1.1mm; }
.obj .en, .rules .en { display: block; font-size: 7.6pt; }

ol.steps { display: flex; gap: 2mm; list-style: none; margin: 0; padding: 0; }
ol.steps li { flex: 1; background: #eef0f3; padding: 1mm 1.4mm; border-radius: 2pt;
              font-size: 7.7pt; }
ol.steps .en { display: block; font-size: 7.1pt; }

table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 7.4pt; color: #555; border-bottom: .8pt solid #111;
     padding: .7mm 1.2mm; }
td { padding: .55mm 1.1mm; border-bottom: .4pt solid #dcdfe4; vertical-align: top;
     font-size: 7.7pt; }
td .en { display: block; font-size: 7.1pt; }
.src .name { width: 30%; }
.src .tool { width: 16%; font-size: 7.3pt; color: #333; }
.later { display: block; font-size: 7pt; color: #8a5a00; font-weight: 600; margin-top: .3mm; }
.cmd .for { width: 25%; font-size: 7.3pt; color: #333; }
.cmd .os { width: 22%; font-size: 7.1pt; color: #666; }
code { font-family: "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
       font-size: 7.4pt; word-break: break-all; }
.pts { width: 58%; }
.pts .n { text-align: right; width: 20%; font-variant-numeric: tabular-nums; }
.net { margin-top: 1.2mm; padding-top: 1mm; border-top: .4pt dotted #9aa0a8; }

footer { margin-top: 2.6mm; padding-top: 1.2mm; border-top: .6pt solid #c9ccd2;
         font-size: 7.2pt; color: #777; display: flex; justify-content: space-between; }
"""


def page(body, title, lang):
    return ("<!doctype html>\n<html lang=\"%s\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n%s\n</body>\n</html>\n"
            % (lang, esc(title), CSS, body))


def find_browser():
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            return p
    return None


def to_pdf(browser, html_path, pdf_path):
    subprocess.run([browser, "--headless", "--disable-gpu",
                    "--no-pdf-header-footer",
                    "--print-to-pdf=" + pdf_path,
                    "file:///" + os.path.abspath(html_path).replace("\\", "/")],
                   check=True, capture_output=True, timeout=180)
    return os.path.exists(pdf_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", default="both", choices=["both", "ja", "en"])
    parser.add_argument("--out", default=OUT)
    parser.add_argument("--pdf", action="store_true", help="also render PDFs")
    args = parser.parse_args()

    global LANGS
    LANGS = ["ja", "en"] if args.lang == "both" else [args.lang]
    suffix = "" if args.lang == "both" else "-" + args.lang
    lead = LANGS[0]

    os.makedirs(args.out, exist_ok=True)
    sheets, made = [], []
    for name in ORDER:
        path = os.path.join(MISSIONS, name)
        if not os.path.exists(path):
            print("absent:", name)
            continue
        sheet = build(path)
        sheets.append(sheet)
        one = os.path.join(args.out, name.replace(".yaml", suffix + ".html"))
        with open(one, "w", encoding="utf-8", newline="\n") as f:
            f.write(page(sheet, name.replace(".yaml", ""), lead))
        made.append(one)

    all_path = os.path.join(args.out, "mission-guides" + suffix + ".html")
    with open(all_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(page("\n".join(sheets), "Predwen SECCAP — mission guides", lead))
    made.append(all_path)

    if args.pdf:
        browser = find_browser()
        if not browser:
            print("  aucun navigateur trouve pour le PDF (Edge ou Chrome)")
        else:
            for h in list(made):
                pdf = h[:-5] + ".pdf"
                try:
                    if to_pdf(browser, h, pdf):
                        made.append(pdf)
                except Exception as exc:                    # noqa: BLE001
                    print("  PDF echec %s: %s" % (os.path.basename(h), exc))

    for p in made:
        print("   ", os.path.relpath(p, ROOT))


if __name__ == "__main__":
    sys.exit(main())
